"""Bounded Slack notifications with conservative external-write recovery."""

from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import urlsplit

import httpx


class DeliveryError(Exception):
    def __init__(
        self, message: str, retryable: bool = False, uncertain: bool = False, retry_after: float = 0
    ):
        super().__init__(message)
        self.code = None
        self.retryable = retryable
        self.uncertain = uncertain
        self.retry_after = retry_after


def _short(value: object, limit: int) -> str:
    value = str(value).replace("\x00", "")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _source_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2500:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not any(ord(char) < 33 for char in value)
        ):
            return value
    except ValueError:
        pass
    return None


def _payload(batch: dict) -> dict:
    events, scores = batch["events"], batch["scores"]
    if not events or len(events) != len(scores):
        raise DeliveryError("Invalid delivery batch")
    # Keep all writes in one request: splitting would introduce partial-send ambiguity.
    header = f"Relevance triage · {len(events)} update(s)"
    lines = [header]
    blocks = [{"type": "section", "text": {"type": "plain_text", "text": header}}]
    # Only validated operator-configured IDs can enter a mention block.
    mentions = batch.get("mentions", [])
    if len(mentions) > 20 or any(not isinstance(m, str) or not re.fullmatch(r"(?:U|W|S)[A-Z0-9]{2,30}", m) for m in mentions):
        raise DeliveryError("Invalid configured mentions")
    if mentions:
        text = " ".join(f"<!subteam^{m}>" if m.startswith("S") else f"<@{m}>" for m in mentions)
        blocks.append({"type":"section", "text":{"type":"mrkdwn", "text":text, "verbatim":True}})
    ranked = sorted(
        zip(events, scores, strict=True), key=lambda pair: float(pair[1]["score"]), reverse=True
    )
    for event, score in ranked[:20]:
        content = (
            f"Score {float(score['score']):.2f}"
            + (" · fallback" if score.get("fallback") else "")
            + "\n"
            + _short(score["summary"], 85)
            + "\nWhy: "
            + _short(score["rationale"], 70)
        )
        section = {"type": "section", "text": {"type": "plain_text", "text": content}}
        url = _source_url(event.get("url"))
        if url:
            section["accessory"] = {
                "type": "button",
                "text": {"type": "plain_text", "text": "View source"},
                "url": url,
                "action_id": f"source_{len(blocks)}",
            }
        blocks.append(section)
        lines.append(content + (f"\n{url}" if url else ""))
    if len(events) > 20:
        note = f"{len(events) - 20} lower-ranked updates retained in the event log; highest scores shown."
        blocks.append({"type": "context", "elements": [{"type": "plain_text", "text": note}]})
        lines.append(note)
    # Escape fallback text as Slack can parse angle-bracket mention syntax even
    # when automatic parsing is off. Blocks use plain_text exclusively.
    fallback = _short(html.escape("\n\n".join(lines), quote=False), 3900)
    marker = hashlib.sha256(f"{batch['org_id']}:{batch['id']}".encode()).hexdigest()
    return {
        "channel": batch["recipient"],
        "text": fallback,
        "blocks": blocks,
        "parse": "none",
        "link_names": False,
        "unfurl_links": False,
        "unfurl_media": False,
        "metadata": {
            "event_type": "relevance_triage_batch",
            "event_payload": {"batch_marker": marker},
        },
    }


class SlackDelivery:
    def __init__(self, settings):
        self.token = settings.slack_bot_token
        self.client = httpx.AsyncClient(timeout=20, follow_redirects=False)

    async def send(self, batch: dict) -> str:
        if not self.token:
            raise DeliveryError("SLACK_BOT_TOKEN is not configured")
        payload = _payload(batch)
        try:
            response = await self.client.post(
                "https://slack.com/api/chat.postMessage",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise DeliveryError(
                "Slack connection unavailable before send", retryable=True
            ) from None
        except httpx.TransportError:
            raise DeliveryError("Slack delivery outcome is unknown", uncertain=True) from None
        if response.status_code == 429:
            try:
                delay = float(response.headers.get("Retry-After", "1"))
                if not 0 <= delay < float("inf"):
                    delay = 1
            except ValueError:
                delay = 1
            raise DeliveryError("Slack rate limit", retryable=True, retry_after=delay)
        if response.status_code >= 500 or response.status_code == 408:
            raise DeliveryError("Slack delivery outcome is unknown", uncertain=True)
        if response.status_code != 200:
            raise DeliveryError(f"Slack rejected request (HTTP {response.status_code})")
        try:
            result = response.json()
        except ValueError:
            raise DeliveryError("Slack returned an invalid response", uncertain=True) from None
        if not isinstance(result, dict):
            raise DeliveryError("Slack returned an invalid response", uncertain=True)
        if result.get("ok") is True:
            ts = result.get("ts")
            if isinstance(ts, str) and ts.strip():
                return ts
            raise DeliveryError("Slack success response missing timestamp", uncertain=True)
        if result.get("ok") is False:
            # Slack documents these as potentially partially successful errors.
            if result.get("error") in {"internal_error", "fatal_error", "request_timeout"}:
                raise DeliveryError("Slack delivery outcome is unknown", uncertain=True)
            if result.get("error") == "ratelimited":
                raise DeliveryError("Slack rate limit", retryable=True, retry_after=1)
            error = DeliveryError("Slack rejected delivery; check token permissions and recipient")
            # Persist only known codes, never arbitrary provider text or response bodies.
            if result.get("error") in {
                "messages_tab_disabled", "channel_not_found", "not_in_channel", "missing_scope",
                "invalid_auth", "token_revoked", "account_inactive", "invalid_blocks", "invalid_metadata",
                "is_archived", "restricted_action", "no_permission",
            }:
                error.code = result["error"]
            raise error
        raise DeliveryError("Slack returned an invalid response", uncertain=True)

    async def close(self):
        await self.client.aclose()
