"""Authenticate provider webhooks and normalize only explicitly allowed sources."""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

from .config import AppConfig
from .models import Event


def verify_github(body: bytes, signature: str, secret: str) -> bool:
    """Delivery ID deduplication supplies replay protection for GitHub."""
    if not secret or not isinstance(signature, str) or not signature.isascii():
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_slack(
    body: bytes, timestamp: str, signature: str, secret: str, now: float | None = None
) -> bool:
    if (
        not secret
        or not isinstance(timestamp, str)
        or not timestamp.isascii()
        or not timestamp.isdigit()
    ):
        return False
    if not isinstance(signature, str) or not signature.isascii():
        return False
    try:
        age = abs((time.time() if now is None else now) - int(timestamp))
    except (ValueError, OverflowError):
        return False
    if age > 300:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def normalize_github(
    payload: dict, event_type: str, delivery_id: str, config: AppConfig
) -> Event | None:
    if not isinstance(payload, dict) or not delivery_id:
        return None
    actions = {
        "pull_request": {
            "opened",
            "edited",
            "synchronize",
            "closed",
            "reopened",
            "ready_for_review",
            "converted_to_draft",
            "review_requested",
            "review_request_removed",
            "labeled",
            "unlabeled",
        },
        "pull_request_review": {"submitted", "edited", "dismissed"},
        "pull_request_review_comment": {"created", "edited"},
        "issue_comment": {"created", "edited"},
    }
    action = _str(payload.get("action"))
    if event_type not in actions or action not in actions[event_type]:
        return None
    repository = _dict(payload.get("repository"))
    owner = _str(_dict(repository.get("owner")).get("login"))
    repo = _str(repository.get("full_name"))
    organization = _str(_dict(payload.get("organization")).get("login"))
    if not owner or not repo or repo.split("/")[0].casefold() != owner.casefold():
        return None
    if organization and organization.casefold() != owner.casefold():
        return None
    matches = [
        org
        for org in config.orgs
        if org.github_org and org.github_org.casefold() == owner.casefold()
    ]
    if len(matches) != 1:
        return None
    org = matches[0]
    # Preserve the configured spelling so subsequent routing uses the same key.
    repos = [name for name in org.repos if name.casefold() == repo.casefold()]
    if len(repos) != 1:
        return None
    pr = _dict(payload.get("pull_request"))
    issue = _dict(payload.get("issue"))
    if event_type == "issue_comment":
        if not _dict(issue.get("pull_request")):
            return None
        subject = _str(_dict(issue.get("pull_request")).get("html_url")) or _str(
            issue.get("html_url")
        )
        title = _str(issue.get("title"))
        body = _str(issue.get("body"))
    else:
        subject = _str(pr.get("html_url"))
        title = _str(pr.get("title"))
        body = _str(pr.get("body"))
    if not subject:
        return None
    detail = _dict(payload.get("comment")) or _dict(payload.get("review"))
    state = _str(detail.get("state"))
    requested = _str(_dict(payload.get("requested_reviewer")).get("login"))
    text = "\n".join(
        part
        for part in [
            title,
            body,
            _str(detail.get("body")),
            state,
            f"Review requested: {requested}" if requested else "",
        ]
        if part
    )
    labels = (
        [
            label["name"]
            for label in (pr or issue).get("labels", [])
            if isinstance(label, dict) and isinstance(label.get("name"), str)
        ]
        if isinstance((pr or issue).get("labels", []), list)
        else []
    )
    return Event(
        id=f"github:{org.id}:{delivery_id}",
        org_id=org.id,
        source="github",
        kind=f"{event_type}.{action}",
        subject_key=subject,
        repo=repos[0],
        actor=_str(_dict(payload.get("sender")).get("login")),
        text=text,
        url=_str(detail.get("html_url")) or subject,
        metadata={
            "action": action,
            "labels": labels,
            "review_state": state,
            "requested_reviewer": requested,
            "draft": bool(pr.get("draft", False)),
            "created_at": _str((pr or issue).get("created_at")),
            "updated_at": _str((pr or issue).get("updated_at")),
            "state": _str((pr or issue).get("state")),
            "merged": bool(pr.get("merged", False)),
        },
    )


def normalize_slack(payload: dict, config: AppConfig, bot_user_id: str) -> Event | None:
    if not isinstance(payload, dict) or payload.get("type") != "event_callback":
        return None
    team = _str(payload.get("team_id"))
    event_id = _str(payload.get("event_id"))
    event = _dict(payload.get("event"))
    if event.get("type") not in ("message", "app_mention") or not team or not event_id:
        return None
    # Ignore edits/deletes/system events and every bot post to prevent loops.
    if event.get("subtype") or event.get("bot_id") or event.get("bot_profile"):
        return None
    user = _str(event.get("user"))
    if not user or (bot_user_id and user == bot_user_id):
        return None
    channel = _str(event.get("channel"))
    ts = _str(event.get("ts"))
    matches = [org for org in config.orgs if org.slack_team_id and org.slack_team_id == team]
    if len(matches) != 1 or channel not in matches[0].slack_channels or not ts:
        return None
    org = matches[0]
    thread = _str(event.get("thread_ts")) or ts
    return Event(
        id=f"slack:{org.id}:{event_id}",
        org_id=org.id,
        source="slack",
        kind=_str(event.get("type")),
        subject_key=f"slack:{team}:{channel}:{thread}",
        actor=user,
        text=_str(event.get("text")),
        url=f"https://app.slack.com/client/{quote(team, safe='')}/{quote(channel, safe='')}?message_ts={quote(ts, safe='')}",
        metadata={"channel": channel, "team_id": team, "thread_ts": thread, "ts": ts},
    )
