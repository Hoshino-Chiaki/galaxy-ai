import asyncio
import concurrent.futures
import copy
import json
import os
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from xingxu.account_storage import Accounts, AccountRepository, owner_key, scoped
from xingxu.app import create_app
from xingxu.auth import AuthService
from xingxu.store import JsonRepository
from xingxu.youzan.schemas import YouzanInput
from xingxu.migrate_accounts import migrate, rollback, legacy_repository


@pytest.fixture
def users(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    auth = AuthService(JsonRepository(tmp_path))
    auth.import_accounts([('chiaki', 'fixture-password-1'), ('yuzhuo', 'fixture-password-2')])
    return [auth.login(name, password, False, 'fixture')['user'] for name, password in
            [('chiaki', 'fixture-password-1'), ('yuzhuo', 'fixture-password-2')]]


def conversation(user, identifier=None):
    return {'id': identifier or str(uuid.uuid4()), 'user_id': user['id'], 'workspace_id': user['workspace_id'],
            'title': 'fixture', 'messages': [], 'created_at': 1, 'updated_at': 1}


def test_named_directories_bind_stable_identity_and_single_browser(users, tmp_path):
    accounts = Accounts(tmp_path)
    first, second = users
    a, b = accounts.ensure(first), accounts.ensure(second)
    assert a.name == 'chiaki' and b.name == 'yuzhuo' and a != b
    assert accounts.browser(first) == accounts.browser(first) == a / 'browser'
    assert accounts.ensure({'id': first['id'], 'workspace_id': first['workspace_id']}) == a
    assert accounts.ensure({**first, 'username': 'renamed'}) == a
    assert json.loads((a / 'account.json').read_text())['username'] == 'renamed'
    assert a.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('name', ['../../outside', '.', '..', 'a/b', 'a\\b', 'same%2Fname'])
def test_account_names_never_escape_root(users, tmp_path, name):
    account = Accounts(tmp_path).ensure({**users[0], 'username': name})
    assert account.parent == tmp_path / 'accounts'
    assert account.resolve().parent == (tmp_path / 'accounts').resolve()


def test_chat_append_reload_delete_and_partial_write_recovery(users, tmp_path):
    user = users[0]
    repo = JsonRepository(tmp_path, 'chats', ('conversations',))
    assert isinstance(repo, AccountRepository)
    chat = conversation(user)
    with scoped(repo, user) as state:
        state['conversations'][chat['id']] = copy.deepcopy(chat)
    path = Accounts(tmp_path).path(user, 'chats') / (chat['id'] + '.jsonl')
    prefix = path.read_bytes()
    with scoped(repo, user) as state:
        state['conversations'][chat['id']]['messages'].append({'role': 'user', 'content': 'hello'})
    assert path.read_bytes().startswith(prefix)
    assert json.loads(path.read_text().splitlines()[-1])['append_messages'][0]['content'] == 'hello'
    with path.open('ab') as stream:
        stream.write(b'{"schema_version":1,"event":"up')
    with scoped(repo, user) as state:
        assert len(state['conversations'][chat['id']]['messages']) == 1
        state['conversations'][chat['id']]['title'] = 'recovered'
    restored = JsonRepository(tmp_path, 'chats', ('conversations',))
    with scoped(restored, user) as state:
        assert state['conversations'][chat['id']]['title'] == 'recovered'
        del state['conversations'][chat['id']]
    with scoped(restored, user, write=False) as state:
        assert not state['conversations']
    assert not path.exists()
    assert not (tmp_path / 'chats.json').exists()


def test_chat_foreign_owner_rejected_and_replacement_roundtrip(users, tmp_path):
    first, second = users
    repo = JsonRepository(tmp_path, 'chats', ('conversations',))
    chat = conversation(first)
    chat['messages'] = [{'role': 'assistant', 'content': 'initial', 'tool_events': [{'id': 'fixture'}]}]
    with scoped(repo, first) as state:
        state['conversations'][chat['id']] = chat
    with scoped(repo, second, write=False) as state:
        assert not state['conversations']
    with pytest.raises(RuntimeError):
        with scoped(repo, second) as state:
            state['conversations'][chat['id']] = chat
    with scoped(repo, first) as state:
        state['conversations'][chat['id']]['messages'][0]['content'] = 'edited'
        state['conversations'][chat['id']]['context_state'] = {'summary': 'kept'}
    with scoped(repo, first, write=False) as state:
        saved = state['conversations'][chat['id']]
        assert saved['messages'][0]['content'] == 'edited'
        assert saved['context_state']['summary'] == 'kept'


def test_concurrent_chat_updates_not_lost(users, tmp_path):
    user = users[0]
    chat = conversation(user)
    repo = JsonRepository(tmp_path, 'chats', ('conversations',))
    with scoped(repo, user) as state:
        state['conversations'][chat['id']] = chat
    def update(number):
        separate = JsonRepository(tmp_path, 'chats', ('conversations',))
        with scoped(separate, user, identifier=chat['id']) as state:
            state['conversations'][chat['id']]['messages'].append({'content': str(number)})
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(update, range(20)))
    with scoped(repo, user, write=False) as state:
        assert len(state['conversations'][chat['id']]['messages']) == 20


