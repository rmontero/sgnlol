import pytest
from pydantic import ValidationError

from triage.config import AppConfig, OrgConfig, RepoConfig, Settings, load_config
from triage.models import Event, Score
from triage.routing import resolve_routes


def config(kind="startup"):
    return AppConfig(
        orgs=[
            OrgConfig(
                id="acme",
                github_org="acme",
                slack_team_id="T1",
                type=kind,
                recipient="U1",
                repos={"acme/api": RepoConfig(recipients=["C1", "C2"], threshold=0.85)},
                slack_channels=["C_SOURCE"],
            )
        ]
    )


def event(**changes):
    values = dict(
        id="github:1",
        org_id="acme",
        source="github",
        kind="pull_request",
        subject_key="https://github.com/acme/api/pull/1",
        repo="acme/api",
    )
    values.update(changes)
    return Event(**values)


def test_startup_uses_single_recipient_and_repo_threshold():
    routes = resolve_routes(event(), config())
    assert [(route.recipient, route.threshold) for route in routes] == [("U1", 0.85)]


def test_enterprise_repo_recipients():
    assert [r.recipient for r in resolve_routes(event(), config("enterprise"))] == ["C1", "C2"]


@pytest.mark.parametrize(
    "changes",
    [{"org_id": "other"}, {"repo": "acme/private"}, {"repo": "other/api"}, {"repo": None}],
)
def test_unknown_github_identity_denied(changes):
    assert resolve_routes(event(**changes), config()) == []


def test_slack_requires_explicit_channel_allowlist():
    assert resolve_routes(event(source="slack", metadata={"channel": "C_OTHER"}), config()) == []
    assert resolve_routes(event(source="slack", metadata={}), config()) == []
    assert [
        r.recipient
        for r in resolve_routes(
            event(source="slack", metadata={"channel": "C_SOURCE", "team_id": "T1"}),
            config("enterprise"),
        )
    ] == ["U1"]


def test_enterprise_missing_recipient_has_no_implicit_destination():
    cfg = config("enterprise")
    cfg.orgs[0].recipient = None
    cfg.orgs[0].repos["acme/api"].recipients = []
    assert resolve_routes(event(), cfg) == []
    assert (
        resolve_routes(
            event(source="slack", metadata={"channel": "C_SOURCE", "team_id": "T1"}), cfg
        )
        == []
    )


def test_threshold_zero_overrides_default():
    cfg = config()
    cfg.orgs[0].repos["acme/api"].threshold = 0
    assert resolve_routes(event(), cfg)[0].threshold == 0


def test_github_identifiers_case_insensitive():
    assert resolve_routes(event(repo="ACME/API"), config())


def test_config_hot_reloads_and_invalid_edit_fails_closed(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("orgs: []")
    assert load_config(path).orgs == []
    path.write_text(
        "orgs:\n  - id: acme\n    github_org: acme\n    slack_team_id: T1\n    type: startup\n    recipient: U1\n"
    )
    assert load_config(path).orgs[0].recipient == "U1"
    path.write_text("orgs: invalid")
    with pytest.raises(ValidationError):
        load_config(path)


@pytest.mark.parametrize("score", [-0.1, 1.1, float("nan"), float("inf")])
def test_rejects_invalid_scores(score):
    with pytest.raises(ValidationError):
        Score(score=score, rationale="reason", summary="summary")


def test_config_rejects_unknown_fields_and_ambiguous_identity():
    with pytest.raises(ValidationError):
        AppConfig(orgs=[], slack_bot_token="secret")
    org = config().orgs[0]
    with pytest.raises(ValidationError):
        AppConfig(orgs=[org, org])


def test_settings_environment_and_secret_repr(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "configured-model")
    monkeypatch.setenv("OPENAI_API_KEY", "private-key")
    monkeypatch.setenv("BATCH_WINDOW_SECONDS", "4")
    settings = Settings.from_env()
    assert settings.model == "configured-model"
    assert settings.batch_window_seconds == 4
    assert settings.openai_api_key == "private-key"
    assert "private-key" not in repr(settings)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"batch_window_seconds": -1},
        {"max_attempts": 0},
        {"scoring_timeout_seconds": float("nan")},
        {"fail_safe": "drop"},
    ],
)
def test_settings_reject_invalid_reliability_controls(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)
