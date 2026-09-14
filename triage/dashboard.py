"""Read-only operator dashboard with environment-backed HTTP Basic authentication."""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import load_config

STATIC_DIR = Path(__file__).parent / "static" / "dashboard"
ASSETS = {"app.js": "text/javascript", "style.css": "text/css"}
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def install_dashboard(app, settings):
    """Install authenticated HTML, assets, and query-only analytics endpoints."""
    basic = HTTPBasic(auto_error=False)

    def authorize(credentials: HTTPBasicCredentials | None = Depends(basic)):
        if not settings.dashboard_password or not settings.dashboard_username:
            raise HTTPException(503, "Dashboard is not configured")
        username = credentials.username if credentials else ""
        password = credentials.password if credentials else ""
        user_ok = secrets.compare_digest(username.encode(), settings.dashboard_username.encode())
        password_ok = secrets.compare_digest(password.encode(), settings.dashboard_password.encode())
        if not (user_ok and password_ok):
            raise HTTPException(
                401, "Authentication required", headers={"WWW-Authenticate": 'Basic realm="sgnlol", charset="UTF-8"'}
            )

    router = APIRouter(dependencies=[Depends(authorize)])

    @app.middleware("http")
    async def dashboard_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/dashboard" or request.url.path.startswith(("/dashboard/", "/api/dashboard/")):
            response.headers.update(SECURITY_HEADERS)
        return response

    def analytics(request: Request):
        from .analytics import Analytics

        return Analytics(request.app.state.store)

    def query(operation, **kwargs):
        try:
            return operation(**kwargs)
        except sqlite3.Error:
            raise HTTPException(503, "Dashboard storage unavailable") from None

    @router.get("/dashboard", include_in_schema=False)
    @router.get("/dashboard/", include_in_schema=False)
    def dashboard():
        path = STATIC_DIR / "index.html"
        if not path.is_file():
            raise HTTPException(503, "Dashboard assets unavailable")
        return FileResponse(path, media_type="text/html")

    @router.get("/dashboard/assets/{filename}", include_in_schema=False)
    def asset(filename: str):
        if filename not in ASSETS or not (STATIC_DIR / filename).is_file():
            raise HTTPException(404, "Asset not found")
        return FileResponse(STATIC_DIR / filename, media_type=ASSETS[filename])

    @router.get("/api/dashboard/overview")
    def overview(request: Request):
        return query(analytics(request).overview)

    @router.get("/api/dashboard/events")
    def events(
        request: Request,
        source: Literal["github", "slack"] | None = None,
        status: Literal["queued", "processing", "completed", "filtered", "dead_letter"] | None = None,
        q: str | None = Query(default=None, max_length=200),
        min_score: float | None = Query(default=None, ge=0, le=1, allow_inf_nan=False),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
        sort: Literal["newest", "score"] = "newest",
    ):
        return query(
            analytics(request).events,
            source=source,
            status=status,
            q=q,
            min_score=min_score,
            limit=limit,
            offset=offset,
            sort=sort,
        )

    @router.get("/api/dashboard/events/{event_id:path}")
    def event(event_id: str, request: Request):
        if len(event_id) > 1024:
            raise HTTPException(422, "Event identifier too long")
        try:
            result = query(analytics(request).event, event_id=event_id)
        except ValueError:
            raise HTTPException(409, "Ambiguous event identifier") from None
        if result is None:
            raise HTTPException(404, "Event not found")
        return result

    @router.get("/api/dashboard/deliveries")
    def deliveries(
        request: Request, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100000)
    ):
        return query(analytics(request).deliveries, limit=limit, offset=offset)

    @router.get("/api/dashboard/routing")
    def routing():
        try:
            config = load_config(settings.config_path)
        except (OSError, ValueError):
            raise HTTPException(503, "Routing configuration unavailable") from None
        return {"orgs": [org.model_dump() for org in config.orgs]}

    app.include_router(router)
