import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from triage.app import create_app
from triage.config import Settings
from triage.store import Store


def setup(tmp_path):
    config = tmp_path / "orgs.yaml"
    config.write_text("""orgs:
  - id: acme
    github_org: acme
    slack_team_id: T123
    type: startup
    recipient: U123
    repos:
      acme/api: {}
    slack_channels: [C123]
""")
    settings = Settings(
        config_path=str(config),
        database_path=str(tmp_path / "test.db"),
        github_webhook_secret="test-github",
        slack_signing_secret="test-slack",
    )
    store = Store(settings.database_path)
    return settings, store, TestClient(create_app(settings, store=store, run_worker=False))


def github_headers(body):
    return {
        "x-hub-signature-256": "sha256="
        + hmac.new(b"test-github", body, hashlib.sha256).hexdigest(),
        "x-github-event": "pull_request",
        "x-github-delivery": "delivery-1",
    }


def test_signature_before_json(tmp_path):
    settings, store, client = setup(tmp_path)
    with client:
        assert client.post("/webhooks/github", content=b"not json").status_code == 401
        assert client.post("/webhooks/slack", content=b"not json").status_code == 401
        body = b"not json"
        assert (
            client.post("/webhooks/github", content=body, headers=github_headers(body)).status_code
            == 400
        )
    store.close()


def test_durable_ack_and_duplicate(tmp_path):
    settings, store, client = setup(tmp_path)
    body = json.dumps(
        {
            "action": "opened",
            "repository": {"full_name": "acme/api", "owner": {"login": "acme"}},
            "organization": {"login": "acme"},
            "sender": {"login": "dev"},
            "pull_request": {
                "number": 1,
                "title": "Blocking security fix",
                "body": "Review needed",
                "html_url": "https://github.com/acme/api/pull/1",
            },
        }
    ).encode()
    with client:
        assert client.get("/healthz").status_code == 200
        first = client.post("/webhooks/github", content=body, headers=github_headers(body))
        assert first.status_code == 200
        assert first.json()["queued"] is True
        assert (
            client.post("/webhooks/github", content=body, headers=github_headers(body)).json()[
                "queued"
            ]
            is False
        )
        altered = github_headers(body)
        altered["x-github-delivery"] = "altered-unsigned-header"
        assert (
            client.post("/webhooks/github", content=body, headers=altered).json()["queued"] is False
        )
        assert store.claim() is not None
    store.close()


def test_slack_challenge_signed(tmp_path):
    settings, store, client = setup(tmp_path)
    body = json.dumps({"type": "url_verification", "challenge": "hello"}).encode()
    timestamp = str(int(time.time()))
    signature = (
        "v0="
        + hmac.new(
            b"test-slack", b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
        ).hexdigest()
    )
    with client:
        result = client.post(
            "/webhooks/slack",
            content=body,
            headers={"x-slack-request-timestamp": timestamp, "x-slack-signature": signature},
        )
        assert result.json() == {"challenge": "hello"}
        assert client.post("/webhooks/github", content=b"x" * (1024 * 1024 + 1)).status_code == 413
    store.close()
