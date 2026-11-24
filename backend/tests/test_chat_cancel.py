import asyncio
import json
import os
import signal
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from test_ai import setup, PROVIDER
from xingxu.ai import ChatInput, ProviderInput
from xingxu.auth import AuthFailure
from xingxu.tools import DispatchDenied, help_arguments, scoped_arguments


def identities(app, headers):
    return app.state.auth.identity(headers['Authorization'][7:])['user']


def test_cancel_model_request_preserves_turn_and_allows_followup(setup):
    app, _, admin, _, _ = setup
    ai = app.state.ai;ai.save_provider(ProviderInput(**PROVIDER))
    user = identities(app, admin)
    async def scenario():
        started, cleaned = asyncio.Event(), asyncio.Event()
        async def upstream(request):
            started.set()
            try:await asyncio.Event().wait()
            finally:cleaned.set()
        ai.transport = httpx.MockTransport(upstream)
        request_id = uuid.uuid4()
        task = asyncio.create_task(ai.send(user, ChatInput(message='fixture question', request_id=request_id), session_token=admin['Authorization'][7:]))
        await started.wait()
        assert await ai.cancel(user, request_id) == {'requested': True}
        response = await task
        assert response['interrupted'] is True and cleaned.is_set()
        assert response['conversation']['messages'][1]['interrupted'] is True
        assert response['conversation']['messages'][1]['content'] == '已停止回复。'
        assert not ai.running and not ai.runs.active
        ai.transport = httpx.MockTransport(lambda _: httpx.Response(200, json={'choices':[{'message':{'content':'继续完成'}}]}))
        followup = await ai.send(user, ChatInput(message='继续', conversation_id=response['conversation']['id']), session_token=admin['Authorization'][7:])
        assert followup['conversation']['messages'][-1]['content'] == '继续完成'
        assert len(followup['conversation']['messages']) == 4
    asyncio.run(scenario())


def test_stop_before_send_is_scoped_to_owner_and_does_not_call_provider(setup):
    app, _, admin, employee, _ = setup
    ai = app.state.ai;ai.save_provider(ProviderInput(**PROVIDER))
    user, other = identities(app, admin), identities(app, employee)
    ai._model_message = AsyncMock(return_value=({'role':'assistant','content':'ok'}, {}, ''))
    async def scenario():
        identifier = uuid.uuid4()
        await ai.cancel(other, identifier)
        response = await ai.send(user, ChatInput(message='other user cannot interrupt', request_id=identifier))
        assert response['interrupted'] is False
        ai._model_message.assert_awaited_once()
        await ai.cancel(user, identifier)
        response = await ai.send(user, ChatInput(message='immediate cancellation', request_id=identifier))
        assert response['interrupted'] is True
        ai._model_message.assert_awaited_once()
    asyncio.run(scenario())


def test_foreign_user_cannot_stop_active_request_and_repeat_stop_is_safe(setup):
    app, _, admin, employee, _ = setup
    ai = app.state.ai;ai.save_provider(ProviderInput(**PROVIDER))
    user, other = identities(app, admin), identities(app, employee)
    async def scenario():
        started = asyncio.Event()
        async def upstream(*args, **kwargs):started.set();await asyncio.Event().wait()
        ai._model_message = upstream
        identifier = uuid.uuid4()
        task = asyncio.create_task(ai.send(user, ChatInput(message='isolation', request_id=identifier)))
        await started.wait()
        await ai.cancel(other, identifier)
        assert not task.done()
        await asyncio.gather(ai.cancel(user, identifier), ai.cancel(user, identifier))
        assert (await task)['interrupted'] is True
    asyncio.run(scenario())


def test_tool_child_is_killed_audited_and_completed_tool_result_is_retained(setup, tmp_path):
    app, _, admin, _, _ = setup
    ai = app.state.ai;ai.save_provider(ProviderInput(**PROVIDER));user=identities(app, admin)
    executable=tmp_path/'fixture-cli';marker=tmp_path/'started'
    executable.write_text('#!/usr/bin/python3\nimport pathlib,time\npathlib.Path('+repr(str(marker))+').touch()\ntime.sleep(30)\n')
    executable.chmod(0o700);ai.dispatcher.executable=executable
    call = lambda ident: {'id':ident,'type':'function','function':{'name':'galaxy_cli','arguments':'{"args":["help"]}'}}
    batch={'role':'assistant','content':None,'tool_calls':[call('first'),call('second')]}
    ai._model_message=AsyncMock(return_value=(batch, {}, '已完成的思考'))
    async def scenario():
        original = ai.dispatcher._run;count=0
        async def run(*args, **kwargs):
            nonlocal count
            count+=1
            if count==1:return {'version':'test','data':[]}
            return await original(*args, **kwargs)
        ai.dispatcher._run=run
        processes=[];spawn=asyncio.create_subprocess_exec
        async def capture(*args, **kwargs):
            process=await spawn(*args,**kwargs);processes.append(process);return process
        from unittest.mock import patch
        identifier=uuid.uuid4()
        with patch('asyncio.create_subprocess_exec',side_effect=capture):
            task=asyncio.create_task(ai.send(user, ChatInput(message='tool cancel',request_id=identifier),session_token=admin['Authorization'][7:]))
            while not marker.exists():await asyncio.sleep(.005)
            await ai.cancel(user,identifier)
            response=await task
        assert processes[0].returncode is not None
        message=response['conversation']['messages'][-1]
        assert message['reasoning_content']=='已完成的思考'
        assert [e['status'] for e in message['tool_events']]==['success','cancelled']
        assert message['tool_events'][-1]['result']['error']['code']=='interrupted'
        saved=ai.conversation(user,response['conversation']['id'],include_context=True)['messages'][-1]['model_context']
        assert all('tool_calls' not in m and m['role']!='tool' for m in saved)
        with ai.dispatcher.audit.transaction(write=False) as state:
            assert sorted(r['status'] for r in state['records'].values())==['cancelled','success']
        ai._model_message.assert_awaited_once()
    asyncio.run(scenario())


