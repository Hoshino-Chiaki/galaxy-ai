import asyncio
import copy
import io
import json
import uuid
import zipfile
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from xingxu.app import create_app
from xingxu.ai import ProviderInput
from xingxu.auth import ROLES
from xingxu.tools import ExecutionContext, help_arguments

SPEC = {'title': '导出联调', 'sheets': [{'name': '客户', 'columns': [
    {'key': 'name', 'title': '姓名'}, {'key': 'amount', 'title': '金额', 'type': 'currency'}],
    'rows': [{'name': '演示客户', 'amount': 0}]}]}


def call(spec=SPEC):
    return {'id': 'export-call', 'type': 'function', 'function': {'name': 'galaxy_cli',
        'arguments': json.dumps({'args': ['excel', 'create', '--input', json.dumps(spec)]})}}


@pytest.fixture
def setup(tmp_path):
    def upstream(request):
        payload = json.loads(request.content)
        if payload['messages'][-1]['role'] == 'tool':
            result = json.loads(payload['messages'][-1]['content'])
            if 'path' in result:
                publish = {'id': 'publish-call', 'type': 'function', 'function': {'name': 'galaxy_cli',
                    'arguments': json.dumps({'args': ['file', 'publish', '--input', json.dumps({'path': result['path']})]})}}
                message = {'role': 'assistant', 'content': None, 'tool_calls': [publish]}
            else:
                message = {'role': 'assistant', 'content': 'Excel 已生成，请下载。'}
        else:
            message = {'role': 'assistant', 'content': None, 'tool_calls': [call()]}
        return httpx.Response(200, json={'choices': [{'message': message}]})
    app = create_app(tmp_path, ai_transport=httpx.MockTransport(upstream))
    app.state.auth.import_accounts([('admin', 'test-admin-password'), ('employee', 'test-employee-password')])
    admin = app.state.auth.login('admin', 'test-admin-password', False, 'localhost')
    employee = app.state.auth.login('employee', 'test-employee-password', False, 'localhost')
    app.state.ai.save_provider(ProviderInput(name='test', base_url='https://fixture.invalid/v1', model='test-model', api_key='fixture-key'))
    return app, admin, employee, tmp_path


def headers(session):
    return {'Authorization': 'Bearer ' + session['access_token']}


def context(session):
    user = session['user']
    return ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), str(uuid.uuid4()), session['access_token'])


def test_excel_category_help_matches_standard_help():
    assert help_arguments(json.dumps({'args': ['excel', 'help']})) == help_arguments(json.dumps({'args': ['help', '--category', 'excel']}))


def test_ai_export_authenticated_download_isolation_and_delete(setup):
    app, admin, employee, directory = setup
    with TestClient(app) as client:
        response = client.post('/api/v1/chat/send', headers=headers(employee), json={'message': '生成演示表格'})
        assert response.status_code == 200
        chat = response.json()['conversation']
        event = chat['messages'][-1]['tool_events'][-1]
        assert event['status'] == 'success' and event['command'] == 'galaxy-cli file publish'
        descriptor = event['result']
        url = '/api/v1/files/' + descriptor['file_id']
        meta = client.get(url + '/metadata', headers=headers(employee))
        assert meta.status_code == 200
        assert meta.json() == descriptor
        named_url = meta.json()['download_path']
        downloaded = client.get(url, headers=headers(employee))
        assert downloaded.status_code == 200 and len(downloaded.content) == descriptor['size']
        assert 'attachment' in downloaded.headers['content-disposition']
        assert downloaded.headers['cache-control'] == 'no-store'
        assert client.get(named_url, headers=headers(employee)).content == downloaded.content
        assert client.get(named_url, headers=headers(admin)).status_code == 404
        assert client.get(named_url).status_code == 401
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as z:
            assert 'xl/workbook.xml' in z.namelist()
            assert '演示客户' in z.read('xl/sharedStrings.xml').decode()
        assert client.get(url).status_code == 401
        assert client.get(url, headers=headers(admin)).status_code == 404
        assert client.get(url + '/metadata', headers=headers(admin)).status_code == 404
        assert client.get('/api/v1/files/' + str(uuid.uuid4()), headers=headers(employee)).status_code == 404
        file, _ = app.state.ai.dispatcher.files.resolve(employee['user'], descriptor['file_id'])
        assert file.stat().st_mode & 0o777 == 0o600
        assert str(directory) not in json.dumps(descriptor)
        assert '演示客户' not in (directory / 'tool_audit.json').read_text()
        assert client.delete('/api/v1/chat/conversations/' + chat['id'], headers=headers(employee)).status_code == 200
        assert client.get(url, headers=headers(employee)).status_code == 404
        assert not file.exists()


def test_permission_revocation_rejects_export_before_spawn(setup, monkeypatch):
    app, _, employee, _ = setup
    monkeypatch.setitem(ROLES, 'employee', [p for p in ROLES['employee'] if p != 'excel:export'])
    dispatcher = app.state.ai.dispatcher
    dispatcher._run = AsyncMock()
    event = asyncio.run(dispatcher.execute(context(employee), call()))
    assert event['result']['error']['code'] == 'permission_denied'
    dispatcher._run.assert_not_awaited()


