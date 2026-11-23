import asyncio
import json
import uuid
from types import SimpleNamespace
import httpx
import pytest
from cryptography.fernet import Fernet
from xingxu.auth import AuthFailure, AuthService
from xingxu.store import JsonRepository
from xingxu.clawbot import ClawbotService
from xingxu.clawbot.protocol import origin


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    auth = AuthService(JsonRepository(tmp_path))
    auth.import_accounts([('owner', 'fixture-password'), ('other', 'fixture-password')])
    users = [auth.login(name, 'fixture-password', False, 'test')['user'] for name in ('owner', 'other')]
    calls, sends = [], []
    def upstream(request):
        value = json.loads(request.content) if request.content else None
        calls.append((request.url.path, value, dict(request.headers)))
        if request.url.path.endswith('get_bot_qrcode'):
            return httpx.Response(200, json={'qrcode': 'fixture-qr', 'qrcode_img_content': 'https://example.com/fixture'})
        if request.url.path.endswith('get_qrcode_status'):
            return httpx.Response(200, json=credentials())
        return httpx.Response(200, json={'ret': 0})
    ai = SimpleNamespace(cipher=Fernet(Fernet.generate_key()))
    async def send(user, body, **kwargs):
        assert auth.identity(kwargs['session_token'])['user']['id'] == user['id']
        sends.append((user, body, kwargs))
        return {'conversation': {'id': str(body.conversation_id or uuid.uuid4()), 'messages': [{'content': '已读取当前账号的数据。'}]}}
    ai.send = send
    service = ClawbotService(tmp_path, auth, ai, httpx.MockTransport(upstream))
    return service, auth, users, calls, sends


def credentials(**kwargs):
    return {'status': 'confirmed', 'bot_token': 'fixture-bot-secret', 'ilink_bot_id': 'fixture-bot',
        'ilink_user_id': 'fixture-peer', 'baseurl': 'https://ilinkai.weixin.qq.com', **kwargs}


def message(**kwargs):
    return {'message_id': 'message-1', 'from_user_id': 'fixture-peer', 'message_type': 1,
        'context_token': 'fixture-context-secret', 'item_list': [{'type': 1, 'text_item': {'text': '查询我自己的数据'}}], **kwargs}


def test_qr_binding_is_owner_scoped_and_encrypted(env):
    service, _, (owner, other), calls, _ = env
    async def run():
        started = await service.connect(owner)
        with pytest.raises(AuthFailure) as failure:
            service.poll(other, started['id'])
        assert failure.value.status == 404
        await service.pending[started['id']]['task']
        assert service.poll(owner, started['id'])['status'] == 'confirmed'
        assert service.summary(owner)['connected'] and not service.summary(other)['connected']
        payload = (service.store.base(owner) / 'connection.json').read_text()
        assert 'fixture-bot-secret' not in payload
        assert 'fixture-bot-secret' not in json.dumps(service.summary(owner))
        assert calls[0][1]['local_token_list'] == []
        await service.close()
    asyncio.run(run())


def test_only_scanning_owner_can_chat_and_replays_are_ignored(env):
    service, auth, (owner, _), calls, sends = env
    async def run():
        await service.bind(owner, credentials())
        record = service.record(owner)
        for invalid in (message(from_user_id='someone-else'), message(group_id='group'), message(context_token=''), message(message_type=2)):
            await service.handle(owner, record, invalid)
        assert sends == []
        await service.handle(owner, record, message())
        await service.handle(owner, record, message())
        assert len(sends) == 1
        assert not sends[0][1].interactive_questions
        outgoing = [c for c in calls if c[0].endswith('sendmessage')]
        assert len(outgoing) == 1
        assert outgoing[0][1]['msg']['to_user_id'] == 'fixture-peer'
        assert outgoing[0][1]['msg']['context_token'] == 'fixture-context-secret'
        with pytest.raises(AuthFailure):
            auth.identity(sends[0][2]['session_token'])
        await service.handle(owner, record, message(message_id='message-2'))
        assert sends[1][1].conversation_id is not None
        assert len(sends) == 2
        await service.close()
    asyncio.run(run())


def test_duplicate_bot_cannot_cross_employee_accounts(env):
    service, _, (owner, other), _, _ = env
    async def run():
        await service.bind(owner, credentials())
        with pytest.raises(AuthFailure) as failure:
            await service.bind(other, credentials())
        assert failure.value.code == 'clawbot_bound'
        assert not service.summary(other)['connected']
        await service.disconnect(owner)
        assert not service.record(owner)
        await service.bind(other, credentials())
        assert service.summary(other)['connected']
        await service.close()
    asyncio.run(run())


def test_security_version_change_prevents_ai_execution(env):
    service, auth, (owner, _), _, sends = env
    async def run():
        await service.bind(owner, credentials())
        record = service.record(owner)
        with auth.repository.transaction() as state:
            state['users'][owner['id']]['auth_version'] += 1
        with pytest.raises(AuthFailure) as failure:
            await service.handle(owner, record, message())
        assert failure.value.code == 'clawbot_expired' and not sends
        await service.close()
    asyncio.run(run())


def test_current_permission_revocation_prevents_execution(env):
    service, auth, (owner, _), _, sends = env
    async def run():
        await service.bind(owner, credentials())
        record = service.record(owner)
        original = auth._public
        auth._public = lambda user: {**original(user), 'permissions': ['ai:chat']}
        with pytest.raises(AuthFailure) as failure:
            await service.handle(owner, record, message())
        assert failure.value.status == 403 and not sends
        await service.close()
    asyncio.run(run())


def test_redirects_and_new_token_not_accepted_without_full_identity(env):
    service, _, (owner, _), _, _ = env
    for host in ('http://ilinkai.weixin.qq.com', 'https://127.0.0.1', 'https://ilinkai.weixin.qq.com.evil.test',
            'https://evil.test@ilinkai.weixin.qq.com', 'https://ilinkai.weixin.qq.com:8443', 'https://ilinkai.weixin.qq.com/path'):
        with pytest.raises(AuthFailure):
            origin(host)
    async def run():
        await service.bind(owner, credentials())
        with pytest.raises(AuthFailure):
            await service.bind(owner, credentials(ilink_user_id=''))
        assert service.record(owner)['token'] == 'fixture-bot-secret'
        await service.close()
    asyncio.run(run())