def test_external_task_cancellation_is_not_recorded_as_user_stop(setup):
    app, _, admin, _, _=setup
    ai=app.state.ai;ai.save_provider(ProviderInput(**PROVIDER));user=identities(app,admin)
    async def scenario():
        started=asyncio.Event()
        async def upstream(*args,**kwargs):started.set();await asyncio.Event().wait()
        ai._model_message=upstream
        task=asyncio.create_task(ai.send(user,ChatInput(message='external cancel')))
        await started.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert not ai.running and not ai.runs.active
        assert ai.list_conversations(user)['conversations']==[]
    asyncio.run(scenario())


def test_cancel_during_cli_spawn_reaps_child_before_acknowledging(setup, tmp_path):
    app, _, admin, _, _ = setup
    ai = app.state.ai;ai.save_provider(ProviderInput(**PROVIDER));user = identities(app, admin)
    executable = tmp_path / 'spawn-fixture'
    executable.write_text('#!/usr/bin/python3\nimport time\ntime.sleep(60)\n')
    executable.chmod(0o700);ai.dispatcher.executable = executable
    ai._model_message = AsyncMock(return_value=({'role': 'assistant', 'content': None, 'tool_calls': [{
        'id': 'spawn', 'type': 'function', 'function': {'name': 'galaxy_cli', 'arguments': '{"args":["help"]}'}}]}, {}, ''))
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        spawn = asyncio.create_subprocess_exec
        processes = []
        async def delayed(*args, **kwargs):
            process = await spawn(*args, **kwargs)
            processes.append(process)
            started.set()
            await release.wait()
            return process
        from unittest.mock import patch
        request_id = uuid.uuid4()
        with patch('asyncio.create_subprocess_exec', side_effect=delayed):
            task = asyncio.create_task(ai.send(user, ChatInput(message='stop during spawn', request_id=request_id), session_token=admin['Authorization'][7:]))
            try:
                await asyncio.wait_for(started.wait(), 2)
                stopping = asyncio.create_task(ai.cancel(user, request_id))
                await asyncio.sleep(.02)
                assert not stopping.done(), 'stop must wait for the newly spawned process to be reaped'
                release.set()
                await asyncio.wait_for(stopping, 2)
                assert (await task)['interrupted']
                assert processes[0].returncode is not None
                assert not ai.running and not ai.runs.active
            finally:
                release.set()
                for process in processes:
                    try:os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:pass
                    await process.communicate()
    asyncio.run(scenario())


def test_cancel_endpoint_requires_login_valid_request_and_ignores_owner_override(setup):
    _, client, admin, _, _=setup
    url='/api/v1/chat/cancel'
    assert client.post(url,json={'request_id':str(uuid.uuid4())}).status_code==401
    assert client.post(url,headers=admin,json={'request_id':'not-a-uuid'}).status_code==422
    assert client.post(url,headers=admin,json={'request_id':str(uuid.uuid4()),'user_id':'another'}).status_code==422
    assert client.post(url,headers=admin,json={'request_id':str(uuid.uuid4())}).json()=={'requested':True}


def test_wrong_command_and_help_scope_explain_how_to_correct_without_relaxing_permissions(setup):
    app, _, admin, _, _=setup
    from xingxu.tools import ExecutionContext
    user=identities(app,admin)
    context=ExecutionContext(user['id'],user['workspace_id'],tuple(user['permissions']),'fixture',admin['Authorization'][7:])
    async def scenario():
        for args in (['youzan','customer','list'],['help']):
            result=await app.state.ai.dispatcher.execute(context,{'id':'fixture','type':'function','function':{'name':'galaxy_cli','arguments':json.dumps({'args':args,'shops':'all'})}})
            assert result['status']=='denied'
            assert ('真实工具名' if args[0]=='youzan' else '不要传 shops') in result['result']['error']['message']
    asyncio.run(scenario())


def test_http_stop_completes_send_response_and_does_not_return_server_error(setup):
    app, _, admin, _, _ = setup
    ai = app.state.ai;ai.save_provider(ProviderInput(**PROVIDER))
    async def scenario():
        started=asyncio.Event()
        async def upstream(request):started.set();await asyncio.Event().wait()
        ai.transport=httpx.MockTransport(upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
            identifier=str(uuid.uuid4())
            send=asyncio.create_task(client.post('/api/v1/chat/send',headers=admin,json={'message':'http cancellation','request_id':identifier}))
            await started.wait()
            stopped=await client.post('/api/v1/chat/cancel',headers=admin,json={'request_id':identifier})
            response=await send
            assert stopped.status_code==200 and response.status_code==200
            assert response.json()['interrupted'] is True
            assert len(response.json()['conversation']['messages'])==2
    asyncio.run(scenario())
