import asyncio
import copy
import json
import time
from urllib.parse import parse_qs
from unittest.mock import AsyncMock

import httpx
import pytest

from xingxu.auth import AuthFailure, COMPANY_ATTENDANCE_READ, ROLES
from xingxu.feishu.application import FeishuApplication
from xingxu.feishu.routes import AppInput
from xingxu.feishu.service import owner_key
from xingxu.tools import ExecutionContext
from galaxy_cli.registry import ToolError
from galaxy_cli.tools.feishu.attendance import prepare
from test_feishu import fixture, connect, APP


def ready(fixture, index=1):
    app,sessions,_,polls,_,_=fixture
    user=sessions[index]['user'];service=app.state.feishu
    service.save_app(sessions[0]['user'],AppInput(**APP))
    status=connect(service,user,polls)
    return app,service,user,status


def body(**extra):
    return {'action':'user_tasks.query','data':{'check_date_from':20261111,'check_date_to':20261111,**extra}}


def contact_result(identity, flag=False, **extra):
    return httpx.Response(200,json={'code':0,'data':{'user':{
        'open_id':identity['open_id'],'user_id':identity['user_id'],
        'is_tenant_manager':flag,**extra}}})


def test_local_admin_company_grant_is_separate_from_platform_super_admin(fixture):
    _,service,user,status=ready(fixture,index=0)
    assert user['role']=='admin' and status['management']['status']=='tenant_member'
    assert status['management']['is_tenant_manager'] is False
    assert status['management']['checked_at']>0
    current=asyncio.run(service.credentials(user,domain='attendance',action='user_tasks.query'))
    assert current['is_tenant_manager'] is False
    assert current['attendance_access']=='application'
    assert prepare(body(user_ids=['someone_else']),current)['data']['user_ids']==['someone_else']
    assert service.summary(user)['attendance_access']=='application'


def test_verified_employee_defaults_to_self_and_other_ids_are_rejected(fixture):
    _,service,user,_=ready(fixture)
    credentials=asyncio.run(service.credentials(user,domain='attendance',action='user_tasks.query'))
    result=prepare(body(),credentials)
    assert result['data']['user_ids']==[credentials['employee_id']]
    assert result['params']['employee_type']=='employee_id'
    assert 'user_ids' not in body()['data']
    value=body();value['params']={'employee_type':'employee_no'}
    assert prepare(value,credentials)['data']['user_ids']==[credentials['employee_no']]
    with pytest.raises(ToolError):prepare(body(user_ids=[credentials['employee_id'],'another']),credentials)


def test_unknown_manager_field_or_contact_denial_never_escalates(fixture):
    _,service,user,_=ready(fixture)
    original=service.application._request
    async def denied(method,url,**kwargs):
        if '/contact/' in url:return httpx.Response(400).status_code,{'code':99991672}
        return await original(method,url,**kwargs)
    service.application._request=denied
    credentials=asyncio.run(service.credentials(user,domain='attendance',action='user_tasks.query'))
    assert credentials['is_tenant_manager'] is False
    assert service.summary(user)['management']['status']=='unknown'
    assert prepare(body(),credentials)['data']['user_ids']==[credentials['employee_id']]
    with pytest.raises(ToolError):prepare(body(user_ids=['someone_else']),credentials)


def test_platform_super_admin_never_bypasses_caller_company_permission(fixture):
    _,service,user,status=ready(fixture)
    identity=status['identity'];original=service.application._request
    flag=True
    async def contact(method,url,**kwargs):
        if '/contact/' in url:
            result=contact_result(identity,flag)
            return result.status_code,result.json()
        return await original(method,url,**kwargs)
    service.application._request=contact
    first=asyncio.run(service.credentials(user,domain='attendance',action='user_tasks.query'))
    assert first['is_tenant_manager'] is True
    assert first['attendance_access']=='self'
    with pytest.raises(ToolError):prepare(body(user_ids=['someone_else']),first)
    flag=False
    second=asyncio.run(service.credentials(user,domain='attendance',action='user_tasks.query'))
    assert second['is_tenant_manager'] is False
    with pytest.raises(ToolError):prepare(body(user_ids=['someone_else']),second)


