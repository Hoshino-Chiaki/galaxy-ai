import asyncio
import importlib.util
import json
import uuid
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from xingxu.auth import AuthFailure
from xingxu.file_storage import FileStorage
from xingxu.files import FileService
from xingxu.personal_prompt import PersonalPrompt, PERMISSION, QUOTA_BYTES
from xingxu.store import JsonRepository
from test_ai import PROVIDER, setup


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    files = FileService(tmp_path)
    config = JsonRepository(tmp_path, 'ai', ('providers', 'settings'))
    a = {'id': 'a', 'username': 'alice', 'workspace_id': 'w', 'permissions': [PERMISSION]}
    b = {**a, 'id': 'b', 'username': 'bob'}
    return PersonalPrompt(files, config), files, config, a, b


async def chunks(*data):
    for value in data:
        yield value


def upload(prompts, user, filename, data):
    item = prompts.reserve(user, filename, len(data))
    return asyncio.run(prompts.receive(user, item['id'], len(data), chunks(data), lambda: user))


def test_own_prompt_directory_attachments_and_file_storage_are_separate(env):
    prompts, files, config, a, b = env
    prompts.save(a, '先给结论')
    item = upload(prompts, a, '工作规范.md', '本人规则'.encode())
    assert prompts.snapshot(a)['prompt'] == '先给结论'
    assert prompts.snapshot(b)['prompt'] == '' and prompts.snapshot(b)['files'] == []
    path, metadata = prompts.resolve(a, item['id'])
    assert path.is_relative_to(files.account_root(a) / 'prompts')
    assert path.read_text() == '本人规则'
    normal = FileStorage(files, config)
    assert normal.list({**a, 'permissions': ['file:read']})['files'] == []
    for function in (prompts.resolve, prompts.delete):
        with pytest.raises(AuthFailure):
            function(b, item['id'])
    assert metadata['filename'] == '工作规范.md'
    prompts.delete(a, item['id'])
    assert prompts.snapshot(a)['used_bytes'] == 0 and not path.exists()


def test_permission_cannot_be_replaced_by_admin_configure_or_file_permissions(env):
    prompts, _, _, a, _ = env
    denied = {**a, 'permissions': ['ai:configure', 'file:read', 'task:upload']}
    for call in (lambda: prompts.snapshot(denied), lambda: prompts.save(denied, 'x'),
                 lambda: prompts.reserve(denied, 'x', 1)):
        with pytest.raises(AuthFailure) as error:
            call()
        assert error.value.status == 403
    assert prompts.messages(denied) == []


def test_all_attachments_and_reservations_share_the_fixed_25mb_limit(env):
    prompts, _, _, a, b = env
    def reserve(index):
        try:
            return prompts.reserve(a, f'{index}.txt', 10 * 1024 ** 2)
        except AuthFailure as error:
            return error.code
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(reserve, range(6)))
    assert sum(isinstance(item, dict) for item in results) == 2
    assert results.count('storage_quota') == 4
    remaining = prompts.reserve(a, '剩余.txt', 5 * 1024 ** 2)
    assert prompts.snapshot(a)['reserved_bytes'] == QUOTA_BYTES
    with pytest.raises(AuthFailure):
        prompts.reserve(a, '超出.txt', 1)
    assert prompts.snapshot(b)['available_bytes'] == QUOTA_BYTES
    prompts.release(a, remaining['id'])
    assert prompts.snapshot(a)['available_bytes'] == 5 * 1024 ** 2


def test_false_size_partial_upload_and_revocation_release_reserved_space(env):
    prompts, _, _, a, _ = env
    for payload, reauthorize in ((b'oversize', lambda: a), (b'x', lambda: a),
                                 (b'123', lambda: {**a, 'permissions': []})):
        ticket = prompts.reserve(a, 'test.txt', 3)
        with pytest.raises(AuthFailure):
            asyncio.run(prompts.receive(a, ticket['id'], 3, chunks(payload), reauthorize))
        assert prompts.snapshot(a)['used_bytes'] == prompts.snapshot(a)['reserved_bytes'] == 0


def test_path_traversal_and_symlink_cannot_escape_personal_prompt_folder(env, tmp_path):
    prompts, _, _, a, _ = env
    for name in ('../secret', 'other/path.txt', 'other\\path.txt'):
        with pytest.raises(AuthFailure):
            prompts.reserve(a, name, 1)
    item = upload(prompts, a, 'test.txt', b'safe')
    path = prompts.resolve(a, item['id'])[0]
    secret = tmp_path / 'outside.txt'
    secret.write_bytes(b'safe')
    path.unlink(); path.symlink_to(secret)
    with pytest.raises(AuthFailure):
        prompts.resolve(a, item['id'])


def test_each_turn_loads_latest_prompt_and_attachment_manifest_without_other_accounts(env):
    prompts, _, _, a, b = env
    assert prompts.messages(a) == []
    prompts.save(a, 'first')
    item = upload(prompts, a, '规范.txt', b'style')
    first = prompts.messages(a)[0]['content']
    assert 'first' in first and '/account-prompts/' in first and item['id'] in first
    prompts.save(a, 'latest')
    assert 'latest' in prompts.messages(a)[0]['content']
    assert prompts.messages(b) == []
    prompts.delete(a, item['id'])
    assert item['id'] not in prompts.messages(a)[0]['content']


