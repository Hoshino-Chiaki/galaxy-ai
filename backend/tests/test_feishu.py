import asyncio
import copy
import json
import time
from urllib.parse import parse_qs
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from xingxu.app import create_app
from xingxu.auth import AuthFailure, ROLES
from xingxu.feishu.service import owner_key
from xingxu.tools import ExecutionContext

APP={'app_id':'cli_fixtureapp','app_secret':'fixture-private-app-secret'}


@pytest.fixture
def fixture(tmp_path):
    calls=[];polls={};refreshes=[]
    def upstream(request):
        calls.append(request)
        if request.url.path=='/oauth/v1/device_authorization':
            assert request.headers['authorization'].startswith('Basic ')
            device='fixture-private-device-'+str(len(calls));polls[device]=False
            return httpx.Response(200,json={'device_code':device,'user_code':'TEST-CODE','verification_uri_complete':'https://accounts.feishu.cn/device?user_code=TEST', 'expires_in':600,'interval':1})
        if request.url.path=='/oauth/v3/token':
            body=parse_qs(request.content.decode())
            assert body['client_id']==[APP['app_id']] and body['client_secret']==[APP['app_secret']]
            if body['grant_type']==['client_credentials']:
                return httpx.Response(200,json={'code':0,'access_token':'fixture-private-application','expires_in':7200,
                    'scope':'attendance:task:readonly contact:contact.base:readonly contact:user.employee:readonly'})
            if body['grant_type']==['refresh_token']:
                refreshes.append(body['refresh_token'][0]);return httpx.Response(200,json={'access_token':'fixture-private-refreshed','expires_in':7200,'refresh_token':'fixture-private-rotated','refresh_token_expires_in':604800,'scope':'docx:document:readonly'})
            device=body['device_code'][0]
            if not polls[device]:return httpx.Response(400,json={'error':'authorization_pending'})
            return httpx.Response(200,json={'access_token':'fixture-private-access-'+device[-1], 'refresh_token':'fixture-private-refresh-'+device[-1], 'expires_in':7200,'refresh_token_expires_in':604800,'scope':'docx:document:readonly search:docs:read','token_type':'Bearer'})
        if request.url.path.startswith('/open-apis/contact/v3/users/'):
            assert request.headers['authorization']=='Bearer fixture-private-application'
            open_id=request.url.path.rsplit('/',1)[-1]
            return httpx.Response(200,json={'code':0,'data':{'user':{'open_id':open_id,
                'user_id':'employee_'+open_id[3:],'employee_no':'number_'+open_id[3:], 'is_tenant_manager':False}}})
        assert request.url.path=='/open-apis/authen/v1/user_info'
        token=request.headers['authorization']
        return httpx.Response(200,json={'code':0,'data':{'open_id':'ou_'+token[-1],'tenant_key':'fixture-tenant','user_id':'employee_'+token[-1],'name':'测试用户'+token[-1]}})
    app=create_app(tmp_path,feishu_transport=httpx.MockTransport(upstream))
    app.state.auth.import_accounts([('admin','fixture-admin-password'),('employee','fixture-employee-password')])
    sessions=[app.state.auth.login(n,'fixture-'+n+'-password',False,'fixture') for n in ('admin','employee')]
    return app,sessions,calls,polls,refreshes,tmp_path


def headers(session):return {'Authorization':'Bearer '+session['access_token']}


def connect(service, user, polls):
    status=asyncio.run(service.start(user));pending=status['pending']
    for key in polls:polls[key]=True
    return asyncio.run(service.poll(user,pending['id']))


