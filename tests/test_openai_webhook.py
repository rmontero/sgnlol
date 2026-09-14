import base64
import hashlib
import hmac
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from triage.app import create_app
from triage.cli import main
from triage.config import Settings
from triage.store import Store

KEY = b"local-webhook-signature-test-key"
SECRET = "whsec_" + base64.b64encode(KEY).decode()


def signed(body, timestamp=None, webhook_id="evt_delivery_1"):
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    signature = base64.b64encode(
        hmac.new(
            KEY, webhook_id.encode() + b"." + timestamp.encode() + b"." + body, hashlib.sha256
        ).digest()
    ).decode()
    return {
        "webhook-id": webhook_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": "v1," + signature,
    }


def event(**changes):
    return json.dumps(
        {
            "id": "evt_test",
            "object": "event",
            "type": "response.completed",
            "created_at": int(time.time()),
            "data": {"id": "resp_test"},
            **changes,
        }
    ).encode()


@pytest.fixture
def endpoint(tmp_path):
    config = tmp_path / "orgs.yaml"
    config.write_text("orgs: []\n")
    settings = Settings(
        config_path=str(config),
        database_path=str(tmp_path / "data.db"),
        openai_webhook_secret=SECRET,
    )
    store = Store(settings.database_path)
    with TestClient(create_app(settings, store=store, run_worker=False)) as client:
        yield client, store, settings
    store.close()


def test_valid_event_persists_and_deduplicates_across_restart(endpoint):
    client, store, settings = endpoint
    body = event()
    first = client.post("/webhooks/openai", content=body, headers=signed(body))
    assert first.status_code == 200
    assert first.json() == {"received": True, "duplicate": False}
    # The provider may retry the same event with another delivery identifier.
    retry = client.post(
        "/webhooks/openai", content=body, headers=signed(body, webhook_id="retry_2")
    )
    assert retry.json() == {"received": True, "duplicate": True}
    reopened = Store(settings.database_path)
    try:
        assert len(reopened.openai_events()) == 1
        assert reopened.openai_events()[0]["id"] == "evt_test"
        assert reopened.claim() is None  # Never rescore lifecycle events or notify Slack.
    finally:
        reopened.close()


@pytest.mark.parametrize("age", [-301, 301])
def test_expired_and_future_signatures_rejected(endpoint, age):
    client, store, _ = endpoint
    body = event()
    response = client.post(
        "/webhooks/openai", content=body, headers=signed(body, int(time.time()) + age)
    )
    assert response.status_code == 401
    assert store.openai_events() == []


def test_invalid_signature_before_json(endpoint):
    client, store, _ = endpoint
    assert client.post("/webhooks/openai", content=b"not json").status_code == 401
    body = event()
    headers = signed(body)
    assert client.post("/webhooks/openai", content=body + b" ", headers=headers).status_code == 401
    assert store.openai_events() == []


def test_signed_invalid_json_rejected(endpoint):
    client, _, _ = endpoint
    body = b"not json"
    assert client.post("/webhooks/openai", content=body, headers=signed(body)).status_code == 400


@pytest.mark.parametrize(
    "body", [b"[]", event(id=""), event(data=[]), event(created_at="123"), event(type="")]
)
def test_invalid_envelope_rejected(endpoint, body):
    client, store, _ = endpoint
    assert client.post("/webhooks/openai", content=body, headers=signed(body)).status_code == 400
    assert store.openai_events() == []


def test_future_event_types_are_received(endpoint):
    client, store, _ = endpoint
    body = event(type="future.event", extra_field=True)
    assert client.post("/webhooks/openai", content=body, headers=signed(body)).status_code == 200
    assert store.openai_events()[0]["type"] == "future.event"


def test_storage_failure_returns_retryable_response(endpoint, monkeypatch):
    client, store, _ = endpoint

    def fail(*args):
        raise sqlite3.OperationalError("sensitive detail")

    monkeypatch.setattr(store, "record_openai_event", fail)
    body = event()
    response = client.post("/webhooks/openai", content=body, headers=signed(body))
    assert response.status_code == 503
    assert "sensitive detail" not in response.text


def test_missing_secret_fails_closed(endpoint):
    client, store, settings = endpoint
    settings.openai_webhook_secret = ""
    body = event()
    assert client.post("/webhooks/openai", content=body, headers=signed(body)).status_code == 503
    assert store.openai_events() == []


def test_body_limit(endpoint):
    client, _, _ = endpoint
    assert client.post("/webhooks/openai", content=b"x" * (1024 * 1024 + 1)).status_code == 413


def test_cli_exposes_only_receipt_metadata(endpoint, capsys):
    client, _, settings = endpoint
    body = event(data={"id": "resp_test", "private": "not-for-cli-output"})
    client.post("/webhooks/openai", content=body, headers=signed(body))
    assert main(["openai-events", "--database", settings.database_path, "--json"]) == 0
    output = capsys.readouterr().out
    assert "evt_test" in output and "not-for-cli-output" not in output


def test_settings_reads_secret_without_repr_leak(monkeypatch):
    monkeypatch.setenv("OPENAI_WEBHOOK_SECRET", SECRET)
    settings = Settings.from_env()
    assert settings.openai_webhook_secret == SECRET
    assert SECRET not in repr(settings)
