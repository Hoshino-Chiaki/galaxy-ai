import asyncio
import copy
import json
import uuid
import httpx
import pytest
from xingxu.app import create_app
from xingxu.auth import AuthFailure
from xingxu.feishu.routes import AppInput
from xingxu.feishu.service import owner_key
from xingxu.tool_context import ExecutionContext


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    monkeypatch.setenv('GALAXY_AI_RUNTIME', 'langgraph')
    replies = []
    def transport(request):
        if request.url.path.endswith('/oauth/v3/token'):
            return httpx.Response(200, json={'access_token': 'fixture-bot-secret', 'expires_in': 7200})
        if request.url.path.endswith('/reply'):
            replies.append((str(request.url), json.loads(request.content)))
            return httpx.Response(200, json={'code': 0})
        raise AssertionError('Unexpected upstream')
    app = create_app(tmp_path, feishu_transport=httpx.MockTransport(transport))
    auth, ai, feishu, bot = app.state.auth, app.state.ai, app.state.feishu, app.state.feishu_bot
    auth.import_accounts([('owner', 'fixture-password'), ('employee', 'fixture-password')])
    logged = [auth.login(n, 'fixture-password', False, 'test') for n in ('owner', 'employee')]
    users = [r['user'] for r in logged]
    feishu.save_app(users[0], AppInput(app_id='cli_fixture', app_secret='fixture-app-secret'))
    application = bot.apps()[users[0]['workspace_id']]
    for user in users:
        with feishu.repo.transaction() as state:
            state['connections'][owner_key(user)] = {'revision': application['revision'], 'grant_id': str(uuid.uuid4()),
                'token': feishu._encrypt({'access_token': 'fixture-user-secret', 'expires_at': 9999999999, 'refresh_expires_at': 9999999999}),
                'identity': {'name': user['username'], 'open_id': 'ou_' + user['username'], 'tenant_key': 'fixture-tenant'}}
    calls, conversations = [], {}
    async def send(user, body, **kwargs):
        current = auth.identity(kwargs['session_token'])['user']
        assert current['id'] == user['id']
        calls.append((user, body, kwargs))
        context = ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), str(uuid.uuid4()), kwargs['session_token'])
        result = await ai.dispatcher.execute(context, {'id': 'self-check', 'type': 'function', 'function': {
            'name': 'galaxy_cli', 'arguments': json.dumps({'args': ['account', 'me']})}})
        assert result['status'] == 'success' and result['result']['account'] == user['username']
        identifier = str(body.conversation_id or uuid.uuid4())
        conversations[identifier] = user['id']
        return {'conversation': {'id': identifier, 'messages': [{'content': '当前账号：' + result['result']['account']}]}}
    def conversation(user, identifier):
        assert conversations[str(identifier)] == user['id']
        return {}
    ai.send, ai.conversation = send, conversation
    return app, logged, application, calls, replies


def event(app, account='employee', message_id=None, **kwargs):
    return {'app_id': app['app_id'], 'tenant_key': 'fixture-tenant', 'open_id': 'ou_' + account,
        'sender_type': 'user', 'message_id': message_id or 'om_' + str(uuid.uuid4()), 'chat_id': 'oc_' + account,
        'chat_type': 'p2p', 'message_type': 'text', 'content': json.dumps({'text': '我是谁？渠道是否已经登录？'}), **kwargs}


async def drain(bot):
    while bot.jobs:
        await asyncio.gather(*list(bot.jobs.values()))


def test_feishu_employee_uses_existing_ai_and_own_account_tool(env):
    app, logged, config, calls, replies = env
    bot = app.state.feishu_bot
    async def run():
        incoming = event(config)
        assert bot.accept('default', config, incoming)
        assert bot.accept('default', config, incoming)
        await drain(bot)
        assert len(calls) == 1 and calls[0][0]['id'] == logged[1]['user']['id']
        assert not calls[0][1].interactive_questions
        assert len(replies) == 1 and '当前账号：employee' in replies[0][1]['content']
        with pytest.raises(AuthFailure):
            app.state.auth.identity(calls[0][2]['session_token'])
        assert bot.accept('default', config, event(config))
        assert bot.accept('default', config, event(config, 'owner'))
        await drain(bot)
        assert len(calls) == 3
        assert calls[1][1].conversation_id is not None
        assert calls[2][1].conversation_id is None
        with bot.store.transaction('default', config['app_id'], False) as state:
            assert len(state['conversations']) == 2
            assert all(m['status'] == 'completed' for m in state['messages'].values())
        await bot.close(); await app.state.clawbot.close()
    asyncio.run(run())


def test_unbound_duplicate_and_cross_tenant_identity_do_not_use_admin(env):
    app, logged, config, calls, replies = env
    bot, feishu = app.state.feishu_bot, app.state.feishu
    async def run():
        assert not bot.accept('default', config, event(config, app_id='cli_forged'))
        assert bot.accept('default', config, event(config, chat_type='group'))
        assert bot.accept('default', config, event(config, 'unbound'))
        assert bot.accept('default', config, event(config, tenant_key='wrong-tenant'))
        await drain(bot)
        assert calls == []
        with feishu.repo.transaction() as state:
            state['connections'][owner_key(logged[0]['user'])]['identity']['open_id'] = 'ou_employee'
        assert bot.accept('default', config, event(config))
        await drain(bot)
        assert calls == [] and len(replies) == 3
        assert all('绑定' in body['content'] for _, body in replies)
        await bot.close(); await app.state.clawbot.close()
    asyncio.run(run())


def test_revoked_permission_and_disconnected_oauth_block_private_results(env):
    app, logged, config, calls, replies = env
    bot, auth = app.state.feishu_bot, app.state.auth
    async def run():
        original = auth._public
        auth._public = lambda raw: {**original(raw), 'permissions': [p for p in original(raw)['permissions'] if p != 'feishu:chat']}
        bot.accept('default', config, event(config))
        await drain(bot)
        assert not calls
        auth._public = original
        original_send = app.state.ai.send
        async def disconnect_during_send(user, body, **kwargs):
            result = await original_send(user, body, **kwargs)
            with app.state.feishu.repo.transaction() as state:
                state['connections'].pop(owner_key(user))
            return result
        app.state.ai.send = disconnect_during_send
        before = len(replies)
        bot.accept('default', config, event(config))
        await drain(bot)
        assert len(calls) == 1 and len(replies) == before
        with bot.store.transaction('default', config['app_id'], False) as state:
            assert all(m['status'] == 'failed' for m in state['messages'].values())
        await bot.close(); await app.state.clawbot.close()
    asyncio.run(run())


def test_account_tool_rejects_identity_override_and_never_returns_credentials(env):
    app, logged, config, _, _ = env
    async def run():
        for entry in logged:
            user = entry['user']
            context = ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), str(uuid.uuid4()), entry['access_token'])
            result = await app.state.ai.dispatcher.execute(context, {'id': 'self', 'type': 'function', 'function': {
                'name': 'galaxy_cli', 'arguments': json.dumps({'args': ['account', 'me']})}})
            assert result['result']['account'] == user['username']
            assert result['result']['channels']['feishu']['account'] == user['username']
            assert 'secret' not in json.dumps(result['result']) and 'token' not in json.dumps(result['result'])
            denied = await app.state.ai.dispatcher.execute(context, {'id': 'override', 'type': 'function', 'function': {
                'name': 'galaxy_cli', 'arguments': json.dumps({'args': ['account', 'me', '--input', '{"user_id":"owner"}']})}})
            assert denied['status'] == 'denied'
        await app.state.feishu_bot.close(); await app.state.clawbot.close()
    asyncio.run(run())
