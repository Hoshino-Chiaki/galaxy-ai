import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from test_ai import setup, PROVIDER
from xingxu.auth import AuthFailure
from xingxu.ai import ProviderInput
from xingxu.jushuitan.service import JushuitanService, KEY, LOGIN, owner
from xingxu.permission_policy import normalize
from xingxu.tools import ExecutionContext, help_visible, scoped_arguments

SESSION = {'access_token': 'fixture-private-access-token-123456', 'refresh_token': 'fixture-refresh-token-abcdef',
           'uid': 101, 'coId': 202, 'user_name': '测试员工', 'expires_in': 3600}


def test_login_uses_platform_key_and_retains_only_encrypted_personal_state(setup, monkeypatch, tmp_path):
    app, client, admin, employee, _ = setup
    service = app.state.jushuitan
    requests = []
    def upstream(request):
        requests.append(request)
        assert request.url.host == 'account.scm121.com'
        assert request.headers['appcode'] == 'account.scm121.com'
        assert request.headers['source'] == 'ACCOUNT'
        assert request.headers['origin'] == 'https://account.scm121.com'
        assert request.headers['referer'] == 'https://account.scm121.com/user/login'
        assert 'authorization' not in request.headers and 'gwfp' not in request.headers
        if str(request.url).startswith(KEY):
            assert request.url.params['username'] == 'own-account'
            return httpx.Response(200, json={'success': True, 'code': '0000', 'data': '0123456789abcdef'})
        assert str(request.url) == LOGIN
        body = json.loads(request.content)
        assert body['fromBu'] == 'jst-gyl-pc'
        decryptor = Cipher(algorithms.AES(b'0123456789abcdef'), modes.ECB()).decryptor()
        clear = decryptor.update(bytes.fromhex(body['password'])) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        assert unpadder.update(clear) + unpadder.finalize() == b'fixture-password'
        return httpx.Response(200, json={'success': True, 'code': '0000', 'data': SESSION})
    service.transport = httpx.MockTransport(upstream)
    result = client.post('/api/v1/jushuitan/login', headers=employee, json={'account': 'own-account', 'password': 'fixture-password'})
    assert result.status_code == 200 and result.json()['connected']
    assert len(requests) == 2
    summary = result.text
    assert all(secret not in summary for secret in (SESSION['access_token'], SESSION['refresh_token'], 'fixture-password'))
    other = client.get('/api/v1/jushuitan', headers=admin).json()
    assert other['account'] == '' and not other['connected']
    assert all(secret not in service.repo.file.read_text() for secret in (SESSION['access_token'], SESSION['refresh_token'], 'fixture-password'))


def test_each_account_center_login_starts_with_empty_cookies_and_only_uses_new_server_cookie(setup):
    app, _, _, _, _ = setup
    requests = []
    key_count = 0
    def upstream(request):
        nonlocal key_count
        requests.append(request)
        if request.url.path == '/api/auth/open/getEncryptKey':
            assert 'cookie' not in request.headers
            key_count += 1
            return httpx.Response(200, headers={'Set-Cookie': f'acw_tc=new-{key_count}; Secure; Path=/'},
                json={'success': True, 'code': '0000', 'data': '0123456789abcdef'})
        assert request.headers['cookie'] == f'acw_tc=new-{key_count}'
        assert set(json.loads(request.content)) == {'loginName', 'password', 'fromBu'}
        return httpx.Response(200, json={'success': True, 'code': '0000', 'data': SESSION})
    service = app.state.jushuitan
    service.transport = httpx.MockTransport(upstream)
    async def scenario():
        for _ in range(2):assert (await service.login('own-account', 'fixture-password'))['access_token']
    asyncio.run(scenario())
    assert len(requests) == 4 and key_count == 2


def test_null_password_is_truly_empty_omission_reuses_saved_and_failure_preserves_session(setup):
    app, client, _, employee, _ = setup
    service = app.state.jushuitan
    runner = AsyncMock(return_value=SESSION)
    service.login_runner = runner
    assert client.post('/api/v1/jushuitan/login', headers=employee, json={'account': 'same', 'password': 'saved-secret'}).status_code == 200
    assert client.post('/api/v1/jushuitan/login', headers=employee, json={'account': 'same'}).status_code == 200
    assert runner.await_args.args == ('same', 'saved-secret')
    assert client.post('/api/v1/jushuitan/login', headers=employee, json={'account': 'same', 'password': None}).status_code == 200
    assert runner.await_args.args == ('same', '')
    identity = app.state.auth.identity(employee['Authorization'][7:])['user']
    saved = service.record(identity)
    runner.side_effect = AuthFailure('jushuitan_verification_required', 'verification needed', 409)
    assert client.post('/api/v1/jushuitan/login', headers=employee, json={'account': 'other', 'password': 'bad'}).status_code == 409
    assert service.record(identity) == saved


