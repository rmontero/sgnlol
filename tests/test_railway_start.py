import pytest

from triage import railway_start


def test_bootstrap_initializes_only_missing_config(tmp_path, monkeypatch):
    monkeypatch.setattr(railway_start, "Path", lambda _: tmp_path)
    monkeypatch.setattr(railway_start.os, "geteuid", lambda: 10001)
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    monkeypatch.setenv("INITIALIZE_EMPTY_CONFIG", "1")
    railway_start.prepare_volume()
    config = tmp_path / "orgs.yaml"
    assert config.read_text() == "orgs: []\n"
    config.write_text("orgs: [configured]\n")
    railway_start.prepare_volume()
    assert config.read_text() == "orgs: [configured]\n"


def test_bootstrap_requires_persistent_volume(monkeypatch):
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    with pytest.raises(RuntimeError, match="persistent"):
        railway_start.prepare_volume()


def test_bootstrap_drops_privileges_and_ignores_symlinks(tmp_path, monkeypatch):
    monkeypatch.setattr(railway_start, "Path", lambda _: tmp_path)
    monkeypatch.setattr(railway_start.os, "geteuid", lambda: 0)
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    monkeypatch.delenv("INITIALIZE_EMPTY_CONFIG", raising=False)
    (tmp_path / "triage.sqlite3").symlink_to("/etc/passwd")
    calls = []
    monkeypatch.setattr(railway_start.os, "chown", lambda *args: calls.append(("chown", args)))
    monkeypatch.setattr(
        railway_start.os, "setgroups", lambda groups: calls.append(("groups", groups))
    )
    monkeypatch.setattr(railway_start.os, "setgid", lambda gid: calls.append(("gid", gid)))
    monkeypatch.setattr(railway_start.os, "setuid", lambda uid: calls.append(("uid", uid)))
    railway_start.prepare_volume()
    assert calls == [
        ("chown", (tmp_path, 10001, 10001)),
        ("groups", []),
        ("gid", 10001),
        ("uid", 10001),
    ]
