"""Optional short-lived Redis read cache; never an authorization or queue authority."""
from __future__ import annotations
import hashlib
import json
import time
import uuid
from threading import Lock


class DashboardCache:
    def __init__(self, url='', ttl=5, client=None):
        self.client = client
        self.ttl = ttl
        self.namespace = 'sgnlol:dashboard:v1:' + uuid.uuid4().hex
        self.counts = {'hits': 0, 'misses': 0, 'errors': 0}
        self.lock = Lock()
        self.retry_at = 0
        if url and client is None:
            from redis import Redis
            from redis.retry import Retry
            from redis.backoff import NoBackoff
            self.client = Redis.from_url(url, socket_connect_timeout=.25, socket_timeout=.25,
                                         decode_responses=True, retry_on_timeout=False, retry=Retry(NoBackoff(), 0), max_connections=16)

    def read(self, principal, resource, version, loader):
        if self.client is None or time.monotonic() < self.retry_at:
            return loader()
        key = self.namespace + ':' + hashlib.sha256(json.dumps(
            [principal.public(), resource, version], sort_keys=True).encode()).hexdigest()
        try:
            value = self.client.get(key)
            if value is not None:
                result = json.loads(value)
                with self.lock:
                    self.counts['hits'] += 1
                return result
            with self.lock:
                self.counts['misses'] += 1
        except Exception:
            self._failed()
            return loader()
        result = loader()
        try:
            raw = json.dumps(result)
            if len(raw.encode()) <= 512 * 1024:
                self.client.setex(key, self.ttl, raw)
        except Exception:
            self._failed()
        return result

    def _failed(self):
        with self.lock:
            self.counts['errors'] += 1
            self.retry_at = time.monotonic() + 5

    def status(self):
        with self.lock:
            return {'configured': self.client is not None, 'ttl_seconds': self.ttl,
                    'state': 'disabled' if self.client is None else 'degraded' if time.monotonic() < self.retry_at else 'ready',
                    **self.counts}

    def close(self):
        if self.client is not None:
            self.client.close()
