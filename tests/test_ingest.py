import copy
import hashlib
import hmac

import pytest

from triage.config import AppConfig
from triage.ingest import normalize_github, normalize_slack, verify_github, verify_slack


@pytest.fixture
def config():
    return AppConfig.model_validate(
        {
            "orgs": [
                {
                    "id": "tenant",
                    "github_org": "acme",
                    "slack_team_id": "T1",
                    "type": "startup",
                    "recipient": "U1",
                    "repos": {"acme/api": {}},
                    "slack_channels": ["C1"],
                }
            ]
        }
    )


@pytest.fixture
def github():
    return {
        "action": "opened",
        "organization": {"login": "acme"},
        "repository": {"full_name": "acme/api", "owner": {"login": "acme"}},
        "sender": {"login": "alice"},
        "pull_request": {
            "html_url": "https://github.com/acme/api/pull/1",
            "title": "Security fix",
            "body": "Please review",
            "labels": [{"name": "security"}],
        },
    }


@pytest.fixture
def slack():
    return {
        "type": "event_callback",
        "team_id": "T1",
        "event_id": "Ev1",
        "event": {
            "type": "message",
            "channel": "C1",
            "user": "U2",
            "text": "Blocking release",
            "ts": "123.456",
        },
    }


def test_github_signature():
    body = b'{"action":"opened"}'
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_github(body, signature, "secret")
    assert not verify_github(body + b" ", signature, "secret")
    assert not verify_github(body, signature, "")
    assert not verify_github(body, "sha1=abc", "secret")
    assert not verify_github(body, "é", "secret")


def test_slack_signature_and_replay():
    body = b'{"type":"event_callback"}'
    signature = "v0=" + hmac.new(b"secret", b"v0:1000:" + body, hashlib.sha256).hexdigest()
    assert verify_slack(body, "1000", signature, "secret", now=1000)
    assert verify_slack(body, "1000", signature, "secret", now=1300)
    assert not verify_slack(body, "1000", signature, "secret", now=1301)
    assert not verify_slack(body, "1000", signature, "secret", now=699)
    assert not verify_slack(body + b" ", "1000", signature, "secret", now=1000)
    assert not verify_slack(body, "invalid", signature, "secret", now=1000)
    assert not verify_slack(body, "1000", "é", "secret", now=1000)
    assert not verify_slack(body, "1000", signature, "", now=1000)


@pytest.mark.parametrize(
    "event_type,action",
    [
        ("pull_request", "opened"),
        ("pull_request", "synchronize"),
        ("pull_request", "edited"),
        ("pull_request", "closed"),
        ("pull_request", "review_requested"),
        ("pull_request_review", "submitted"),
        ("pull_request_review_comment", "created"),
        ("issue_comment", "created"),
    ],
)
def test_github_event_families(config, github, event_type, action):
    github["action"] = action
    github["review"] = {"body": "Blocked", "state": "changes_requested"}
    github["issue"] = {
        "title": "Security fix",
        "pull_request": {"html_url": github["pull_request"]["html_url"]},
    }
    event = normalize_github(github, event_type, "delivery1", config)
    assert event.id == "github:tenant:delivery1"
    assert event.org_id == "tenant"
    assert event.repo == "acme/api"
    assert event.subject_key == "https://github.com/acme/api/pull/1"
    assert "Blocked" in event.text


def test_non_pr_issue_excluded(config, github):
    github.update(action="created", issue={"title": "An issue"})
    assert normalize_github(github, "issue_comment", "id", config) is None


def test_github_identity_allowlist(config, github):
    github["org_id"] = "attacker"
    assert normalize_github(github, "pull_request", "id", config).org_id == "tenant"
    for patch in [
        {"organization": {"login": "other"}},
        {"repository": {"full_name": "acme/private", "owner": {"login": "acme"}}},
        {"repository": {"full_name": "acme/api", "owner": {"login": "other"}}},
    ]:
        invalid = copy.deepcopy(github)
        invalid.update(patch)
        assert normalize_github(invalid, "pull_request", "id", config) is None
    assert normalize_github(github, "push", "id", config) is None
    assert normalize_github(github, "pull_request", "", config) is None


def test_slack_thread_and_mention(config, slack):
    first = normalize_slack(slack, config, "UBOT")
    assert first.id == "slack:tenant:Ev1"
    slack["event"].update(type="app_mention", ts="124.567", thread_ts="123.456")
    second = normalize_slack(slack, config, "UBOT")
    assert first.subject_key == second.subject_key
    assert second.kind == "app_mention"
    assert second.metadata["channel"] == "C1"


@pytest.mark.parametrize(
    "patch",
    [
        {"user": "UBOT"},
        {"bot_id": "B1"},
        {"bot_profile": {"id": "B1"}},
        {"subtype": "bot_message"},
        {"subtype": "message_changed"},
        {"channel": "C2"},
        {"user": ""},
        {"ts": ""},
    ],
)
def test_slack_exclusions(config, slack, patch):
    slack["event"].update(patch)
    assert normalize_slack(slack, config, "UBOT") is None


def test_slack_team_cannot_be_selected_by_org_id(config, slack):
    slack.update(team_id="TOTHER", org_id="tenant")
    assert normalize_slack(slack, config, "UBOT") is None


@pytest.mark.parametrize("payload", [{}, {"event": []}, {"repository": []}])
def test_malformed_payloads_excluded(config, payload):
    assert normalize_slack(payload, config, "UBOT") is None
    assert normalize_github(payload, "pull_request", "id", config) is None
