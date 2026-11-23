"""Purpose-gated upload lifecycle, streaming bounds, isolation and AI continuation."""
import asyncio
import copy
import hashlib
import json
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from xingxu.app import create_app
from xingxu.ai import ProviderInput
from xingxu.auth import AuthFailure
from xingxu.tools import ExecutionContext
from xingxu import uploads as upload_module

PURPOSE = '上传有赞商品操作 HAR，用于分析请求头和成本价修改字段，上传后解析请求。'


def tool(name, body):
    return {'id': str(uuid.uuid4()), 'type': 'function', 'function': {'name': 'galaxy_cli',
            'arguments': json.dumps({'args': ['task', name, '--input', json.dumps(body)]})}}


@pytest.fixture
def setup(tmp_path):
    calls = []

    def upstream(request):
        payload = json.loads(request.content)
        calls.append(payload)
        last = payload['messages'][-1]
        if last['role'] == 'tool':
            message = {'content': '请按用途选择文件。' if 'upload_request_id' in last['content'] else '已按用途解析 HAR。'}
        elif '【服务器确认的已上传文件' in last['content']:
            receipt = json.loads(last['content'].split('\n')[-1])
            message = {'content': None, 'tool_calls': [tool('har', {'path': receipt['path']})]}
        else:
            message = {'content': None, 'tool_calls': [tool('request-upload', {'purpose': PURPOSE, 'max_size_mb': 1, 'accepted_extensions': ['har', 'json']})]}
        return httpx.Response(200, json={'choices': [{'message': message}]})

    app = create_app(tmp_path, ai_transport=httpx.MockTransport(upstream))
    app.state.auth.import_accounts([('admin', 'test-admin-password'), ('employee', 'test-employee-password')])
    sessions = [app.state.auth.login(name, password, False, 'localhost') for name, password in
                [('admin', 'test-admin-password'), ('employee', 'test-employee-password')]]
    app.state.ai.save_provider(ProviderInput(name='Upload fixture', base_url='https://fixture.invalid/v1', model='fixture-model', api_key='fixture-only'))
    with TestClient(app) as client:
        yield app, client, sessions[1], sessions[0], calls


def headers(session):
    return {'Authorization': 'Bearer ' + session['access_token']}


def invitation(setup):
    app, client, session, _, _ = setup
    response = client.post('/api/v1/chat/send', headers=headers(session), json={'message': '请分析文件，先提供上传入口。'})
    assert response.status_code == 200, response.text
    conversation = response.json()['conversation']
    event = conversation['messages'][-1]['tool_events'][-1]
    assert event['status'] == 'success', event
    return conversation['id'], event['result']['upload_request_id']


def put(setup, identifier, content=b'abc', filename='capture.har', session=None, **extra):
    _, client, owner, _, _ = setup
    return client.put(f'/api/v1/uploads/{identifier}/content', content=content,
                      headers={**headers(session or owner), 'Content-Type': 'application/octet-stream',
                               'X-Upload-Filename': filename, **extra})


def har_bytes():
    return json.dumps({'log': {'version': '1.2', 'creator': {'name': 'Fixture', 'version': '1'}, 'entries': [
        {'startedDateTime': '2026-11-16T00:00:00Z', 'time': 0,
         'request': {'method': 'POST', 'url': 'https://example.test/product', 'headers': [{'name': 'User-Agent', 'value': 'Fixture/1'}],
                     'cookies': [], 'queryString': [], 'postData': {'mimeType': 'application/json', 'text': '{"costPrice":"11.00"}'}},
         'response': {'status': 200, 'headers': [], 'cookies': [], 'content': {'mimeType': 'application/json', 'text': '{"success":true}'}},
         'timings': {}, 'comment': 'large body fixture ' * 16000}]}}).encode()


