"""Small local accounts with hashed passwords and server-derived organization scopes."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass

PERMISSIONS = {
    'viewer': frozenset({'events:read'}),
    'analyst': frozenset({'events:read', 'deliveries:read', 'routing:read'}),
    'admin': frozenset({'events:read', 'deliveries:read', 'routing:read', 'users:manage', 'cache:read', 'sources:manage'}),
}


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return 'scrypt$' + salt.hex() + '$' + digest.hex()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, expected = encoded.split('$')
        if algorithm != 'scrypt' or len(password) > 1024:
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


DUMMY_HASH = password_hash('not-a-real-account-password')


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    orgs: tuple[str, ...]

    def can(self, permission: str) -> bool:
        return permission in PERMISSIONS.get(self.role, ())

    @property
    def scope(self):
        return None if self.role == 'admin' else self.orgs

    def public(self):
        return {'username': self.username, 'role': self.role, 'orgs': list(self.orgs),
                'permissions': sorted(PERMISSIONS[self.role])}


class Accounts:
    def __init__(self, store):
        self.store = store
        with store._lock:
            store.db.execute('''CREATE TABLE IF NOT EXISTS dashboard_users (
                username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL,
                orgs TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL)''')
            store.db.execute('''CREATE TABLE IF NOT EXISTS dashboard_sessions (
                token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at REAL NOT NULL)''')
            store.db.commit()

    def new_session(self, username):
        token = secrets.token_urlsafe(32)
        with self.store._lock:
            self.store.db.execute('DELETE FROM dashboard_sessions WHERE expires_at <= ? OR username = ?', (time.time(), username))
            self.store.db.execute('INSERT INTO dashboard_sessions VALUES (?,?,?)',
                                  (hashlib.sha256(token.encode()).hexdigest(), username, time.time() + 43200))
            self.store.db.commit()
        return token

    def session(self, token):
        if not token or len(token) > 128:
            return None
        with self.store._lock:
            row = self.store.db.execute('''SELECT u.* FROM dashboard_sessions s
                JOIN dashboard_users u ON u.username=s.username
                WHERE s.token_hash=? AND s.expires_at>? AND u.active=1''',
                (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return Principal(row['username'], row['role'], tuple(json.loads(row['orgs']))) if row else None

    def end_session(self, token):
        with self.store._lock:
            self.store.db.execute('DELETE FROM dashboard_sessions WHERE token_hash=?',
                                  (hashlib.sha256(token.encode()).hexdigest(),))
            self.store.db.commit()

    def bootstrap(self, username, password):
        # Environment credentials seed the first administrator only. They can never
        # resurrect an account or override a password/role changed by an operator.
        if not username or not password:
            return
        with self.store._lock:
            if self.store.db.execute('SELECT 1 FROM dashboard_users LIMIT 1').fetchone():
                return
            self.store.db.execute('INSERT INTO dashboard_users VALUES (?,?,?,?,1,?)',
                                  (username, password_hash(password), 'admin', '[]', time.time()))
            self.store.db.commit()

    def authenticate(self, username, password):
        with self.store._lock:
            row = self.store.db.execute('SELECT * FROM dashboard_users WHERE username=?', (username,)).fetchone()
        valid = verify_password(password, row['password_hash'] if row else DUMMY_HASH)
        if not valid or not row or not row['active']:
            return None
        return Principal(row['username'], row['role'], tuple(json.loads(row['orgs'])))

    def configured(self):
        with self.store._lock:
            return bool(self.store.db.execute('SELECT 1 FROM dashboard_users LIMIT 1').fetchone())

    def list(self):
        with self.store._lock:
            return [{'username': r['username'], 'role': r['role'], 'orgs': json.loads(r['orgs']),
                     'active': bool(r['active'])} for r in self.store.db.execute(
                         'SELECT username,role,orgs,active FROM dashboard_users ORDER BY username')]

    def save(self, username, role, orgs, *, password=None, active=True, create=False):
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}', username) or role not in PERMISSIONS:
            raise ValueError('Invalid username or role')
        if not isinstance(orgs, list) or len(orgs) > 100 or any(not isinstance(o, str) or not o or len(o) > 200 for o in orgs):
            raise ValueError('Invalid organization assignments')
        if role != 'admin' and not orgs:
            raise ValueError('Non-admin users require organization assignments')
        if password is not None and (not 12 <= len(password) <= 1024 or not password.isascii() or not password.isprintable()):
            raise ValueError('Passwords must contain 12 to 1024 printable ASCII characters')
        hashed = password_hash(password) if password is not None else None
        with self.store._lock:
            old = self.store.db.execute('SELECT * FROM dashboard_users WHERE username=?', (username,)).fetchone()
            if create and old:
                raise ValueError('Username already exists')
            if not create and not old:
                raise ValueError('Unknown user')
            if not old and not hashed:
                raise ValueError('New users require a password')
            if old and old['active'] and old['role'] == 'admin' and (not active or role != 'admin'):
                admins = self.store.db.execute("SELECT COUNT(*) FROM dashboard_users WHERE active=1 AND role='admin'").fetchone()[0]
                if admins <= 1:
                    raise ValueError('Cannot disable or demote the last active administrator')
            self.store.db.execute('''INSERT INTO dashboard_users VALUES (?,?,?,?,?,?)
                ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash,
                role=excluded.role, orgs=excluded.orgs, active=excluded.active, updated_at=excluded.updated_at''',
                (username, hashed or old['password_hash'], role, json.dumps(sorted(set(orgs))), int(active), time.time()))
            self.store.db.execute('DELETE FROM dashboard_sessions WHERE username=?', (username,))
            self.store.db.commit()
        return {'username': username, 'role': role, 'orgs': sorted(set(orgs)), 'active': active}
