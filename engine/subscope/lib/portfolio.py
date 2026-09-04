"""Multi-product opportunity routing for human-led Reddit engagement.

The router is deliberately accountless. It reads normalized Reddit posts,
scores them against local product profiles, and returns a review brief. It
never drafts, posts, comments, votes, or sends direct messages on Reddit.
"""

from __future__ import annotations

import math
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config" / "portfolio.yml"
REQUIRED_PROJECT_KEYS = {
    "id",
    "name",
    "priority",
    "enabled",
    "offer",
    "subreddits",
    "pain_signals",
    "intent_signals",
    "context_signals",
}


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load and validate the portfolio profile registry."""
    config_path = Path(path) if path else DEFAULT_CONFIG
    if not config_path.exists():
        raise FileNotFoundError(f"portfolio config not found: {config_path}")
    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    validate_config(config)
    config["_path"] = str(config_path)
    return config


def validate_config(config: dict[str, Any]) -> None:
    """Raise ValueError with actionable messages for malformed profiles."""
    if config.get("version") != 1:
        raise ValueError("portfolio.yml must declare version: 1")
    projects = config.get("projects")
    if not isinstance(projects, list) or not projects:
        raise ValueError("portfolio.yml must contain at least one project")

    seen: set[str] = set()
    errors: list[str] = []
    for index, project in enumerate(projects, start=1):
        if not isinstance(project, dict):
            errors.append(f"project #{index} must be a mapping")
            continue
        missing = sorted(REQUIRED_PROJECT_KEYS - set(project))
        if missing:
            errors.append(f"project #{index} missing: {', '.join(missing)}")
            continue
        project_id = str(project["id"]).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_id):
            errors.append(f"project #{index} has invalid id: {project_id!r}")
        elif project_id in seen:
            errors.append(f"duplicate project id: {project_id}")
        seen.add(project_id)

        offer = project.get("offer") or {}
        if not isinstance(offer, dict) or not offer.get("cta_type"):
            errors.append(f"{project_id}: offer.cta_type is required")
        if not isinstance(project.get("subreddits"), list) or not project["subreddits"]:
            errors.append(f"{project_id}: at least one subreddit is required")
        threshold = project.get("min_score", config.get("defaults", {}).get("min_score", 50))
        try:
            threshold_num = float(threshold)
        except (TypeError, ValueError):
            errors.append(f"{project_id}: min_score must be numeric")
        else:
            if not 0 <= threshold_num <= 100:
                errors.append(f"{project_id}: min_score must be between 0 and 100")
    if errors:
        raise ValueError("invalid portfolio config:\n- " + "\n- ".join(errors))


def active_projects(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return enabled profiles in rollout order."""
    return sorted(
        (p for p in config["projects"] if p.get("enabled")),
        key=lambda p: (int(p.get("priority", 999)), str(p["id"])),
    )


def subreddit_specs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Build one deduplicated batched-fetch specification across all profiles."""
    by_name: dict[str, dict[str, Any]] = {}
    for project in active_projects(config):
        for raw in project.get("subreddits", []):
            item = {"name": raw} if isinstance(raw, str) else dict(raw)
            name = str(item.get("name") or "").strip().removeprefix("r/")
            if not name:
                continue
            key = name.lower()
            saturation = str(item.get("saturation") or "medium")
            previous = by_name.get(key)
            if previous is None:
                by_name[key] = {
                    "name": name,
                    "tier": 1,
                    "bucket": "portfolio",
                    "saturation": saturation,
                    "weight": float(item.get("weight", 1.0)),
                }
            else:
                previous["weight"] = max(previous["weight"], float(item.get("weight", 1.0)))
    return sorted(by_name.values(), key=lambda item: item["name"].lower())


def _normalize(value: str) -> str:
    value = value.casefold().replace("&", " and ")
    value = re.sub(r"[^a-z0-9+#./'-]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _matched_signals(text: str, signals: list[Any]) -> list[str]:
    normalized_text = f" {_normalize(text)} "
    found: list[str] = []
    for raw in signals or []:
        signal = str(raw).strip()
        normalized_signal = _normalize(signal)
        if normalized_signal and f" {normalized_signal} " in normalized_text:
            found.append(signal)
    return found


def _project_subreddit(project: dict[str, Any], subreddit: str) -> dict[str, Any] | None:
    target = subreddit.casefold().removeprefix("r/")
    for raw in project.get("subreddits", []):
        item = {"name": raw} if isinstance(raw, str) else raw
        name = str(item.get("name") or "").casefold().removeprefix("r/")
        if name == target:
            return dict(item)
    return None


def _freshness_points(created_utc: int, max_points: float = 10.0) -> float:
    if not created_utc:
        return 0.0
    age_hours = max(0.0, (time.time() - created_utc) / 3600)
    return max(0.0, max_points * (1.0 - age_hours / 72.0))


def _tracked_url(url: str, project_id: str, post_id: str) -> str:
    """Attach stable campaign attribution without discarding existing query data."""
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.update(
        {
            "utm_source": "reddit",
            "utm_medium": "community",
            "utm_campaign": f"opportunity-router-{project_id}",
            "utm_content": post_id,
        }
    )
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )


def build_search_query(project: dict[str, Any], max_length: int = 512) -> str:
    """Build a bounded Reddit search query from a project's strongest terms."""
    raw_terms = project.get("search_terms") or (
        list(project.get("competitor_signals", []))[:4]
        + list(project.get("intent_signals", []))[:4]
        + list(project.get("pain_signals", []))[:4]
    )
    terms: list[str] = []
    seen: set[str] = set()
    for raw in raw_terms:
        term = str(raw).strip()
        if not term or term.casefold() in seen:
            continue
        seen.add(term.casefold())
        quoted = f'"{term}"' if " " in term else term
        candidate = " OR ".join([*terms, quoted])
        if len(candidate) > max_length:
            break
        terms.append(quoted)
    return " OR ".join(terms)


