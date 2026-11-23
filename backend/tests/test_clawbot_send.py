import asyncio
import base64
import json
import uuid
from types import SimpleNamespace
import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from galaxy_cli.registry import load_registry, ToolError
from galaxy_cli.tools.clawbot.send import normalize
from xingxu.auth import AuthFailure, AuthService
from xingxu.clawbot import ClawbotService
from xingxu.store import JsonRepository
from xingxu.tools import GalaxyDispatcher
from xingxu.tool_context import ExecutionContext


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    auth = AuthService(JsonRepository(tmp_path))
    auth.import_accounts([('owner', 'fixture-password'), ('other', 'fixture-password')])
    logged = [auth.login(name, 'fixture-password', False, 'test') for name in ('owner', 'other')]
    calls, replies = [], []
    def upstream(request):
        calls.append(request)
        if request.url.path.endswith('getuploadurl'):
            return httpx.Response(200, json={'upload_param': 'fixture-upload'})
        if request.url.host == 'novac2c.cdn.weixin.qq.com':
            return httpx.Response(200, headers={'x-encrypted-param': 'fixture-download'})
        if replies:
            value = replies.pop(0)
            if isinstance(value, Exception):
                raise value
            return httpx.Response(200, json=value)
        return httpx.Response(200, json={'ret': 0})
    dispatcher = GalaxyDispatcher(tmp_path, authorize=auth.identity)
    ai = SimpleNamespace(cipher=Fernet(Fernet.generate_key()), dispatcher=dispatcher)
    service = ClawbotService(tmp_path, auth, ai, httpx.MockTransport(upstream))
    dispatcher.clawbot_provider = service
    return service, auth, logged, dispatcher, calls, replies


async def bind(service, user):
    await service.bind(user, {'bot_token': 'fixture-bot-secret', 'ilink_bot_id': user['id'] + '@bot',
        'ilink_user_id': user['id'] + '@peer', 'baseurl': 'https://ilinkai.weixin.qq.com'})
    with service.store.transaction(user) as record:
        record['context_token'] = 'fixture-context-secret'


def call(body):
    return {'id': str(uuid.uuid4()), 'type': 'function', 'function': {'name': 'galaxy_cli',
        'arguments': json.dumps({'args': ['clawbot', 'send', '--input', json.dumps(body)]})}}


def test_catalog_validation_and_schedule_description():
    registry = load_registry()
    assert registry.get('clawbot.send').category == 'clawbot'
    assert 'clawbot.send' in registry.get('schedule.create').description
    for body in ({}, {'text': 'hello'}, {'text': 'hello', 'request_key': 'a', 'to': 'someone'},
            {'path': '../connection.json', 'request_key': 'a'}, {'path': '/etc/passwd', 'request_key': 'a'}):
        with pytest.raises(ToolError):
            normalize(body)


def test_scheduled_tool_sends_file_and_text_with_current_owner_permissions(env):
    service, _, logged, dispatcher, calls, _ = env
    user = logged[0]['user']
    async def run():
        await bind(service, user)
        workspace = dispatcher.files.workspace(user)
        (workspace / 'report.xlsx').write_bytes(b'fixture-excel-data')
        context = ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), str(uuid.uuid4()), logged[0]['access_token'], background_run=True)
        body = {'text': '今天的经营日报', 'path': 'report.xlsx', 'request_key': 'delivery-test'}
        first = await dispatcher.execute(context, call(body))
        assert first['status'] == 'success' and first['result']['status'] == 'sent'
        assert [p['type'] for p in first['result']['parts']] == ['text', 'file']
        assert first['result']['parts'][1]['filename'] == 'report.xlsx'
        assert first['result']['parts'][1]['size'] == 18
        metadata = json.loads(next(r for r in calls if r.url.path.endswith('getuploadurl')).content)
        cdn = next(r for r in calls if r.url.host == 'novac2c.cdn.weixin.qq.com')
        assert 'authorization' not in cdn.headers
        key = bytes.fromhex(metadata['aeskey'])
        decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        padded = decryptor.update(cdn.content) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        assert unpadder.update(padded) + unpadder.finalize() == b'fixture-excel-data'
        outbound = [json.loads(r.content)['msg'] for r in calls if r.url.path.endswith('sendmessage')]
        assert len(outbound) == 2 and all(m['to_user_id'] == user['id'] + '@peer' for m in outbound)
        media = outbound[1]['item_list'][0]['file_item']['media']
        assert base64.b64decode(media['aes_key']).decode() == metadata['aeskey']
        assert 'fixture-context-secret' not in json.dumps(first['result'])
        second = await dispatcher.execute(context, call(body))
        assert second['result']['delivery_id'] == first['result']['delivery_id']
        assert len([r for r in calls if r.url.path.endswith('sendmessage')]) == 2
        await service.close()
    asyncio.run(run())


def test_owner_paths_and_missing_file_permission_are_denied(env):
    service, auth, logged, dispatcher, calls, _ = env
    owner, other = [entry['user'] for entry in logged]
    async def run():
        await bind(service, owner)
        (dispatcher.files.workspace(other) / 'secret.xlsx').write_bytes(b'other account data')
        with pytest.raises(AuthFailure):
            await service.send(owner, {'path': 'secret.xlsx', 'request_key': 'test'}, lambda: None)
        original = auth._public
        auth._public = lambda user: {**original(user), 'permissions': [p for p in original(user)['permissions'] if p != 'file:read']}
        with pytest.raises(AuthFailure):
            await service.send(owner, {'path': 'secret.xlsx', 'request_key': 'test'}, lambda: None)
        assert not calls
        await service.close()
    asyncio.run(run())


def test_ambiguous_network_failure_is_not_replayed_and_is_a_tool_error(env):
    service, _, logged, dispatcher, calls, replies = env
    user = logged[0]['user']
    async def run():
        await bind(service, user)
        replies.append(httpx.ReadTimeout('fixture'))
        context = ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), str(uuid.uuid4()), logged[0]['access_token'])
        body = {'text': 'test', 'request_key': 'test-timeout'}
        result = await dispatcher.execute(context, call(body))
        assert result['status'] == 'error'
        assert result['result']['status'] == 'unknown'
        result2 = await dispatcher.execute(context, call(body))
        assert result2['result']['status'] == 'unknown'
        assert len(calls) == 1
        with pytest.raises(AuthFailure):
            await service.send(user, {**body, 'text': 'changed'}, lambda: None)
        await service.close()
    asyncio.run(run())


def test_partial_delivery_and_unconnected_account_do_not_report_success(env):
    service, _, logged, dispatcher, _, replies = env
    user, other = [entry['user'] for entry in logged]
    async def run():
        with pytest.raises(AuthFailure):
            await service.send(other, {'text': 'test', 'request_key': 'x'}, lambda: None)
        await bind(service, user)
        replies.extend([{'ret': 0}, {'ret': 1}])
        result = await service.send(user, {'text': 'x' * 4000, 'request_key': 'partial'}, lambda: None)
        assert result['status'] == 'partial'
        assert [p['status'] for p in result['parts']] == ['sent', 'failed']
        await service.close()
    asyncio.run(run())