def test_only_admin_configures_shared_app_and_users_get_no_secret(fixture):
    app,sessions,calls,_,_,directory=fixture
    with TestClient(app) as client:
        assert client.get('/api/v1/feishu').status_code==401
        assert client.put('/api/v1/feishu/app',headers=headers(sessions[1]),json=APP).status_code==403
        saved=client.put('/api/v1/feishu/app',headers=headers(sessions[0]),json=APP)
        assert saved.status_code==200 and saved.json()['configured']
        assert saved.json()['has_app_secret'] is True
        employee=client.get('/api/v1/feishu',headers=headers(sessions[1])).json()
        assert employee['configured'] is True and 'has_app_secret' not in employee
        assert employee['connected'] is False
        assert APP['app_secret'] not in saved.text+(directory/'feishu.json').read_text()
        assert client.get('/api/v1/admin/ai',headers=headers(sessions[1])).status_code==403
        assert client.put('/api/v1/admin/ai/prompt',headers=headers(sessions[1]),json={'prompt':'forbidden'}).status_code==403
        assert not calls


def test_device_authorization_is_owned_encrypted_and_does_not_share_user_tokens(fixture):
    app,sessions,_,polls,_,directory=fixture;service=app.state.feishu
    from xingxu.feishu.routes import AppInput
    service.save_app(sessions[0]['user'],AppInput(**APP))
    with TestClient(app) as client:
        a=client.post('/api/v1/feishu/authorize',headers=headers(sessions[0])).json()
        b=client.post('/api/v1/feishu/authorize',headers=headers(sessions[1])).json()
        assert a['pending']['id']!=b['pending']['id']
        assert client.post('/api/v1/feishu/poll',headers=headers(sessions[1]),json={'id':a['pending']['id']}).status_code==404
        pending=client.post('/api/v1/feishu/poll',headers=headers(sessions[0]),json={'id':a['pending']['id']})
        assert pending.status_code==200 and pending.json()['pending']
        for key in polls:polls[key]=True
        with service.repo.transaction() as state:
            state['connections'][owner_key(sessions[0]['user'])]['pending']['next_poll_at']=0
        a=client.post('/api/v1/feishu/poll',headers=headers(sessions[0]),json={'id':a['pending']['id']}).json()
        b=client.post('/api/v1/feishu/poll',headers=headers(sessions[1]),json={'id':b['pending']['id']}).json()
        assert a['connected'] and b['connected'] and a['identity']['open_id']!=b['identity']['open_id']
        text=json.dumps([a,b])+ (directory/'feishu.json').read_text()
        for secret in ['fixture-private-access','fixture-private-refresh','fixture-private-device',APP['app_secret']]:assert secret not in text
        credentials=[asyncio.run(service.credentials(s['user'])) for s in sessions]
        assert credentials[0]['access_token']!=credentials[1]['access_token']
        assert 'app_secret' not in credentials[0] and 'refresh_token' not in credentials[0]
        assert client.delete('/api/v1/feishu',headers=headers(sessions[1])).json()['connected'] is False
        assert service.summary(sessions[0]['user'])['connected'] is True
        assert service.unchanged(sessions[1]['user'],credentials[1]) is False


def test_oauth_refresh_is_serialized_and_rotated_tokens_stay_encrypted(fixture):
    app,sessions,_,polls,refreshes,directory=fixture;service=app.state.feishu
    from xingxu.feishu.routes import AppInput
    service.save_app(sessions[0]['user'],AppInput(**APP));connect(service,sessions[1]['user'],polls)
    with service.repo.transaction() as state:
        record=state['connections'][owner_key(sessions[1]['user'])];token=service._decrypt(record['token']);token['expires_at']=time.time()-1;record['token']=service._encrypt(token)
    async def scenario():return await asyncio.gather(service.credentials(sessions[1]['user']),service.credentials(sessions[1]['user']))
    credentials=asyncio.run(scenario())
    assert len(refreshes)==1 and credentials[0]==credentials[1]
    assert credentials[0]['access_token']=='fixture-private-refreshed'
    assert 'fixture-private' not in (directory/'feishu.json').read_text()


def test_app_changes_or_rejected_authorization_cannot_replace_other_identity(fixture):
    app,sessions,_,polls,_,_=fixture;service=app.state.feishu
    from xingxu.feishu.routes import AppInput
    service.save_app(sessions[0]['user'],AppInput(**APP));connect(service,sessions[1]['user'],polls)
    before=asyncio.run(service.credentials(sessions[1]['user']))
    service.save_app(sessions[0]['user'],AppInput(**{**APP,'app_secret':'new-private-secret'}))
    assert not service.summary(sessions[1]['user'])['connected']
    assert not service.unchanged(sessions[1]['user'],before)
    with pytest.raises(AuthFailure):asyncio.run(service.credentials(sessions[1]['user']))