def test_complete_streaming_upload_ai_receipt_har_and_reuse_denied(setup):
    app, client, session, _, calls = setup
    conversation, identifier = invitation(setup)
    data = har_bytes()
    response = put(setup, identifier, data)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt['path'] == f'uploads/{identifier}/payload'
    assert receipt['purpose'] == PURPOSE
    assert receipt['sha256'] == hashlib.sha256(data).hexdigest()
    assert receipt['size'] > 128 * 1024  # The generic API body limit must not break streaming uploads.
    assert (app.state.ai.dispatcher.files.workspace(session['user']) / receipt['path']).read_bytes() == data
    response = client.post('/api/v1/chat/send', headers=headers(session), json={
        'conversation_id': conversation, 'message': '文件已上传，请继续。', 'upload_id': identifier})
    assert response.status_code == 200, response.text
    chat = response.json()['conversation']
    assert chat['messages'][-2]['uploaded_file']['file_id'] == identifier
    assert 'model_content' not in chat['messages'][-2]
    event = chat['messages'][-1]['tool_events'][-1]
    assert event['command'] == 'galaxy-cli task har' and event['status'] == 'success', event
    assert event['result']['source']['sha256'] == receipt['sha256']
    assert event['result']['source']['path'] == receipt['path']
    assert event['result']['source']['file_id'] == identifier
    assert event['result']['source']['filename'] == 'capture.har'
    assert any(PURPOSE in message['content'] and receipt['path'] in message['content']
               for payload in calls for message in payload['messages'] if message['role'] == 'user')
    assert put(setup, identifier).status_code == 409


def test_upload_requires_real_saved_invitation_and_owner(setup):
    app, client, session, other, _ = setup
    conversation, identifier = invitation(setup)
    route = f'/api/v1/uploads/{identifier}'
    assert client.get(route).status_code == 401
    assert client.get(route, headers=headers(other)).status_code == 404
    assert put(setup, identifier, session=other).status_code == 404
    assert client.delete(route, headers=headers(other)).status_code == 404
    unknown = str(uuid.uuid4())
    assert put(setup, unknown).status_code == 404
    assert client.get(route, headers=headers(session)).json()['status'] == 'pending'
    orphan = app.state.ai.dispatcher.uploads.create(session['user'], conversation, {'purpose': '这个入口尚未提交到对话中'})
    assert client.get('/api/v1/uploads/' + orphan['upload_request_id'], headers=headers(session)).status_code == 404


def test_other_conversation_and_arbitrary_paths_cannot_read_file(setup):
    app, client, session, _, calls = setup
    conversation, identifier = invitation(setup)
    assert put(setup, identifier, har_bytes()).status_code == 200
    other_chat, _ = invitation(setup)
    before = len(calls)
    result = client.post('/api/v1/chat/send', headers=headers(session), json={
        'conversation_id': other_chat, 'message': '继续', 'upload_id': identifier})
    assert result.status_code == 404 and len(calls) == before
    user = session['user']
    ctx = ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), conversation, session['access_token'])
    for body in ({'path': '/etc/passwd'}, {'file_id': identifier, 'path': '/etc/passwd'}):
        event = asyncio.run(app.state.ai.dispatcher.execute(ctx, tool('har', body)))
        assert event['status'] == 'denied'
    with pytest.raises(AuthFailure):
        app.state.ai.dispatcher.uploads.resolve({**user, 'workspace_id': 'other-workspace'}, identifier, conversation)


def test_file_size_type_and_incomplete_body_are_enforced_and_reservations_released(setup):
    app, client, session, _, _ = setup
    _, identifier = invitation(setup)
    assert put(setup, identifier, b'abc', filename='wrong.exe').status_code == 415
    assert put(setup, identifier, b'').status_code == 413
    assert put(setup, identifier, b'a', **{'Content-Length': '2'}).status_code == 422
    service = app.state.ai.dispatcher.uploads
    assert service.metadata(session['user'], identifier)['status'] == 'pending'
    assert not (app.state.ai.dispatcher.files.workspace(session['user']) / 'uploads' / identifier / 'partial').exists()
    with service.repo.transaction() as state:
        state['requests'][identifier]['max_size_bytes'] = 2
    assert put(setup, identifier, b'abc').status_code == 413
    assert put(setup, identifier, b'ok', filename='no-extension').status_code == 200


def test_quota_is_per_user_and_reserved_before_streaming(setup, monkeypatch):
    app, _, session, other, _ = setup
    service = app.state.ai.dispatcher.uploads
    monkeypatch.setattr(app.state.file_storage, 'settings', lambda: {'quota_mb': 5 / 1048576, 'quota_bytes': 5})
    _, first = invitation(setup)
    assert put(setup, first, b'abcd').status_code == 200
    _, second = invitation(setup)
    assert put(setup, second, b'ab').status_code == 413
    assert service.metadata(session['user'], second)['status'] == 'pending'
    with pytest.raises(AuthFailure):
        service.resolve(other['user'], first, 'unknown')


