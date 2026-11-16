"""Exercise the real AiService wiring with in-memory storage and a fake gateway.

The production repository and gateway require Linux. Only those dependencies are
substituted here, in an isolated module namespace; HTTP adaptation, graph execution,
conversation ownership, summary commits and owner-scoped cancellation are real.
"""
import asyncio
import copy
import importlib.util
import json
import sys
import types
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from xingxu import agent_runtime, conversation_context, tool_context
from test_agent_runtime import history, call


@pytest.fixture
def isolated_ai(monkeypatch, tmp_path):
    prefix = '_galaxy_graph_service_test'
    source = Path(__file__).resolve().parents[1] / 'xingxu'
    package = types.ModuleType(prefix)
    package.__path__ = [str(source)]
    monkeypatch.setitem(sys.modules, prefix, package)

    def module(name, **attributes):
        value = types.ModuleType(prefix + '.' + name)
        value.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, value.__name__, value)
        return value

    class AuthFailure(Exception):
        def __init__(self, code, message, status=401, retry_after=None):
            self.code, self.message, self.status, self.retry_after = code, message, status, retry_after

    stores = {}

    class Repository:
        def __init__(self, directory, name='auth', fields=()):
            Path(directory).mkdir(parents=True, exist_ok=True)
            self.key = (str(directory), name)
            stores.setdefault(self.key, {'schema_version': 1, **{key: {} for key in fields}})

        @contextmanager
        def transaction(self, *, write=True):
            state = copy.deepcopy(stores[self.key])
            yield state
            if write:
                stores[self.key] = state

    class Dispatcher:
        def __init__(self, directory, authorize):
            pass

        def definitions(self, context):
            return [{'type': 'function', 'function': {'name': 'galaxy_cli', 'parameters': {'type': 'object'}}}]

        async def execute(self, context, request):
            return {'status': 'success', 'result': {'fixture': request['id']}}

    class ToolPrompts:
        def __init__(self, directory):
            pass

        def messages(self, user, permissions):
            return []

    module('auth', AuthFailure=AuthFailure)
    module('store', JsonRepository=Repository)
    module('tools', COMMAND_PERMISSIONS={}, ExecutionContext=tool_context.ExecutionContext, GalaxyDispatcher=Dispatcher)
    module('tool_prompts', ToolPrompts=ToolPrompts)
    for name, value in [('agent_runtime', agent_runtime), ('conversation_context', conversation_context), ('tool_context', tool_context)]:
        monkeypatch.setitem(sys.modules, prefix + '.' + name, value)

    def load(name):
        spec = importlib.util.spec_from_file_location(prefix + '.' + name, source / (name + '.py'))
        value = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, value)
        spec.loader.exec_module(value)
        return value

    load('chat_runs')
    ai = load('ai')
    requests = []

    def upstream(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'done'}}]})

    service = ai.AiService(tmp_path, httpx.MockTransport(upstream))
    service.save_provider(ai.ProviderInput(name='Fixture', model='deepseek-fixture',
        base_url='https://fixture.invalid/v1', api_key='fixture-only-secret'))
    return ai, service, {'id': 'user-a', 'workspace_id': 'workspace-a', 'permissions': []}, requests, AuthFailure


def seed(service, user):
    identifier = str(uuid.uuid4())
    conversation = {'id': identifier, 'user_id': user['id'], 'workspace_id': user['workspace_id'],
        'title': 'fixture', 'created_at': 1, 'updated_at': 1, 'model': 'deepseek-fixture', 'messages': history(12, 600, tool=True)}
    with service.chats.transaction() as state:
        state['conversations'][identifier] = conversation
    return identifier, conversation