def test_symlink_and_hardlink_rejected(users, tmp_path):
    user = users[0]
    repo = JsonRepository(tmp_path, 'chats', ('conversations',))
    directory = Accounts(tmp_path).path(user, 'chats')
    outside = tmp_path / 'outside'
    outside.write_text('sensitive')
    link = directory / 'fixture.jsonl'
    link.symlink_to(outside)
    with pytest.raises(OSError):
        with scoped(repo, user, write=False):
            pass
    link.unlink()
    os.link(outside, link)
    with pytest.raises(RuntimeError):
        with scoped(repo, user, write=False):
            pass
    assert outside.read_text() == 'sensitive'


@pytest.mark.parametrize('name', ['youzan', 'feishu', 'guanjia', 'jushuitan'])
def test_personal_channels_are_per_account_and_company_apps_stay_shared(users, tmp_path, name):
    first, second = users
    fields = ('apps', 'connections') if name == 'feishu' else ('connections',)
    repo = JsonRepository(tmp_path, name, fields)
    with repo.transaction() as state:
        state['connections'][owner_key(first)] = {'password': 'fixture-encrypted-value'}
        state['connections'][owner_key(second)] = {'password': 'fixture-other-encrypted-value'}
        if name == 'feishu':
            state['apps'][first['workspace_id']] = {'secret': 'fixture-app'}
    a = json.loads((tmp_path / 'accounts/chiaki/channels' / name / 'connection.json').read_text())
    b = json.loads((tmp_path / 'accounts/yuzhuo/channels' / name / 'connection.json').read_text())
    assert list(a['connections']) == [owner_key(first)]
    assert list(b['connections']) == [owner_key(second)]
    if name == 'feishu':
        with repo.transaction(write=False) as state:
            assert state['apps'][first['workspace_id']]['secret'] == 'fixture-app'


def test_existing_profile_not_rotated_on_save_and_runner_receives_fixed_profile(users, tmp_path):
    first = users[0]
    seen = []
    async def login(account, password, profile, progress):
        seen.append(profile)
        return {'sid': 'fixture-sid', 'expires_at': 9999999999}
    from cryptography.fernet import Fernet
    from xingxu.youzan.service import YouzanService
    service = YouzanService(tmp_path, Fernet(Fernet.generate_key()), login_runner=login)
    service.save(first, YouzanInput(account='fixture-one', password='fixture-password'))
    key = owner_key(first)
    async def run():
        with service.repo.transaction(write=False) as state:
            record = copy.deepcopy(state['connections'][key])
        await service._login(key, record, None)
        service._update(key, record['revision'], code='sid_ready', status='success')
    asyncio.run(run())
    service.save(first, YouzanInput(account='fixture-one', password='fixture-new-password'))
    asyncio.run(run())
    assert seen == [tmp_path / 'accounts/chiaki/browser'] * 2


