"""Initialize Railway's mounted volume, then start the API as the app user."""

import os
from pathlib import Path
import tempfile

import yaml

from triage.config import AppConfig


def apply_routing_config(path: Path, raw: str) -> bool:
    """Apply authoritative operator YAML atomically, without logging its contents.

    ROUTING_CONFIG_YAML takes precedence over the persistent file at startup.
    An invalid override stops startup and preserves the previous configuration.
    Call only after dropping the Railway root identity to the application user.
    """
    path = Path(path)
    if len(raw.encode("utf-8")) > 64 * 1024:
        raise ValueError("Routing configuration exceeds 64 KiB")
    try:
        desired = AppConfig.model_validate(yaml.safe_load(raw))
    except (ValueError, TypeError, yaml.YAMLError):
        raise ValueError("Invalid routing configuration override") from None
    if path.is_symlink():
        raise ValueError("Routing configuration target must not be a symlink")
    if path.exists():
        try:
            current = AppConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        except (ValueError, TypeError, yaml.YAMLError):
            current = None
        if current == desired:
            return False
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".orgs-", suffix=".yaml", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Check again before replacement; never follow an operator's symlink.
        if path.is_symlink():
            raise ValueError("Routing configuration target must not be a symlink")
        os.replace(temporary, path)
        return True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_volume():
    mount = Path("/app/data")
    if os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") != str(mount):
        raise RuntimeError("Railway startup requires the persistent /app/data volume")
    if os.geteuid() == 0:
        # Railway mounts volumes as root. Only adjust our mount and owned files;
        # do not recursively traverse operator-managed backups or symlinks.
        os.chown(mount, 10001, 10001)
        for name in ("orgs.yaml", "triage.sqlite3", "triage.sqlite3-wal", "triage.sqlite3-shm"):
            path = mount / name
            if path.exists() and not path.is_symlink():
                os.chown(path, 10001, 10001)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    config = mount / "orgs.yaml"
    override = os.environ.get("ROUTING_CONFIG_YAML")
    if override is not None:
        apply_routing_config(config, override)
    if os.environ.get("INITIALIZE_EMPTY_CONFIG") == "1":
        try:
            with config.open("x", encoding="utf-8") as stream:
                stream.write("orgs: []\n")
        except FileExistsError:
            pass


def main():
    prepare_volume()
    os.execvp(
        "uvicorn",
        [
            "uvicorn",
            "triage.app:app",
            "--host",
            "0.0.0.0",
            "--port",
            os.environ.get("PORT", "8000"),
            "--workers",
            "1",
        ],
    )


if __name__ == "__main__":
    main()
