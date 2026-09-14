import json
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from triage.accounts import Accounts, Principal, password_hash, verify_password
from triage.analytics import Analytics
from triage.app import create_app
from triage.cache import DashboardCache
from triage.config import Settings
from triage.models import Event, Route, Score
from triage.store import Store

PASSWORD = 'a-strong-test-password'
HEADERS = {'X-Sgnlol-Request': 'dashboard', 'Content-Type': 'application/json'}


class MemoryRedis:
    def __init__(self):
        self.values = {}
    def get(self, key):
        value = self.values.get(key)
        return value[0] if value and value[1] > time.monotonic() else None
    def setex(self, key, ttl, value):
        self.values[key] = (value, time.monotonic() + ttl)
    def close(self):
        pass


@pytest.fixture
def system(tmp_path):
    config = tmp_path / 'orgs.yaml'
    config.write_text(yaml.safe_dump({'orgs': [dict(id=o, github_org=o, slack_team_id='T'+o,
                     type='startup', recipient='U'+o, repos={o+'/repo': {}}) for o in ['alpha', 'beta']]}))
    store = Store(':memory:')
    for org, number in [('alpha', .8), ('beta', .9)]:
        e = Event(id='event-'+org, org_id=org, source='github', kind='comment', subject_key=org,
                  text=org+' secret event', repo=org+'/repo')
        store.enqueue(e)
        store.complete(e.id, Score(score=number, summary=org, rationale=org, model='test-model'), [Route(recipient='U'+org, threshold=.7)], 0)
    settings = Settings(config_path=str(config), dashboard_password=PASSWORD)
    app = create_app(settings=settings, store=store, run_worker=False)
    with TestClient(app) as client:
        accounts = app.state.accounts
        accounts.save('alice', 'analyst', ['alpha'], password=PASSWORD, create=True)
        accounts.save('bob', 'viewer', ['beta'], password=PASSWORD, create=True)
        app.state.cache = DashboardCache(client=MemoryRedis())
        yield client, store, app
    store.close()


def test_password_hash_and_bootstrap_never_override():
    encoded = password_hash(PASSWORD)
    assert PASSWORD not in encoded
    assert encoded != password_hash(PASSWORD)
    assert verify_password(PASSWORD, encoded)
    assert not verify_password('wrong', encoded)
    assert not verify_password(PASSWORD, 'bad')
    store = Store(':memory:')
    users = Accounts(store)
    users.bootstrap('rob', PASSWORD)
    with pytest.raises(ValueError, match='printable ASCII'):
        users.save('rob', 'admin', [], password='unicode-password-🔒')
    assert users.authenticate('rob', PASSWORD)
    users.save('rob', 'admin', [], password='replacement-password')
    users.bootstrap('rob', PASSWORD)
    assert users.authenticate('rob', PASSWORD) is None
    assert users.authenticate('rob', 'replacement-password')
    assert 'password_hash' not in json.dumps(users.list())
    store.close()


@pytest.mark.parametrize('cached', [False, True])
def test_organization_scope_in_all_reads_and_aggregates(system, cached):
    client, store, app = system
    if not cached:
        app.state.cache = DashboardCache()
    for path in ['/overview', '/events', '/deliveries', '/routing', '/events/event-alpha']:
        for _ in range(2):
            r = client.get('/api/dashboard'+path, auth=('alice', PASSWORD))
            assert r.status_code == 200
            assert 'beta' not in r.text
    assert client.get('/api/dashboard/events/event-beta', auth=('alice', PASSWORD)).status_code == 404
    assert client.get('/api/dashboard/events', auth=('alice', PASSWORD), params={'org_id': 'beta'}).json()['total'] == 1
    assert client.get('/api/dashboard/overview', auth=('alice', PASSWORD)).json()['avg_score'] == .8
    assert client.get('/api/dashboard/events', auth=('rob', PASSWORD)).json()['total'] == 2
    assert Analytics(store, []).events()['total'] == 0
    assert Analytics(store, []).overview()['total_events'] == 0
    assert Analytics(store, []).deliveries()['total'] == 0
    assert Analytics(store, []).event('event-alpha') is None


def test_viewer_cannot_read_delivery_details_routing_users_or_mutate(system):
    client, _, _ = system
    for path in ['/deliveries', '/routing', '/users', '/cache']:
        assert client.get('/api/dashboard'+path, auth=('bob', PASSWORD)).status_code == 403
    r = client.get('/api/dashboard/events', auth=('bob', PASSWORD)).json()
    assert r['items'][0]['org_id'] == 'beta'
    assert 'deliveries' not in r['items'][0]
    assert 'deliveries' not in client.get('/api/dashboard/events/event-beta', auth=('bob', PASSWORD)).json()
    assert 'batches' not in client.get('/api/dashboard/overview', auth=('bob', PASSWORD)).json()
    assert client.post('/api/dashboard/users', auth=('bob', PASSWORD), headers=HEADERS, json={}).status_code == 403


def test_cache_hits_invalidation_and_permission_revocation(system):
    client, store, app = system
    path = '/api/dashboard/events'
    for _ in range(2):
        assert client.get(path, auth=('alice', PASSWORD)).json()['total'] == 1
    assert app.state.cache.status()['hits'] == 1
    store.enqueue(Event(id='new-alpha', org_id='alpha', source='slack', kind='message', subject_key='new'))
    assert client.get(path, auth=('alice', PASSWORD)).json()['total'] == 2
    app.state.accounts.save('alice', 'analyst', ['beta'])
    r = client.get(path, auth=('alice', PASSWORD))
    assert 'alpha' not in r.text and r.json()['total'] == 1
    app.state.accounts.save('alice', 'analyst', ['beta'], active=False)
    assert client.get(path, auth=('alice', PASSWORD)).status_code == 401