def test_account_file_mode_stores_connection_under_own_channel_directory(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    service = JushuitanService(tmp_path, Fernet(Fernet.generate_key()), login_runner=AsyncMock(return_value=SESSION))
    user = {'id': 'a', 'workspace_id': 'w', 'username': 'alice', 'permissions': ['jushuitan:order:read']}
    asyncio.run(service.connect(user, 'own', 'secret'))
    connection = tmp_path / 'accounts/alice/channels/jushuitan/connection.json'
    assert connection.is_file() and connection.stat().st_mode & 0o777 == 0o600
    assert SESSION['access_token'] not in connection.read_text()
    assert service.credentials(user)['uid'] == 101
    with pytest.raises(AuthFailure):service.credentials({**user, 'id': 'b', 'username': 'bob'})
    with pytest.raises(AuthFailure):service.credentials({**user, 'permissions': []})


def test_gateway_dispatches_current_user_credentials_and_hides_denied_tools(setup):
    app, _, _, employee, _ = setup
    ai = app.state.ai
    user = app.state.auth.identity(employee['Authorization'][7:])['user']
    service = app.state.jushuitan
    service.login_runner = AsyncMock(return_value=SESSION)
    async def scenario():
        await service.connect(user, 'own', '')
        ai.dispatcher._run = AsyncMock(return_value={'count': {'success': True, 'total': 1}})
        context = ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), 'c', employee['Authorization'][7:])
        call = {'id': 'test', 'type': 'function', 'function': {'name': 'galaxy_cli', 'arguments': json.dumps({'args': ['jushuitan', 'order-search', '--input', '{"online_order_nos":["fixture-order"]}']})}}
        result = await ai.dispatcher.execute(context, call)
        assert result['status'] == 'success'
        assert ai.dispatcher._run.await_args.kwargs['credentials'] == {key: SESSION[key] for key in ('access_token', 'uid', 'coId')}
        assert not service.operation_guard(user).locked()
    asyncio.run(scenario())
    assert help_visible('jushuitan.order-search', ['jushuitan:order:read'])
    assert not help_visible('jushuitan.order-search', ['guanjia:configure'])
    assert 'jushuitan:configure' in normalize(['jushuitan:order:read'])
    assert 'jushuitan:configure' not in normalize(['jushuitan:configure'])
    with pytest.raises(Exception):scoped_arguments(json.dumps({'args': ['jushuitan', 'order-search', '--input', '{"access_token":"wrong"}']}))


@pytest.mark.parametrize('code', ['20001', '20002', '1007', '3333'])
def test_verification_is_reported_without_automatic_retries(setup, code):
    app, client, _, employee, _ = setup
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={'code': code, 'success': False, 'data': None})
    app.state.jushuitan.transport = httpx.MockTransport(upstream)
    result = client.post('/api/v1/jushuitan/login', headers=employee, json={'account': 'own', 'password': ''})
    assert result.status_code == 409 and result.json()['error']['code'] == 'jushuitan_verification_required'
    assert len(calls) == 1


def test_routes_require_login_and_do_not_accept_foreign_identity(setup):
    _, client, _, employee, _ = setup
    assert client.get('/api/v1/jushuitan').status_code == 401
    assert client.post('/api/v1/jushuitan/login', headers=employee, json={'account': 'own', 'password': '', 'user_id': 'other'}).status_code == 422


def test_login_error_retains_platform_reason_without_echoing_credentials():
    with pytest.raises(AuthFailure) as caught:
        JushuitanService.check({'success': False, 'code': '20003',
            'message': '账号或密码错误 account-fixture secret-password-fixture abcdefghijklmnopqrstuvwxyz123456'},
            secrets=('account-fixture', 'secret-password-fixture'))
    assert '20003' in caught.value.message and '账号或密码错误' in caught.value.message
    assert 'account-fixture' not in caught.value.message and 'secret-password-fixture' not in caught.value.message
    assert 'abcdefghijklmnopqrstuvwxyz123456' not in caught.value.message


def test_wrong_password_message_takes_precedence_over_check_code_hint():
    with pytest.raises(AuthFailure) as caught:
        JushuitanService.check({'success': False, 'code': '20001', 'message': '用户名或密码错误！'})
    assert caught.value.code == 'jushuitan_login_failed'
    assert caught.value.status == 400
    assert '账号或密码不正确' in caught.value.message and '20001' in caught.value.message


def test_complete_business_response_is_cached_only_under_calling_account(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from xingxu.files import FileService
    from xingxu.tool_cache import cache_result
    monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    user = {'id': 'a', 'workspace_id': 'w', 'username': 'alice', 'permissions': ['jushuitan:order:read']}
    files = FileService(tmp_path)
    dispatcher = SimpleNamespace(directory=tmp_path, files=files, _check_command=lambda *_args: user)
    context = ExecutionContext('a', 'w', tuple(user['permissions']), 'conversation', 'synthetic')
    raw = {'query': {'data': [{'soId': 'fixture', 'line_items': [{'quantity': 3}]}], 'total': -1},
           'count': {'data': [], 'total': 1}, 'page': 1, 'page_size': 50, 'has_more': False}
    receipt = asyncio.run(cache_result(dispatcher, context, 'jushuitan.order-search',
        ['jushuitan', 'order-search', '--input', '{}', '--format', 'json'], None, raw, False))
    root = tmp_path / 'accounts/alice/cache'
    assert receipt['sandbox_path'].startswith('/account-cache/jushuitan/order-search/')
    assert json.loads((root / receipt['path']).read_text()) == raw
    assert receipt['cached'] and 'query' not in receipt
    assert not (tmp_path / 'accounts/bob/cache').exists()
