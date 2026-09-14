"""Durable SQLite queue and atomic, tenant-separated notification outbox."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from triage.models import Event, Route, Score


class Store:
    LEASE_SECONDS = 120

    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS openai_webhook_events (
                id TEXT PRIMARY KEY, webhook_id TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL, payload TEXT NOT NULL, received_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                key INTEGER PRIMARY KEY, id TEXT NOT NULL, org_id TEXT NOT NULL,
                source TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL,
                lease_until REAL, error TEXT, score TEXT, completed_at REAL,
                UNIQUE(org_id, source, id)
            );
            CREATE INDEX IF NOT EXISTS events_ready ON events(status, available_at);
            CREATE TABLE IF NOT EXISTS ingress_digests (
                org_id TEXT NOT NULL, source TEXT NOT NULL, digest TEXT NOT NULL,
                PRIMARY KEY(org_id, source, digest)
            );
            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY, org_id TEXT NOT NULL, recipient TEXT NOT NULL,
                subject_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                due_at REAL NOT NULL, lease_until REAL, error TEXT, slack_ts TEXT,
                attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS batches_ready ON batches(status, due_at);
            CREATE TABLE IF NOT EXISTS batch_events (
                batch_id TEXT NOT NULL REFERENCES batches(id),
                event_key INTEGER NOT NULL REFERENCES events(key),
                PRIMARY KEY(batch_id, event_key)
            );
        """)
        self.db.commit()

    @contextmanager
    def _transaction(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def _event(self, event_id):
        rows = self.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchall()
        if len(rows) != 1:
            raise ValueError(
                "Event identifier missing or ambiguous; include tenant and source in IDs"
            )
        return rows[0]

    def record_openai_event(self, payload: dict, webhook_id: str) -> bool:
        """Commit a provider receipt before acknowledging; retries are idempotent."""
        with self._transaction():
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO openai_webhook_events(id,webhook_id,type,payload,received_at) VALUES(?,?,?,?,?)",
                (payload["id"], webhook_id, payload["type"], json.dumps(payload), time.time()),
            )
            return cursor.rowcount == 1

    def openai_events(self, limit=50) -> list[dict]:
        """List receipt metadata without exposing raw event data."""
        with self._lock:
            rows = self.db.execute(
                "SELECT id,webhook_id,type,received_at FROM openai_webhook_events ORDER BY received_at DESC LIMIT ?",
                (max(0, min(int(limit), 1000)),),
            ).fetchall()
            return [dict(row) for row in rows]

    def enqueue(self, event: Event) -> bool:
        with self._transaction():
            digest = event.metadata.get("webhook_body_sha256")
            if (
                digest
                and self.db.execute(
                    "SELECT 1 FROM ingress_digests WHERE org_id=? AND source=? AND digest=?",
                    (event.org_id, event.source, digest),
                ).fetchone()
            ):
                return False
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO events(id,org_id,source,payload,available_at) VALUES(?,?,?,?,?)",
                (event.id, event.org_id, event.source, event.model_dump_json(), time.time()),
            )
            inserted = cursor.rowcount == 1
            if inserted and digest:
                self.db.execute(
                    "INSERT INTO ingress_digests(org_id,source,digest) VALUES(?,?,?)",
                    (event.org_id, event.source, digest),
                )
            return inserted

    def claim(self, now=None) -> Event | None:
        now = time.time() if now is None else now
        with self._transaction():
            row = self.db.execute(
                """SELECT * FROM events WHERE
                (status='queued' AND available_at<=?) OR
                (status='processing' AND lease_until<=?) ORDER BY key LIMIT 1""",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            self.db.execute(
                "UPDATE events SET status='processing',attempts=attempts+1,lease_until=? WHERE key=?",
                (now + self.LEASE_SECONDS, row["key"]),
            )
            return Event.model_validate_json(row["payload"])

    def attempt_count(self, event_id: str) -> int:
        with self._lock:
            return self._event(event_id)["attempts"]

    def retry(self, event_id: str, error: str, delay: float) -> None:
        with self._transaction():
            row = self._event(event_id)
            self.db.execute(
                "UPDATE events SET status='queued',available_at=?,lease_until=NULL,error=? WHERE key=? AND status='processing'",
                (time.time() + max(0, delay), error[:500], row["key"]),
            )

    def dead_letter(self, event_id: str, error: str) -> None:
        with self._transaction():
            row = self._event(event_id)
            self.db.execute(
                "UPDATE events SET status='dead_letter',lease_until=NULL,error=? WHERE key=? AND status IN ('queued','processing')",
                (error[:500], row["key"]),
            )

    def complete(
        self, event_id: str, score: Score, routes: list[Route], batch_window: float
    ) -> None:
        now = time.time()
        with self._transaction():
            row = self._event(event_id)
            if row["status"] not in ("processing", "queued"):
                return
            event = Event.model_validate_json(row["payload"])
            # Defense in depth: callers may pass unfiltered configured routes.
            recipients = {route.recipient for route in routes if score.score >= route.threshold}
            self.db.execute(
                "UPDATE events SET status=?,score=?,completed_at=?,lease_until=NULL,error=NULL WHERE key=?",
                (
                    "completed" if recipients else "filtered",
                    score.model_dump_json(),
                    now,
                    row["key"],
                ),
            )
            for recipient in sorted(recipients):
                batch = self.db.execute(
                    """SELECT id FROM batches WHERE org_id=? AND recipient=?
                    AND subject_key=? AND status='pending' AND due_at>? ORDER BY due_at LIMIT 1""",
                    (event.org_id, recipient, event.subject_key, now),
                ).fetchone()
                batch_id = batch["id"] if batch else uuid.uuid4().hex
                if not batch:
                    self.db.execute(
                        "INSERT INTO batches(id,org_id,recipient,subject_key,due_at) VALUES(?,?,?,?,?)",
                        (
                            batch_id,
                            event.org_id,
                            recipient,
                            event.subject_key,
                            now + max(0, batch_window),
                        ),
                    )
                self.db.execute(
                    "INSERT OR IGNORE INTO batch_events(batch_id,event_key) VALUES(?,?)",
                    (batch_id, row["key"]),
                )

    def due_batches(self, now=None) -> list[dict]:
        now = time.time() if now is None else now
        with self._transaction():
            self.db.execute(
                """UPDATE batches SET status='unknown',error='Delivery lease expired; reconcile with Slack before retry'
                            WHERE status='sending' AND lease_until<=?""",
                (now,),
            )
            rows = self.db.execute(
                "SELECT * FROM batches WHERE status='pending' AND due_at<=? ORDER BY due_at LIMIT 20",
                (now,),
            ).fetchall()
            result = []
            for row in rows:
                self.db.execute(
                    "UPDATE batches SET status='sending',lease_until=?,attempts=attempts+1 WHERE id=?",
                    (now + self.LEASE_SECONDS, row["id"]),
                )
                entries = self.db.execute(
                    """SELECT e.payload,e.score FROM events e JOIN batch_events b
                    ON e.key=b.event_key WHERE b.batch_id=? ORDER BY e.key""",
                    (row["id"],),
                ).fetchall()
                result.append(
                    {
                        "id": row["id"],
                        "org_id": row["org_id"],
                        "recipient": row["recipient"],
                        "subject_key": row["subject_key"],
                        "events": [json.loads(e["payload"]) for e in entries],
                        "scores": [json.loads(e["score"]) for e in entries],
                    }
                )
            return result

    def begin_test_delivery(self, batch_id, org_id, recipient, actor):
        with self._transaction():
            cursor = self.db.execute("""INSERT OR IGNORE INTO batches
                (id,org_id,recipient,subject_key,status,due_at,lease_until,attempts)
                VALUES(?,?,?,?,'sending',?,?,1)""",
                (batch_id, org_id, recipient, 'test:requested by '+actor, time.time(), time.time()+120))
            return cursor.rowcount == 1

    def import_test_receipt(self, batch_id, org_id, recipient, channel, ts, actor):
        with self._transaction():
            self.db.execute("""INSERT OR IGNORE INTO batches
                (id,org_id,recipient,subject_key,status,due_at,slack_ts,attempts)
                VALUES(?,?,?,?,'sent',?,?,1)""",
                (batch_id, org_id, recipient, 'test:imported Slack confirmation '+channel+' by '+actor, float(ts), ts))

    def delivery_record(self, batch_id):
        with self._lock:
            row = self.db.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
            return dict(row) if row else None

    def mark_sent(self, batch_id: str, ts: str) -> None:
        with self._transaction():
            self.db.execute(
                "UPDATE batches SET status='sent',slack_ts=?,lease_until=NULL,error=NULL WHERE id=? AND status IN ('sending','unknown')",
                (ts, batch_id),
            )

    def retry_batch(self, batch_id: str, error: str, delay: float) -> None:
        with self._transaction():
            self.db.execute(
                "UPDATE batches SET status='pending',due_at=?,lease_until=NULL,error=? WHERE id=? AND status='sending'",
                (time.time() + max(0, delay), error[:500], batch_id),
            )

    def unknown_batch(self, batch_id: str, error: str) -> None:
        with self._transaction():
            self.db.execute(
                "UPDATE batches SET status='unknown',lease_until=NULL,error=? WHERE id=? AND status='sending'",
                (error[:500], batch_id),
            )

    def batch_attempt_count(self, batch_id: str) -> int:
        with self._lock:
            row = self.db.execute("SELECT attempts FROM batches WHERE id=?", (batch_id,)).fetchone()
            if row is None:
                raise ValueError("Batch identifier missing")
            return row["attempts"]

    def fail_batch(self, batch_id: str, error: str) -> None:
        with self._transaction():
            self.db.execute(
                "UPDATE batches SET status='failed',lease_until=NULL,error=? WHERE id=? AND status='sending'",
                (error[:500], batch_id),
            )

    def stats(self) -> dict:
        with self._lock:
            events = dict(
                self.db.execute("SELECT status,COUNT(*) FROM events GROUP BY status").fetchall()
            )
            batches = dict(
                self.db.execute("SELECT status,COUNT(*) FROM batches GROUP BY status").fetchall()
            )
            scores = [
                json.loads(r[0])
                for r in self.db.execute("SELECT score FROM events WHERE score IS NOT NULL")
            ]
            processed = len(scores)
            return {
                "events": events,
                "batches": batches,
                "total_events": sum(events.values()),
                "processed": processed,
                "filtered": events.get("filtered", 0),
                "filter_rate": events.get("filtered", 0) / processed if processed else 0,
                "input_tokens": sum(s.get("input_tokens", 0) for s in scores),
                "output_tokens": sum(s.get("output_tokens", 0) for s in scores),
            }

    def filtered(self, limit=50) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT payload,score,completed_at FROM events WHERE status='filtered' ORDER BY completed_at DESC LIMIT ?",
                (max(0, min(int(limit), 1000)),),
            ).fetchall()
            return [
                {
                    "event": json.loads(r["payload"]),
                    "score": json.loads(r["score"]),
                    "reason": "No eligible recipient met the configured routing threshold",
                    "completed_at": r["completed_at"],
                }
                for r in rows
            ]

    def close(self):
        with self._lock:
            self.db.close()
