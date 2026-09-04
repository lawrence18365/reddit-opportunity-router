"""Portfolio routing, persistence, and immediate-notification contracts."""

from __future__ import annotations

import json
import time

import pytest
from subscope.lib import notifications, portfolio, portfolio_runner, reddit, store


def _post(
    post_id: str,
    subreddit: str,
    title: str,
    body: str = "",
) -> dict:
    return {
        "id": post_id,
        "subreddit": subreddit,
        "title": title,
        "body": body,
        "url": f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/example/",
        "canonical_url": f"https://reddit.com/comments/{post_id}/",
        "author": "example_op",
        "created_utc": int(time.time()),
        "score": 0,
        "num_comments": 0,
        "removed": False,
        "locked": False,
        "over_18": False,
        "is_crosspost": False,
    }


def test_seeded_registry_has_rollout_order_and_expansion_profiles():
    config = portfolio.load_config()
    assert [project["id"] for project in portfolio.active_projects(config)] == [
        "freshcarrier",
        "quotetier",
        "restaurant-roster",
        "revenue-recovery",
    ]
    inactive = {project["id"] for project in config["projects"] if not project["enabled"]}
    assert {"bridal-os", "ratetap-mexico", "affordable-email-marketing"} <= inactive
    assert len(portfolio.subreddit_specs(config)) >= 20


@pytest.mark.parametrize(
    ("post", "expected_project"),
    [
        (
            _post(
                "fresh1",
                "FreightBrokers",
                "Where do you get leads? DAT is full of stale leads",
                "I sell trucking insurance and need newly authorized motor carrier leads.",
            ),
            "freshcarrier",
        ),
        (
            _post(
                "quote1",
                "smallbusiness",
                "Anyone use PandaDoc? Clients ghost my quotes",
                "I need quote software with good better best pricing packages.",
            ),
            "quotetier",
        ),
        (
            _post(
                "roster1",
                "Restaurant_Managers",
                "7shifts alternative for staff scheduling?",
                "Our restaurant rota has shift conflicts and last minute call outs.",
            ),
            "restaurant-roster",
        ),
        (
            _post(
                "recovery1",
                "smallbusinessuk",
                "How do you follow up when old quotes go cold?",
                "I run a window installer and need help recovering dormant quotes.",
            ),
            "revenue-recovery",
        ),
    ],
)
def test_priority_projects_route_high_intent_posts(post, expected_project):
    matches = portfolio.route_post(post, portfolio.load_config())
    assert expected_project in {match["project_id"] for match in matches}
    match = next(match for match in matches if match["project_id"] == expected_project)
    assert match["workflow"] == "review_manually"
    assert "disclos" in match["disclosure"].lower() or "built" in match["disclosure"].lower()
    assert "utm_source=reddit" in match["offer"]["tracked_url"]
    assert f"utm_content={post['id']}" in match["offer"]["tracked_url"]


def test_generic_single_signal_does_not_alert():
    post = _post("generic1", "smallbusiness", "Can someone explain a quote?", "Thanks")
    assert portfolio.route_post(post, portfolio.load_config()) == []


def test_search_query_uses_configured_high_intent_terms():
    config = portfolio.load_config()
    project = next(item for item in config["projects"] if item["id"] == "restaurant-roster")
    query = portfolio.build_search_query(project)
    assert '"staff scheduling"' in query
    assert '"scheduling software"' in query
    assert "7shifts" in query
    assert len(query) <= 512


def test_profile_only_notification_suppresses_offer_link():
    post = _post(
        "profile1",
        "smallbusiness",
        "What is the best restaurant scheduling software?",
        "Restaurant owner looking for staff scheduling recommendations.",
    )
    matches = portfolio.route_post(post, portfolio.load_config())
    match = next(item for item in matches if item["project_id"] == "restaurant-roster")
    message = notifications._message(match)
    assert match["engagement"]["cta_policy"] == "profile_only"
    assert match["offer"]["tracked_url"] not in message
    assert "no product name or link" in message


def test_disabled_profiles_never_route():
    post = _post(
        "bridal1",
        "weddingplanning",
        "Looking for BridalLive alternative",
        "I own a bridal shop and appointment chaos is killing us.",
    )
    assert portfolio.route_post(post, portfolio.load_config()) == []


