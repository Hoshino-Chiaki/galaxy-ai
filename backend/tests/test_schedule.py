import asyncio
import copy
import json
import time
import uuid
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import httpx
import pytest
from fastapi.testclient import TestClient
from xingxu.auth import AuthFailure,AuthService
from xingxu.store import JsonRepository
from xingxu.schedule import ScheduleService
from xingxu.schedule.rules import normalize,occurrences


@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG',raising=False)
    monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE','files-v1')
    auth=AuthService(JsonRepository(tmp_path));auth.import_accounts([('owner','fixture-password'),('other','fixture-password')])
    users=[auth.login(n,'fixture-password',False,'test')['user'] for n in ['owner','other']]
    now=[datetime(2026,11,23,0,0,tzinfo=timezone.utc).timestamp()]
    ai=SimpleNamespace(calls=[])
    async def send(user,body,**kwargs):
        ai.calls.append((user,body,kwargs));return {'conversation':{'messages':[{'tool_events':[{'status':'success'}]}]}}
    ai.send=send
    service=ScheduleService(tmp_path,auth,ai,clock=lambda:now[0])
    return service,auth,users,now,ai


def body(**kwargs):
    return {'name':'测试日报','instruction':'查询明确指定店铺的昨天数据，生成结果。','time_description':'每天九点',
        'rule':{'type':'cron','expression':'0 9 * * *'},'request_key':str(uuid.uuid4()),**kwargs}


def test_rule_calendar_interval_once_and_invalid_dates():
    now=datetime(2026,11,23,0,0,tzinfo=timezone.utc).timestamp()
    r=normalize({'type':'cron','expression':'0 9 * * 1-5'},now)
    assert [datetime.fromtimestamp(t,timezone.utc).isoformat() for t in occurrences(r,now)]==[
        '2026-11-23T01:00:00+00:00','2026-11-24T01:00:00+00:00','2026-11-25T01:00:00+00:00']
    r=normalize({'type':'interval','seconds':90},now);assert occurrences(r,now)==[now+90,now+180,now+270]
    r=normalize({'type':'once','at':'2026-11-23T09:00:00+08:00'},now)
    assert occurrences(r,now)==[now+3600] and not occurrences(r,now+3600)
    for invalid in [{'type':'once','at':'2026-11-22T09:00:00+08:00'},{'type':'once','at':'2026-11-24T09:00:00'},
        {'type':'interval','seconds':True},{'type':'cron','expression':'0 0 0 * * *'},{'type':'cron','expression':'0 9 * * *','timezone':'invalid/zone'},
        {'type':'cron','expression':'0 9 * * *','user_id':'other'}]:
        with pytest.raises(AuthFailure):normalize(invalid,now)


def test_create_idempotence_owner_isolation_and_time_only_revision(env):
    service,auth,(owner,other),now,ai=env
    b=body();first=service.create(owner,b)['task']
    assert service.create(owner,b)['created'] is False
    with pytest.raises(AuthFailure):service.create(owner,{**b,'instruction':'different'})
    assert service.list(other)['tasks']==[]
    with pytest.raises(AuthFailure) as e:service.list(other,first['id'])
    assert e.value.status==404
    assert len(first['next_runs'])==3
    changed=service.update_time(owner,{'id':first['id'],'revision':first['revision'],'time_description':'每周五六点',
        'rule':{'type':'cron','expression':'0 18 * * 5'}})['task']
    assert changed['instruction']==first['instruction'] and changed['revision']!=first['revision']
    assert not ai.calls
    with pytest.raises(AuthFailure):service.update_time(owner,{'id':first['id'],'revision':first['revision'],'time_description':'旧版本','rule':{'type':'cron','expression':'0 1 * * *'}})
    recreated=ScheduleService(service.store.directory,auth,ai,clock=lambda:now[0])
    assert recreated.list(owner)['tasks'][0]['revision']==changed['revision']