def test_gateway_rechecks_employee_grants_and_disconnect_hides_result(fixture,monkeypatch):
    app,sessions,_,polls,_,_=fixture;service=app.state.feishu;dispatcher=app.state.ai.dispatcher
    from xingxu.feishu.routes import AppInput
    service.save_app(sessions[0]['user'],AppInput(**APP));connect(service,sessions[1]['user'],polls)
    user=sessions[1]['user'];context=ExecutionContext(user['id'],user['workspace_id'],tuple(user['permissions']),'fixture-chat',sessions[1]['access_token'])
    call={'id':'fixture','type':'function','function':{'name':'galaxy_cli','arguments':json.dumps({'args':['feishu','search','--input','{"query":"业务"}']})}}
    async def run(*args,**kwargs):
        assert kwargs['credentials']['open_id']==service.summary(user)['identity']['open_id']
        await service.disconnect(user)
        return {'ok':True,'data':{'private_result':'must-not-escape'}}
    dispatcher._run=AsyncMock(side_effect=run)
    result=asyncio.run(dispatcher.execute(context,call))
    assert result['status']=='denied' and 'must-not-escape' not in json.dumps(result)
    monkeypatch.setitem(ROLES,'employee',[p for p in ROLES['employee'] if p!='feishu:read'])
    assert asyncio.run(dispatcher.execute(context,call))['result']['error']['code']=='permission_denied'


def test_foreign_verification_url_and_upstream_messages_are_not_exposed(fixture):
    app,sessions,_,_,_,_=fixture;service=app.state.feishu
    from xingxu.feishu.routes import AppInput
    service.save_app(sessions[0]['user'],AppInput(**APP))
    service.oauth.transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'device_code':'secret','verification_uri':'https://evil.invalid/auth','expires_in':600}))
    with pytest.raises(AuthFailure) as error:asyncio.run(service.start(sessions[1]['user']))
    assert error.value.code=='feishu_oauth_error'
    service.oauth.transport=httpx.MockTransport(lambda r:httpx.Response(400,json={'error':'invalid_client','error_description':APP['app_secret']}))
    with pytest.raises(AuthFailure) as error:asyncio.run(service.start(sessions[1]['user']))
    assert APP['app_secret'] not in error.value.message


def test_automatic_full_authorization_preserves_existing_tokens_and_employee_isolation(fixture):
    app,sessions,calls,polls,_,_=fixture;service=app.state.feishu
    from xingxu.feishu.routes import AppInput
    from galaxy_cli.feishu_capabilities import all_scopes
    user=sessions[0]['user']
    service.save_app(user,AppInput(**APP));connected=connect(service,user,polls)
    _,before=service._snapshot(user)
    assert connected['authorization_mode']=='all' and 'authorization_groups' not in connected
    service.oauth.identity=AsyncMock(return_value=connected['identity'])
    service.oauth.poll=AsyncMock(return_value=(200,{'access_token':'fixture-private-writing','refresh_token':'fixture-private-refresh-writing','expires_in':7200,'scope':' '.join(all_scopes())}))
    pending=asyncio.run(service.start(user,group='docs-write-1'))['pending']
    requested=parse_qs(calls[-1].content.decode())['scope'][0].split()
    assert requested==all_scopes() and len(requested)>50
    status=asyncio.run(service.poll(user,pending['id']))
    _,after=service._snapshot(user)
    assert before['token'] in after['grants'].values() and after['token']!=before['token']
    assert status['connected'] and status['authorization_mode']=='all'
    assert asyncio.run(service.credentials(user,domain='docs',action='+create'))['access_token']=='fixture-private-writing'
    assert asyncio.run(service.credentials(user,domain='docs',action='+fetch'))['access_token'].startswith('fixture-private-')
    with pytest.raises(AuthFailure) as error:asyncio.run(service.credentials(sessions[1]['user'],domain='docs',action='+create'))
    assert error.value.code=='feishu_connection_required'