def match_project(
    post: dict[str, Any],
    project: dict[str, Any],
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Score one normalized post against one project profile."""
    if not project.get("enabled"):
        return None
    sub = _project_subreddit(project, str(post.get("subreddit") or ""))
    if sub is None:
        return None
    if post.get("removed") or post.get("over_18"):
        return None

    defaults = defaults or {}
    text = " ".join((str(post.get("title") or ""), str(post.get("body") or "")))
    excluded = _matched_signals(text, project.get("exclusion_signals", []))
    if excluded:
        return None

    groups = {
        "pain": _matched_signals(text, project.get("pain_signals", [])),
        "intent": _matched_signals(text, project.get("intent_signals", [])),
        "competitor": _matched_signals(text, project.get("competitor_signals", [])),
        "context": _matched_signals(text, project.get("context_signals", [])),
        "audience": _matched_signals(text, project.get("audience_signals", [])),
    }
    nonempty_groups = sum(bool(values) for values in groups.values())
    minimum_groups = int(project.get("min_signal_groups", defaults.get("min_signal_groups", 2)))
    if nonempty_groups < minimum_groups:
        return None

    weights = {
        "pain": 15.0,
        "intent": 15.0,
        "competitor": 25.0,
        "context": 8.0,
        "audience": 6.0,
    }
    weights.update(defaults.get("group_points", {}))
    weights.update(project.get("group_points", {}))
    caps = {
        "pain": 30.0,
        "intent": 30.0,
        "competitor": 35.0,
        "context": 16.0,
        "audience": 12.0,
    }
    caps.update(defaults.get("group_caps", {}))

    sub_weight = max(0.5, min(2.0, float(sub.get("weight", 1.0))))
    points = min(20.0, 12.0 * sub_weight)
    for group, values in groups.items():
        points += min(float(caps[group]), len(values) * float(weights[group]))
    points += _freshness_points(int(post.get("created_utc") or 0))
    score = round(min(100.0, points), 1)
    threshold = float(project.get("min_score", defaults.get("min_score", 50)))
    if score < threshold:
        return None

    reason_parts = [f"{name}: {', '.join(values[:3])}" for name, values in groups.items() if values]
    offer = project.get("offer") or {}
    offer_url = str(offer.get("url") or "")
    cta_policy = str(sub.get("cta_policy") or "disclosed_if_helpful")
    if cta_policy == "profile_only":
        engagement_guidance = (
            "Give a useful answer without naming or linking the product. "
            "Keep your affiliation visible in your Reddit profile."
        )
    else:
        engagement_guidance = (
            "Help first. Mention or link the product only when directly relevant, "
            "and disclose your affiliation."
        )
    return {
        "post_id": str(post.get("id") or ""),
        "project_id": project["id"],
        "project_name": project["name"],
        "priority": int(project.get("priority", 999)),
        "score": score,
        "threshold": threshold,
        "subreddit": post.get("subreddit", ""),
        "title": post.get("title", ""),
        "body_excerpt": str(post.get("body") or "")[:400],
        "url": post.get("url", ""),
        "created_utc": int(post.get("created_utc") or 0),
        "matched_signals": groups,
        "reason": "; ".join(reason_parts),
        "offer": {
            "cta_type": offer.get("cta_type", "free_trial"),
            "cta_label": offer.get("cta_label", "Try it free"),
            "url": offer_url,
            "tracked_url": _tracked_url(offer_url, str(project["id"]), str(post.get("id") or "")),
        },
        "engagement": {
            "cta_policy": cta_policy,
            "guidance": engagement_guidance,
        },
        "disclosure": project.get(
            "disclosure",
            "If you mention the product, disclose that you built it or are affiliated with it.",
        ),
        "workflow": "review_manually",
    }


def route_post(post: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all qualifying project matches, sorted by rollout priority and score."""
    matches = [
        match
        for project in active_projects(config)
        if (match := match_project(post, project, config.get("defaults", {}))) is not None
    ]
    return sorted(matches, key=lambda match: (match["priority"], -match["score"]))


def estimate_request_batches(config: dict[str, Any], cost_cap: int = 6) -> int:
    """Estimate batched RSS requests using the fetcher's saturation costs."""
    costs = {"high": 3, "medium": 2, "low": 1}
    total = sum(costs.get(str(s.get("saturation")), 2) for s in subreddit_specs(config))
    return max(1, math.ceil(total / max(1, cost_cap)))
