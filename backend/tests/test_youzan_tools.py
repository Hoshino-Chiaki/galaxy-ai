from galaxy_cli.feishu_capabilities import DOMAINS
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from xingxu.app import create_app
from xingxu.ai import ProviderInput
from xingxu.auth import ROLES
from xingxu.tools import ExecutionContext
from xingxu.youzan import YouzanInput

SID, CSRF, TOKEN = 'fixture-sid-private-1234', 'fixture-csrf-private-4567', 'fixture-business-token-7890'


def call(args):
    return {'id': 'fixture-call', 'type': 'function', 'function': {'name': 'galaxy_cli', 'arguments': json.dumps({'args': args})}}


@pytest.fixture
def fixture(tmp_path):
    app = create_app(tmp_path)
    auth = app.state.auth
    auth.import_accounts([('admin', 'fixture-admin-pass'), ('employee', 'fixture-employee-pass')])
    admin = auth.login('admin', 'fixture-admin-pass', False, '127.0.0.1')
    employee = auth.login('employee', 'fixture-employee-pass', False, '127.0.0.1')
    service = app.state.youzan
    service.save(admin['user'], YouzanInput(account='fixture-merchant', password='fixture-merchant-pass', kdt_id='123456'))
    with service.repo.transaction() as state:
        record = next(iter(state['connections'].values()))
        record.update(sid={'secret': service._encrypt(SID), 'expires_at': int(time.time()) + 86400, 'updated_at': int(time.time())},
            csrf={'secret': service._encrypt(CSRF), 'updated_at': int(time.time())},
            shops=[{'id': '123456', 'name': 'Fixture shop'}],
            category_credentials={'secret': service._encrypt(json.dumps({'cookies': {'access_token': TOKEN}, 'origin': 'https://store.youzan.com'}))})
    service._list_shops = AsyncMock(return_value=[{'id': '123456', 'name': 'Fixture shop'}])
    async def crm(record):
        record['_category_session'] = {'cookies': {'access_token': TOKEN}, 'origin': 'https://store.youzan.com'}
        return CSRF
    service._crm_csrf = AsyncMock(side_effect=crm)
    context = ExecutionContext(admin['user']['id'], admin['user']['workspace_id'], tuple(admin['user']['permissions']), 'fixture-conversation', admin['access_token'])
    return app, admin, employee, context, tmp_path


def fake_executable(directory):
    path = directory / 'fixed-fixture-cli'
    path.write_text('''#!/usr/bin/python3
import json,os,sys
context=json.load(sys.stdin)['categories']['youzan']
print(json.dumps({'ok':True,'data':{'owner':os.environ['GALAXY_USER_ID'],'shop_id':context['kdt_id'],
    'reflected_sid':context['sid'],'reflected_token':context['cookies']['access_token']}}))
''')
    path.chmod(0o700)
    return path


def test_credentials_use_private_stdin_never_arguments_env_audit_or_model(fixture, monkeypatch):
    app, admin, _, context, directory = fixture
    dispatcher = app.state.ai.dispatcher
    dispatcher.executable = fake_executable(directory)
    spawn = asyncio.create_subprocess_exec
    captured = []
    async def spy(*args, **kwargs):
        captured.append((args, kwargs))
        return await spawn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spy)
    event = asyncio.run(dispatcher.execute(context, call(['youzan', 'customer-search', '--input', '{"keyword":"fixture-customer"}'])))
    assert event['status'] == 'success'
    assert event['result']['data']['owner'] == admin['user']['id']
    assert event['result']['data']['shop_id'] == '123456'
    assert event['result']['data']['reflected_sid'] == '[已隐藏凭证]'
    assert event['result']['data']['reflected_token'] == '[已隐藏凭证]'
    args, options = captured[0]
    assert options['stdin'] == asyncio.subprocess.PIPE
    assert args[1:3] == ('youzan', 'customer-search')
    for secret in [SID, CSRF, TOKEN, context.session_token, 'fixture-customer']:
        assert secret not in dispatcher.audit.file.read_text()
    for secret in [SID, CSRF, TOKEN, context.session_token]:
        assert secret not in json.dumps(args) + json.dumps(options['env']) + json.dumps(event)
    assert 'SUPPLIER_API_KEY' not in options['env'] and 'PYTHONPATH' not in options['env']


