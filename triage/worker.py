"""Single-worker queue processing with bounded retries and conservative delivery."""

from __future__ import annotations

import asyncio
import logging

from triage.config import load_config
from triage.delivery import DeliveryError
from triage.models import Event, Score
from triage.routing import resolve_routes

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, store, settings, scorer, delivery):
        self.store = store
        self.settings = settings
        self.scorer = scorer
        self.delivery = delivery

    @staticmethod
    def _backoff(attempt):
        return min(60, 2 ** min(max(attempt - 1, 0), 6))

    async def _score_one(self):
        # A broken config must not consume an attempt or authorize fallback sends.
        config = load_config(self.settings.config_path)
        event = self.store.claim()
        if event is None:
            return
        if not resolve_routes(event, config):
            self.store.complete(
                event.id,
                Score(score=0, rationale="Source or route no longer authorized", summary=""),
                [],
                0,
            )
            return
        try:
            if self.store.attempt_count(event.id) > self.settings.max_attempts:
                raise RuntimeError("Scoring attempt limit exhausted")
            score = await asyncio.wait_for(
                self.scorer.score(event),
                timeout=min(self.settings.scoring_timeout_seconds, 90),
            )
            score = Score.model_validate(score)
        except Exception as error:
            diagnostic = ("OpenAI API credits exhausted" if getattr(error, "code", None) in
                          {"credit_balance_exhausted", "insufficient_quota"} else "Scoring failed")
            attempt = self.store.attempt_count(event.id)
            if attempt < self.settings.max_attempts:
                self.store.retry(event.id, diagnostic, self._backoff(attempt))
                return
            if self.settings.fail_safe == "dead_letter":
                self.store.dead_letter(event.id, diagnostic + "; scoring attempts exhausted")
                return
            score = Score(
                score=1,
                rationale=diagnostic + "; unavailable after bounded retries; forwarding for review",
                summary="Scoring unavailable. Review the original event.",
                fallback=True,
            )
        # Configuration may have changed during the model request.
        try:
            config = load_config(self.settings.config_path)
        except Exception:
            self.store.retry(event.id, "Routing configuration unavailable", 5)
            return
        routes = [
            route for route in resolve_routes(event, config) if score.score >= route.threshold
        ]
        self.store.complete(event.id, score, routes, self.settings.batch_window_seconds)

    async def _send_batch(self, batch):
        try:
            config = load_config(self.settings.config_path)
        except Exception:
            self.store.retry_batch(batch["id"], "Routing configuration unavailable", 5)
            return
        # Revoke stale destinations and removed sources even after they were scored.
        pairs = []
        for raw_event, raw_score in zip(batch["events"], batch["scores"], strict=True):
            event, score = Event.model_validate(raw_event), Score.model_validate(raw_score)
            if event.org_id != batch["org_id"]:
                continue
            if any(
                route.recipient == batch["recipient"] and score.score >= route.threshold
                for route in resolve_routes(event, config)
            ):
                pairs.append((raw_event, raw_score))
        if not pairs:
            self.store.fail_batch(batch["id"], "Current configuration revoked delivery")
            return
        from .routing import source_rule
        mentions = sorted({mention for raw, _ in pairs for mention in source_rule(Event.model_validate(raw), config).mentions})
        outgoing = dict(batch, mentions=mentions, events=[p[0] for p in pairs], scores=[p[1] for p in pairs])
        try:
            ts = await asyncio.wait_for(self.delivery.send(outgoing), timeout=30)
        except DeliveryError as error:
            if error.uncertain:
                self.store.unknown_batch(
                    batch["id"], "Delivery outcome uncertain; manual reconciliation required"
                )
            elif (
                error.retryable
                and self.store.batch_attempt_count(batch["id"]) < self.settings.max_attempts
            ):
                delay = max(
                    self._backoff(self.store.batch_attempt_count(batch["id"])), error.retry_after
                )
                self.store.retry_batch(batch["id"], "Explicit retryable Slack rejection", delay)
            else:
                self.store.fail_batch(batch["id"], "Slack rejected delivery or retry limit reached" + (f" ({error.code})" if error.code else ""))
        except Exception:
            # Unexpected failures may occur after Slack accepted a write.
            self.store.unknown_batch(
                batch["id"], "Delivery outcome uncertain; manual reconciliation required"
            )
        else:
            self.store.mark_sent(batch["id"], ts)

    async def _flush(self):
        # Validate before claiming, so invalid configuration does not consume attempts.
        load_config(self.settings.config_path)
        batches = self.store.due_batches()
        results = await asyncio.gather(
            *(self._send_batch(batch) for batch in batches), return_exceptions=True
        )
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Batch processing failed; durable state retained")

    async def tick(self):
        try:
            await self._flush()
            await self._score_one()
        finally:
            # Ready deliveries continue even when a model or queue operation fails.
            await self._flush()

    async def run(self, stop: asyncio.Event):
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:
                # Never log model, config, event content, or exception messages.
                logger.warning("Worker iteration failed; will retry")
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.25)
            except TimeoutError:
                pass