def test_admin_without_verified_company_membership_remains_self_only(fixture):
    _,service,user,_=ready(fixture,index=0)
    original=service.application._request
    async def denied(method,url,**kwargs):
        if '/contact/' in url:return 400,{'code':99991672}
        return await original(method,url,**kwargs)
    service.application._request=denied
    credentials=asyncio.run(service.credentials(user,domain='attendance',action='user_tasks.query'))
    assert COMPANY_ATTENDANCE_READ in user['permissions']
    assert credentials['attendance_access']=='self'
    with pytest.raises(ToolError):prepare(body(user_ids=['someone_else']),credentials)


def test_company_permission_is_rechecked_after_remote_identity_verification(fixture):
    _,service,user,_=ready(fixture,index=0)
    original=service.application.inspect
    current=dict(user)
    async def inspect(app,identity):
        result=await original(app,identity)
        current['permissions']=[p for p in user['permissions'] if p!=COMPANY_ATTENDANCE_READ]
        return result
    service.application.inspect=inspect
    credentials=asyncio.run(service.credentials(user,lambda:current,domain='attendance',action='user_tasks.query'))
    assert credentials['attendance_access']=='self'


@pytest.mark.parametrize('flag',[None,'true',1,{},[]])
def test_only_explicit_boolean_role_can_grant_company_access(fixture,flag):
    _,service,user,status=ready(fixture)
    response=contact_result(status['identity'],flag)
    service.application._request=AsyncMock(return_value=(response.status_code,response.json()))
    credentials=asyncio.run(service.credentials(user,domain='attendance',action='user_tasks.query'))
    assert credentials['is_tenant_manager'] is False


def test_application_cache_is_serialized_and_invalidated_by_app_revision():
    calls=[]
    def upstream(request):
        calls.append(parse_qs(request.content.decode()))
        return httpx.Response(200,json={'code':0,'access_token':'private-app-'+str(len(calls)),
            'expires_in':7200,'scope':'attendance:task:readonly'})
    client=FeishuApplication(httpx.MockTransport(upstream))
    app={**APP,'revision':'r1'}
    async def scenario():return await asyncio.gather(*(client.token(app) for _ in range(8)))
    result=asyncio.run(scenario())
    assert len(calls)==1 and len({t['access_token'] for t in result})==1
    changed=asyncio.run(client.token({**app,'revision':'r2'}))
    assert changed['access_token']!=result[0]['access_token'] and len(calls)==2
    client.tokens[(app['app_id'],'r1')]['expires_at']=time.time()
    asyncio.run(client.token(app))
    assert len(calls)==3


def test_newly_granted_app_scope_retries_with_new_token_without_relogin():
    calls=[]
    def upstream(request):
        if request.url.path=='/oauth/v3/token':
            calls.append('token')
            return httpx.Response(200,json={'access_token':'token-'+str(len(calls)),'expires_in':7200,'scope':'attendance:task:readonly'})
        calls.append('contact')
        if len(calls)==2:return httpx.Response(400,json={'code':99991672})
        return httpx.Response(200,json={'code':0,'data':{'user':{'open_id':'ou_self','user_id':'self','is_tenant_manager':True}}})
    client=FeishuApplication(httpx.MockTransport(upstream))
    result=asyncio.run(client.inspect({**APP,'revision':'r1'},{'open_id':'ou_self','user_id':'self'}))
    assert result['is_tenant_manager'] is True
    assert calls==['token','contact','token','contact']


def test_contact_wrong_identity_and_inactive_employee_cannot_query(fixture):
    _,service,user,status=ready(fixture)
    for fields in [{'user_id':'different'}, {'status':{'is_frozen':True}}]:
        response=contact_result(status['identity'],True,**fields)
        service.application._request=AsyncMock(return_value=(response.status_code,response.json()))
        with pytest.raises(AuthFailure) as error:
            asyncio.run(service.credentials(user,domain='attendance',action='user_tasks.query'))
        assert error.value.code=='feishu_identity_unavailable'