@pytest.mark.parametrize('args', [
    ['youzan', 'delete-customer'], ['youzan', 'customer-search', '--token', SID],
    ['youzan', 'customer-search', '--input', '{"sid":"bad"}'],
    ['youzan', 'customer-search', '--input', '{"user_id":"someone"}'],
    ['youzan', 'customer-search', '--input', '{"url":"https://outside.example"}'],
    ['youzan', 'customer-search', '--input', 'null'],
    ['youzan', 'customer-search', '--input', '{"page":1,"page":2}'],
    ['youzan', 'customer-search', '--input', '{}', '--input', '{}'],
    ['youzan', 'customer-search', '--input', '{"last_trade_start":"2026-02-30"}'],
    ['youzan', 'customer-search', '--format', 'text'],
])
def test_unapproved_business_args_never_start_a_process(fixture, args):
    app, _, _, context, _ = fixture
    dispatcher = app.state.ai.dispatcher
    dispatcher._run = AsyncMock()
    result = asyncio.run(dispatcher.execute(context, call(args)))
    assert result['status'] == 'denied'
    dispatcher._run.assert_not_awaited()


def test_employee_cannot_fall_back_to_admin_youzan_credentials(fixture):
    app, _, employee, _, _ = fixture
    user = employee['user']
    context = ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), 'employee-conversation', employee['access_token'])
    app.state.ai.dispatcher._run = AsyncMock()
    result = asyncio.run(app.state.ai.dispatcher.execute(context, call(['youzan', 'customer-search'])))
    assert result['result']['error']['code'] == 'connection_required'
    app.state.ai.dispatcher._run.assert_not_awaited()
    assert not any(secret in json.dumps(result) for secret in [SID, CSRF, TOKEN])


def test_category_command_permission_is_separate_from_registry(fixture, monkeypatch):
    app, _, _, context, _ = fixture
    monkeypatch.setitem(ROLES, 'admin', [p for p in ROLES['admin'] if p != 'youzan:customer:read'])
    app.state.ai.dispatcher._run = AsyncMock()
    result = asyncio.run(app.state.ai.dispatcher.execute(context, call(['youzan', 'customer-search'])))
    assert result['result']['error']['code'] == 'permission_denied'
    app.state.ai.dispatcher._run.assert_not_awaited()


def test_store_changed_during_request_hides_stale_result(fixture):
    app, _, _, context, _ = fixture
    async def process(*args, **kwargs):
        with app.state.youzan.repo.transaction() as state:
            next(iter(state['connections'].values()))['kdt_id'] = '789'
        return {'ok': True, 'data': {'customers': ['fixture-private-customer']}}
    app.state.ai.dispatcher._run = process
    result = asyncio.run(app.state.ai.dispatcher.execute(context, call(['youzan', 'customer-search'])))
    assert result['result']['error']['code'] == 'connection_changed'
    assert 'fixture-private-customer' not in json.dumps(result)


def test_catalog_route_is_actual_help_and_admin_only(fixture):
    app, admin, employee, _, _ = fixture
    with TestClient(app) as client:
        result = client.get('/api/v1/admin/ai/tools', headers={'Authorization': 'Bearer ' + admin['access_token']})
        assert result.status_code == 200
        data = result.json()['data']
        assert len(data) == 18 + len(DOMAINS) + int(any(t['tool']=='youzan.product-search' for t in data))
        assert {t['tool'] for t in data} | {'youzan.product-search'} == {'feishu.authorized-users', 'time.now', 'youzan.material-category', 'youzan.material-list', 'youzan.material-upload', 'youzan.product-get', 'youzan.product-options', 'youzan.product-update', 'youzan.product-search', 'feishu.search', 'feishu.document-get', 'feishu.folder-list', 'feishu.file-download', 'file.publish', 'excel.create', 'youzan.customer-get', 'youzan.customer-search', 'youzan.customer-count', 'youzan.business-report'} | {'feishu.'+d for d in DOMAINS}
        assert set(data[0]) == {'tool', 'category', 'description'}
        assert len(result.content) < 128000
        assert client.get('/api/v1/admin/ai/tools', headers={'Authorization': 'Bearer ' + employee['access_token']}).status_code == 403
        for secret in [SID, CSRF, TOKEN]:
            assert secret not in result.text