def test_real_service_uses_graph_and_preserves_model_parameters_and_tool_gateway_identity(isolated_ai):
    ai, service, user, requests, _ = isolated_ai
    executed = []

    def upstream(request):
        payload = json.loads(request.content)
        requests.append(payload)
        message = ({'content': None, 'reasoning_content': 'original-protocol', 'tool_calls': [call('one'), call('two')]}
                   if len(requests) == 1 else {'content': 'all done', 'reasoning_content': 'final-reasoning'})
        return httpx.Response(200, json={'choices': [{'message': message}], 'usage': {'total_tokens': 10}})

    async def execute(context, request):
        executed.append((context, request))
        return {'status': 'denied' if request['id'] == 'two' else 'success', 'result': {'id': request['id']}}

    service.transport = httpx.MockTransport(upstream)
    service.dispatcher.execute = execute
    result = asyncio.run(service.send(user, ai.ChatInput(message='question', thinking=True, reasoning_effort='low'),
        session_token='fixture-session'))
    assert result['usage'] == {'total_tokens': 20}
    assert [context.user_id for context, _ in executed] == [user['id'], user['id']]
    assert all(context.session_token == 'fixture-session' for context, _ in executed)
    assert requests[1]['messages'][-3]['reasoning_content'] == 'original-protocol'
    assert requests[1]['thinking'] == {'type': 'enabled'} and requests[1]['reasoning_effort'] == 'low'
    reply = result['conversation']['messages'][-1]
    assert [event['status'] for event in reply['tool_events']] == ['success', 'denied']
    assert reply['reasoning_content'] == 'original-protocol\n\nfinal-reasoning'
    assert 'model_context' not in reply and 'fixture-only-secret' not in json.dumps(result)


def test_summary_is_saved_with_completed_turn_and_reused_after_service_restart(isolated_ai, tmp_path):
    ai, service, user, requests, _ = isolated_ai
    identifier, before = seed(service, user)
    service.context_policy = agent_runtime.ContextPolicy(input_tokens=4096, summary_tokens=128, recent_turns=1)
    service.agent = agent_runtime.ChatGraph(service.context_policy)

    def upstream(request):
        payload = json.loads(request.content)
        requests.append(payload)
        summary = not payload.get('tools')
        if summary:
            assert payload['thinking'] == {'type': 'disabled'} and payload['max_tokens'] == 128
        return httpx.Response(200, json={'choices': [{'message': {
            'content': '历史目标：按门店汇总，待处理事项保留。' if summary else 'answer'}}],
            'usage': {'total_tokens': 10}})

    service.transport = httpx.MockTransport(upstream)
    result = asyncio.run(service.send(user, ai.ChatInput(message='continue', conversation_id=identifier)))
    saved = service.conversation(user, identifier, include_context=True)
    assert len(saved['messages']) == len(before['messages']) + 2 and saved['context_state']['covered_messages'] > 0
    assert 'context_state' not in result['conversation'] and 'context_state' not in service.conversation(user, identifier)
    assert result['usage']['total_tokens'] == len(requests) * 10
    restarted = ai.AiService(tmp_path, service.transport)
    restarted.context_policy, restarted.agent = service.context_policy, service.agent
    old_summary_requests = sum(not request.get('tools') for request in requests)
    asyncio.run(restarted.send(user, ai.ChatInput(message='next', conversation_id=identifier)))
    assert sum(not request.get('tools') for request in requests) == old_summary_requests
    assert any('历史目标' in message['content'] for message in requests[-1]['messages'])


def test_failed_summary_keeps_original_history_and_returns_existing_error_format(isolated_ai):
    ai, service, user, requests, AuthFailure = isolated_ai
    identifier, before = seed(service, user)
    service.context_policy = agent_runtime.ContextPolicy(input_tokens=4096, summary_tokens=128, recent_turns=1)
    service.agent = agent_runtime.ChatGraph(service.context_policy)
    service.transport = httpx.MockTransport(lambda _: httpx.Response(401, json={'private': 'fixture-only-secret'}))
    with pytest.raises(AuthFailure) as error:
        asyncio.run(service.send(user, ai.ChatInput(message='continue', conversation_id=identifier)))
    assert error.value.code == 'provider_error'
    assert service.conversation(user, identifier, include_context=True) == before
    assert not service.running and not service.runs.active


def test_truncated_history_summary_recovers_without_replaying_business_tools(isolated_ai):
    ai, service, user, requests, _ = isolated_ai
    identifier, before = seed(service, user)
    service.context_policy = agent_runtime.ContextPolicy(input_tokens=4096, summary_tokens=128, recent_turns=1)
    service.agent = agent_runtime.ChatGraph(service.context_policy)
    summary_caps, executed = [], []

    def upstream(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if not payload.get('tools'):
            summary_caps.append(payload['max_tokens'])
            assert payload['thinking'] == {'type': 'disabled'}
            if len(summary_caps) == 1:
                # Even a syntactically complete invented call in a truncated
                # summary is unusable and must never reach the tool gateway.
                return httpx.Response(200, json={'choices': [{'finish_reason': 'length',
                    'message': {'content': 'incomplete-summary-must-not-be-saved',
                                'tool_calls': [call('historical-write-must-not-run')]}}]})
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
                'content': '已读取五个片区；现有文件 generated/report.xlsx，用户要求复用，尚未完成建表。'}}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': '已恢复历史，复用已有数据。'}}]})

    async def never_execute(*args):
        executed.append(args)
        pytest.fail('Summarizing history must not replay any business tool')

    service.transport = httpx.MockTransport(upstream)
    service.dispatcher.execute = never_execute
    result = asyncio.run(service.send(user, ai.ChatInput(message='不要重新拉取数据', conversation_id=identifier)))
    saved = service.conversation(user, identifier, include_context=True)
    assert summary_caps[:2] == [128, 256]
    assert saved['messages'][:-2] == before['messages']
    assert 'incomplete-summary-must-not-be-saved' not in json.dumps(saved)
    assert 'generated/report.xlsx' in saved['context_state']['summary']
    assert result['conversation']['messages'][-1]['content'] == '已恢复历史，复用已有数据。'
    assert not executed and not service.running