def test_delete_file_expiry_and_conversation_reclaim_disk(setup):
    app, client, session, _, _ = setup
    service = app.state.ai.dispatcher.uploads
    conversation, identifier = invitation(setup)
    receipt = put(setup, identifier).json()
    path = app.state.ai.dispatcher.files.workspace(session['user']) / receipt['path']
    assert path.exists()
    assert client.delete('/api/v1/uploads/' + identifier, headers=headers(session)).status_code == 200
    assert not path.exists()
    conversation2, identifier2 = invitation(setup)
    receipt2 = put(setup, identifier2).json()
    path2 = app.state.ai.dispatcher.files.workspace(session['user']) / receipt2['path']
    with service.repo.transaction() as state:
        state['requests'][identifier2]['expires_at'] = int(time.time()) - 1
    service.cleanup()
    assert not path2.exists()
    assert service.metadata(session['user'], identifier2)['status'] == 'expired'
    conversation3, identifier3 = invitation(setup)
    receipt3 = put(setup, identifier3).json()
    path3 = app.state.ai.dispatcher.files.workspace(session['user']) / receipt3['path']
    assert client.delete('/api/v1/chat/conversations/' + conversation3, headers=headers(session)).status_code == 200
    assert not path3.exists()


def test_interrupted_stream_and_permission_revocation_leave_no_file(setup):
    app, _, session, _, _ = setup
    _, identifier = invitation(setup)
    service = app.state.ai.dispatcher.uploads

    async def interrupted():
        yield b'a'
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.receive(session['user'], identifier, 'a.har', 2, interrupted(), lambda: session['user']))
    assert service.metadata(session['user'], identifier)['status'] == 'pending'

    async def complete():
        yield b'a'

    revoked = {**session['user'], 'permissions': []}
    with pytest.raises(AuthFailure):
        asyncio.run(service.receive(session['user'], identifier, 'a.har', 1, complete(), lambda: revoked))
    assert service.metadata(session['user'], identifier)['status'] == 'pending'
    assert not (app.state.ai.dispatcher.files.workspace(session['user']) / 'uploads' / identifier).exists()


def test_linked_upload_directory_is_rejected_without_touching_outside(setup, tmp_path):
    app, _, session, _, _ = setup
    _, identifier = invitation(setup)
    work = app.state.ai.dispatcher.files.workspace(session['user'])
    (work / 'uploads').mkdir(exist_ok=True)
    outside = tmp_path / 'outside-upload'; outside.mkdir()
    (outside / 'payload').write_bytes(b'outside')
    (work / 'uploads' / identifier).symlink_to(outside, target_is_directory=True)
    assert put(setup, identifier).status_code == 502
    assert (outside / 'payload').read_bytes() == b'outside'


def test_purpose_duplicate_pending_limit_and_permission_gate(setup):
    app, _, session, _, _ = setup
    conversation, identifier = invitation(setup)
    service = app.state.ai.dispatcher.uploads
    assert service.create(session['user'], conversation, {'purpose': PURPOSE})['upload_request_id'] == identifier
    for n in (1, 2):
        service.create(session['user'], conversation, {'purpose': f'另一个明确的文件用途{n}'})
    with pytest.raises(AuthFailure) as error:
        service.create(session['user'], conversation, {'purpose': '第四个明确的文件用途'})
    assert error.value.code == 'upload_limit'
    with pytest.raises(AuthFailure):
        service.create({**session['user'], 'permissions': []}, conversation, {'purpose': PURPOSE})


def test_modified_uploaded_bytes_are_rejected_after_har_parse(setup):
    app, _, session, _, _ = setup
    conversation, identifier = invitation(setup)
    receipt = put(setup, identifier, har_bytes()).json()
    path = app.state.ai.dispatcher.files.workspace(session['user']) / receipt['path']
    content = path.read_bytes().replace(b'11.00', b'12.00')
    path.write_bytes(content)
    user = session['user']
    context = ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), conversation, session['access_token'])
    result = asyncio.run(app.state.ai.dispatcher.execute(context, tool('har', {'file_id': identifier})))
    assert result['result']['error']['code'] == 'file_changed'