def test_concurrent_exports_private_pipe_and_no_youzan_credentials(setup, monkeypatch):
    app, admin, employee, directory = setup
    dispatcher = app.state.ai.dispatcher
    dispatcher.category_provider = lambda _: pytest.fail('Excel must not request Youzan credentials')
    spawn = asyncio.create_subprocess_exec
    observed = []
    async def spy(*args, **kwargs):
        observed.append((args, kwargs))
        return await spawn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spy)
    async def run():
        return await asyncio.gather(*(dispatcher.execute(context(s), call()) for s in (admin, employee)))
    events = asyncio.run(run())
    assert all(e['status'] == 'success' for e in events)
    assert len({e['result']['path'] for e in events}) == 2
    for args, options in observed:
        assert args[1:] == ('excel', 'create', '--input-stdin')
        assert options['stdin'] == asyncio.subprocess.PIPE
        assert '演示客户' not in json.dumps(args) + json.dumps(options['env'])
    assert not list(directory.glob('excel-*'))


def test_large_input_uses_pipe_and_preserves_rows(setup):
    app, _, employee, _ = setup
    spec = copy.deepcopy(SPEC)
    spec['sheets'][0]['rows'] = [{'name': 'fixture-private-value-' + str(i), 'amount': i} for i in range(800)]
    assert len(call(spec)['function']['arguments']) > 8192
    event = asyncio.run(app.state.ai.dispatcher.execute(context(employee), call(spec)))
    assert event['status'] == 'success'
    assert event['result']['sheets'][0]['rows'] == 800


def test_session_revoked_during_generation_discards_file(setup, monkeypatch):
    app, _, employee, directory = setup
    dispatcher = app.state.ai.dispatcher
    original = dispatcher._run
    async def revoke(*args, **kwargs):
        result = await original(*args, **kwargs)
        app.state.auth.logout(employee['access_token'])
        return result
    monkeypatch.setattr(dispatcher, '_run', revoke)
    event = asyncio.run(dispatcher.execute(context(employee), call()))
    assert event['result']['error']['code'] == 'session_expired'
    assert not list((directory / 'files').glob('*.xlsx'))
    assert not list(directory.glob('excel-*'))


@pytest.mark.parametrize('spec', [{**SPEC, 'path': '/tmp/not-allowed'},
    {**SPEC, 'sheets': [{'name': 'bad/name', 'columns': [{'key': 'a', 'title': 'A'}], 'rows': []}]}])
def test_invalid_export_never_starts_process(setup, spec):
    app, _, employee, _ = setup
    app.state.ai.dispatcher._run = AsyncMock()
    event = asyncio.run(app.state.ai.dispatcher.execute(context(employee), call(spec)))
    assert event['status'] == 'denied'
    app.state.ai.dispatcher._run.assert_not_awaited()


def test_project_hosting_directory_supports_shared_files_and_blocks_escape(setup, tmp_path):
    app, admin, employee, _ = setup
    root = app.state.ai.dispatcher.files.root
    (root / '说明.txt').write_text('shared hosting fixture')
    private = tmp_path / 'outside.txt'; private.write_text('not downloadable')
    (root / 'escape.txt').symlink_to(private)
    (root / '.private.txt').write_text('not downloadable')
    reserved = str(uuid.uuid4()) + '_unfinished.xlsx'
    (root / reserved).write_bytes(b'not committed')
    with TestClient(app) as client:
        assert client.get('/files/说明.txt', headers=headers(employee)).text == 'shared hosting fixture'
        assert client.get('/files/说明.txt', headers=headers(admin)).status_code == 200
        assert client.get('/files/说明.txt').status_code == 401
        for name in ('escape.txt', '.private.txt', reserved):
            assert client.get('/files/' + name, headers=headers(admin)).status_code == 404
        assert client.get('/files/..%5Coutside.txt', headers=headers(admin)).status_code == 404


def test_hosting_root_can_live_in_project_and_legacy_files_still_work(setup, monkeypatch, tmp_path):
    app, _, employee, directory = setup
    with TestClient(app) as client:
        result = client.post('/api/v1/chat/send', headers=headers(employee), json={'message': 'legacy migration fixture'})
        descriptor = result.json()['conversation']['messages'][-1]['tool_events'][-1]['result']
        files = app.state.ai.dispatcher.files
        path, _ = files.resolve(employee['user'], descriptor['file_id'])
        legacy = directory / 'files' / (descriptor['file_id'] + '.xlsx')
        path.rename(legacy)
        with files.repo.transaction() as state:
            state['files'][descriptor['file_id']].pop('stored_name')
        monkeypatch.setenv('GALAXY_FILES_DIR', str(tmp_path / 'hosted-files'))
        from xingxu.files import FileService
        migrated = FileService(directory)
        recovered, metadata = migrated.resolve(employee['user'], descriptor['file_id'])
        assert recovered == legacy and metadata['download_path'].startswith('/files/')


def test_publication_uses_no_cross_mount_rename(setup, monkeypatch):
    import os
    app, _, employee, _ = setup
    rename = os.replace
    def cross_device(source, destination):
        from pathlib import Path
        if Path(source).suffix == '.xlsx': pytest.fail('Publication must stream-copy, not rename private source')
        return rename(source, destination)
    monkeypatch.setattr(os, 'replace', cross_device)
    with TestClient(app) as client:
        response = client.post('/api/v1/chat/send', headers=headers(employee), json={'message': 'cross mount export'})
        events = response.json()['conversation']['messages'][-1]['tool_events']
        assert [e['command'] for e in events] == ['galaxy-cli excel create', 'galaxy-cli file publish']
        assert all(e['status'] == 'success' for e in events)
        assert 'file_id' not in events[0]['result']
        assert not any(p.name.endswith('.part') for p in app.state.ai.dispatcher.files.root.iterdir())