def test_due_runs_once_on_repeated_ticks_and_survives_desktop_logout(env):
    service,auth,(owner,_),now,ai=env
    task=service.create(owner,body(rule={'type':'interval','seconds':60}))['task']
    with auth.repository.transaction() as state:state['sessions'].clear()
    now[0]+=60
    async def run():
        await service.tick();await service.tick()
        await asyncio.gather(*list(service.active.values()))
        await service.tick()
    asyncio.run(run())
    assert len(ai.calls)==1
    user,b,kwargs=ai.calls[0];assert user['id']==owner['id']
    assert kwargs['scheduled_run']['task_id']==task['id'] and not b.interactive_questions
    record=service.list(owner,task['id']);assert record['task']['last_run']['status']=='success'
    assert record['task']['last_run']['result_saved'] and record['task']['next_at']==now[0]+60
    with auth.repository.transaction(write=False) as state:assert not state['sessions']


def test_revoked_permissions_and_password_changes_prevent_execution(env,monkeypatch):
    service,auth,(owner,_),now,ai=env
    task=service.create(owner,body(rule={'type':'interval','seconds':10}))['task']
    original=auth._public
    monkeypatch.setattr(auth,'_public',lambda raw:{**original(raw),'permissions':['schedule:read']})
    now[0]+=10
    async def run():await service.tick();await asyncio.gather(*list(service.active.values()))
    asyncio.run(run());assert not ai.calls
    assert service.list(owner,task['id'])['runs'][0]['status']=='failed'
    monkeypatch.setattr(auth,'_public',original)
    with auth.repository.transaction() as state:state['users'][owner['id']]['auth_version']+=1
    now[0]+=10;asyncio.run(run());assert not ai.calls


def test_restart_marks_inflight_interrupted_without_replay(env):
    service,_,(owner,_),now,ai=env
    task=service.create(owner,body(rule={'type':'once','at':'2026-11-23T09:00:00+08:00'}))['task']
    rid=str(uuid.uuid4())
    with service.store.transaction(owner) as state:
        state['runs'][rid]={'id':rid,'task_id':task['id'],'status':'running','scheduled_at':now[0]-1,'conversation_id':str(uuid.uuid4())}
        state['tasks'][task['id']].update(active_run=rid,status='running',next_at=None)
    service.recover()
    asyncio.run(service.tick())
    assert not ai.calls
    r=service.list(owner,task['id']);assert r['runs'][0]['status']=='interrupted' and r['task']['next_at'] is None


def test_delete_cancels_execution_and_preserves_result_history(env):
    service,_,(owner,_),now,ai=env
    task=service.create(owner,body(rule={'type':'interval','seconds':1}))['task']
    entered=asyncio.Event()
    async def send(*args,**kwargs):entered.set();await asyncio.Event().wait()
    ai.send=send;now[0]+=1
    async def run():
        await service.tick();await entered.wait()
        active=list(service.active.values())
        assert (await service.delete(owner,{'id':task['id'],'revision':task['revision']}))['deleted']
        await asyncio.gather(*active)
        await service.tick()
    asyncio.run(run());assert not service.list(owner)['tasks']
    with service.store.transaction(owner,False) as state:assert list(state['runs'].values())[0]['status']=='interrupted'


