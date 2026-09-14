import time
import pytest
from tests.test_accounts_cache import system as _system, PASSWORD
from triage.analytics import Analytics


@pytest.fixture
def system(tmp_path):
    yield from _system.__wrapped__(tmp_path)


def test_delivery_filters_preserve_scope_and_pagination(system):
    client,store,_=system
    store.begin_test_delivery('test:recent','alpha','Ualpha','rob')
    store.mark_sent('test:recent','1789388690.871589')
    store.import_test_receipt('test-receipt:old','alpha','Ualpha','Dalpha',str(time.time()-2*86400),'rob')
    store.begin_test_delivery('test:beta','beta','Ubeta','rob')
    store.fail_batch('test:beta','Test failure')
    auth=('alice',PASSWORD)
    data=client.get('/api/dashboard/deliveries?kind=test&status=sent&days=1',auth=auth).json()
    assert data['total']==1 and data['items'][0]['id']=='test:recent'
    assert client.get('/api/dashboard/deliveries?kind=imported&days=1',auth=auth).json()['total']==0
    assert client.get('/api/dashboard/deliveries?kind=imported',auth=auth).json()['total']==1
    assert client.get('/api/dashboard/deliveries?kind=scored',auth=auth).json()['total']==1
    assert client.get('/api/dashboard/deliveries?kind=test&status=failed',auth=auth).json()['total']==0
    assert client.get('/api/dashboard/deliveries?kind=test&offset=1',auth=auth).json()['items']==[]
    for query in ['kind=invalid','status=invalid','days=0','days=366']:
        assert client.get('/api/dashboard/deliveries?'+query,auth=auth).status_code==422


def test_source_activity_reports_observed_events_only(system):
    client,store,_=system
    rows=Analytics(store,['alpha']).source_activity()
    assert len(rows)==1
    assert rows[0]['org_id']=='alpha' and rows[0]['identity']=='alpha/repo'
    assert rows[0]['event_count']==1 and rows[0]['last_received']>0
    assert Analytics(store,[]).source_activity()==[]
    response=client.get('/api/dashboard/sources',auth=('rob',PASSWORD))
    assert response.status_code==200 and len(response.json()['activity'])==2
    assert client.get('/api/dashboard/sources',auth=('alice',PASSWORD)).status_code==403
