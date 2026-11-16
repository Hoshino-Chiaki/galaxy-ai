"""Uploaded paths are references, not user-selected filesystem locations."""
import json
import asyncio
import uuid

import pytest

from xingxu.task_tools import arguments
from xingxu.tool_context import DispatchDenied
from xingxu.tools import ExecutionContext
from test_uploads import setup, invitation, put, har_bytes, tool

IDENTIFIER = '11111111-1111-4111-8111-111111111111'


def test_upload_path_and_file_id_select_the_same_canonical_tool_input():
    options = {'action': 'entry', 'entry_index': 308, 'include_sensitive': True, 'body_limit': 64000}
    by_path = arguments(['task', 'har', '--input', json.dumps({'path': f'uploads/{IDENTIFIER}/payload', **options})])
    by_id = arguments(['task.har', '--input', json.dumps({'file_id': IDENTIFIER, **options})])
    assert by_path[:3] == by_id[:3]
    assert by_path[4:] == by_id[4:]
    assert json.loads(by_path[3]) == json.loads(by_id[3])
    assert json.loads(by_path[3]) == {'file_id': IDENTIFIER, **options}


@pytest.mark.parametrize('path', [
    '/etc/passwd', '../uploads/' + IDENTIFIER + '/payload', 'uploads/' + IDENTIFIER + '/payload/..',
    'uploads/' + IDENTIFIER + '/other.har', 'uploads/' + IDENTIFIER + '//payload',
    'uploads/' + IDENTIFIER + '/payload?token=x', 'uploads/' + IDENTIFIER + '/payload#x',
    'uploads%2f' + IDENTIFIER + '%2fpayload', 'uploads\\' + IDENTIFIER + '\\payload',
    'https://example.test/uploads/' + IDENTIFIER + '/payload', 'C:/Users/demo/capture.har',
    'uploads/' + IDENTIFIER + '/payload\n', '.work/user/uploads/' + IDENTIFIER + '/payload',
])
def test_non_upload_paths_are_rejected_before_any_file_access(path):
    with pytest.raises(DispatchDenied):
        arguments(['task', 'har', '--input', json.dumps({'path': path})])


def test_uploaded_path_returns_full_request_data_but_cannot_cross_owner_or_conversation(setup):
    app, client, session, other, _ = setup
    conversation, identifier = invitation(setup)
    capture = json.loads(har_bytes())
    request = capture['log']['entries'][0]['request']
    request['headers'].append({'name': 'Cookie', 'value': 'sid=fixture-upload-cookie-only'})
    request['cookies'].append({'name': 'sid', 'value': 'fixture-upload-cookie-only'})
    response = put(setup, identifier, json.dumps(capture).encode())
    assert response.status_code == 200
    receipt = response.json()

    def execute(current, chat, body):
        user = current['user']
        context = ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), chat, current['access_token'])
        return asyncio.run(app.state.ai.dispatcher.execute(context, tool('har', body)))

    event = execute(session, conversation, {'path': receipt['path'], 'action': 'entry', 'entry_index': 0, 'include_sensitive': True})
    assert event['status'] == 'success', event
    data = event['result']['data']
    assert {'name': 'Cookie', 'value': 'sid=fixture-upload-cookie-only'} in data['request']['headers']
    assert data['request']['cookies'][0]['value'] == 'fixture-upload-cookie-only'
    assert data['request']['body']['parsed']['costPrice'] == '11.00'
    assert data['response']['body']['parsed']['success'] is True
    assert execute(session, conversation, {'file_id': identifier})['status'] == 'success'
    assert execute(other, conversation, {'path': receipt['path']})['result']['error']['code'] == 'not_found'
    another_chat, _ = invitation(setup)
    assert execute(session, another_chat, {'path': receipt['path']})['result']['error']['code'] == 'not_found'
    unknown = f'uploads/{uuid.uuid4()}/payload'
    assert execute(session, conversation, {'path': unknown})['result']['error']['code'] == 'not_found'
