"""Read-only, bounded inspection of the existing queue and scoring database."""

from __future__ import annotations

import json
import math

from triage.store import Store


class Analytics:
    """Operator inspection; callers must enforce operator authentication."""

    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def _page(limit, offset):
        return max(1, min(int(limit), 100)), max(0, min(int(offset), 1_000_000))

    def overview(self) -> dict:
        with self.store._lock:
            db = self.store.db
            events = dict(db.execute("SELECT status,COUNT(*) FROM events GROUP BY status"))
            batches = dict(db.execute("SELECT status,COUNT(*) FROM batches GROUP BY status"))
            row = db.execute("""
                SELECT COUNT(*) AS processed,
                    AVG(CASE WHEN COALESCE(json_extract(score, '$.fallback'),0) = 0 AND NULLIF(json_extract(score, '$.model'),'') IS NOT NULL THEN json_extract(score, '$.score') END) AS avg_score,
                    COALESCE(SUM(COALESCE(json_extract(score, '$.fallback'),0) = 0 AND NULLIF(json_extract(score, '$.model'),'') IS NOT NULL),0) AS model_scored_count,
                    COALESCE(SUM(json_extract(score, '$.input_tokens')),0) AS input_tokens,
                    COALESCE(SUM(json_extract(score, '$.output_tokens')),0) AS output_tokens,
                    COALESCE(SUM(json_extract(score, '$.fallback') = 1),0) AS fallback_count,
                    COALESCE(SUM(COALESCE(json_extract(score, '$.fallback'),0) = 0 AND NULLIF(json_extract(score, '$.model'),'') IS NOT NULL AND json_extract(score, '$.score') < .4),0) AS low,
                    COALESCE(SUM(COALESCE(json_extract(score, '$.fallback'),0) = 0 AND NULLIF(json_extract(score, '$.model'),'') IS NOT NULL AND json_extract(score, '$.score') >= .4 AND json_extract(score, '$.score') < .7),0) AS medium,
                    COALESCE(SUM(COALESCE(json_extract(score, '$.fallback'),0) = 0 AND NULLIF(json_extract(score, '$.model'),'') IS NOT NULL AND json_extract(score, '$.score') >= .7 AND json_extract(score, '$.score') < .9),0) AS high,
                    COALESCE(SUM(COALESCE(json_extract(score, '$.fallback'),0) = 0 AND NULLIF(json_extract(score, '$.model'),'') IS NOT NULL AND json_extract(score, '$.score') >= .9),0) AS critical
                FROM events WHERE score IS NOT NULL
            """).fetchone()
            return {
                "events": events, "batches": batches, "total_events": sum(events.values()),
                "processed": row["processed"], "filtered": events.get("filtered", 0),
                "filter_rate": events.get("filtered", 0) / row["processed"] if row["processed"] else 0,
                "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
                "avg_score": row["avg_score"], "model_scored_count": row["model_scored_count"], "fallback_count": row["fallback_count"],
                "score_buckets": {name: row[name] for name in ("low", "medium", "high", "critical")},
            }

    def _items(self, rows):
        if not rows:
            return []
        keys = [row["key"] for row in rows]
        deliveries = {key: [] for key in keys}
        placeholders = ",".join("?" for _ in keys)
        for row in self.store.db.execute(
            f"""SELECT be.event_key,b.id,b.status,b.recipient,b.slack_ts,b.error
                FROM batch_events be JOIN batches b ON b.id=be.batch_id
                WHERE be.event_key IN ({placeholders}) ORDER BY b.id""", keys
        ):
            item = dict(row)
            deliveries[item.pop("event_key")].append(item)
        result = []
        for row in rows:
            payload = json.loads(row["payload"])
            result.append({
                **{name: row[name] for name in (
                    "id", "org_id", "source", "status", "completed_at", "attempts", "error"
                )},
                **{name: payload.get(name) for name in (
                    "kind", "repo", "actor", "text", "url", "received_at"
                )},
                "score": json.loads(row["score"]) if row["score"] else None,
                "deliveries": deliveries[row["key"]],
            })
        return result

    def events(self, source=None, status=None, q=None, min_score=None,
               limit=50, offset=0, sort="newest") -> dict:
        limit, offset = self._page(limit, offset)
        orders = {
            "newest": "e.key DESC", "oldest": "e.key ASC",
            "score": "CASE WHEN COALESCE(json_extract(e.score, '$.fallback'),0) = 0 THEN json_extract(e.score, '$.score') END DESC, e.key DESC",
        }
        if sort not in orders:
            raise ValueError("sort must be newest, oldest, or score")
        conditions, params = [], []
        for name, value in (("source", source), ("status", status)):
            if value is not None:
                conditions.append(f"e.{name} = ?")
                params.append(value)
        if q:
            # Literal substring search: wildcard characters have no special meaning.
            conditions.append("""(instr(lower(COALESCE(json_extract(e.payload, '$.text'),'')),lower(?)) > 0
                OR instr(lower(COALESCE(json_extract(e.payload, '$.repo'),'')),lower(?)) > 0
                OR instr(lower(COALESCE(json_extract(e.payload, '$.actor'),'')),lower(?)) > 0)""")
            params.extend([str(q)[:200]] * 3)
        if min_score is not None:
            min_score = float(min_score)
            if not math.isfinite(min_score) or not 0 <= min_score <= 1:
                raise ValueError("min_score must be between 0 and 1")
            conditions.append("COALESCE(json_extract(e.score, '$.fallback'),0) = 0 AND json_extract(e.score, '$.score') >= ?")
            params.append(min_score)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.store._lock:
            total = self.store.db.execute("SELECT COUNT(*) FROM events e" + where, params).fetchone()[0]
            rows = self.store.db.execute(
                "SELECT e.* FROM events e" + where + f" ORDER BY {orders[sort]} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            return {"items": self._items(rows), "total": total, "limit": limit, "offset": offset}

    def event(self, event_id: str) -> dict | None:
        with self.store._lock:
            rows = self.store.db.execute("SELECT * FROM events WHERE id=? LIMIT 2", (event_id,)).fetchall()
            if len(rows) > 1:
                raise ValueError("Ambiguous event identifier")
            return self._items(rows)[0] if rows else None

    def deliveries(self, limit=50, offset=0) -> dict:
        limit, offset = self._page(limit, offset)
        with self.store._lock:
            total = self.store.db.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
            rows = self.store.db.execute("""
                SELECT b.id,b.org_id,b.recipient,b.subject_key,b.status,b.due_at,
                    b.error,b.slack_ts,b.attempts,
                    (SELECT COUNT(*) FROM batch_events be WHERE be.batch_id=b.id) AS event_count
                FROM batches b ORDER BY b.due_at DESC,b.id DESC LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()
            return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}
