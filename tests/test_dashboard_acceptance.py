"""Provider-free acceptance checks across signed ingress, worker, storage and UI API."""

import asyncio
import hashlib
import hmac
import json
import sqlite3
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from triage.app import create_app
from triage.config import Settings
from triage.delivery import DeliveryError
from triage.models import Score
from triage.store import Store
from triage.worker import Worker

AUTH = ("operator", "fixture-dashboard-password")
HOSTILE = '<img src=x onerror="alert(1)"> javascript:alert(1)'


@pytest.fixture
def pipeline(tmp_path):
    config = tmp_path / "orgs.yaml"
    config.write_text("""orgs:
  - id: fixture
    github_org: fixture-owner
    slack_team_id: T_FIXTURE
    type: startup
    recipient: U_FIXTURE_AUTHORITY
    threshold: 0.7
    repos:
      fixture-owner/repo: {}
    slack_channels: [C_FIXTURE_ALLOWED]
""")
    settings = Settings(
        config_path=str(config), database_path=str(tmp_path / "triage.sqlite3"),
        github_webhook_secret="fixture-github", slack_signing_secret="fixture-slack",
        dashboard_username=AUTH[0], dashboard_password=AUTH[1], batch_window_seconds=0,
    )
    store = Store(settings.database_path)
    scorer, delivery = AsyncMock(), AsyncMock()
    scorer.score.return_value = Score(score=0.95, rationale="Owner decision blocks release", summary="Review release")
    delivery.send.return_value = "123.456"
    worker = Worker(store, settings, scorer, delivery)
    app = create_app(settings=settings, store=store, scorer=scorer, delivery=delivery, run_worker=False)
    with TestClient(app) as client:
        yield client, store, settings, scorer, delivery, worker
    store.close()


