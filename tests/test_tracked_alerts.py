from unittest.mock import AsyncMock
import pytest
from tests.test_accounts_cache import system as _system, PASSWORD, HEADERS
from triage.delivery import DeliveryError


@pytest.fixture
def system(tmp_path):
    yield from _system.__wrapped__(tmp_path)


def prepare(client):
    auth=('rob',PASSWORD)
    config=client.get('/api/dashboard/sources',auth=auth).json()
    r=client.put('/api/dashboard/sources',auth=auth,headers=HEADERS,json={'revision':config['revision'],'org_id':'alpha','source':'github','identity':'alpha/repo','rule':{'recipients':['U123ABC']}})
    assert r.status_code==200
    return {'org_id':'alpha','recipient':'U123ABC','request_id':'5cc14f34-d29f-4b9e-9289-510f33f0b4a8'}


def test_tracked_send_is_durable_and_idempotent(system,monkeypatch):
    client,store,_=system
    body=prepare(client)
    async def send(recipient):
        row=store.delivery_record('test:'+body['request_id'])
        assert row['status']=='sending' and row['attempts']==1
        return '1789388690.871589'
    mock=AsyncMock(side_effect=send)
    monkeypatch.setattr('triage.delivery.SlackDelivery.send_test',mock)
    for _ in range(2):
        r=client.post('/api/dashboard/test-alert',auth=('rob',PASSWORD),headers=HEADERS,json=body)
        assert r.status_code==200 and r.json()['status']=='sent'
    assert mock.call_count==1
    rows=client.get('/api/dashboard/deliveries',auth=('alice',PASSWORD)).json()['items']
    assert any(r['id'].startswith('test:') and r['slack_ts']=='1789388690.871589' for r in rows)
    assert not any(r['id'].startswith('test:') for r in client.get('/api/dashboard/deliveries',auth=('rob',PASSWORD)).json()['items'] if r['org_id']=='beta')
    assert store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]==2
    assert client.post('/api/dashboard/test-alert',auth=('alice',PASSWORD),headers=HEADERS,json=body).status_code==403


@pytest.mark.parametrize('uncertain,status',[(True,'unknown'),(False,'failed')])
def test_failed_and_uncertain_tests_are_not_retried(system,monkeypatch,uncertain,status):
    client,_,_=system
    body=prepare(client)
    mock=AsyncMock(side_effect=DeliveryError('provider error',uncertain=uncertain))
    monkeypatch.setattr('triage.delivery.SlackDelivery.send_test',mock)
    for _ in range(2):
        r=client.post('/api/dashboard/test-alert',auth=('rob',PASSWORD),headers=HEADERS,json=body)
        assert r.json()['status']==status
    assert mock.call_count==1


def test_import_confirmation_does_not_send_or_duplicate(system,monkeypatch):
    client,store,_=system
    body=prepare(client)
    mock=AsyncMock()
    monkeypatch.setattr('triage.delivery.SlackDelivery.send_test',mock)
    body.update(channel='D123ABC',slack_ts='1789388690.871589')
    for _ in range(2):
        r=client.post('/api/dashboard/test-alert/receipt',auth=('rob',PASSWORD),headers=HEADERS,json=body)
        assert r.status_code==200 and r.json()['status']=='sent'
        assert r.json()['due_at']==float(body['slack_ts'])
    assert store.db.execute("SELECT COUNT(*) FROM batches WHERE id LIKE 'test-receipt:%'").fetchone()[0]==1
    assert mock.call_count==0
    assert client.post('/api/dashboard/test-alert/receipt',auth=('rob',PASSWORD),json=body).status_code==403
