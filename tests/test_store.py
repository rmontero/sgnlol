import sqlite3
import time

import pytest

from triage.models import Event, Route, Score
from triage.store import Store


def event(id="github:org:one", org="org", subject="https://github.com/acme/repo/pull/1"):
    return Event(id=id, org_id=org, source="github", kind="pull_request", subject_key=subject)


def score(value=0.9):
    return Score(
        score=value,
        summary="Review required",
        rationale="Blocking review",
        input_tokens=12,
        output_tokens=8,
    )


def test_durable_dedup_claim_and_lease_recovery(tmp_path):
    path = str(tmp_path / "queue.db")
    store = Store(path)
    assert store.enqueue(event())
    assert not store.enqueue(event())
    now = time.time() + 1
    assert store.claim(now).id == event().id
    assert store.claim(now) is None
    store.close()
    store = Store(path)
    assert store.claim(now) is None
    assert store.claim(now + store.LEASE_SECONDS + 1).id == event().id
    assert store.attempt_count(event().id) == 2
    store.close()


def test_batching_separates_tenants_recipients_and_subjects():
    store = Store(":memory:")
    for id, org, subject in [
        ("a", "org", "pr1"),
        ("b", "org", "pr1"),
        ("c", "other", "pr1"),
        ("d", "org", "pr2"),
    ]:
        e = event(id, org, subject)
        store.enqueue(e)
        store.complete(id, score(), [Route(recipient="C1", threshold=0.7)], 10)
    batches = store.due_batches(time.time() + 11)
    assert len(batches) == 3
    assert sorted(len(b["events"]) for b in batches) == [1, 1, 2]
    assert all(all(e["org_id"] == b["org_id"] for e in b["events"]) for b in batches)
    assert store.due_batches(time.time() + 12) == []


def test_complete_is_atomic_and_idempotent():
    store = Store(":memory:")
    store.enqueue(event())
    # Force an outbox constraint failure and verify the scored event rolls back too.
    store.db.execute(
        "CREATE TRIGGER reject_batch BEFORE INSERT ON batches BEGIN SELECT RAISE(ABORT, 'test'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.complete(event().id, score(), [Route(recipient="C1", threshold=0.7)], 0)
    assert store.stats()["processed"] == 0
    store.db.execute("DROP TRIGGER reject_batch")
    store.complete(event().id, score(), [Route(recipient="C1", threshold=0.7)], 0)
    store.complete(event().id, score(), [Route(recipient="C1", threshold=0.7)], 0)
    assert len(store.due_batches(time.time() + 1)) == 1


def test_uncertain_delivery_never_replays(tmp_path):
    path = str(tmp_path / "queue.db")
    store = Store(path)
    store.enqueue(event())
    store.complete(event().id, score(), [Route(recipient="C1", threshold=0.7)], 0)
    batch = store.due_batches()[0]
    store.close()
    store = Store(path)
    assert store.due_batches(time.time() + 121) == []
    assert store.stats()["batches"] == {"unknown": 1}
    store.retry_batch(batch["id"], "must not replay", 0)
    assert store.due_batches(time.time() + 200) == []


def test_explicit_delivery_failure_can_retry():
    store = Store(":memory:")
    store.enqueue(event())
    store.complete(event().id, score(), [Route(recipient="C1", threshold=0.7)], 0)
    batch = store.due_batches()[0]
    store.retry_batch(batch["id"], "rate limited", 30)
    assert store.due_batches() == []
    assert store.due_batches(time.time() + 31)[0]["id"] == batch["id"]
    store.mark_sent(batch["id"], "123.456")
    assert store.due_batches(time.time() + 1000) == []
    assert store.stats()["batches"] == {"sent": 1}


def test_filtered_log_and_usage():
    store = Store(":memory:")
    store.enqueue(event())
    store.complete(event().id, score(0.1), [Route(recipient="C1", threshold=0.7)], 0)
    assert store.due_batches() == []
    assert store.filtered()[0]["score"]["rationale"] == "Blocking review"
    assert store.stats()["filter_rate"] == 1
    assert store.stats()["input_tokens"] == 12


def test_retry_and_dead_letter():
    store = Store(":memory:")
    store.enqueue(event())
    store.claim()
    store.retry(event().id, "scoring unavailable", 30)
    assert store.claim() is None
    assert store.claim(time.time() + 31)
    store.dead_letter(event().id, "attempt limit")
    assert store.claim(time.time() + 1000) is None
    assert store.stats()["events"] == {"dead_letter": 1}


def test_duplicate_external_ids_are_tenant_isolated():
    store = Store(":memory:")
    assert store.enqueue(event("same", "org1"))
    assert store.enqueue(event("same", "org2"))
    with pytest.raises(ValueError, match="ambiguous"):
        store.complete("same", score(), [Route(recipient="C1", threshold=0.7)], 0)
    assert store.stats()["processed"] == 0


def test_independent_connections_do_not_claim_twice(tmp_path):
    path = str(tmp_path / "queue.db")
    first, second = Store(path), Store(path)
    first.enqueue(event())
    assert first.claim()
    assert second.claim() is None