def test_routes_model_injection_and_employee_denial(setup):
    app, client, admin, employee, requests = setup
    path = '/api/v1/personal-prompt'
    assert client.get(path, headers=employee).status_code == 403
    assert client.get(path, headers=admin).json()['quota_bytes'] == QUOTA_BYTES
    assert client.put(path, headers=admin, json={'prompt': 'reply in Chinese', 'user_id': 'employee'}).status_code == 422
    assert client.put(path, headers=admin, json={'prompt': 'reply in Chinese'}).status_code == 200
    assert client.post(path + '/uploads', headers=admin, json={'filename': 'large.txt', 'size': QUOTA_BYTES + 1}).status_code == 413
    ticket = client.post(path + '/uploads', headers=admin, json={'filename': 'guide.md', 'size': 3}).json()
    content = client.put(path + f'/uploads/{ticket["id"]}/content', headers={**admin, 'Content-Type': 'application/octet-stream'}, content=b'abc')
    assert content.status_code == 200
    assert client.get(path + f'/files/{ticket["id"]}/content', headers=admin).content == b'abc'
    assert client.get(path + f'/files/{ticket["id"]}/content', headers=employee).status_code == 403
    client.post('/api/v1/admin/ai/providers', headers=admin, json=PROVIDER)
    response = client.post('/api/v1/chat/send', headers=admin, json={'message': 'hello'})
    assert response.status_code == 200
    personal = [row for row in requests[-1][2]['messages'] if 'reply in Chinese' in row.get('content', '')]
    assert personal and '/account-prompts/' in personal[0]['content']
    client.put(path, headers=admin, json={'prompt': 'updated preference'})
    assert client.post('/api/v1/chat/send', headers=admin, json={'message': 'hello again'}).status_code == 200
    assert any('updated preference' in row.get('content', '') for row in requests[-1][2]['messages'])


def test_worker_exposes_only_the_account_prompt_library_and_read_only(env, monkeypatch):
    prompts, files, _, a, b = env
    prompts.library(a); prompts.library(b)
    spec = importlib.util.spec_from_file_location('prompt_test_worker', Path(__file__).resolve().parents[2] / 'deploy/dsh/worker.py')
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    root = files.account_root(a)
    worker.ACCOUNTS = root.parent
    with patch.object(worker, 'scope_root', return_value=root / 'tasks' / 'dsh'):
        library = worker.account_prompts('owner')
        assert library == root / 'prompts' / 'library'
        assert not library.is_relative_to(files.account_root(b))
        args = worker.jail_arguments(Path('/stage'), Path('/broker'), 'token', prompts=library)
        index = args.index('/account-prompts')
        assert args[index - 2:index] == ['--ro-bind', str(library)]
        library.rmdir(); library.symlink_to(files.account_root(b), target_is_directory=True)
        with pytest.raises(OSError):
            worker.account_prompts('owner')


def test_dsh_refreshes_personal_policy_and_mount_without_large_environment_values(tmp_path, monkeypatch):
    from xingxu.dsh_runtime import DshTurn
    from xingxu.tool_context import ExecutionContext
    monkeypatch.delenv('GALAXY_ACCOUNT_STORAGE', raising=False)
    async def run():
        received = []
        async def worker(reader, writer):
            payload = json.loads(await reader.readline())
            received.append(payload)
            writer.write(json.dumps({'type': 'result', 'run_id': payload['run_id'], 'content': 'ok'}).encode() + b'\n')
            await writer.drain()
            writer.close(); await writer.wait_closed()
        socket_path = tmp_path / 'worker.sock'
        server = await asyncio.start_unix_server(worker, str(socket_path), limit=2 * 1024 * 1024)
        ai = SimpleNamespace(dispatcher=SimpleNamespace(directory=tmp_path, files=SimpleNamespace(root=tmp_path)),
                             personal_prompts=SimpleNamespace(library=Mock()),
                             context_policy=SimpleNamespace(input_tokens=4096))
        turn = DshTurn(ai, ExecutionContext('u', 'w', (PERMISSION,), str(uuid.uuid4()), 's'),
                       {'base_url': 'https://api.deepseek.com', 'model': 'fixture'},
                       run=None, progress={}, thinking=None, reasoning_effort=None)
        personal = '【当前账号的个人提示词；本轮已重新加载】\n' + '个人偏好' * 20000
        messages = [{'role': 'system', 'content': 'global rules'}, {'role': 'system', 'content': personal},
                    {'role': 'user', 'content': 'hello'}]
        async def prepare(value):return value
        try:
            with patch('xingxu.dsh_runtime.SOCKET', str(socket_path)), patch.object(turn, 'principal', return_value={'permissions': [PERMISSION]}):
                assert (await turn.execute(messages, prepare=prepare))[0] == 'ok'
            assert received[0]['account_prompts'] is True
            assert personal not in received[0]['input']['system_prompt']
            assert personal in turn.with_system_policy({})['system']
            ai.personal_prompts.library.assert_called_once()
        finally:
            server.close(); await server.wait_closed()
    with tempfile.TemporaryDirectory(prefix='gp-broker-') as broker:
        monkeypatch.setenv('GALAXY_DSH_BROKER_DIR', broker)
        asyncio.run(run())
