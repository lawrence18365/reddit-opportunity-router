"""Scanning, persistence, and notification orchestration for portfolio routing."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import notifications, portfolio, reddit, store


def _dispatch_notifications(
    conn: Any,
    match: dict[str, Any],
    notification_config: dict[str, Any],
) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for channel in notifications.configured_channels(notification_config):
        channel_id = str(channel.get("id") or channel.get("type") or "unknown")
        if store.notification_delivered(conn, match["post_id"], match["project_id"], channel_id):
            outcomes.append({"channel": channel_id, "status": "deduplicated", "error": ""})
            continue
        try:
            notifications.send(channel, match)
        except notifications.DELIVERY_ERRORS as error:
            result = {"channel": channel_id, "status": "failed", "error": str(error)}
            store.record_notification_attempt(
                conn,
                match["post_id"],
                match["project_id"],
                channel_id,
                delivered=False,
                error=str(error),
            )
        else:
            result = {"channel": channel_id, "status": "delivered", "error": ""}
            store.record_notification_attempt(
                conn,
                match["post_id"],
                match["project_id"],
                channel_id,
                delivered=True,
            )
        outcomes.append(result)
    return outcomes


def scan(
    *,
    portfolio_config_path: Path | str | None = None,
    notification_config_path: Path | str | None = None,
    limit_per_sub: int | None = None,
    max_requests: int | None = None,
    notify: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch each target community once, route posts, persist, and alert."""
    config = portfolio.load_config(portfolio_config_path)
    notification_config = notifications.load_config(notification_config_path)
    scan_config = config.get("scanner", {}) or {}
    sub_specs = portfolio.subreddit_specs(config)
    limit = int(limit_per_sub or scan_config.get("limit_per_sub", 25))
    request_budget = int(max_requests or scan_config.get("request_budget", 8))
    cost_cap = int(scan_config.get("batch_cost_cap", reddit.BATCH_COST_CAP))
    retention_days = max(1, int(scan_config.get("retention_days", 30)))

    reddit.reset_fetch_stats()
    reddit.set_request_budget(request_budget)
    skipped = reddit.prime_new_cache(
        sub_specs, limit=max(limit, reddit.MULTI_FEED_LIMIT), cost_cap=cost_cap
    )
    skipped_lower = {name.casefold() for name in skipped}
    fetched = 0
    routed = 0
    high_intent = 0
    review_matches = 0
    new_matches: list[dict[str, Any]] = []
    notification_outcomes: list[dict[str, Any]] = []

    with store.connect() as conn:
        pruned = (
            {"matches": 0, "notifications": 0, "posts": 0}
            if dry_run
            else store.prune_portfolio_data(conn, days=retention_days)
        )
        for spec in sub_specs:
            name = str(spec["name"])
            if name.casefold() in skipped_lower:
                continue
            cursor = store.portfolio_cursor(conn, name)
            posts = reddit.fetch_delta(name, cursor, max_limit=limit)
            fetched += len(posts)
            if posts and not dry_run:
                store.update_portfolio_cursor(conn, name, posts[0]["id"])
            elif not posts and not dry_run:
                store.update_portfolio_cursor(conn, name, cursor)

            for post in posts:
                matches = portfolio.route_post(post, config)
                routed += len(matches)
                if not matches:
                    continue
                if not dry_run:
                    stored_post = {**post, "author": "[not retained]"}
                    store.insert_post(conn, stored_post)
                for match in matches:
                    if match["qualification_tier"] == "high_intent":
                        high_intent += 1
                    else:
                        review_matches += 1
                    is_new = True
                    if not dry_run:
                        is_new = store.record_portfolio_match(
                            conn, match, json.dumps(match, ensure_ascii=False, sort_keys=True)
                        )
                    match["is_new"] = is_new
                    if is_new:
                        new_matches.append(match)
                    if notify and not dry_run:
                        results = _dispatch_notifications(conn, match, notification_config)
                        notification_outcomes.extend(
                            [
                                {
                                    "post_id": match["post_id"],
                                    "project_id": match["project_id"],
                                    **result,
                                }
                                for result in results
                            ]
                        )

    stats = reddit.get_fetch_stats()
    if stats.get("rate_limited", 0) or skipped:
        status = "partial"
    elif stats.get("ok", 0) == 0 and stats.get("failed", 0):
        status = "blocked"
    else:
        status = "ok"
    return {
        "status": status,
        "dry_run": dry_run,
        "projects_active": [project["id"] for project in portfolio.active_projects(config)],
        "subreddits_configured": len(sub_specs),
        "subreddits_skipped": skipped,
        "posts_fetched": fetched,
        "routes_found": routed,
        "routes_qualified": high_intent,
        "routes_for_review": review_matches,
        "new_matches": new_matches,
        "new_match_count": len(new_matches),
        "notifications": notification_outcomes,
        "notification_channels": notifications.channel_status(notification_config),
        "retention_days": retention_days,
        "pruned": pruned,
        "reddit_requests": reddit.requests_used(),
        "fetch_stats": stats,
    }


