import asyncio
from unittest.mock import AsyncMock

import pytest
import yaml

from triage.config import Settings
from triage.delivery import DeliveryError
from triage.models import Event, Route, Score
from triage.store import Store
from triage.worker import Worker


@pytest.fixture
def worker(tmp_path):
    config = tmp_path / "orgs.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "orgs": [
                    {
                        "id": "a",
                        "github_org": "acme",
                        "slack_team_id": "T1",
                        "type": "startup",
                        "recipient": "U1",
                        "repos": {"acme/app": {}},
                        "slack_channels": [],
                    }
                ]
            }
        )
    )
    store = Store(":memory:")
    settings = Settings(config_path=str(config), batch_window_seconds=0, max_attempts=2)
    scorer = AsyncMock()
    scorer.score.return_value = Score(score=0.9, rationale="Relevant", summary="Review")
    delivery = AsyncMock()
    delivery.send.return_value = "123.45"
    instance = Worker(store, settings, scorer, delivery)
    yield instance
    store.close()


def event(id="github:a:1"):
    return Event(
        id=id, org_id="a", source="github", kind="pull_request", subject_key="pr1", repo="acme/app"
    )


def ready_batch(worker):
    worker.store.enqueue(event())
    worker.store.claim()
    worker.store.complete(
        event().id,
        Score(score=0.9, rationale="Relevant", summary="Review"),
        [Route(recipient="U1", threshold=0.7)],
        0,
    )


def ready_retries(worker):
    worker.store.db.execute("UPDATE events SET available_at=0")
    worker.store.db.execute("UPDATE batches SET due_at=0")
    worker.store.db.commit()


async def test_scores_and_delivers(worker):
    worker.store.enqueue(event())
    await worker.tick()
    assert worker.store.stats()["batches"] == {"sent": 1}
    worker.delivery.send.assert_awaited_once()


async def test_low_score_is_auditable(worker):
    worker.scorer.score.return_value = Score(score=0.1, rationale="Noise", summary="Routine")
    worker.store.enqueue(event())
    await worker.tick()
    assert worker.store.filtered()[0]["score"]["rationale"] == "Noise"
    worker.delivery.send.assert_not_awaited()


async def test_exhausted_scoring_forwards(worker):
    worker.scorer.score.side_effect = RuntimeError("secret must not leak")
    worker.store.enqueue(event())
    await worker.tick()
    assert worker.store.stats()["events"] == {"queued": 1}
    ready_retries(worker)
    await worker.tick()
    assert worker.store.stats()["batches"] == {"sent": 1}
    assert worker.delivery.send.call_args.args[0]["scores"][0]["fallback"] is True


async def test_exhausted_scoring_dead_letters(worker):
    worker.settings.fail_safe = "dead_letter"
    worker.settings.max_attempts = 1
    worker.scorer.score.side_effect = RuntimeError("secret")
    worker.store.enqueue(event())
    await worker.tick()
    assert worker.store.stats()["events"] == {"dead_letter": 1}
    worker.delivery.send.assert_not_awaited()


async def test_timeout_bounded(worker):
    async def slow(_):
        await asyncio.sleep(10)

    worker.scorer.score.side_effect = slow
    worker.settings.scoring_timeout_seconds = 0.001
    worker.store.enqueue(event())
    await asyncio.wait_for(worker.tick(), 0.5)
    assert worker.store.stats()["events"] == {"queued": 1}


async def test_batches_flush_when_scoring_fails(worker):
    ready_batch(worker)
    worker.store.enqueue(event("github:a:2"))
    worker.scorer.score.side_effect = RuntimeError("secret")
    await worker.tick()
    assert worker.store.stats()["batches"] == {"sent": 1}
    assert worker.store.stats()["events"]["queued"] == 1


async def test_unknown_write_never_retried(worker):
    ready_batch(worker)
    worker.delivery.send.side_effect = DeliveryError("secret", uncertain=True)
    await worker.tick()
    ready_retries(worker)
    await worker.tick()
    assert worker.store.stats()["batches"] == {"unknown": 1}
    worker.delivery.send.assert_awaited_once()


async def test_explicit_retry_bounded(worker):
    ready_batch(worker)
    worker.delivery.send.side_effect = DeliveryError("rate limited", retryable=True)
    await worker.tick()
    assert worker.store.stats()["batches"] == {"pending": 1}
    ready_retries(worker)
    await worker.tick()
    assert worker.store.stats()["batches"] == {"failed": 1}
    assert worker.delivery.send.await_count == 2


async def test_revoked_config_prevents_old_batch(worker):
    ready_batch(worker)
    from pathlib import Path

    Path(worker.settings.config_path).write_text("orgs: []")
    await worker.tick()
    worker.delivery.send.assert_not_awaited()
    assert worker.store.stats()["batches"] == {"failed": 1}


async def test_invalid_config_preserves_queue_and_run_survives(worker, caplog):
    from pathlib import Path

    ready_batch(worker)
    Path(worker.settings.config_path).write_text("bad: [secret")
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    await asyncio.sleep(0.01)
    stop.set()
    await task
    assert worker.store.stats()["batches"] == {"pending": 1}
    worker.delivery.send.assert_not_awaited()
    assert "secret" not in caplog.text
