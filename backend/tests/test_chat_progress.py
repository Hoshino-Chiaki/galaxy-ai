"""Owner-scoped live progress API with real temporary authentication/storage."""
import asyncio
import json
import sys
import uuid

import httpx
import pytest

if sys.platform == 'win32':
    pytest.skip('Production repositories and file gateway require Linux', allow_module_level=True)

from test_ai import PROVIDER, setup
from test_agent_runtime import call
from test_context_integration import seed


def test_progress_api_requires_login_and_valid_request_id(setup):
    _, client, admin, _, _ = setup
    assert client.get('/api/v1/chat/runs/' + str(uuid.uuid4())).status_code == 401
    assert client.get('/api/v1/chat/runs/not-a-uuid', headers=admin).status_code == 422
    assert client.get('/api/v1/chat/runs/' + str(uuid.uuid4()), headers=admin).status_code == 404


def test_live_model_progress_is_owner_scoped_and_completion_saves_timing(setup):
    app, _, admin, employee, _ = setup
    from xingxu.ai import ProviderInput
    app.state.ai.save_provider(ProviderInput(**PROVIDER))

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def upstream(request):
            started.set()
            await release.wait()
            return httpx.Response(200, json={'choices': [{'message': {'content': 'finished'}}]})

        app.state.ai.transport = httpx.MockTransport(upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
            identifier = str(uuid.uuid4())
            task = asyncio.create_task(client.post('/api/v1/chat/send', headers=admin,
                json={'message': 'private-user-question', 'request_id': identifier}))
            await asyncio.wait_for(started.wait(), 5)
            await asyncio.sleep(.02)
            url = '/api/v1/chat/runs/' + identifier
            progress = await client.get(url, headers=admin)
            assert progress.status_code == 200
            value = progress.json()
            assert value['phase'] == 'model' and value['model_calls'] == 1
            assert value['elapsed_ms'] >= value['model_elapsed_ms'] >= 10
            assert value['tools_completed'] == 0 and value['tool_name'] is None
            assert 'private-user-question' not in progress.text and PROVIDER['api_key'] not in progress.text
            assert (await client.get(url, headers=employee)).status_code == 404
            release.set()
            result = await task
            assert result.status_code == 200
            conversation = result.json()['conversation']
            reply = conversation['messages'][-1]
            assert reply['elapsed_ms'] >= reply['model_elapsed_ms'] >= value['model_elapsed_ms']
            stored = await client.get('/api/v1/chat/conversations/' + conversation['id'], headers=admin)
            assert stored.json()['messages'][-1]['model_elapsed_ms'] == reply['model_elapsed_ms']
            assert (await client.get(url, headers=admin)).status_code == 404

    asyncio.run(scenario())


def test_tool_progress_transitions_and_never_exposes_raw_arguments_or_results(setup):
    app, _, admin, _, _ = setup
    from xingxu.ai import ProviderInput
    app.state.ai.save_provider(ProviderInput(**PROVIDER))

    async def scenario():
        tool_started, tool_release, model_started, model_release = (asyncio.Event() for _ in range(4))
        calls = []

        async def upstream(request):
            calls.append(json.loads(request.content))
            if len(calls) == 1:
                request_call = call('one')
                request_call['function']['arguments'] = json.dumps({'args': ['help'], 'private': 'argument-secret'})
                message = {'content': None, 'tool_calls': [request_call]}
            else:
                model_started.set()
                await model_release.wait()
                message = {'content': 'done'}
            return httpx.Response(200, json={'choices': [{'message': message}]})

        async def tool(context, request):
            tool_started.set()
            await tool_release.wait()
            return {'status': 'success', 'result': {'private': 'result-secret'}}

        app.state.ai.transport = httpx.MockTransport(upstream)
        app.state.ai.dispatcher.execute = tool
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
            identifier = str(uuid.uuid4())
            task = asyncio.create_task(client.post('/api/v1/chat/send', headers=admin,
                json={'message': 'tool question', 'request_id': identifier}))
            await asyncio.wait_for(tool_started.wait(), 5)
            url = '/api/v1/chat/runs/' + identifier
            progress = await client.get(url, headers=admin)
            assert progress.json()['phase'] == 'tool' and progress.json()['tool_name'] == '读取工具目录'
            assert 'argument-secret' not in progress.text and 'result-secret' not in progress.text
            tool_release.set()
            await asyncio.wait_for(model_started.wait(), 5)
            value = (await client.get(url, headers=admin)).json()
            assert value['phase'] == 'model' and value['tools_completed'] == 1 and value['model_calls'] == 2
            assert value['tool_name'] is None
            model_release.set()
            assert (await task).status_code == 200

    asyncio.run(scenario())


def test_summary_progress_is_visible_and_user_can_cancel_during_summary(setup):
    app, _, admin, _, _ = setup
    from xingxu.ai import ProviderInput
    app.state.ai.save_provider(ProviderInput(**PROVIDER))
    _, conversation_id, _ = seed(app, admin)

    async def scenario():
        started = asyncio.Event()

        async def upstream(request):
            assert not json.loads(request.content).get('tools')
            started.set()
            await asyncio.Event().wait()

        app.state.ai.transport = httpx.MockTransport(upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
            identifier = str(uuid.uuid4())
            task = asyncio.create_task(client.post('/api/v1/chat/send', headers=admin,
                json={'message': 'continue', 'conversation_id': conversation_id, 'request_id': identifier}))
            await asyncio.wait_for(started.wait(), 5)
            value = (await client.get('/api/v1/chat/runs/' + identifier, headers=admin)).json()
            assert value['phase'] == 'summarizing' and value['summary_calls'] == 1 and value['model_calls'] == 0
            assert value['model_elapsed_ms'] == 0
            stopped = await client.post('/api/v1/chat/cancel', headers=admin, json={'request_id': identifier})
            result = await task
            assert stopped.status_code == 200 and result.json()['interrupted'] is True
            assert result.json()['conversation']['messages'][-1]['model_elapsed_ms'] == 0

    asyncio.run(scenario())