def test_disconnect_during_role_verification_cannot_recreate_connection(fixture):
    _,service,user,status=ready(fixture)
    original=service.application.inspect
    async def inspect(app,identity):
        result=await original(app,identity)
        with service.repo.transaction() as state:state['connections'].pop(owner_key(user))
        return result
    service.application.inspect=inspect
    with pytest.raises(AuthFailure) as error:
        asyncio.run(service.credentials(user,domain='attendance',action='user_tasks.query'))
    assert error.value.code=='feishu_connection_changed'
    assert not service.summary(user)['connected']


def test_old_grant_cannot_be_rebound_to_changed_app_revision(fixture):
    _,service,user,_=ready(fixture)
    _,record=service._snapshot(user)
    admin=fixture[1][0]['user']
    service.save_app(admin,AppInput(**{**APP,'app_secret':'new-private-app-secret'}))
    app,_=service._snapshot(user)
    with pytest.raises(AuthFailure) as error:
        service._commit(user,app['revision'],{'management':{'is_tenant_manager':True}},grant_id=record['grant_id'])
    assert error.value.code=='feishu_connection_changed'
    assert service._snapshot(user)[1]==record


def test_gateway_uses_private_application_identity_and_supplies_only_own_id(fixture):
    app,service,user,_=ready(fixture)
    session=fixture[1][1]
    context=ExecutionContext(user['id'],user['workspace_id'],tuple(user['permissions']),'attendance-test',session['access_token'])
    dispatcher=app.state.ai.dispatcher
    seen=[]
    async def run(canonical,user,**kwargs):
        credentials=kwargs['credentials'];value=json.loads(canonical[canonical.index('--input')+1])
        assert credentials['identity']=='application'
        assert value['data']['user_ids']==[credentials['employee_id']]
        seen.append(value)
        return {'ok':True,'data':{'user_task_results':[{'records':[]}],'invalid_user_ids':[]}}
    dispatcher._run=AsyncMock(side_effect=run)
    def call(value):return {'id':'attendance-test','type':'function','function':{'name':'galaxy_cli','arguments':json.dumps({'args':['feishu','attendance','--input',json.dumps(value)]})}}
    result=asyncio.run(dispatcher.execute(context,call(body())))
    assert result['status']=='success' and len(seen)==1
    denied=asyncio.run(dispatcher.execute(context,call(body(user_ids=['another']))))
    assert denied['status']=='denied' and denied['result']['error']['code']=='feishu_attendance_forbidden'
    assert len(seen)==1
    public=json.dumps(result)+json.dumps(service.summary(user))
    assert 'fixture-private-application' not in public and APP['app_secret'] not in public


def test_revocation_of_company_permission_discards_in_flight_result(fixture,monkeypatch):
    app,service,user,_=ready(fixture,index=0)
    session=fixture[1][0]
    context=ExecutionContext(user['id'],user['workspace_id'],tuple(user['permissions']),'company-test',session['access_token'])
    async def run(*args,**kwargs):
        assert kwargs['credentials']['attendance_access']=='application'
        monkeypatch.setitem(ROLES,'admin',[p for p in ROLES['admin'] if p!=COMPANY_ATTENDANCE_READ])
        return {'ok':True,'data':{'private_result':'must-not-escape'}}
    app.state.ai.dispatcher._run=AsyncMock(side_effect=run)
    call={'id':'company-test','type':'function','function':{'name':'galaxy_cli','arguments':json.dumps({'args':['feishu','attendance','--input',json.dumps(body(user_ids=['other']))]})}}
    result=asyncio.run(app.state.ai.dispatcher.execute(context,call))
    assert result['status']=='denied' and result['result']['error']['code']=='permission_denied'
    assert 'must-not-escape' not in json.dumps(result)


@pytest.mark.parametrize('start,end',[(20260230,20260301),(20261112,20261111),(2026111,20261111)])
def test_invalid_working_dates_are_rejected(start,end):
    credentials={'identity':'application','employee_id':'self','is_tenant_manager':False}
    with pytest.raises(ToolError):prepare({'data':{'check_date_from':start,'check_date_to':end}},credentials)
