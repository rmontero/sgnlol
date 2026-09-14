"""Local acceptance: real ingress, persistence, scorer and sender; no provider calls."""

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from triage.app import create_app
from triage.config import Settings
from triage.delivery import SlackDelivery
from triage.scoring import Scorer
from triage.store import Store
from triage.worker import Worker


@pytest.fixture
async def system(tmp_path, monkeypatch):
    path = tmp_path / "orgs.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "orgs": [
                    {
                        "id": "enterprise",
                        "github_org": "acme",
                        "slack_team_id": "TENTERPRISE",
                        "type": "enterprise",
                        "repos": {"acme/api": {"recipients": ["UREVIEWER"]}},
                        "slack_channels": [],
                    },
                    {
                        "id": "startup",
                        "github_org": "small",
                        "slack_team_id": "TSTARTUP",
                        "type": "startup",
                        "recipient": "UFOUNDER",
                        "repos": {"small/app": {}},
                        "slack_channels": ["CENGINEERING"],
                    },
                ]
            }
        )
    )
    settings = Settings(
        config_path=str(path),
        database_path=str(tmp_path / "queue.db"),
        github_webhook_secret="local-github",
        slack_signing_secret="local-slack",
        openai_api_key="local-test-only",
        slack_bot_token="local-test-only",
        batch_window_seconds=0,
    )
    result = SimpleNamespace(
        final_output={
            "score": 0.9,
            "summary": "Review blocking fix",
            "rationale": "Explicit review needed",
        },
        context_wrapper=SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20)),
    )
    runner = AsyncMock(return_value=result)
    monkeypatch.setattr("triage.scoring.Runner.run", runner)

    # Fail closed on any accidental request outside the explicit Slack transport.
    async def unexpected_network(*args, **kwargs):
        raise AssertionError("Unexpected live network request")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", unexpected_network)
    posts = []

    def slack_api(request):
        assert str(request.url) == "https://slack.com/api/chat.postMessage"
        assert request.method == "POST"
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "ts": "1720000000.123"})

    scorer = Scorer(settings)
    delivery = SlackDelivery(settings)
    await delivery.client.aclose()
    delivery.client = httpx.AsyncClient(transport=httpx.MockTransport(slack_api))
    state = SimpleNamespace(
        settings=settings,
        runner=runner,
        result=result,
        posts=posts,
        scorer=scorer,
        delivery=delivery,
    )
    yield state
    await scorer.close()
    await delivery.close()


def post_github(client, delivery_id="delivery-1", action="review_requested"):
    body = json.dumps(
        {
            "action": action,
            "organization": {"login": "acme"},
            "repository": {"full_name": "acme/api", "owner": {"login": "acme"}},
            "sender": {"login": "alice"},
            "requested_reviewer": {"login": "bob"},
            "pull_request": {
                "number": 7,
                "title": "Blocking fix",
                "body": "Please review before release",
                "html_url": "https://github.com/acme/api/pull/7",
            },
        }
    ).encode()
    headers = {
        "x-github-event": "pull_request",
        "x-github-delivery": delivery_id,
        "x-hub-signature-256": "sha256="
        + hmac.new(b"local-github", body, hashlib.sha256).hexdigest(),
    }
    return client.post("/webhooks/github", content=body, headers=headers)


def post_slack(client, payload):
    body = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    signature = (
        "v0="
        + hmac.new(
            b"local-slack", b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
        ).hexdigest()
    )
    return client.post(
        "/webhooks/slack",
        content=body,
        headers={"x-slack-request-timestamp": timestamp, "x-slack-signature": signature},
    )


def app(system, store=None):
    return create_app(
        system.settings,
        store=store,
        scorer=system.scorer,
        delivery=system.delivery,
        run_worker=False,
    )


def worker(system, store):
    return Worker(store, system.settings, system.scorer, system.delivery)


async def test_enterprise_webhook_survives_restart_and_delivers_once(system):
    # App-owned connection closes, then a fresh Store resumes persisted ingress.
    with TestClient(app(system)) as client:
        response = post_github(client)
        assert response.status_code == 200
        assert response.json() == {"accepted": True, "queued": True}
        assert system.posts == []
        system.runner.assert_not_awaited()
    store = Store(system.settings.database_path)
    try:
        assert store.stats()["events"] == {"queued": 1}
        await worker(system, store).tick()
        assert store.stats()["batches"] == {"sent": 1}
        assert store.stats()["input_tokens"] == 100
        assert len(system.posts) == 1
        assert system.posts[0]["channel"] == "UREVIEWER"
        assert system.posts[0]["blocks"][1]["accessory"]["url"].endswith("/pull/7")
        with TestClient(app(system, store)) as client:
            assert post_github(client).json()["queued"] is False
        await worker(system, store).tick()
        assert len(system.posts) == 1
    finally:
        store.close()


async def test_startup_slack_challenge_message_and_loop_prevention(system):
    store = Store(system.settings.database_path)
    try:
        payload = {
            "type": "event_callback",
            "team_id": "TSTARTUP",
            "event_id": "Ev1",
            "event": {
                "type": "message",
                "channel": "CENGINEERING",
                "user": "UALICE",
                "ts": "1720000000.123",
                "text": "Deployment blocked; help needed",
            },
        }
        with TestClient(app(system, store)) as client:
            assert post_slack(
                client, {"type": "url_verification", "challenge": "challenge"}
            ).json() == {"challenge": "challenge"}
            assert post_slack(client, payload).json()["queued"] is True
            assert post_slack(client, payload).json()["queued"] is False
            payload["event_id"] = "EvBot"
            payload["event"]["bot_id"] = "BTRIAGE"
            assert post_slack(client, payload).json()["queued"] is False
        await worker(system, store).tick()
        assert [post["channel"] for post in system.posts] == ["UFOUNDER"]
        assert store.stats()["total_events"] == 1
        model_input = json.loads(system.runner.call_args.kwargs["input"])
        assert model_input["untrusted_event"]["source"] == "slack"
    finally:
        store.close()


async def test_low_relevance_stays_auditable_without_post(system):
    system.result.final_output = {
        "score": 0.1,
        "summary": "Routine update",
        "rationale": "No action",
    }
    store = Store(system.settings.database_path)
    try:
        with TestClient(app(system, store)) as client:
            assert post_github(client).json()["queued"] is True
        await worker(system, store).tick()
        assert system.posts == []
        assert store.stats()["filter_rate"] == 1
        assert store.filtered()[0]["score"]["rationale"] == "No action"
    finally:
        store.close()


async def test_same_pr_updates_batch_into_one_notification(system):
    system.settings.batch_window_seconds = 60
    store = Store(system.settings.database_path)
    try:
        with TestClient(app(system, store)) as client:
            assert post_github(client, "first").json()["queued"] is True
            assert post_github(client, "second", "synchronize").json()["queued"] is True
        instance = worker(system, store)
        await instance.tick()
        await instance.tick()
        assert system.posts == []
        assert store.stats()["batches"] == {"pending": 1}
        # Advance only the durable due time, avoiding a wall-clock wait in this test.
        store.db.execute("UPDATE batches SET due_at=0")
        store.db.commit()
        await instance.tick()
        assert len(system.posts) == 1
        assert "2 update(s)" in system.posts[0]["text"]
        assert store.stats()["batches"] == {"sent": 1}
    finally:
        store.close()
