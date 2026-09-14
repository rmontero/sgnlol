"""The dashboard requires operator credentials and exposes only bounded reads."""

import pytest
from fastapi.testclient import TestClient

from triage.app import create_app
from triage.config import Settings
from triage.models import Event, Score
from triage.store import Store


@pytest.fixture
def dashboard(tmp_path):
    config = tmp_path / "orgs.yaml"
    config.write_text("orgs: []\n")
    settings = Settings(config_path=str(config), dashboard_password="test-password", slack_bot_token="secret-token")
    store = Store(":memory:")
    with TestClient(create_app(settings=settings, store=store, run_worker=False)) as client:
        yield client, store, settings
    store.close()


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard",
        "/dashboard/assets/app.js",
        "/api/dashboard/overview",
        "/api/dashboard/events",
        "/api/dashboard/deliveries",
        "/api/dashboard/routing",
    ],
)
def test_every_dashboard_surface_requires_auth(dashboard, path):
    client, _, _ = dashboard
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get(path, auth=("rob", "wrong-password")).status_code == 401


def test_disabled_dashboard_and_unaffected_health(dashboard):
    client, _, settings = dashboard
    settings.dashboard_password = ""
    assert client.get("/api/dashboard/overview").status_code == 503
    assert client.get("/healthz").json() == {"status": "ok"}


def test_authenticated_empty_inspection(dashboard):
    client, store, _ = dashboard
    client.auth = ("rob", "test-password")
    assert client.get("/api/dashboard/overview").json()["total_events"] == 0
    assert client.get("/api/dashboard/events").json()["items"] == []
    assert client.get("/api/dashboard/deliveries").json()["items"] == []
    assert client.get("/api/dashboard/routing").json() == {"orgs": []}
    assert client.get("/api/dashboard/events/not-found").status_code == 404
    assert "secret-token" not in client.get("/api/dashboard/overview").text
    assert store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 101},
        {"limit": 0},
        {"offset": -1},
        {"offset": 100001},
        {"min_score": "nan"},
        {"min_score": 1.1},
        {"source": "unknown"},
        {"sort": "invalid"},
        {"q": "x" * 201},
    ],
)
def test_invalid_filters_are_rejected(dashboard, params):
    client, _, _ = dashboard
    assert client.get("/api/dashboard/events", params=params, auth=("rob", "test-password")).status_code == 422


def test_assets_are_fixed_and_dashboard_read_only(dashboard):
    client, _, _ = dashboard
    client.auth = ("rob", "test-password")
    assert client.get("/dashboard/assets/config.py").status_code == 404
    assert client.post("/api/dashboard/routing", json={"orgs": []}).status_code == 405


def test_settings_env_and_secret_repr(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "operator")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "private-dashboard-password")
    settings = Settings.from_env()
    assert settings.dashboard_username == "operator"
    assert settings.dashboard_password == "private-dashboard-password"
    assert "private-dashboard-password" not in repr(settings)


def test_scored_event_search_and_detail(dashboard):
    client, store, _ = dashboard
    store.enqueue(
        Event(
            id="github:org/repo:123",
            org_id="org",
            source="github",
            kind="pull_request",
            subject_key="org/repo#1",
            text="Review blocker",
        )
    )
    store.complete(
        "github:org/repo:123", Score(score=0.9, rationale="Needs owner decision", summary="Review blocker"), [], 10
    )
    client.auth = ("rob", "test-password")
    response = client.get("/api/dashboard/events", params={"q": "blocker", "min_score": 0.7, "sort": "score"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["score"]["score"] == 0.9
    detail = client.get("/api/dashboard/events/github:org/repo:123")
    assert detail.status_code == 200
    assert detail.json()["score"]["rationale"] == "Needs owner decision"
    store.enqueue(
        Event(id="github:org/repo:123", org_id="other", source="github", kind="pull_request", subject_key="other/repo#1")
    )
    assert client.get("/api/dashboard/events/github:org/repo:123").status_code == 409