def test_business_grant_cannot_mix_different_feishu_users_or_teams(fixture):
    app,sessions,_,polls,_,_=fixture;service=app.state.feishu
    from xingxu.feishu.routes import AppInput
    user=sessions[0]['user'];service.save_app(user,AppInput(**APP));connect(service,user,polls)
    _,before=service._snapshot(user)
    service.oauth.poll=AsyncMock(return_value=(200,{'access_token':'fixture-other-person','expires_in':7200,'scope':'docx:document:create'}))
    service.oauth.identity=AsyncMock(return_value={'open_id':'another-user','tenant_key':'another-team'})
    pending=asyncio.run(service.start(user,group='docs-write-1'))['pending']
    with pytest.raises(AuthFailure) as error:asyncio.run(service.poll(user,pending['id']))
    assert error.value.code=='feishu_identity_mismatch'
    _,after=service._snapshot(user)
    assert after['token']==before['token'] and after.get('grants')==before.get('grants')


def test_help_works_without_business_authorization_and_write_is_checked_server_side(fixture,monkeypatch):
    app,sessions,_,_,_,_=fixture;dispatcher=app.state.ai.dispatcher;user=sessions[1]['user']
    context=ExecutionContext(user['id'],user['workspace_id'],tuple(user['permissions']),'fixture-chat',sessions[1]['access_token'])
    def call(body):return {'id':'fixture','type':'function','function':{'name':'galaxy_cli','arguments':json.dumps({'args':['feishu','docs','--input',json.dumps(body)]})}}
    result=asyncio.run(dispatcher.execute(context,call({'action':'help','command':'+create'})))
    assert result['status']=='success' and result['result']['data']['risk']=='write'
    assert result['command']=='galaxy-cli feishu docs help'
    with dispatcher.audit.transaction(write=False) as state:
        assert state['records'][result['id']]['command']=='galaxy-cli feishu docs help'
    monkeypatch.setitem(ROLES,'employee',[p for p in ROLES['employee'] if p!='feishu:write'])
    result=asyncio.run(dispatcher.execute(context,call({'action':'+create','options':{'content':'text','doc-format':'markdown'}})))
    assert result['result']['error']['code']=='permission_denied'


def test_no_client_can_narrow_the_automatic_authorization_and_legacy_no_body_works(fixture):
    app,sessions,calls,_,_,_=fixture
    from galaxy_cli.feishu_capabilities import all_scopes
    with TestClient(app) as client:
        client.put('/api/v1/feishu/app',headers=headers(sessions[0]),json=APP).raise_for_status()
        assert client.post('/api/v1/feishu/authorize',headers=headers(sessions[0]),json={'group':'documents'}).status_code==200
        assert parse_qs(calls[-1].content.decode())['scope'][0].split()==all_scopes()
        assert client.post('/api/v1/feishu/authorize',headers=headers(sessions[0]),json={'scopes':['offline_access']}).status_code==422
        response=client.post('/api/v1/feishu/authorize',headers=headers(sessions[0]))
        assert response.status_code==200 and response.json()['pending']


def test_attendance_uses_application_token_without_exporting_shared_secrets(fixture):
    app,sessions,_,polls,_,_=fixture;service=app.state.feishu
    from xingxu.feishu.routes import AppInput
    user=sessions[0]['user'];service.save_app(user,AppInput(**APP));connect(service,user,polls)
    credentials=asyncio.run(service.credentials(user,domain='attendance',action='user_tasks.query'))
    assert credentials['identity']=='application' and credentials['access_token']=='fixture-private-application'
    assert credentials['employee_id'].startswith('employee_') and credentials['is_tenant_manager'] is False
    assert 'app_secret' not in credentials and 'refresh_token' not in credentials
    assert service.summary(user)['management']['status']=='tenant_member'
