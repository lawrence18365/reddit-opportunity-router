"""Immediate, opt-in notifications for qualified portfolio opportunities."""

from __future__ import annotations

import json
import os
import platform
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config" / "notifications.yml"
DELIVERY_ERRORS = (
    ValueError,
    RuntimeError,
    OSError,
    subprocess.SubprocessError,
    urllib.error.URLError,
    ssl.SSLError,
)


def _log(message: str) -> None:
    sys.stderr.write(f"[notifications] {message}\n")
    sys.stderr.flush()


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_CONFIG
    if not config_path.exists():
        return {"version": 1, "channels": []}
    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    channels = config.get("channels", [])
    if not isinstance(channels, list):
        raise TypeError("notifications.yml channels must be a list")
    return config


def configured_channels(config: dict[str, Any]) -> list[dict[str, Any]]:
    configured: list[dict[str, Any]] = []
    for channel in config.get("channels", []):
        if not channel.get("enabled", False):
            continue
        channel_type = str(channel.get("type") or "")
        if channel_type == "desktop":
            configured.append(channel)
            continue
        required = [channel.get("url_env")]
        if channel_type == "telegram":
            required = [channel.get("token_env"), channel.get("chat_id_env")]
        if all(name and os.environ.get(str(name)) for name in required):
            configured.append(channel)
    return configured


def channel_status(config: dict[str, Any]) -> list[dict[str, Any]]:
    active_ids = {str(c.get("id")) for c in configured_channels(config)}
    return [
        {
            "id": str(channel.get("id") or ""),
            "type": str(channel.get("type") or ""),
            "enabled": bool(channel.get("enabled", False)),
            "configured": str(channel.get("id")) in active_ids,
        }
        for channel in config.get("channels", [])
    ]


def _message(match: dict[str, Any]) -> str:
    offer = match.get("offer") or {}
    tier = str(match.get("qualification_tier") or "high_intent").replace("_", " ").upper()
    project_name = match.get("project_name", match.get("project_id", "Project"))
    lines = [
        f"Reddit opportunity [{tier}]: {project_name}",
        f"Score {match.get('score', '?')} | r/{match.get('subreddit', '?')}",
        str(match.get("title") or "")[:180],
        str(match.get("url") or ""),
        f"Why: {match.get('reason', '')}"[:500],
    ]
    engagement = match.get("engagement") or {}
    cta_policy = engagement.get("cta_policy", "disclosed_if_helpful")
    offer_url = offer.get("tracked_url") or offer.get("url")
    if cta_policy == "profile_only":
        lines.append("CTA policy: helpful reply only, no product name or link in the comment.")
    elif offer_url:
        lines.append(f"CTA after helping: {offer.get('cta_label', 'Learn more')} | {offer_url}")
    lines.append("Manual reply only. Disclose your affiliation if you mention the product.")
    return "\n".join(lines)


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("webhook URL must be HTTPS")
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "opportunity-router/0.1"},
        method="POST",
    )
    with urllib.request.urlopen(
        request, timeout=timeout, context=ssl.create_default_context()
    ) as resp:
        if not 200 <= resp.status < 300:
            raise RuntimeError(f"webhook returned HTTP {resp.status}")


def _send_desktop(message: str) -> None:
    title, _, body = message.partition("\n")
    body = body[:700]
    if platform.system() == "Darwin":
        script = (
            "on run argv\n"
            "display notification (item 2 of argv) with title (item 1 of argv)\n"
            "end run"
        )
        subprocess.run(
            ["osascript", "-e", script, title, body],
            check=True,
            timeout=5,
            capture_output=True,
            text=True,
        )
        return
    if platform.system() == "Linux":
        subprocess.run(
            ["notify-send", title, body], check=True, timeout=5, capture_output=True, text=True
        )
        return
    raise RuntimeError("desktop notifications are supported on macOS and Linux")


def send(channel: dict[str, Any], match: dict[str, Any], timeout: float = 5.0) -> None:
    """Deliver one alert or raise. Callers own retry and dedup state."""
    channel_type = str(channel.get("type") or "")
    message = _message(match)
    if channel_type == "desktop":
        _send_desktop(message)
        return
    if channel_type == "telegram":
        token = os.environ[str(channel["token_env"])]
        chat_id = os.environ[str(channel["chat_id_env"])]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        _post_json(
            url,
            {
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout,
        )
        return

    url = os.environ[str(channel["url_env"])]
    parsed = urllib.parse.urlparse(url)
    if channel_type == "slack" and parsed.hostname != "hooks.slack.com":
        raise ValueError("Slack webhook must use hooks.slack.com")
    if channel_type == "discord" and parsed.hostname not in {
        "discord.com",
        "discordapp.com",
        "canary.discord.com",
        "ptb.discord.com",
    }:
        raise ValueError("Discord webhook must use an official Discord host")
    if channel_type == "slack":
        payload = {"text": message, "unfurl_links": False, "unfurl_media": False}
    elif channel_type == "discord":
        payload = {"content": message[:1900]}
    elif channel_type == "webhook":
        payload = {"event": "reddit.opportunity.qualified", "match": match}
    else:
        raise ValueError(f"unsupported notification channel type: {channel_type!r}")
    _post_json(url, payload, timeout)


def deliver(
    match: dict[str, Any],
    config: dict[str, Any],
    *,
    timeout: float = 5.0,
) -> list[dict[str, Any]]:
    """Attempt all configured channels and return structured outcomes."""
    outcomes: list[dict[str, Any]] = []
    for channel in configured_channels(config):
        channel_id = str(channel.get("id") or channel.get("type") or "unknown")
        try:
            send(channel, match, timeout=timeout)
        except DELIVERY_ERRORS as error:
            _log(f"{channel_id} failed: {error}")
            outcomes.append({"channel": channel_id, "status": "failed", "error": str(error)})
        else:
            outcomes.append({"channel": channel_id, "status": "delivered", "error": ""})
    return outcomes