def ingest(client, settings, source, allowed=True):
    if source == "github":
        payload = {
            "action": "opened", "repository": {"owner": {"login": "fixture-owner"},
            "full_name": "fixture-owner/repo" if allowed else "fixture-owner/unauthorized"},
            "pull_request": {"html_url": "https://github.com/fixture-owner/repo/pull/1", "title": HOSTILE},
            "sender": {"login": "fixture-author"},
        }
        body = json.dumps(payload).encode()
        headers = {"x-github-event": "pull_request", "x-github-delivery": "fixture-delivery",
                   "x-hub-signature-256": "sha256=" + hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()}
    else:
        payload = {"type": "event_callback", "team_id": "T_FIXTURE", "event_id": "fixture-event",
                   "event": {"type": "message", "channel": "C_FIXTURE_ALLOWED" if allowed else "C_OTHER",
                             "user": "U_FIXTURE_AUTHOR", "ts": "123.456", "text": HOSTILE}}
        body = json.dumps(payload).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(settings.slack_signing_secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
        headers = {"x-slack-request-timestamp": timestamp, "x-slack-signature": "v0=" + signature}
    return client.post("/webhooks/" + source, content=body, headers=headers)


@pytest.mark.parametrize("source", ["github", "slack"])
@pytest.mark.parametrize("outcome", ["sent", "filtered", "unknown"])
def test_signed_source_to_ranked_dashboard_and_duplicate_suppression(pipeline, source, outcome):
    client, store, settings, scorer, delivery, worker = pipeline
    if outcome == "filtered":
        scorer.score.return_value = Score(score=0.1, rationale="Routine update", summary="Low relevance")
    elif outcome == "unknown":
        delivery.send.side_effect = DeliveryError("fixture timeout", uncertain=True)
    response = ingest(client, settings, source)
    assert response.status_code == 200
    assert response.json() == {"accepted": True, "queued": True}
    assert scorer.score.await_count == delivery.send.await_count == 0  # Durable ack precedes providers.
    asyncio.run(worker.tick())
    assert ingest(client, settings, source).json()["queued"] is False
    asyncio.run(worker.tick())
    assert scorer.score.await_count == 1
    assert client.get("/api/dashboard/events").status_code == 401
    response = client.get("/api/dashboard/events", auth=AUTH, params={"source": source, "sort": "score"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.json()["total"] == 1
    event = response.json()["items"][0]
    assert event["text"] == HOSTILE
    assert event["score"]["rationale"] == scorer.score.return_value.rationale
    assert event["score"]["score"] == scorer.score.return_value.score
    assert event["status"] == ("filtered" if outcome == "filtered" else "completed")
    assert client.get("/api/dashboard/events/" + event["id"], auth=AUTH).json() == event
    batches = client.get("/api/dashboard/deliveries", auth=AUTH).json()
    if outcome == "filtered":
        assert batches["total"] == 0
        assert event["deliveries"] == []
        delivery.send.assert_not_awaited()
    else:
        assert batches["total"] == 1
        assert batches["items"][0]["status"] == outcome
        assert event["deliveries"][0]["status"] == outcome
        delivery.send.assert_awaited_once()
        assert delivery.send.call_args.args[0]["recipient"] == "U_FIXTURE_AUTHORITY"
    overview = client.get("/api/dashboard/overview", auth=AUTH).json()
    assert overview["processed"] == 1
    assert overview["filtered"] == int(outcome == "filtered")
    # A fresh connection proves the dashboard is showing committed, durable records.
    reopened = Store(settings.database_path)
    try:
        row = reopened.db.execute("SELECT score FROM events").fetchone()
        assert json.loads(row[0])["rationale"] == event["score"]["rationale"]
    finally:
        reopened.close()


@pytest.mark.parametrize("source", ["github", "slack"])
def test_signed_but_unauthorized_source_never_scores_or_sends(pipeline, source):
    client, _, settings, scorer, delivery, worker = pipeline
    assert ingest(client, settings, source, allowed=False).json() == {"accepted": True, "queued": False}
    asyncio.run(worker.tick())
    scorer.score.assert_not_awaited()
    delivery.send.assert_not_awaited()
    assert client.get("/api/dashboard/overview", auth=AUTH).json()["total_events"] == 0


def test_existing_schema_and_pre_provenance_score_remain_visible(pipeline, tmp_path):
    client, _, settings, scorer, delivery, _ = pipeline
    path = tmp_path / "legacy.sqlite3"
    # Frozen original events schema, populated without the new Score model.
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE events (
            key INTEGER PRIMARY KEY, id TEXT NOT NULL, org_id TEXT NOT NULL,
            source TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL,
            lease_until REAL, error TEXT, score TEXT, completed_at REAL,
            UNIQUE(org_id, source, id))""")
        db.execute("""INSERT INTO events(id,org_id,source,payload,status,available_at,score)
            VALUES (?,?,?,?,?,?,?)""", ("legacy", "fixture", "slack", json.dumps({"text": "Old message", "kind": "message"}),
            "filtered", 1, json.dumps({"score": .2, "rationale": "Historical rationale", "summary": "Old summary"})))
    store = Store(str(path))
    try:
        with TestClient(create_app(settings=settings, store=store, scorer=scorer, delivery=delivery, run_worker=False)) as legacy:
            event = legacy.get("/api/dashboard/events/legacy", auth=AUTH).json()
            assert event["score"]["rationale"] == "Historical rationale"
            assert legacy.get("/api/dashboard/overview", auth=AUTH).json()["processed"] == 1
    finally:
        store.close()


def test_dashboard_assets_do_not_interpret_provider_text_as_markup(pipeline):
    client, *_ = pipeline
    js = client.get("/dashboard/assets/app.js", auth=AUTH)
    assert js.status_code == 200
    assert "textContent" in js.text
    assert "innerHTML" not in js.text
    assert "insertAdjacentHTML" not in js.text
    assert 'url.protocol === "https:"' in js.text
    assert client.get("/dashboard/assets/../../config.py", auth=AUTH).status_code == 404
