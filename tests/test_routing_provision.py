"""Persistent operator routing updates must be validated before replacement."""

import os

import pytest

from triage.config import load_config
from triage.railway_start import apply_routing_config


ROUTING = """orgs:
  - id: talacha
    github_org: rmontero
    slack_team_id: T02CGKDRDV1
    type: startup
    recipient: U02C1MHKQF9
    threshold: 0.7
    slack_channels: [C0C18A105S7]
    repos:
      rmontero/sgnlol: {}
"""


def test_creates_valid_private_persistent_routing(tmp_path):
    path = tmp_path / "orgs.yaml"
    assert apply_routing_config(path, ROUTING)
    org = load_config(path).orgs[0]
    assert org.recipient == "U02C1MHKQF9"
    assert org.slack_channels == ["C0C18A105S7"]
    assert path.stat().st_mode & 0o777 == 0o600
    assert not apply_routing_config(path, ROUTING)


@pytest.mark.parametrize("raw", ["", "[", "orgs: invalid", ROUTING.replace("0.7", "1.1"), "x" * 65537])
def test_invalid_override_preserves_previous_file(tmp_path, raw):
    path = tmp_path / "orgs.yaml"
    path.write_text(ROUTING)
    inode = path.stat().st_ino
    with pytest.raises(ValueError):
        apply_routing_config(path, raw)
    assert path.read_text() == ROUTING
    assert path.stat().st_ino == inode
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("existing", [True, False])
def test_refuses_symlinks_including_dangling(tmp_path, existing):
    target = tmp_path / "target.yaml"
    if existing:
        target.write_text("orgs: []\n")
    path = tmp_path / "orgs.yaml"
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        apply_routing_config(path, ROUTING)
    assert path.is_symlink()
    if existing:
        assert target.read_text() == "orgs: []\n"
    else:
        assert not target.exists()


def test_semantic_noop_preserves_operator_format_and_inode(tmp_path):
    path = tmp_path / "orgs.yaml"
    path.write_text(ROUTING + "# preserve operator comment\n")
    inode = path.stat().st_ino
    assert not apply_routing_config(path, ROUTING.replace("    threshold: 0.7\n", ""))
    assert path.stat().st_ino == inode
    assert "# preserve operator comment" in path.read_text()


def test_atomic_replacement_and_cleanup(tmp_path, monkeypatch):
    path = tmp_path / "orgs.yaml"
    path.write_text("orgs: []\n")
    replace = os.replace

    def inspect_replace(source, destination):
        assert path.read_text() == "orgs: []\n"
        assert source.parent == path.parent
        assert load_config(source).orgs[0].id == "talacha"
        replace(source, destination)

    monkeypatch.setattr(os, "replace", inspect_replace)
    assert apply_routing_config(path, ROUTING)
    assert list(tmp_path.iterdir()) == [path]


def test_failed_replace_preserves_previous_file_and_cleans_temporary(tmp_path, monkeypatch):
    path = tmp_path / "orgs.yaml"
    path.write_text("orgs: []\n")

    def fail_replace(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated disk failure"):
        apply_routing_config(path, ROUTING)
    assert path.read_text() == "orgs: []\n"
    assert list(tmp_path.iterdir()) == [path]