def test_manual_crm_refresh_collects_category_token_encrypted(fixture):
    app, admin, _, _, directory = fixture
    del app.state.youzan._crm_csrf
    requests = []
    def upstream(request):
        requests.append(request)
        if request.url.path == '/v4/shop/enter-shop':
            return httpx.Response(302, headers={'Location': 'https://store.youzan.com/v4/scrm/customer/manage',
                'Set-Cookie': 'access_token=' + TOKEN + '; Domain=.youzan.com; Path=/'})
        return httpx.Response(200, text='window._global={"csrf_token":"' + CSRF + '"}')
    app.state.youzan.transport = httpx.MockTransport(upstream)
    headers = {'Authorization': 'Bearer ' + admin['access_token']}
    with TestClient(app) as client:
        assert client.post('/api/v1/admin/youzan/refresh', headers=headers, json={'kind': 'csrf'}).status_code == 202
        for _ in range(100):
            summary = client.get('/api/v1/admin/youzan', headers=headers)
            if summary.json()['job']['status'] != 'running':
                break
            time.sleep(.01)
        assert summary.json()['job']['code'] == 'csrf_success'
        assert app.state.youzan.tool_credentials(admin['user'])['cookies']['access_token'] == TOKEN
        assert len(requests) == 2
        for secret in [SID, CSRF, TOKEN]:
            assert secret not in summary.text
            assert secret not in (directory / 'youzan.json').read_text()