def test_api_dispatcher_and_trusted_task_context(tmp_path,monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG',raising=False);monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE','files-v1');monkeypatch.setenv('GALAXY_AI_RUNTIME','langgraph')
    model_calls=[]
    def upstream(request):
        model_calls.append(json.loads(request.content));return httpx.Response(200,json={'choices':[{'message':{'content':'已理解新时间。'}}],'usage':{}})
    from xingxu.app import create_app
    from xingxu.ai import ProviderInput
    from xingxu.tool_context import ExecutionContext
    app=create_app(tmp_path,ai_transport=httpx.MockTransport(upstream));auth=app.state.auth
    auth.import_accounts([('owner','fixture-password'),('other','fixture-password')])
    a=auth.login('owner','fixture-password',False,'fixture');b=auth.login('other','fixture-password',False,'fixture')
    app.state.ai.save_provider(ProviderInput(name='fixture',base_url='https://fixture.invalid/v1',api_key='fixture',model='fixture'))
    headers={'Authorization':'Bearer '+a['access_token']};other={'Authorization':'Bearer '+b['access_token']}
    with TestClient(app) as client:
        result=client.post('/api/v1/schedule',headers=headers,json=body());assert result.status_code==201,result.text
        task=result.json()['task']
        assert client.get('/api/v1/schedule/'+task['id'],headers=other).status_code==404
        assert client.post('/api/v1/chat/send',headers=other,json={'message':'改为九点','schedule_task_id':task['id']}).status_code==404
        res=client.post('/api/v1/chat/send',headers=headers,json={'message':'改为每周五六点','schedule_task_id':task['id']});assert res.status_code==200,res.text
        assert any('服务器关联的本人任务' in str(m) and task['id'] in str(m) for m in model_calls[-1]['messages'])
        assert client.put('/api/v1/schedule/'+task['id']+'/time',headers=headers,json={'rule':{'type':'cron','expression':'0 18 * * 5'},'revision':task['revision'],'time_description':'每周五六点'}).status_code==200
        assert client.delete('/api/v1/schedule/'+task['id']+'?revision='+task['revision'],headers=headers).status_code==409
    context=ExecutionContext(a['user']['id'],a['user']['workspace_id'],tuple(a['user']['permissions']),'fixture',a['access_token'])
    call={'id':'fixture','type':'function','function':{'name':'galaxy_cli','arguments':json.dumps({'args':['schedule','list','--input','{}']})}}
    event=asyncio.run(app.state.ai.dispatcher.execute(context,call));assert event['status']=='success',event
    assert event['result']['tasks'][0]['id']==task['id']
    background=ExecutionContext(context.user_id,context.workspace_id,context.permissions,context.conversation_id,context.session_token,background_run=True)
    denied=asyncio.run(app.state.ai.dispatcher.execute(background,call));assert denied['status']=='denied'


def test_ai_creates_and_updates_through_registered_tools_without_running_business(tmp_path,monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG',raising=False);monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE','files-v1');monkeypatch.setenv('GALAXY_AI_RUNTIME','langgraph')
    from xingxu.app import create_app
    from xingxu.ai import ProviderInput
    state={'calls':0,'mode':'create','task':None}
    def upstream(request):
        state['calls']+=1
        if state['calls']%2==0:return httpx.Response(200,json={'choices':[{'message':{'content':'任务时间已保存。'}}]})
        if state['mode']=='create':args=['schedule','create','--input',json.dumps(body(rule={'type':'cron','expression':'0 9 * * 1-5'}))]
        else:
            t=state['task'];args=['schedule','update-time','--input',json.dumps({'id':t['id'],'revision':t['revision'],'time_description':'每周五十八点','rule':{'type':'cron','expression':'0 18 * * 5'}})]
        return httpx.Response(200,json={'choices':[{'message':{'content':'','tool_calls':[{'id':str(uuid.uuid4()),'type':'function','function':{'name':'galaxy_cli','arguments':json.dumps({'args':args})}}]}}]})
    app=create_app(tmp_path,ai_transport=httpx.MockTransport(upstream));auth=app.state.auth;auth.import_accounts([('owner','fixture-password')]);a=auth.login('owner','fixture-password',False,'test')
    app.state.ai.save_provider(ProviderInput(name='fixture',base_url='https://fixture.invalid/v1',api_key='fixture',model='fixture'))
    headers={'Authorization':'Bearer '+a['access_token']}
    with TestClient(app) as client:
        response=client.post('/api/v1/chat/send',headers=headers,json={'message':'每个工作日九点整理日报'});assert response.status_code==200,response.text
        event=response.json()['conversation']['messages'][-1]['tool_events'][0];assert event['status']=='success',event
        task=event['result']['task'];state.update(task=task,mode='update')
        response=client.post('/api/v1/chat/send',headers=headers,json={'message':'改到每周五下午六点','schedule_task_id':task['id']});assert response.status_code==200,response.text
        event=response.json()['conversation']['messages'][-1]['tool_events'][0];assert event['status']=='success',event
        assert len(event['result']['task']['next_runs'])==3
        saved=client.get('/api/v1/schedule/'+task['id'],headers=headers).json()
        assert saved['task']['rule']['expression']=='0 18 * * 5' and not saved['runs']
