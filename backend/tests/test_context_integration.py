"""Linux API/storage regression for summaries, using real temporary repositories."""
import asyncio
import copy
import json
import sys
import uuid

import httpx
import pytest

if sys.platform == 'win32':
    pytest.skip('Production repositories and file gateway require Linux', allow_module_level=True)

from test_ai import PROVIDER, setup
from test_agent_runtime import history
from xingxu.agent_runtime import ChatGraph, ContextPolicy
from xingxu.ai import AiService, ChatInput


def seed(app, headers, *, size=1800):
    user = app.state.auth.identity(headers['Authorization'][7:])['user']
    identifier = str(uuid.uuid4())
    conversation = {'id': identifier, 'user_id': user['id'], 'workspace_id': user['workspace_id'],
        'title': 'Long history', 'created_at': 1, 'updated_at': 1, 'model': 'fixture-model',
        'messages': history(12, size=size, tool=True)}
    with app.state.ai.chats.transaction() as state:
        state['conversations'][identifier] = copy.deepcopy(conversation)
    app.state.ai.context_policy = ContextPolicy(input_tokens=8192, summary_tokens=128, recent_turns=1)
    app.state.ai.agent = ChatGraph(app.state.ai.context_policy)
    return user, identifier, conversation


def test_real_api_and_json_repository_commit_hide_and_reuse_summary(setup, tmp_path):
    app, client, admin, employee, _ = setup
    assert client.post('/api/v1/admin/ai/providers', headers=admin, json=PROVIDER).status_code == 200
    user, identifier, original = seed(app, employee)
    requests = []

    def upstream(request):
        payload = json.loads(request.content)
        requests.append(payload)
        is_summary = not payload.get('tools')
        return httpx.Response(200, json={'choices': [{'message': {'content':
            '历史目标：按店铺汇总，保留未完成任务。' if is_summary else '业务回复'}}]})

    app.state.ai.transport = httpx.MockTransport(upstream)
    response = client.post('/api/v1/chat/send', headers=employee,
        json={'message': '继续', 'conversation_id': identifier})
    assert response.status_code == 200
    assert 'context_state' not in response.json()['conversation']
    stored = json.loads((tmp_path / 'chats.json').read_text())['conversations'][identifier]
    assert stored['context_state']['covered_messages'] > 0
    assert stored['messages'][:-2] == original['messages']
    assert 'context_state' not in client.get('/api/v1/chat/conversations/' + identifier, headers=employee).json()
    count = len(requests)
    assert client.post('/api/v1/chat/send', headers=admin,
        json={'message': 'foreign', 'conversation_id': identifier}).status_code == 404
    assert len(requests) == count
    restarted = AiService(tmp_path, app.state.ai.transport, authorize=app.state.auth.identity)
    restarted.context_policy, restarted.agent = app.state.ai.context_policy, app.state.ai.agent
    summaries = sum(not payload.get('tools') for payload in requests)
    result = asyncio.run(restarted.send(user, ChatInput(message='下一步', conversation_id=identifier),
        session_token=employee['Authorization'][7:]))
    assert result['conversation']['messages'][-1]['content'] == '业务回复'
    assert sum(not payload.get('tools') for payload in requests) == summaries


def test_real_api_summary_failure_keeps_chat_file_byte_for_byte_unchanged(setup, tmp_path):
    app, client, admin, employee, _ = setup
    assert client.post('/api/v1/admin/ai/providers', headers=admin, json=PROVIDER).status_code == 200
    _, identifier, _ = seed(app, employee, size=3000)
    before = (tmp_path / 'chats.json').read_bytes()
    requests = []

    def upstream(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(200, json={'choices': [{'message': {'content': '第一段摘要。'}}]})
        return httpx.Response(503, json={'error': 'fixture failure'})

    app.state.ai.transport = httpx.MockTransport(upstream)
    result = client.post('/api/v1/chat/send', headers=employee, json={'message': '继续', 'conversation_id': identifier})
    assert result.status_code == 502 and result.json()['error']['code'] == 'provider_error'
    assert len(requests) == 2 and all(not payload.get('tools') for payload in requests)
    assert (tmp_path / 'chats.json').read_bytes() == before
    assert not app.state.ai.running and not app.state.ai.runs.active
