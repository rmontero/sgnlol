"""Inspection respects existing storage and never changes delivery state."""

import sqlite3
import threading
from types import SimpleNamespace

import pytest

from triage.analytics import Analytics
from triage.models import Event, Route, Score
from triage.store import Store


@pytest.fixture
def populated():
    store = Store(":memory:")
    for number, score in enumerate((0.2, 0.4, 0.7, 0.9)):
        store.enqueue(Event(
            id=f"event-{number}", org_id="org", source="slack" if number == 0 else "github",
            kind="comment", subject_key=f"pr:{number}", repo="org/repo",
            text="100% blocker" if number == 3 else "routine comment", actor="rob",
            metadata={"private_internal": "must not leak"},
        ))
        store.complete(f"event-{number}", Score(
            score=score, summary="summary", rationale="reason", input_tokens=10,
            output_tokens=2, fallback=number == 3, model="test-model" if number != 3 else None,
        ), [Route(recipient="UROB", threshold=0.6)], 0)
    yield store, Analytics(store)
    store.close()


def test_overview_boundaries_and_original_stats(populated):
    _, analytics = populated
    overview = analytics.overview()
    assert overview["score_buckets"] == dict(low=1, medium=1, high=1, critical=0)
    assert overview["total_events"] == overview["processed"] == 4
    assert overview["filtered"] == 2
    assert overview["filter_rate"] == 0.5
    assert overview["avg_score"] == pytest.approx((.2+.4+.7)/3)
    assert overview["fallback_count"] == 1
    assert overview["model_scored_count"] == 3
    assert overview["input_tokens"] == 40
    assert overview["output_tokens"] == 8


def test_filter_rank_page_literal_search_and_injection(populated):
    _, analytics = populated
    page = analytics.events(source="github", status="completed", min_score=0.7, sort="score", limit=1, offset=0)
    assert page["total"] == 1
    assert page["items"][0]["id"] == "event-2"
    assert len(analytics.events(q="%") ["items"]) == 1
    assert analytics.events(q="' OR 1=1 --")["total"] == 0
    assert analytics.events(source="github' OR 1=1 --")["total"] == 0
    assert analytics.events(limit=10000, offset=-1)["limit"] == 100
    assert analytics.events(offset=100)["items"] == []
    for score in (float("nan"), float("inf"), -1, 2):
        with pytest.raises(ValueError):
            analytics.events(min_score=score)
    with pytest.raises(ValueError):
        analytics.events(sort="e.key; DROP TABLE events")


def test_event_detail_delivery_and_no_raw_provider_receipts(populated):
    store, analytics = populated
    store.record_openai_event({"id": "provider", "type": "done", "data": {"secret": "private"}}, "wh-id")
    detail = analytics.event("event-3")
    assert detail["score"]["score"] == 0.9
    assert detail["deliveries"][0]["recipient"] == "UROB"
    assert "metadata" not in detail
    assert analytics.event("provider") is None
    assert analytics.event("missing") is None
    batches = analytics.deliveries(limit=1)
    assert batches["total"] == 2
    assert batches["items"][0]["event_count"] == 1
    assert batches["items"][0]["status"] == "pending"
    # Reading an expired/ready batch cannot claim it or change its state.
    assert store.stats()["batches"] == {"pending": 2}


def test_ambiguous_event_id_fails_closed(populated):
    store, analytics = populated
    store.enqueue(Event(id="event-3", org_id="other-org", source="slack", kind="comment", subject_key="other"))
    with pytest.raises(ValueError, match="Ambiguous"):
        analytics.event("event-3")


def test_legacy_database_without_provider_table_needs_no_migration():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE events (key INTEGER PRIMARY KEY,id TEXT,org_id TEXT,source TEXT,payload TEXT,
            status TEXT,attempts INTEGER,available_at REAL,lease_until REAL,error TEXT,score TEXT,completed_at REAL);
        CREATE TABLE batches (id TEXT PRIMARY KEY,org_id TEXT,recipient TEXT,subject_key TEXT,
            status TEXT,due_at REAL,lease_until REAL,error TEXT,slack_ts TEXT,attempts INTEGER);
        CREATE TABLE batch_events (batch_id TEXT,event_key INTEGER);
    """)
    analytics = Analytics(SimpleNamespace(db=db, _lock=threading.RLock()))
    before = db.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    db.execute("PRAGMA query_only=ON")
    assert analytics.overview()["avg_score"] is None
    assert analytics.overview()["fallback_count"] == 0
    assert analytics.events()["items"] == []
    assert analytics.deliveries()["total"] == 0
    assert analytics.event("missing") is None
    assert db.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == before
    db.close()


def test_policy_and_legacy_scores_are_not_measured_model_relevance():
    store = Store(":memory:")
    for i, score in enumerate((Score(score=1,rationale="fallback",summary="",fallback=True),Score(score=0,rationale="Source or route no longer authorized",summary=""),Score(score=.8,rationale="legacy",summary=""))):
        event=Event(id=str(i),org_id="org",source="slack",kind="message",subject_key=str(i))
        store.enqueue(event)
        store.complete(event.id,score,[],0)
    overview=Analytics(store).overview()
    assert overview["avg_score"] is None
    assert overview["model_scored_count"] == 0
    assert sum(overview["score_buckets"].values()) == 0
    assert overview["processed"] == 3
    store.close()