def test_repeated_summary_truncation_is_bounded_and_preserves_original_history(isolated_ai):
    ai, service, user, requests, AuthFailure = isolated_ai
    identifier, before = seed(service, user)
    service.context_policy = agent_runtime.ContextPolicy(input_tokens=4096, summary_tokens=128, recent_turns=1)
    service.agent = agent_runtime.ChatGraph(service.context_policy)

    def upstream(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert not payload.get('tools')
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length',
            'message': {'content': 'partial'}}]})

    service.transport = httpx.MockTransport(upstream)
    with pytest.raises(AuthFailure) as error:
        asyncio.run(service.send(user, ai.ChatInput(message='continue', conversation_id=identifier)))
    assert error.value.code == 'summary_output_truncated'
    assert [request['max_tokens'] for request in requests] == [128, 256, 512]
    assert service.conversation(user, identifier, include_context=True) == before
    assert not service.running and not service.runs.active


def test_summary_cancellation_uses_existing_owner_scoped_stop_and_keeps_valid_history(isolated_ai):
    ai, service, user, _, _ = isolated_ai
    identifier, before = seed(service, user)
    service.context_policy = agent_runtime.ContextPolicy(input_tokens=4096, summary_tokens=128, recent_turns=1)
    service.agent = agent_runtime.ChatGraph(service.context_policy)

    async def scenario():
        started, cleaned = asyncio.Event(), asyncio.Event()

        async def upstream(request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        service.transport = httpx.MockTransport(upstream)
        request_id = uuid.uuid4()
        task = asyncio.create_task(service.send(user, ai.ChatInput(message='continue', conversation_id=identifier, request_id=request_id)))
        await asyncio.wait_for(started.wait(), 5)
        await service.cancel({**user, 'id': 'other-user'}, request_id)
        assert not task.done()
        await service.cancel(user, request_id)
        result = await task
        assert result['interrupted'] and cleaned.is_set()
        saved = service.conversation(user, identifier, include_context=True)
        assert saved['messages'][:-2] == before['messages'] and 'context_state' not in saved
        assert saved['messages'][-1]['model_context'] == [
            {'role': 'assistant', 'content': '已停止回复。', 'reasoning_content': ''}]
        assert not service.running and not service.runs.active

    asyncio.run(scenario())


def test_conversation_ownership_is_checked_before_summarizing_or_calling_model(isolated_ai):
    ai, service, user, requests, AuthFailure = isolated_ai
    identifier, _ = seed(service, user)
    for other in ({**user, 'id': 'other'}, {**user, 'workspace_id': 'other'}):
        with pytest.raises(AuthFailure) as error:
            asyncio.run(service.send(other, ai.ChatInput(message='foreign', conversation_id=identifier)))
        assert error.value.code == 'not_found'
    assert not requests


def test_summary_tool_requests_cannot_reach_gateway_or_commit_history(isolated_ai):
    ai, service, user, _, AuthFailure = isolated_ai
    identifier, before = seed(service, user)
    service.context_policy = agent_runtime.ContextPolicy(input_tokens=4096, summary_tokens=128, recent_turns=1)
    service.agent = agent_runtime.ChatGraph(service.context_policy)
    service.transport = httpx.MockTransport(lambda _: httpx.Response(200, json={'choices': [{'message': {
        'content': 'bad summary', 'tool_calls': [call('should-never-run')]}}]}))

    async def never_execute(*args):
        pytest.fail('Summary requests must not dispatch tools')

    service.dispatcher.execute = never_execute
    with pytest.raises(AuthFailure) as error:
        asyncio.run(service.send(user, ai.ChatInput(message='continue', conversation_id=identifier)))
    assert error.value.code == 'invalid_summary'
    assert service.conversation(user, identifier, include_context=True) == before


def test_gateway_interruption_retains_completed_events_and_discards_incomplete_tool_protocol(isolated_ai):
    ai, service, user, _, _ = isolated_ai
    service.transport = httpx.MockTransport(lambda _: httpx.Response(200, json={'choices': [{'message': {
        'content': None, 'reasoning_content': 'completed thinking', 'tool_calls': [call('first'), call('second')]}}]}))

    async def scenario():
        started = asyncio.Event()

        async def execute(context, request):
            event = {'status': 'success', 'result': {'id': request['id']}}
            if request['id'] == 'first':
                return event
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise tool_context.ToolInterrupted({'status': 'cancelled', 'result': {'error': {'code': 'interrupted'}}})

        service.dispatcher.execute = execute
        request_id = uuid.uuid4()
        task = asyncio.create_task(service.send(user, ai.ChatInput(message='tools', request_id=request_id)))
        await asyncio.wait_for(started.wait(), 5)
        await service.cancel(user, request_id)
        result = await task
        reply = result['conversation']['messages'][-1]
        assert result['interrupted'] and [event['status'] for event in reply['tool_events']] == ['success', 'cancelled']
        assert reply['reasoning_content'] == 'completed thinking'
        saved = service.conversation(user, result['conversation']['id'], include_context=True)
        assert len(saved['messages'][-1]['model_context']) == 1
        assert not any('tool_calls' in message or message['role'] == 'tool' for message in saved['messages'][-1]['model_context'])

    asyncio.run(scenario())


def test_external_cancellation_does_not_commit_a_user_stop_or_summary(isolated_ai):
    ai, service, user, _, _ = isolated_ai

    async def scenario():
        started = asyncio.Event()

        async def upstream(request):
            started.set()
            await asyncio.Event().wait()

        service.transport = httpx.MockTransport(upstream)
        task = asyncio.create_task(service.send(user, ai.ChatInput(message='external cancel')))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.list_conversations(user)['conversations'] == []
        assert not service.running and not service.runs.active

    asyncio.run(scenario())


def test_large_identical_tool_results_are_referenced_but_every_execution_and_raw_event_is_retained(isolated_ai):
    ai, service, user, requests, _ = isolated_ai
    large_result = {'catalog': '目录条目' * 3500}
    executed = []

    def upstream(request):
        payload = json.loads(request.content)
        requests.append(payload)
        reply = ({'content': None, 'tool_calls': [call(f'catalog-{index}') for index in range(7)]}
                 if len(requests) == 1 else {'content': 'seven calls finished'})
        return httpx.Response(200, json={'choices': [{'message': reply}]})

    async def execute(context, request):
        executed.append(request['id'])
        return {'status': 'success', 'result': copy.deepcopy(large_result)}

    service.transport = httpx.MockTransport(upstream)
    service.dispatcher.execute = execute
    result = asyncio.run(service.send(user, ai.ChatInput(message='read the same catalog seven times')))
    assert len(executed) == 7 and len(requests) == 2
    wire_results = [message for message in requests[1]['messages'] if message['role'] == 'tool']
    assert json.loads(wire_results[0]['content']) == large_result
    assert all(json.loads(message['content'])['same_result_as_tool_call_id'] == 'catalog-0' for message in wire_results[1:])
    assert all(event['result'] == large_result for event in result['conversation']['messages'][-1]['tool_events'])


def test_run_clock_accumulates_model_time_without_counting_tool_wait(isolated_ai):
    _, service, user, _, _ = isolated_ai

    async def scenario():
        identifier = uuid.uuid4()
        run = service.runs.begin(user, identifier)
        run.transition('model')
        await asyncio.sleep(.02)
        run.transition('tool')
        first = service.runs.status(user, identifier)
        await asyncio.sleep(.02)
        second = service.runs.status(user, identifier)
        assert second['model_elapsed_ms'] == first['model_elapsed_ms'] >= 10
        assert second['elapsed_ms'] >= first['elapsed_ms'] + 10
        run.transition('model')
        await asyncio.sleep(.02)
        third = service.runs.status(user, identifier)
        assert third['model_elapsed_ms'] >= second['model_elapsed_ms'] + 10
        service.runs.finish(run)

    asyncio.run(scenario())
