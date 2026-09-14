"""Authenticated dashboard with role permissions and organization-scoped reads."""
from __future__ import annotations
import json
import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import load_config

STATIC_DIR = Path(__file__).parent / 'static' / 'dashboard'
ASSETS = {'app.js': 'text/javascript', 'style.css': 'text/css'}
SECURITY_HEADERS = {
    'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
    'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
}


class UserInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    username: str = Field(min_length=1, max_length=64)
    role: Literal['admin', 'analyst', 'viewer']
    orgs: list[str] = Field(default_factory=list, max_length=100)
    password: str | None = Field(default=None, max_length=1024)
    active: bool = True


def install_dashboard(app, settings):
    basic = HTTPBasic(auto_error=False)
    attempts = {}
    attempt_lock = Lock()

    def authorize(request: Request, credentials: HTTPBasicCredentials | None = Depends(basic)):
        accounts = request.app.state.accounts
        if not accounts.configured():
            raise HTTPException(503, 'Dashboard is not configured')
        username = credentials.username if credentials else ''
        password = credentials.password if credentials else ''
        # Limit repeated incorrect passwords; successful authenticated reads are not throttled.
        now = time.monotonic()
        with attempt_lock:
            count, until = attempts.get(username[:64], (0, now + 60))
            if until <= now:
                count, until = 0, now + 60
            if count >= 10:
                raise HTTPException(429, 'Too many sign-in attempts; retry in a minute', headers={'Retry-After': '60'})
        principal = accounts.authenticate(username, password) if credentials and len(username) <= 64 and len(password) <= 1024 else None
        if not principal:
            if credentials:
                with attempt_lock:
                    if len(attempts) >= 4000:
                        attempts.clear()
                    attempts[username[:64]] = (count + 1, until)
            raise HTTPException(401, 'Authentication required', headers={'WWW-Authenticate': 'Basic realm="sgnlol", charset="UTF-8"'})
        with attempt_lock:
            attempts.pop(username[:64], None)
        request.state.principal = principal
        return principal

    router = APIRouter(dependencies=[Depends(authorize)])

    def permission(name):
        def require(principal=Depends(authorize)):
            if not principal.can(name):
                raise HTTPException(403, 'Your role does not allow this action')
            return principal
        return require

    @app.middleware('http')
    async def dashboard_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path in ('/', '/dashboard') or request.url.path.startswith(('/dashboard/', '/api/dashboard/')):
            response.headers.update(SECURITY_HEADERS)
        return response

    @app.get('/', include_in_schema=False)
    def home():
        return RedirectResponse('/dashboard', status_code=303)

    def analytics(request):
        from .analytics import Analytics
        return Analytics(request.app.state.store, request.state.principal.scope)

    def read(request, operation, **kwargs):
        store = request.app.state.store
        with store._lock:
            version = (store.db.total_changes, store.db.execute('PRAGMA data_version').fetchone()[0])
        def load():
            try:
                result = operation(**kwargs)
            except sqlite3.Error:
                raise HTTPException(503, 'Dashboard storage unavailable') from None
            if not request.state.principal.can('deliveries:read'):
                result.pop('batches', None)
                result.pop('deliveries', None)
                for item in result.get('items', []):
                    item.pop('deliveries', None)
            return result
        resource = [request.url.path, sorted(request.query_params.multi_items())]
        return request.app.state.cache.read(request.state.principal, resource, version, load)

    @router.get('/dashboard', include_in_schema=False)
    @router.get('/dashboard/', include_in_schema=False)
    def dashboard():
        if not (STATIC_DIR / 'index.html').is_file():
            raise HTTPException(503, 'Dashboard assets unavailable')
        return FileResponse(STATIC_DIR / 'index.html', media_type='text/html')

    @router.get('/dashboard/assets/{filename}', include_in_schema=False)
    def asset(filename: str):
        if filename not in ASSETS or not (STATIC_DIR / filename).is_file():
            raise HTTPException(404, 'Asset not found')
        return FileResponse(STATIC_DIR / filename, media_type=ASSETS[filename])

    @router.get('/api/dashboard/me')
    def me(request: Request):
        return request.state.principal.public()

    @router.get('/api/dashboard/overview')
    def overview(request: Request):
        return read(request, analytics(request).overview)

    @router.get('/api/dashboard/events')
    def events(request: Request, source: Literal['github', 'slack'] | None = None,
               status: Literal['queued', 'processing', 'completed', 'filtered', 'dead_letter'] | None = None,
               q: str | None = Query(default=None, max_length=200),
               min_score: float | None = Query(default=None, ge=0, le=1, allow_inf_nan=False),
               limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100000),
               sort: Literal['newest', 'score'] = 'newest'):
        return read(request, analytics(request).events, source=source, status=status, q=q, min_score=min_score,
                    limit=limit, offset=offset, sort=sort)

    @router.get('/api/dashboard/events/{event_id:path}')
    def event(event_id: str, request: Request):
        if len(event_id) > 1024:
            raise HTTPException(422, 'Event identifier too long')
        def load():
            try:
                result = analytics(request).event(event_id)
            except ValueError:
                raise HTTPException(409, 'Ambiguous event identifier') from None
            if result is None:
                raise HTTPException(404, 'Event not found')
            return result
        return read(request, load)

    @router.get('/api/dashboard/deliveries', dependencies=[Depends(permission('deliveries:read'))])
    def deliveries(request: Request, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100000)):
        return read(request, analytics(request).deliveries, limit=limit, offset=offset)

    @router.get('/api/dashboard/routing', dependencies=[Depends(permission('routing:read'))])
    def routing(request: Request):
        try:
            config = load_config(settings.config_path)
        except (OSError, ValueError):
            raise HTTPException(503, 'Routing configuration unavailable') from None
        scope = request.state.principal.scope
        return {'orgs': [org.model_dump() for org in config.orgs if scope is None or org.id in scope]}

    @router.get('/api/dashboard/cache', dependencies=[Depends(permission('cache:read'))])
    def cache_status(request: Request):
        return request.app.state.cache.status()

    @router.get('/api/dashboard/users', dependencies=[Depends(permission('users:manage'))])
    def users(request: Request):
        return {'users': request.app.state.accounts.list()}

    async def save_user(request, create):
        # Basic credentials are browser-automatic; custom header and Origin checks prevent CSRF.
        if request.headers.get('x-sgnlol-request') != 'dashboard':
            raise HTTPException(403, 'Missing request verification header')
        origin = request.headers.get('origin')
        if origin and origin != f'{request.url.scheme}://{request.url.netloc}' and origin != f'https://{request.url.netloc}':
            raise HTTPException(403, 'Origin not allowed')
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise HTTPException(415, 'JSON body required')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                raise HTTPException(413, 'Request too large')
        try:
            value = UserInput.model_validate(json.loads(body))
            orgs = {o.id for o in load_config(settings.config_path).orgs}
            if not set(value.orgs) <= orgs:
                raise ValueError('Unknown organization assignment')
            return request.app.state.accounts.save(**value.model_dump(), create=create)
        except (ValidationError, json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(400, 'Invalid user configuration') from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post('/api/dashboard/users', dependencies=[Depends(permission('users:manage'))], status_code=201)
    async def add_user(request: Request):
        return await save_user(request, True)

    @router.put('/api/dashboard/users', dependencies=[Depends(permission('users:manage'))])
    async def update_user(request: Request):
        return await save_user(request, False)

    app.include_router(router)