def test_users_creation_update_csrf_and_last_admin(system):
    client, _, app = system
    client.auth = ('rob', PASSWORD)
    body = {'username': 'new', 'password': PASSWORD, 'role': 'viewer', 'orgs': ['alpha']}
    assert client.post('/api/dashboard/users', json=body).status_code == 403
    assert client.post('/api/dashboard/users', headers={**HEADERS, 'Origin': 'https://evil.example'}, json=body).status_code == 403
    r = client.post('/api/dashboard/users', headers=HEADERS, json=body)
    assert r.status_code == 201 and PASSWORD not in r.text
    assert client.post('/api/dashboard/users', headers=HEADERS, json=body).status_code == 400
    assert client.put('/api/dashboard/users', headers=HEADERS, json={'username':'rob','role':'viewer','orgs':['alpha']}).status_code == 400
    assert client.put('/api/dashboard/users', headers=HEADERS, json={'username':'rob','role':'admin','orgs':[],'active':False}).status_code == 400
    assert client.post('/api/dashboard/users', headers=HEADERS, json={**body, 'username':'x','orgs':['unknown']}).status_code == 400
    assert client.post('/api/dashboard/users', headers=HEADERS, json={**body, 'username':'x','password':'short'}).status_code == 400
    assert client.put('/api/dashboard/users', headers=HEADERS, json={**body,'password':'new-strong-password','active':False}).status_code == 200
    assert app.state.accounts.authenticate('new','new-strong-password') is None


def test_redis_failure_does_not_block_database_or_leak_errors(system):
    client, _, app = system
    class Broken:
        def get(self, key):
            raise RuntimeError('redis://secret-credentials')
        def close(self):
            pass
    app.state.cache = DashboardCache(client=Broken())
    r = client.get('/api/dashboard/events', auth=('alice', PASSWORD))
    assert r.status_code == 200 and 'beta' not in r.text and 'credentials' not in r.text
    assert app.state.cache.status()['state'] == 'degraded'
    assert client.get('/api/dashboard/events', auth=('alice', PASSWORD)).status_code == 200
    assert app.state.cache.status()['errors'] == 1


def test_cache_ttl_scope_and_restart_namespace():
    backend = MemoryRedis()
    cache = DashboardCache(client=backend, ttl=1)
    alice, bob = Principal('alice','analyst',('a',)), Principal('bob','viewer',('b',))
    assert cache.read(alice,'events',0,lambda:{'org':'a'}) == {'org':'a'}
    assert cache.read(bob,'events',0,lambda:{'org':'b'}) == {'org':'b'}
    for key, (value, _) in list(backend.values.items()):
        backend.values[key] = (value, 0)
    assert cache.read(alice,'events',0,lambda:{'org':'new'}) == {'org':'new'}
    assert DashboardCache(client=backend).namespace != cache.namespace


def test_failed_signins_are_throttled_and_health_still_works(system):
    client, _, _ = system
    for _ in range(10):
        assert client.get('/api/dashboard/me', auth=('alice','wrong')).status_code == 401
    assert client.get('/api/dashboard/me', auth=('alice','wrong')).status_code == 429
    assert client.get('/healthz').status_code == 200
    assert client.get('/').url.path == '/dashboard/login'


def test_browser_session_login_logout_and_revocation(system):
    client, store, app = system
    assert client.get('/dashboard').url.path == '/dashboard/login'
    assert client.get('/dashboard/login').status_code == 200
    assert client.get('/dashboard/login/login.js').status_code == 200
    bad = client.post('/api/dashboard/login', headers=HEADERS, json={'username':'alice','password':'wrong'})
    assert bad.status_code == 401 and 'www-authenticate' not in bad.headers
    assert client.post('/api/dashboard/login', json={'username':'alice','password':PASSWORD}).status_code == 403
    assert client.post('/api/dashboard/login', headers={**HEADERS,'Origin':'https://evil.example'}, json={'username':'alice','password':PASSWORD}).status_code == 403
    response = client.post('/api/dashboard/login', headers=HEADERS, json={'username':'alice','password':PASSWORD})
    assert response.status_code == 200
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    token = client.cookies.get('sgnlol_session')
    assert token not in str([tuple(r) for r in store.db.execute('SELECT * FROM dashboard_sessions')])
    assert client.get('/dashboard').url.path == '/dashboard'
    assert client.get('/api/dashboard/me').json()['username'] == 'alice'
    assert client.get('/api/dashboard/events/event-beta').status_code == 404
    assert client.get('/api/dashboard/users').status_code == 403
    assert client.post('/api/dashboard/logout', headers=HEADERS, json={}).status_code == 200
    assert app.state.accounts.session(token) is None
    assert client.get('/api/dashboard/me').status_code == 401
    client.post('/api/dashboard/login', headers=HEADERS, json={'username':'alice','password':PASSWORD})
    app.state.accounts.save('alice', 'viewer', ['alpha'])
    assert client.get('/api/dashboard/me').status_code == 401
    client.post('/api/dashboard/login', headers=HEADERS, json={'username':'alice','password':PASSWORD})
    store.db.execute('UPDATE dashboard_sessions SET expires_at=0')
    store.db.commit()
    assert client.get('/api/dashboard/me').status_code == 401


def test_session_cookie_secure_in_production(system):
    client, _, _ = system
    response = client.post('https://sgn.lol/api/dashboard/login', headers=HEADERS, json={'username':'alice','password':PASSWORD})
    assert response.status_code == 200
    assert 'Secure' in response.headers['set-cookie']
