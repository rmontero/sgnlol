"""Signature-verified ingress; all external API work runs after durable ack."""

import asyncio
import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError
from standardwebhooks import WebhookVerificationError

from .config import Settings, load_config
from .dashboard import install_dashboard
from .ingest import normalize_github, normalize_slack, verify_github, verify_slack
from .openai_webhook import OpenAIEvent, verify_openai
from .store import Store

MAX_BODY = 1024 * 1024


def create_app(settings=None, store=None, scorer=None, delivery=None, run_worker=True):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        from .delivery import SlackDelivery
        from .scoring import Scorer
        from .worker import Worker

        # Validate routing before accepting traffic. No network calls at startup.
        load_config(settings.config_path)
        app.state.store = store or Store(settings.database_path)
        sender = delivery or SlackDelivery(settings)
        stop = asyncio.Event()
        classifier = scorer or Scorer(settings)
        worker = Worker(app.state.store, settings, classifier, sender)
        task = asyncio.create_task(worker.run(stop)) if run_worker else None
        try:
            yield
        finally:
            stop.set()
            if task:
                try:
                    await asyncio.wait_for(task, timeout=5)
                except asyncio.TimeoutError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            if scorer is None:
                await classifier.close()
            if delivery is None:
                await sender.close()
            if store is None:
                app.state.store.close()

    app = FastAPI(title="sgnlol relevance triage", lifespan=lifespan)

    async def body_bytes(request):
        chunks = []
        length = 0
        async for chunk in request.stream():
            length += len(chunk)
            if length > MAX_BODY:
                raise HTTPException(413, "Payload too large")
            chunks.append(chunk)
        return b"".join(chunks)

    def parse(body):
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Invalid JSON") from None
        if not isinstance(payload, dict):
            raise HTTPException(400, "Expected JSON object")
        return payload

    def config():
        try:
            return load_config(settings.config_path)
        except (ValueError, OSError):
            raise HTTPException(503, "Routing configuration unavailable") from None

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.post("/webhooks/openai")
    async def openai_webhook(request: Request):
        body = await body_bytes(request)
        if not settings.openai_webhook_secret:
            raise HTTPException(503, "OpenAI webhook signing secret is not configured")
        try:
            verify_openai(body, request.headers, settings.openai_webhook_secret)
        except (WebhookVerificationError, ValueError):
            raise HTTPException(401, "Invalid signature") from None
        payload = parse(body)
        try:
            OpenAIEvent.model_validate(payload)
        except ValidationError:
            raise HTTPException(400, "Invalid OpenAI event envelope") from None
        webhook_id = request.headers.get("webhook-id", "")
        if not webhook_id or len(webhook_id) > 256:
            raise HTTPException(400, "Invalid webhook ID")
        try:
            inserted = app.state.store.record_openai_event(payload, webhook_id)
        except sqlite3.Error:
            raise HTTPException(503, "Webhook storage unavailable") from None
        return {"received": True, "duplicate": not inserted}

    @app.post("/webhooks/github")
    async def github(request: Request):
        body = await body_bytes(request)
        if not verify_github(body, request.headers.get("x-hub-signature-256", ""), settings.github_webhook_secret):
            raise HTTPException(401, "Invalid signature")
        payload = parse(body)
        delivery_id = request.headers.get("x-github-delivery", "")
        if not delivery_id or len(delivery_id) > 256:
            raise HTTPException(400, "Missing or invalid delivery ID")
        event = normalize_github(payload, request.headers.get("x-github-event", ""), delivery_id, config())
        if event:
            event.metadata["webhook_body_sha256"] = hashlib.sha256(body).hexdigest()
        queued = app.state.store.enqueue(event) if event else False
        return {"accepted": True, "queued": queued}

    @app.post("/webhooks/slack")
    async def slack(request: Request):
        body = await body_bytes(request)
        if not verify_slack(
            body,
            request.headers.get("x-slack-request-timestamp", ""),
            request.headers.get("x-slack-signature", ""),
            settings.slack_signing_secret,
        ):
            raise HTTPException(401, "Invalid signature")
        payload = parse(body)
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str):
                raise HTTPException(400, "Invalid challenge")
            return {"challenge": challenge}
        event = normalize_slack(payload, config(), settings.slack_bot_user_id)
        queued = app.state.store.enqueue(event) if event else False
        return {"accepted": True, "queued": queued}

    install_dashboard(app, settings)
    return app


app = create_app()
