"""Initialize Railway's mounted volume, then start the API as the app user."""

import os
from pathlib import Path


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
