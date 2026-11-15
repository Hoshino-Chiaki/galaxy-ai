import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from xingxu.auth import AuthFailure, COMPANY_ATTENDANCE_READ, ROLES
from xingxu.tools import ExecutionContext
from test_feishu import fixture
from test_feishu_attendance import ready


def install_directory(service, response=None, status=200):
    original=service.application._request
    seen=[]
    async def request(method,url,**kwargs):
        if (url.endswith('/scopes') or url.endswith('/find_by_department') or url.endswith('/children')
                or url.endswith('/users/employee_a')):
            seen.append((method,url,kwargs))
            assert kwargs['headers']['Authorization']=='Bearer fixture-private-application'
            return status, response or {'code':0,'msg':'success','data':{
                'user_ids':['employee_a','employee_b'],'has_more':True,'page_token':'next+/='}}
        return await original(method,url,**kwargs)
    service.application._request=request
    return seen


def test_directory_needs_company_capability_before_any_platform_call(fixture):
    _,service,user,_=ready(fixture,index=1)
    service.application._request=AsyncMock()
    with pytest.raises(AuthFailure) as error:asyncio.run(service.directory_credentials(user,{}))
    assert error.value.code=='permission_denied'
    service.application._request.assert_not_called()


@pytest.mark.parametrize('body,path,extra',[
    ({},'/contact/v3/scopes',{'page_size':50}),
    ({'action':'scope','page_size':3,'page_token':'next+/='},'/contact/v3/scopes',{'page_size':3,'page_token':'next+/='}),
    ({'action':'users','department_id':'od-authorized'},'/contact/v3/users/find_by_department',{'department_id':'od-authorized','page_size':50}),
    ({'action':'departments','department_id':'od-authorized','fetch_child':True},'/contact/v3/departments/od-authorized/children',{'fetch_child':'true','page_size':50}),
    ({'action':'user','user_id':'employee_a'},'/contact/v3/users/employee_a',{})])
def test_fixed_directory_endpoints_and_pagination_need_no_personal_search_scope(fixture,body,path,extra):
    _,service,user,_=ready(fixture,index=0)
    seen=install_directory(service)
    # The directory operation must not require the attendance API scope either.
    for token in service.application.tokens.values():token['scope']='contact:contact.base:readonly'
    result=asyncio.run(service.directory_credentials(user,body))
    assert result['directory_access']=='application'
    assert 'access_token' not in result and 'app_secret' not in result and 'refresh_token' not in result
    assert seen[0][0]=='GET' and seen[0][1]=='https://open.feishu.cn/open-apis'+path
    assert seen[0][2]['params']=={'user_id_type':'user_id','department_id_type':'open_department_id',**extra}


def test_prepared_directory_result_redacts_credential_reflections(fixture):
    _,service,user,_=ready(fixture,index=0)
    install_directory(service,{'code':0,'data':{'reflection':'fixture-private-application'}})
    result=asyncio.run(service.directory_credentials(user,{}))
    assert 'fixture-private-application' not in json.dumps(result)
    assert result['directory_result']['data']['reflection']=='[已隐藏凭证]'


def test_directory_authorization_revoked_during_request_discards_result(fixture,monkeypatch):
    app,service,user,_=ready(fixture,index=0)
    original=service.application._request
    async def request(method,url,**kwargs):
        if url.endswith('/scopes'):
            monkeypatch.setitem(ROLES,'admin',[p for p in ROLES['admin'] if p!=COMPANY_ATTENDANCE_READ])
            return 200,{'code':0,'data':{'private':'must-not-escape'}}
        return await original(method,url,**kwargs)
    service.application._request=request
    session=fixture[1][0]
    validate=lambda:app.state.auth.identity(session['access_token'])['user']
    with pytest.raises(AuthFailure) as error:asyncio.run(service.directory_credentials(user,{},validate))
    assert error.value.code=='permission_denied'


def test_disconnect_during_directory_fetch_never_returns_data(fixture):
    _,service,user,_=ready(fixture,index=0)
    original=service.application._request
    async def request(method,url,**kwargs):
        if url.endswith('/scopes'):
            await service.disconnect(user)
            return 200,{'code':0,'data':{'private':'must-not-escape'}}
        return await original(method,url,**kwargs)
    service.application._request=request
    with pytest.raises(AuthFailure) as error:asyncio.run(service.directory_credentials(user,{}))
    assert error.value.code=='feishu_connection_changed'


def test_authenticated_gateway_runs_real_cli_without_sending_platform_token(fixture):
    app,service,user,_=ready(fixture,index=0)
    install_directory(service)
    session=fixture[1][0]
    context=ExecutionContext(user['id'],user['workspace_id'],tuple(user['permissions']),'directory-test',session['access_token'])
    call={'id':'directory-test','type':'function','function':{'name':'galaxy_cli',
        'arguments':json.dumps({'args':['feishu','authorized-users']})}}
    event=asyncio.run(app.state.ai.dispatcher.execute(context,call))
    assert event['status']=='success'
    assert event['result']['data']['user_ids']==['employee_a','employee_b']
    assert event['result']['data']['page_token']=='next+/='
    assert event['result']['coverage']=='application_authorized_scope'
    assert 'fixture-private-application' not in json.dumps(event)


def test_department_denial_reaches_chat_with_exact_business_code(fixture):
    app,service,user,_=ready(fixture,index=0)
    install_directory(service,{'code':40004,'msg':'no dept authority error','error':{'log_id':'LOG_DEPT'}},403)
    session=fixture[1][0]
    context=ExecutionContext(user['id'],user['workspace_id'],tuple(user['permissions']),'directory-test',session['access_token'])
    call={'id':'directory-test','type':'function','function':{'name':'galaxy_cli',
        'arguments':json.dumps({'args':['feishu','authorized-users','--input',json.dumps({'action':'users','department_id':'0'})]})}}
    event=asyncio.run(app.state.ai.dispatcher.execute(context,call))
    assert event['status']=='error'
    assert event['result']['error']['upstream_code']==40004
    assert event['result']['error']['http_status']==403
    assert event['result']['error']['request_id']=='LOG_DEPT'