def test_chat_api_and_file_download_preserve_owner_checks(users, tmp_path, monkeypatch):
    # No production provider, platform login or production DB is touched.
    def upstream(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': 'fixture reply'}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})
    monkeypatch.setenv('GALAXY_AI_RUNTIME', 'langgraph')
    app = create_app(tmp_path, ai_transport=httpx.MockTransport(upstream))
    auth = app.state.auth
    first = auth.login('chiaki', 'fixture-password-1', False, 'fixture')
    second = auth.login('yuzhuo', 'fixture-password-2', False, 'fixture')
    headers = {'Authorization': 'Bearer ' + first['access_token']}
    other = {'Authorization': 'Bearer ' + second['access_token']}
    from xingxu.ai import ProviderInput
    app.state.ai.save_provider(ProviderInput(name='fixture', base_url='https://fixture.example/v1', api_key='fixture', model='fixture'))
    with TestClient(app) as client:
        response = client.post('/api/v1/chat/send', headers=headers, json={'message': 'hello'})
        assert response.status_code == 200, response.text
        chat = response.json()['conversation']
        assert list((tmp_path / 'accounts/chiaki/chats').glob('*.jsonl'))
        assert client.get('/api/v1/chat/conversations/' + chat['id'], headers=other).status_code == 404
        assert len(client.get('/api/v1/chat/conversations', headers=headers).json()['conversations']) == 1
        assert client.get('/api/v1/chat/conversations', headers=other).json()['conversations'] == []
        from xingxu.tools import ExecutionContext
        files = app.state.ai.dispatcher.files
        original = files.workspace(first['user']) / 'fixture.txt'
        original.write_bytes(b'account scoped publication')
        ctx = ExecutionContext(first['user']['id'], first['user']['workspace_id'],
            tuple(first['user']['permissions']), chat['id'], first['access_token'])
        event = asyncio.run(app.state.ai.dispatcher.execute(ctx, {'id': 'fixture-publish', 'type': 'function',
            'function': {'name': 'galaxy_cli', 'arguments': json.dumps({'args': [
                'file', 'publish', '--input', json.dumps({'path': 'fixture.txt'})]})}}))
        assert event['status'] == 'success', event
        with scoped(app.state.ai.chats, first['user'], identifier=chat['id']) as state:
            state['conversations'][chat['id']]['messages'][-1]['tool_events'] = [event]
        url = event['result']['download_path']
        hosted, _ = files.resolve(first['user'], event['result']['file_id'])
        assert hosted.parent == tmp_path / 'accounts/chiaki/files/published'
        assert client.get(url, headers=headers).content == original.read_bytes()
        assert client.get(url, headers=other).status_code == 404
        assert client.delete('/api/v1/chat/conversations/' + chat['id'], headers=headers).status_code == 200
        assert not hosted.exists()
        assert client.get('/api/v1/chat/conversations/' + chat['id'], headers=headers).status_code == 404


def test_anonymous_denial_audit_does_not_require_or_impersonate_an_account(users, tmp_path):
    repository = JsonRepository(tmp_path, 'tool_audit', ('records',))
    with repository.transaction() as state:
        state['records']['fixture-denial'] = {'user_id': None, 'workspace_id': None, 'status': 'denied'}
        state['records']['deleted-user-history'] = {'user_id': 'former-user', 'workspace_id': 'default', 'status': 'denied'}
    with repository.transaction(write=False) as state:
        assert state['records']['fixture-denial']['status'] == 'denied'
        assert state['records']['deleted-user-history']['user_id'] == 'former-user'
    assert 'fixture-denial' not in (tmp_path / 'accounts/chiaki/logs/tool-audit.json').read_text()


def test_offline_migration_preserves_payloads_and_actual_files(users, tmp_path, monkeypatch):
    first, second = users
    monkeypatch.delenv('GALAXY_ACCOUNT_STORAGE')
    chat = conversation(first)
    with legacy_repository(tmp_path, 'chats', ('conversations',)).transaction() as state:
        state['conversations'][chat['id']] = chat
    record = {'id': 'fixture-connection', 'profile_revision': 'old-revision', 'account': 'fixture-account', 'password': 'fixture-cipher'}
    with legacy_repository(tmp_path, 'youzan', ('connections',)).transaction() as state:
        state['connections'][owner_key(first)] = record
    profile = tmp_path / 'youzan-profiles/fixture-connection/old-revision/Default'
    profile.mkdir(parents=True)
    (profile / 'Cookies').write_bytes(b'fixture-cookie-database')
    assert migrate(tmp_path, tmp_path / 'hosted', apply=False)['records']['chats'] == 1
    report = migrate(tmp_path, tmp_path / 'hosted', apply=True)
    assert report['users'] == 2 and report['records']['chats'] == 1
    assert (tmp_path / 'accounts/chiaki/browser/Default/Cookies').read_bytes() == b'fixture-cookie-database'
    assert (profile / 'Cookies').exists()
    monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    with JsonRepository(tmp_path, 'chats', ('conversations',)).transaction(write=False) as state:
        assert state['conversations'][chat['id']] == chat
    with JsonRepository(tmp_path, 'youzan', ('connections',)).transaction(write=False) as state:
        assert state['connections'][owner_key(first)] == record
    with pytest.raises(RuntimeError, match='already complete'):
        migrate(tmp_path, tmp_path / 'hosted', apply=True)
    with JsonRepository(tmp_path, 'chats', ('conversations',)).transaction() as state:
        state['conversations'][chat['id']]['messages'].append({'role':'user','content':'post-cutover fixture'})
    assert rollback(tmp_path, tmp_path/'hosted', tmp_path/'rollback-backup')['rollback_exported']
    with legacy_repository(tmp_path,'chats',('conversations',)).transaction(write=False) as state:
        assert state['conversations'][chat['id']]['messages'][-1]['content'] == 'post-cutover fixture'


def test_shared_profile_lease_and_channel_account_change(users, tmp_path, monkeypatch):
    import xingxu.youzan.login as module
    profile = Accounts(tmp_path).browser(users[0])
    script = tmp_path / 'session_research/youzan-login.cjs'
    script.parent.mkdir()
    script.touch()
    package = tmp_path / 'session_research/runtime/node_modules/playwright-core/package.json'
    package.parent.mkdir(parents=True)
    package.write_text('{}')
    monkeypatch.setattr(module.VirtualDisplay, 'prefix', lambda _: ('fixture-display',))
    monkeypatch.setattr(module.VirtualDisplay, 'environment', lambda _: {'PATH': '/usr/bin:/bin'})
    active, maximum, calls = 0, 0, []
    async def process(argv, payload, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        calls.append((argv, payload))
        await asyncio.sleep(.02)
        if 'reset-channel.cjs' not in argv[-1]:
            await kwargs['on_event']({'event': 'managed_credentials', 'sid': 'fixture-sid'})
            await kwargs['on_event']({'authenticated': True, 'result': 'session_reused'})
        active -= 1
        return 0
    monkeypatch.setattr(module, 'run_json_process', process)
    async def progress(_):pass
    async def run():
        adapter = module.YouzanLoginAdapter(tmp_path)
        await asyncio.gather(adapter('first', 'fixture', profile, progress), adapter('first', 'fixture', profile, progress))
        await adapter('changed-account', 'fixture', profile, progress)
    asyncio.run(run())
    assert maximum == 1
    assert sum('reset-channel.cjs' in argv[-1] for argv, _ in calls) == 1
    assert {payload['profile'] for _, payload in calls} == {str(profile)}
    assert json.loads((profile / '.channel-bindings.json').read_text())['youzan'] == 'changed-account'


def test_missing_key_never_replaces_existing_personal_credential_key(users, tmp_path):
    from xingxu.ai import AiService
    repository = JsonRepository(tmp_path, 'guanjia', ('connections',))
    with repository.transaction() as state:
        state['connections'][owner_key(users[0])] = {'session': 'fixture-encrypted'}
    with pytest.raises(RuntimeError, match='encryption key is missing'):
        AiService(tmp_path)
    assert not (tmp_path / 'ai-secret.key').exists()