def test_store_deduplicates_matches_and_notifications(tmp_path):
    database = tmp_path / "portfolio.sqlite"
    post = _post("persist1", "FreightBrokers", "DAT stale leads", "Need carrier leads")
    match = {
        "post_id": post["id"],
        "project_id": "freshcarrier",
        "score": 80,
    }
    with store.connect(database) as conn:
        store.insert_post(conn, post)
        assert store.record_portfolio_match(conn, match, json.dumps(match)) is True
        assert store.record_portfolio_match(conn, match, json.dumps(match)) is False
        assert not store.notification_delivered(conn, post["id"], "freshcarrier", "desktop")
        store.record_notification_attempt(
            conn, post["id"], "freshcarrier", "desktop", delivered=True
        )
        assert store.notification_delivered(conn, post["id"], "freshcarrier", "desktop")


def test_store_prunes_expired_portfolio_content(tmp_path):
    database = tmp_path / "portfolio.sqlite"
    post = _post("expired1", "FreightBrokers", "DAT stale leads", "Need carrier leads")
    match = {"post_id": post["id"], "project_id": "freshcarrier", "score": 80}
    with store.connect(database) as conn:
        store.insert_post(conn, post)
        store.record_portfolio_match(conn, match, json.dumps(match))
        store.record_notification_attempt(
            conn, post["id"], "freshcarrier", "desktop", delivered=True
        )
        expired = int(time.time()) - 31 * 86400
        conn.execute(
            "UPDATE portfolio_matches SET matched_at = ? WHERE post_id = ?",
            (expired, post["id"]),
        )
        conn.execute("UPDATE posts SET first_seen_at = ? WHERE id = ?", (expired, post["id"]))
        result = store.prune_portfolio_data(conn, days=30)
        assert result == {"matches": 1, "notifications": 1, "posts": 1}


def test_scan_delivers_once_per_post_project_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBSCOPE_DATA", str(tmp_path / "data"))
    post = _post(
        "scan1",
        "FreightBrokers",
        "Where do you get leads? DAT has stale leads",
        "Need newly authorized motor carrier leads for trucking insurance sales.",
    )
    monkeypatch.setattr(reddit, "prime_new_cache", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        reddit,
        "fetch_delta",
        lambda sub, cursor, max_limit=50: [post] if sub == "FreightBrokers" else [],
    )
    monkeypatch.setattr(
        reddit,
        "get_fetch_stats",
        lambda: {
            "ok": 1,
            "failed": 0,
            "rate_limited": 0,
            "fallback_used": 0,
        },
    )
    delivered: list[tuple[str, str]] = []
    monkeypatch.setattr(
        notifications,
        "send",
        lambda channel, match: delivered.append((channel["id"], match["project_id"])),
    )

    first = portfolio_runner.scan(max_requests=1)
    second = portfolio_runner.scan(max_requests=1)

    assert first["new_match_count"] == 1
    assert first["new_matches"][0]["project_id"] == "freshcarrier"
    assert second["new_match_count"] == 0
    assert delivered == [("desktop", "freshcarrier")]


def test_search_delivers_once_per_post_project_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBSCOPE_DATA", str(tmp_path / "data"))
    post = _post(
        "search1",
        "smallbusiness",
        "What is the best restaurant scheduling software?",
        "Restaurant owner looking for staff scheduling recommendations.",
    )
    monkeypatch.setattr(
        reddit,
        "fetch_search",
        lambda sub, query, **kwargs: [post] if "7shifts" in query else [],
    )
    monkeypatch.setattr(
        reddit,
        "get_fetch_stats",
        lambda: {"ok": 4, "failed": 0, "rate_limited": 0, "fallback_used": 0},
    )
    delivered: list[tuple[str, str]] = []
    monkeypatch.setattr(
        notifications,
        "send",
        lambda channel, match: delivered.append((channel["id"], match["project_id"])),
    )

    first = portfolio_runner.search()
    second = portfolio_runner.search()

    assert first["new_match_count"] == 1
    assert first["new_matches"][0]["project_id"] == "restaurant-roster"
    assert second["new_match_count"] == 0
    assert delivered == [("desktop", "restaurant-roster")]


def test_notification_channel_configuration_uses_environment(monkeypatch):
    for name in (
        "OPPORTUNITY_SLACK_WEBHOOK_URL",
        "OPPORTUNITY_DISCORD_WEBHOOK_URL",
        "OPPORTUNITY_TELEGRAM_BOT_TOKEN",
        "OPPORTUNITY_TELEGRAM_CHAT_ID",
        "OPPORTUNITY_WEBHOOK_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    config = notifications.load_config()
    active = {channel["id"] for channel in notifications.configured_channels(config)}
    assert active == {"desktop"}
    monkeypatch.setenv("OPPORTUNITY_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/test")
    active = {channel["id"] for channel in notifications.configured_channels(config)}
    assert active == {"desktop", "slack"}
