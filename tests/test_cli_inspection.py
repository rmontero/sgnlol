import json
import sqlite3
import stat

import pytest

from triage.cli import backup, main
from triage.models import Event, Route, Score
from triage.store import Store


@pytest.fixture
def populated(tmp_path):
    path = tmp_path / "events.sqlite3"
    store = Store(str(path))
    for event_id, source, text, score in (
        ("urgent", "github", "Production blocker", .95),
        ("routine", "slack", "Routine update", .1),
    ):
        store.enqueue(Event(id=event_id, org_id="org", source=source,
                            kind="message", subject_key=event_id, text=text))
        store.complete(event_id, Score(score=score, rationale="Reason", summary=text),
                       [Route(recipient="U1", threshold=.7)] if score > .7 else [], 0)
    yield path, store
    store.close()


def test_events_filter_and_inspect_delivery(populated, capsys):
    path, _ = populated
    assert main(["events", "--database", str(path), "--json", "--source", "github",
                 "--search", "blocker", "--min-score", ".8", "--sort", "score", "--limit", "1"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == "urgent"
    assert main(["inspect-event", "urgent", "--database", str(path), "--json"]) == 0
    event = json.loads(capsys.readouterr().out)
    assert event["score"]["score"] == .95
    assert event["deliveries"][0]["recipient"] == "U1"
    assert event["deliveries"][0]["status"] == "pending"
    assert main(["events", "--database", str(path), "--status", "filtered", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["items"][0]["id"] == "routine"


@pytest.mark.parametrize("args", [["inspect-event", "missing"], ["events", "--min-score", "nan"],
                                  ["events", "--limit", "101"]])
def test_invalid_inspection_fails(populated, args, capsys):
    path, _ = populated
    assert main([*args, "--database", str(path)]) == 1
    assert "Command failed" in capsys.readouterr().err


def test_backup_includes_committed_wal_and_is_private(populated, tmp_path, capsys):
    path, source = populated
    assert (tmp_path / "events.sqlite3-wal").exists()
    destination = tmp_path / "snapshot.sqlite3"
    assert main(["backup", str(destination), "--database", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["backed_up"] is True
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    with sqlite3.connect(destination) as snapshot:
        assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert snapshot.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
        assert snapshot.execute("SELECT COUNT(*) FROM batch_events").fetchone()[0] == 1
    assert source.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    assert not (tmp_path / "snapshot.sqlite3-wal").exists()


def test_backup_refuses_source_sidecars_and_existing_destinations(populated, tmp_path):
    path, source = populated
    for suffix in ("", "-wal", "-shm", "-journal"):
        with pytest.raises(ValueError):
            backup(source, str(path) + suffix)
    existing = tmp_path / "existing.sqlite3"
    existing.write_bytes(b"preserve")
    with pytest.raises(ValueError):
        backup(source, str(existing))
    assert existing.read_bytes() == b"preserve"
    dangling = tmp_path / "dangling.sqlite3"
    dangling.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError):
        backup(source, str(dangling))
    assert dangling.is_symlink()
    alias = tmp_path / "alias.sqlite3"
    alias.symlink_to(path)
    with pytest.raises(ValueError):
        backup(source, str(alias))


def test_backup_missing_source_does_not_create_database(tmp_path):
    source = tmp_path / "missing.sqlite3"
    target = tmp_path / "snapshot.sqlite3"
    assert main(["backup", str(target), "--database", str(source)]) == 1
    assert not source.exists()
    assert not target.exists()
