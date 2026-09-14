"""Local operator commands. These commands never contact providers or send messages."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import yaml

from triage.config import AppConfig, Settings, load_config
from triage.store import Store


def _common(parser: argparse.ArgumentParser) -> None:
    # Suppressed defaults allow options before or after the subcommand.
    parser.add_argument("--config", default=argparse.SUPPRESS, help="Routing YAML path")
    parser.add_argument("--database", default=argparse.SUPPRESS, help="SQLite database path")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="sgnlol", description=__doc__)
    _common(result)
    commands = result.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("validate-config", "Validate routing configuration"),
        ("stats", "Show queue, filtering, and token counts"),
        ("digest", "Print filtered items locally; no Slack delivery"),
        ("set-threshold", "Atomically update an organization or repository threshold"),
        ("batches", "List failed and unknown batches for manual reconciliation"),
    ):
        command = commands.add_parser(name, help=help_text)
        _common(command)
        if name == "set-threshold":
            command.add_argument("org")
            command.add_argument("threshold", type=float)
            command.add_argument("--repo", help="Existing repository owner/name")
        if name in {"digest", "batches"}:
            command.add_argument("--limit", type=int, default=50)
    return result


def set_threshold(path: str, org_id: str, threshold: float, repo: str | None) -> dict:
    target = Path(path)
    original = target.read_text(encoding="utf-8")
    document = yaml.safe_load(original)
    AppConfig.model_validate(document)
    org = next((org for org in document["orgs"] if org["id"] == org_id), None)
    if org is None:
        raise ValueError("Unknown organization")
    if repo is None:
        org["threshold"] = threshold
    else:
        repositories = org.get("repos", {})
        key = next((key for key in repositories if key.casefold() == repo.casefold()), None)
        if key is None:
            raise ValueError("Unknown repository")
        repositories[key]["threshold"] = threshold
    AppConfig.model_validate(document)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, target.stat().st_mode & 0o777)
            yaml.safe_dump(document, stream, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        load_config(temporary)
        # Avoid overwriting edits observed since this command began.
        if target.read_text(encoding="utf-8") != original:
            raise ValueError("Configuration changed during update")
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"updated": True, "org": org_id, "repo": repo, "threshold": threshold}


def _render(value: dict | list, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=True, indent=2))
    elif isinstance(value, dict):
        for key, item in value.items():
            print(f"{key}: {json.dumps(item, ensure_ascii=True)}")
    elif not value:
        print("No items.")
    else:
        for item in value:
            # JSON escaping also prevents terminal control characters in untrusted content.
            print(json.dumps(item, ensure_ascii=True))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = None
    try:
        settings = Settings.from_env()
        config_path = getattr(args, "config", settings.config_path)
        database_path = getattr(args, "database", settings.database_path)
        if args.command == "validate-config":
            config = load_config(config_path)
            result = {"valid": True, "organizations": len(config.orgs)}
        elif args.command == "set-threshold":
            result = set_threshold(config_path, args.org, args.threshold, args.repo)
        else:
            if hasattr(args, "limit") and not 1 <= args.limit <= 1000:
                raise ValueError("Limit must be between 1 and 1000")
            store = Store(database_path)
            if args.command == "stats":
                result = store.stats()
            elif args.command == "digest":
                result = store.filtered(args.limit)
            else:
                # Do not print stored error strings, which can contain provider secrets.
                result = [
                    dict(row)
                    for row in store.db.execute(
                        "SELECT id,org_id,recipient,status,attempts FROM batches "
                        "WHERE status IN ('failed','unknown') ORDER BY due_at LIMIT ?",
                        (args.limit,),
                    ).fetchall()
                ]
        _render(result, getattr(args, "json", False))
        return 0
    except (OSError, ValueError, yaml.YAMLError, sqlite3.Error):
        print(
            "Command failed: check configuration, arguments, and database access.", file=sys.stderr
        )
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