def test_real_chat_dispatches_business_tool_only_via_galaxy(fixture):
    app, admin, _, _, directory = fixture
    app.state.ai.dispatcher.executable = fake_executable(directory)
    app.state.ai.save_provider(ProviderInput(name='fixture', base_url='https://provider.example/v1', api_key='fixture-provider-key', model='fixture-model'))
    calls = []
    def provider(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert [t['function']['name'] for t in payload['tools']] == ['galaxy_cli']
        if len(calls) == 1:
            msg = {'content': None, 'tool_calls': [call(['youzan', 'customer-search', '--input', '{"page_size":1}'])]}
        else:
            tool_result = json.loads(payload['messages'][-1]['content'])
            assert tool_result['ok'] is True
            assert tool_result['data']['reflected_sid'] == '[已隐藏凭证]'
            assert not any(secret in json.dumps(payload) for secret in [SID, CSRF, TOKEN])
            msg = {'content': '客户查询已完成。'}
        return httpx.Response(200, json={'choices': [{'message': msg}]})
    app.state.ai.transport = httpx.MockTransport(provider)
    with TestClient(app) as client:
        result = client.post('/api/v1/chat/send', headers={'Authorization': 'Bearer ' + admin['access_token']}, json={'message': '搜索有赞客户'})
        assert result.status_code == 200
        event = result.json()['conversation']['messages'][-1]['tool_events'][0]
        assert event['status'] == 'success' and event['command'] == 'galaxy-cli youzan customer-search'
        assert not any(secret in result.text for secret in [SID, CSRF, TOKEN])


def test_full_native_business_result_reaches_model_without_field_loss(fixture):
    app, admin, _, _, directory = fixture
    path = directory / 'compact-fixture-cli'
    path.write_text('''#!/usr/bin/python3
import json,sys
context=json.load(sys.stdin)['categories']['youzan']
print(json.dumps({'code':0,'msg':'success','data':{'total':1,'list':[{'accountId':'c1','name':'测试客户','tradeCount':0,'extra':None,'authInfo':{'isMobileAuth':False}}]},'trace_id':'fixture-trace'}))
''')
    path.chmod(0o700)
    app.state.ai.dispatcher.executable = path
    app.state.ai.save_provider(ProviderInput(name='fixture', base_url='https://provider.example/v1', api_key='fixture-provider-key', model='fixture-model'))
    calls = []
    def provider(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            msg = {'content': None, 'tool_calls': [call(['youzan', 'customer-search'])]}
        else:
            result = json.loads(payload['messages'][-1]['content'])
            assert set(result) == {'code', 'msg', 'data', 'trace_id'}
            assert result['data']['list'][0] == {'accountId':'c1','name':'测试客户','tradeCount':0,'extra':None,'authInfo':{'isMobileAuth':False}}
            msg = {'content': '1 位客户。'}
        return httpx.Response(200, json={'choices': [{'message': msg}]})
    app.state.ai.transport = httpx.MockTransport(provider)
    with TestClient(app) as client:
        response = client.post('/api/v1/chat/send', headers={'Authorization': 'Bearer ' + admin['access_token']}, json={'message': '查询客户'})
        assert response.status_code == 200
        result = response.json()['conversation']['messages'][-1]['tool_events'][0]['result']
        assert set(result) == {'code', 'msg', 'data', 'trace_id'}


def test_customer_get_executes_with_private_category_credentials_and_full_sections(fixture):
    app, admin, _, context, directory = fixture
    executable = directory / 'detail-fixture-cli'
    executable.write_text('''#!/usr/bin/python3
import json,sys
context=json.load(sys.stdin)['categories']['youzan']
args=json.loads(sys.argv[sys.argv.index('--input')+1])
print(json.dumps({'customer':{'code':0,'msg':'ok','data':{'accountId':args['account_id'],'accountType':args['account_type'],'extra':None}},'relations':{'code':0,'data':[]},'behavior':{'code':0,'data':{'allBehavior':[{'amount':0,'active':False}]}}}))
''')
    executable.chmod(0o700)
    app.state.ai.dispatcher.executable = executable
    result = asyncio.run(app.state.ai.dispatcher.execute(context, call(['youzan', 'customer-get', '--input', '{"account_id":"fixture-customer"}'])))
    assert result['status'] == 'success'
    assert result['command'] == 'galaxy-cli youzan customer-get'
    assert set(result['result']) == {'customer', 'relations', 'behavior'}
    assert result['result']['customer']['data'] == {'accountId':'fixture-customer','accountType':1,'extra':None}
    assert result['result']['behavior']['data']['allBehavior'][0] == {'amount':0,'active':False}
    for secret in [SID, CSRF, TOKEN, context.session_token]:
        assert secret not in json.dumps(result) and secret not in app.state.ai.dispatcher.audit.file.read_text()


@pytest.mark.parametrize('arguments', [
    ['youzan', 'customer-get'],
    ['youzan', 'customer-get', '--input', '{"account_id":"c1","url":"https://outside.example"}'],
    ['youzan', 'customer-get', '--input', '{"account_id":"c1","user_id":"other-user"}'],
    ['youzan', 'customer-get', '--input', '{"account_id":"c1","sid":"private"}'],
    ['youzan', 'customer-get', '--input', '{"account_id":"c1","account_type":true}'],
])
def test_customer_get_invalid_args_never_start_process(fixture, arguments):
    app, _, _, context, _ = fixture
    app.state.ai.dispatcher._run = AsyncMock()
    result = asyncio.run(app.state.ai.dispatcher.execute(context, call(arguments)))
    assert result['status'] == 'denied'
    app.state.ai.dispatcher._run.assert_not_awaited()


def test_customer_get_employee_cannot_borrow_admin_connection(fixture):
    app, _, employee, _, _ = fixture
    user = employee['user']
    context = ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), 'fixture', employee['access_token'])
    app.state.ai.dispatcher._run = AsyncMock()
    result = asyncio.run(app.state.ai.dispatcher.execute(context, call(['youzan', 'customer-get', '--input', '{"account_id":"fixture-customer"}'])))
    assert result['result']['error']['code'] == 'connection_required'
    app.state.ai.dispatcher._run.assert_not_awaited()


def test_customer_get_partial_failure_is_reported_without_discarding_data(fixture):
    app, _, _, context, _ = fixture
    response = {'customer': {'code': 0, 'data': {'name':'fixture'}},
                'relations': {'error': {'code':'upstream_error','message':'fixture'}},
                'behavior': {'code': 0, 'data': {'allBehavior':[]}}}
    app.state.ai.dispatcher._run = AsyncMock(return_value=response)
    result = asyncio.run(app.state.ai.dispatcher.execute(context, call(['youzan', 'customer-get', '--input', '{"account_id":"fixture-customer"}'])))
    assert result['status'] == 'error' and result['result'] == response
