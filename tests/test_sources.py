import os
import pytest
from tests.test_accounts_cache import system as _system, PASSWORD, HEADERS
from triage.config import load_config
from triage.models import Event
from triage.routing import source_allowed, resolve_routes
from triage.delivery import _payload, DeliveryError
from triage.sources import Sources


@pytest.fixture
def system(tmp_path):
    yield from _system.__wrapped__(tmp_path)


def change(client, **kwargs):
    revision = client.get('/api/dashboard/sources',auth=('rob',PASSWORD)).json()['revision']
    return client.put('/api/dashboard/sources',auth=('rob',PASSWORD),headers=HEADERS,json={
        'revision':revision,'org_id':'alpha','source':'slack','identity':'C123ABC','rule':{},**kwargs})


def test_admin_only_and_csrf(system):
    client, _, _ = system
    for user in ['alice','bob']:
        assert client.get('/api/dashboard/sources',auth=(user,PASSWORD)).status_code==403
        assert client.put('/api/dashboard/sources',auth=(user,PASSWORD),headers=HEADERS,json={}).status_code==403
    assert client.put('/api/dashboard/sources',auth=('rob',PASSWORD),json={}).status_code==403
    assert client.put('/api/dashboard/sources',auth=('rob',PASSWORD),headers={**HEADERS,'Origin':'https://evil.example'},json={}).status_code==403


def test_persist_and_conflict_without_losing_other_org(system, tmp_path):
    client, _, app = system
    old=client.get('/api/dashboard/sources',auth=('rob',PASSWORD)).json()
    result=change(client,rule={'threshold':.9,'recipients':['C456ABC'],'mentions':['U123ABC','S123ABC']})
    assert result.status_code==200
    assert result.json()['orgs'][1]==old['orgs'][1]
    r=client.put('/api/dashboard/sources',auth=('rob',PASSWORD),headers=HEADERS,json={'revision':old['revision'],'org_id':'alpha','source':'slack','identity':'C999ABC','rule':{}})
    assert r.status_code==409
    # New editor process and base-file reprovisioning preserve the admin override.
    from pathlib import Path
    editor=Sources(tmp_path / 'orgs.yaml')
    editor.path.write_text('orgs: []\n')
    config=load_config(editor.path)
    assert 'C123ABC' in config.orgs[0].slack_channels
    assert Sources(editor.path).read()['revision']==result.json()['revision']
    assert os.stat(Path(str(editor.path)+'.admin.yaml')).st_mode & 0o777 == 0o600


def test_source_filters_destinations_and_revocation(system):
    client, _, _ = system
    result=change(client,rule={'threshold':.9,'recipients':['C456ABC'],'include_keywords':['security','blocker'],'exclude_keywords':['resolved']})
    assert result.status_code==200
    from triage.config import AppConfig
    config=AppConfig.model_validate({'orgs':result.json()['orgs']})
    event=Event(id='new',org_id='alpha',source='slack',kind='message',subject_key='x',text='SECURITY blocker',metadata={'channel':'C123ABC','team_id':'Talpha'})
    assert source_allowed(event,config)
    assert resolve_routes(event,config)[0].recipient=='C456ABC'
    assert resolve_routes(event,config)[0].threshold==.9
    event.text='security issue resolved'
    assert not source_allowed(event,config)
    event.text='routine status'
    assert not source_allowed(event,config)
    config.orgs[0].slack_rules['C123ABC'].enabled=False
    event.text='security blocker'
    assert resolve_routes(event,config)==[]


def test_repo_event_selection_and_invalid_edits(system):
    client,_,_=system
    result=change(client,source='github',identity='alpha/new',rule={'event_types':['issue_comment'],'recipients':['U123ABC']})
    assert result.status_code==200
    from triage.config import AppConfig
    config=AppConfig.model_validate({'orgs':result.json()['orgs']})
    e=Event(id='gh',org_id='alpha',source='github',kind='pull_request.opened',repo='alpha/new',subject_key='x')
    assert not source_allowed(e,config)
    e.kind='issue_comment.created'
    assert source_allowed(e,config)
    assert resolve_routes(e,config)[0].recipient=='U123ABC'
    before=client.get('/api/dashboard/sources',auth=('rob',PASSWORD)).json()['revision']
    for options in [{'identity':'#random'}, {'rule':{'mentions':['<!channel>']}}, {'rule':{'recipients':['S123ABC']}}, {'source':'github','identity':'other/repo'}, {'rule':{'threshold':1.2}}]:
        assert change(client,**options).status_code==400
    assert client.get('/api/dashboard/sources',auth=('rob',PASSWORD)).json()['revision']==before


def test_only_configured_mentions_render_as_mentions():
    batch={'id':'b','org_id':'alpha','recipient':'C123ABC','events':[{'url':''}],
           'scores':[{'score':.9,'summary':'<@U999ABC> injected','rationale':'<!channel>'}], 'mentions':['U123ABC','S123ABC']}
    payload=_payload(batch)
    mention_blocks=[b for b in payload['blocks'] if b.get('text',{}).get('type')=='mrkdwn']
    assert len(mention_blocks)==1
    assert mention_blocks[0]['text']['text']=='<@U123ABC> <!subteam^S123ABC>'
    assert '<@U999ABC>' not in payload['text']
    batch['mentions']=['<!channel>']
    with pytest.raises(DeliveryError):
        _payload(batch)


@pytest.mark.asyncio
async def test_worker_uses_saved_rules_for_scoring_and_mentions(system, tmp_path):
    from unittest.mock import AsyncMock
    from triage.config import Settings
    from triage.models import Score
    from triage.store import Store
    from triage.worker import Worker
    client, _, _ = system
    assert change(client, rule={'mentions':['S123ABC'],'recipients':['C456ABC'],'include_keywords':['blocker']}).status_code==200
    store=Store(':memory:')
    scorer, delivery=AsyncMock(), AsyncMock()
    scorer.score.return_value=Score(score=.9,summary='Action',rationale='Blocker')
    delivery.send.return_value='1.2'
    worker=Worker(store,Settings(config_path=str(tmp_path/'orgs.yaml'),batch_window_seconds=0),scorer,delivery)
    event=Event(id='pipeline',org_id='alpha',source='slack',kind='message',subject_key='topic',text='blocker',metadata={'team_id':'Talpha','channel':'C123ABC'})
    store.enqueue(event)
    await worker._score_one()
    batch=store.due_batches()[0]
    await worker._send_batch(batch)
    sent=delivery.send.call_args.args[0]
    assert sent['recipient']=='C456ABC' and sent['mentions']==['S123ABC']
    assert change(client, rule={'enabled':False}).status_code==200
    event.id='disabled'
    store.enqueue(event)
    await worker._score_one()
    assert scorer.score.call_count==1
    store.close()
