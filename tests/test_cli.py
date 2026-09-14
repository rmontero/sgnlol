import json

import pytest
import yaml

from triage.cli import main
from triage.models import Event, Score
from triage.store import Store


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "orgs.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "orgs": [
                    {
                        "id": "acme",
                        "github_org": "acme",
                        "slack_team_id": "T1",
                        "type": "startup",
                        "recipient": "U1",
                        "threshold": 0.7,
                        "repos": {"acme/app": {"recipients": ["U2"], "threshold": 0.8}},
                    }
                ]
            }
        )
    )
    return path


def test_validate(config, capsys):
    assert main(["--config", str(config), "validate-config", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"valid": True, "organizations": 1}


@pytest.mark.parametrize("repo", [None, "ACME/APP"])
def test_threshold_preserves_other_values(config, repo):
    before = yaml.safe_load(config.read_text())
    command = ["set-threshold", "acme", "0.4", "--config", str(config)]
    if repo:
        command += ["--repo", repo]
    assert main(command) == 0
    after = yaml.safe_load(config.read_text())
    org = before["orgs"][0]
    if repo:
        org["repos"]["acme/app"]["threshold"] = 0.4
    else:
        org["threshold"] = 0.4
    assert after == before
    assert not list(config.parent.glob(".orgs.yaml.*"))


@pytest.mark.parametrize("value", ["1.1", "nan", "-0.1"])
def test_invalid_threshold_preserves_original(config, value):
    original = config.read_bytes()
    assert main(["set-threshold", "acme", value, "--config", str(config)]) == 1
    assert config.read_bytes() == original


def test_invalid_config_does_not_leak_content(tmp_path, capsys):
    path = tmp_path / "invalid.yaml"
    path.write_text("orgs: super-secret-token")
    assert main(["validate-config", "--config", str(path)]) == 1
    output = capsys.readouterr()
    assert "super-secret-token" not in output.err + output.out


def test_stats_and_digest(tmp_path, capsys):
    path = str(tmp_path / "db.sqlite3")
    store = Store(path)
    store.enqueue(
        Event(
            id="e1",
            org_id="acme",
            source="github",
            kind="issue",
            subject_key="issue:1",
            text="Routine change",
        )
    )
    store.complete("e1", Score(score=0.2, rationale="Routine", summary="Change"), [], 0)
    store.close()
    assert main(["stats", "--database", path, "--json"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["filtered"] == 1
    assert stats["filter_rate"] == 1
    assert main(["digest", "--database", path, "--json"]) == 0
    digest = json.loads(capsys.readouterr().out)
    assert digest[0]["event"]["id"] == "e1"
    assert digest[0]["score"]["score"] == 0.2


def test_batches_omits_error_strings(tmp_path, capsys):
    path = str(tmp_path / "db.sqlite3")
    store = Store(path)
    store.db.execute(
        "INSERT INTO batches(id,org_id,recipient,subject_key,status,due_at,error) "
        "VALUES('b','acme','U1','s','unknown',0,'secret-token')"
    )
    store.db.commit()
    store.close()
    assert main(["batches", "--database", path, "--json"]) == 0
    output = capsys.readouterr().out
    assert "secret-token" not in output
    assert json.loads(output)[0]["status"] == "unknown"


def test_unknown_repo_preserves_file(config):
    original = config.read_bytes()
    assert (
        main(["set-threshold", "acme", "0.5", "--repo", "acme/missing", "--config", str(config)])
        == 1
    )
    assert config.read_bytes() == original