def search(
    *,
    portfolio_config_path: Path | str | None = None,
    notification_config_path: Path | str | None = None,
    days: int = 7,
    limit_per_project: int = 100,
    max_requests: int | None = None,
    notify: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Search globally per project, filter to target communities, persist, and alert."""
    config = portfolio.load_config(portfolio_config_path)
    notification_config = notifications.load_config(notification_config_path)
    scan_config = config.get("scanner", {}) or {}
    projects = portfolio.active_projects(config)
    lookback_days = max(1, int(days))
    limit = max(1, min(100, int(limit_per_project)))
    configured_budget = int(scan_config.get("request_budget", 8))
    request_budget = (
        max(len(projects), configured_budget) if max_requests is None else max(0, int(max_requests))
    )
    retention_days = max(1, int(scan_config.get("retention_days", 30)))
    cutoff = int(time.time()) - lookback_days * 86400

    reddit.reset_fetch_stats()
    reddit.set_request_budget(request_budget)
    fetched = 0
    routed = 0
    high_intent = 0
    review_matches = 0
    skipped_projects: list[str] = []
    project_results: list[dict[str, Any]] = []
    new_matches: list[dict[str, Any]] = []
    notification_outcomes: list[dict[str, Any]] = []

    with store.connect() as conn:
        pruned = (
            {"matches": 0, "notifications": 0, "posts": 0}
            if dry_run
            else store.prune_portfolio_data(conn, days=retention_days)
        )
        for project_index, project in enumerate(projects):
            if reddit.budget_exhausted() or reddit.is_rate_limited():
                skipped_projects.extend(item["id"] for item in projects[project_index:])
                break

            query = portfolio.build_search_query(project)
            posts = reddit.fetch_search(
                None,
                query,
                sort="new",
                limit=limit,
                restrict_sr=False,
            )
            if posts is None:
                project_results.append(
                    {
                        "project_id": project["id"],
                        "query": query,
                        "status": "unreachable",
                        "posts_fetched": 0,
                        "posts_in_window": 0,
                        "routes_found": 0,
                        "high_intent": 0,
                        "review": 0,
                        "new_matches": 0,
                    }
                )
                continue

            fetched += len(posts)
            recent_posts = [post for post in posts if int(post.get("created_utc") or 0) >= cutoff]
            project_match_count = 0
            project_high_count = 0
            project_review_count = 0
            project_new_count = 0
            for post in recent_posts:
                match = portfolio.match_project(post, project, config.get("defaults", {}))
                if match is None:
                    continue
                routed += 1
                project_match_count += 1
                if match["qualification_tier"] == "high_intent":
                    high_intent += 1
                    project_high_count += 1
                else:
                    review_matches += 1
                    project_review_count += 1
                if not dry_run:
                    stored_post = {**post, "author": "[not retained]"}
                    store.insert_post(conn, stored_post)
                is_new = True
                if not dry_run:
                    is_new = store.record_portfolio_match(
                        conn, match, json.dumps(match, ensure_ascii=False, sort_keys=True)
                    )
                match["is_new"] = is_new
                if is_new:
                    project_new_count += 1
                    new_matches.append(match)
                if notify and not dry_run:
                    results = _dispatch_notifications(conn, match, notification_config)
                    notification_outcomes.extend(
                        [
                            {
                                "post_id": match["post_id"],
                                "project_id": match["project_id"],
                                **result,
                            }
                            for result in results
                        ]
                    )

            project_results.append(
                {
                    "project_id": project["id"],
                    "query": query,
                    "status": "ok",
                    "posts_fetched": len(posts),
                    "posts_in_window": len(recent_posts),
                    "routes_found": project_match_count,
                    "high_intent": project_high_count,
                    "review": project_review_count,
                    "new_matches": project_new_count,
                }
            )

    stats = reddit.get_fetch_stats()
    if stats.get("rate_limited", 0) or skipped_projects:
        result_status = "partial"
    elif stats.get("ok", 0) == 0 and stats.get("failed", 0):
        result_status = "blocked"
    else:
        result_status = "ok"
    return {
        "status": result_status,
        "mode": "focused_search",
        "dry_run": dry_run,
        "lookback_days": lookback_days,
        "projects_active": [project["id"] for project in projects],
        "projects_skipped": skipped_projects,
        "project_results": project_results,
        "posts_fetched": fetched,
        "routes_found": routed,
        "routes_qualified": high_intent,
        "routes_for_review": review_matches,
        "new_matches": new_matches,
        "new_match_count": len(new_matches),
        "notifications": notification_outcomes,
        "notification_channels": notifications.channel_status(notification_config),
        "retention_days": retention_days,
        "pruned": pruned,
        "reddit_requests": reddit.requests_used(),
        "fetch_stats": stats,
    }


def route_input(
    payload: Any,
    *,
    portfolio_config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Route supplied normalized post JSON without network or database writes."""
    config = portfolio.load_config(portfolio_config_path)
    posts = payload if isinstance(payload, list) else [payload]
    matches: list[dict[str, Any]] = []
    for post in posts:
        if not isinstance(post, dict):
            raise TypeError("each input post must be a JSON object")
        missing = [key for key in ("id", "subreddit", "title", "url") if not post.get(key)]
        if missing:
            raise ValueError(f"input post missing required keys: {', '.join(missing)}")
        matches.extend(portfolio.route_post(post, config))
    return {
        "posts": len(posts),
        "matches": matches,
        "match_count": len(matches),
        "projects_active": [project["id"] for project in portfolio.active_projects(config)],
    }


def status(
    *,
    portfolio_config_path: Path | str | None = None,
    notification_config_path: Path | str | None = None,
    recent_limit: int = 20,
) -> dict[str, Any]:
    config = portfolio.load_config(portfolio_config_path)
    notification_config = notifications.load_config(notification_config_path)
    with store.connect() as conn:
        recent_rows = store.recent_portfolio_matches(conn, limit=recent_limit)
    recent = []
    for row in recent_rows:
        try:
            recent.append(json.loads(row["match_json"]))
        except (json.JSONDecodeError, TypeError):
            recent.append(
                {
                    "post_id": row["post_id"],
                    "project_id": row["project_id"],
                    "score": row["match_score"],
                }
            )
    return {
        "config_path": config["_path"],
        "active_projects": [project["id"] for project in portfolio.active_projects(config)],
        "inactive_projects": [
            project["id"] for project in config["projects"] if not project.get("enabled")
        ],
        "subreddits": [spec["name"] for spec in portfolio.subreddit_specs(config)],
        "estimated_request_batches": portfolio.estimate_request_batches(
            config, int(config.get("scanner", {}).get("batch_cost_cap", 6))
        ),
        "notification_channels": notifications.channel_status(notification_config),
        "recent_matches": recent,
    }


def watch(
    *,
    portfolio_config_path: Path | str | None = None,
    notification_config_path: Path | str | None = None,
    interval_seconds: int | None = None,
) -> None:
    """Continuously search, pushing each qualifying match as soon as it is found."""
    config = portfolio.load_config(portfolio_config_path)
    configured = int(config.get("scanner", {}).get("interval_seconds", 900))
    interval = max(300, int(interval_seconds or configured))
    while True:
        result = search(
            portfolio_config_path=portfolio_config_path,
            notification_config_path=notification_config_path,
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
        time.sleep(interval)
