"""Regression checks for tenant revocation, malformed input, and output isolation."""

import json
from unittest.mock import AsyncMock

import pytest
import yaml

from triage.config import AppConfig, Settings
from triage.delivery import _payload
from triage.ingest import normalize_github, normalize_slack
from triage.models import Event, Route, Score
from triage.routing import resolve_routes
from triage.scoring import _event_input
from triage.store import Store
from triage.worker import Worker


def config(team="T1"):
    return AppConfig.model_validate(
        {
            "orgs": [
                {
                    "id": "tenant",
                    "github_org": "acme",
                    "slack_team_id": team,
                    "type": "startup",
                    "recipient": "U1",
                    "slack_channels": ["C1"],
                    "repos": {"acme/api": {}},
                }
            ]
        }
    )


def slack_event():
    return Event(
        id="slack:tenant:1",
        org_id="tenant",
        source="slack",
        kind="message",
        subject_key="thread",
        text="Private old workspace",
        metadata={"team_id": "T1", "channel": "C1"},
    )


def test_workspace_reassignment_revokes_queued_event():
    assert resolve_routes(slack_event(), config())
    assert resolve_routes(slack_event(), config("T2")) == []


async def test_workspace_reassignment_revokes_pending_delivery(tmp_path):
    path = tmp_path / "orgs.yaml"
    path.write_text(yaml.safe_dump(config("T2").model_dump()))
    store = Store(":memory:")
    event = slack_event()
    store.enqueue(event)
    store.claim()
    store.complete(
        event.id,
        Score(score=1, rationale="Review", summary="Private"),
        [Route(recipient="U1", threshold=0.7)],
        0,
    )
    sender = AsyncMock()
    sender.send.return_value = "1.1"
    try:
        await Worker(store, Settings(config_path=str(path)), AsyncMock(), sender).tick()
        sender.send.assert_not_awaited()
        assert store.stats()["batches"] == {"failed": 1}
    finally:
        store.close()


def test_collapsed_batch_prioritizes_highest_risk_and_discloses_remainder():
    batch = {
        "id": "1",
        "org_id": "tenant",
        "recipient": "U1",
        "events": [{"url": "https://github.com/acme/api/pull/1"} for _ in range(21)],
        "scores": [{"score": 0.7, "summary": "Routine", "rationale": "Review"} for _ in range(20)]
        + [{"score": 1, "summary": "URGENT", "rationale": "Blocking incident"}],
    }
    payload = _payload(batch)
    assert "URGENT" in payload["blocks"][1]["text"]["text"]
    assert "1 lower-ranked updates retained in the event log" in str(payload["blocks"][-1])


async def test_expired_claim_does_not_bypass_scoring_attempt_ceiling(tmp_path):
    path = tmp_path / "orgs.yaml"
    path.write_text(yaml.safe_dump(config().model_dump()))
    store = Store(":memory:")
    store.enqueue(slack_event())
    store.claim(now=0)
    store.db.execute("UPDATE events SET status='processing', attempts=2, lease_until=0")
    store.db.commit()
    scorer = AsyncMock()
    sender = AsyncMock()
    try:
        settings = Settings(config_path=str(path), max_attempts=2, fail_safe="dead_letter")
        await Worker(store, settings, scorer, sender).tick()
        scorer.score.assert_not_awaited()
        assert store.stats()["events"] == {"dead_letter": 1}
    finally:
        store.close()


@pytest.mark.parametrize("bad", [None, [], 7, True, {"nested": "value"}])
def test_malformed_nested_webhooks_fail_closed(bad):
    assert (
        normalize_github({"action": "opened", "repository": bad}, "pull_request", "1", config())
        is None
    )
    assert (
        normalize_slack(
            {"type": "event_callback", "event_id": "E1", "team_id": "T1", "event": bad},
            config(),
            "",
        )
        is None
    )


def test_model_input_excludes_opaque_metadata_and_identity():
    event = slack_event().model_copy(
        update={
            "metadata": {
                "team_id": "T1",
                "channel": "C1",
                "secret": "UNIQUE_SECRET",
                "routing_recipient": "UNIQUE_DESTINATION",
            }
        }
    )
    serialized = _event_input(event)
    assert "UNIQUE_SECRET" not in serialized
    assert "UNIQUE_DESTINATION" not in serialized
    assert set(json.loads(serialized)) == {"untrusted_event"}


def test_untrusted_summary_cannot_ping_slack():
    batch = {
        "id": "1",
        "org_id": "tenant",
        "recipient": "U1",
        "events": [{"url": "https://github.com/acme/api/pull/1"}],
        "scores": [
            {
                "score": 1,
                "summary": "<!channel> <@UATTACK>",
                "rationale": "See <https://evil.example|login>",
            }
        ],
    }
    payload = _payload(batch)
    assert "<!channel>" not in payload["text"]
    assert "<@UATTACK>" not in payload["text"]
    assert payload["blocks"][1]["text"]["type"] == "plain_text"
    assert payload["unfurl_links"] is False
