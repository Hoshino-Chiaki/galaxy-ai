import asyncio
import json
import uuid
from types import SimpleNamespace
import httpx
import pytest
from fastapi.testclient import TestClient
from xingxu.app import create_app
from xingxu.ai import ChatInput, ProviderInput
from xingxu.auth import AuthFailure
from xingxu.dsh_runtime import DshTurn
from xingxu.tool_context import ExecutionContext
from xingxu.usage import SCOPE, UsageScope, normalize
from xingxu.usage_routes import report


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    monkeypatch.setenv('GALAXY_AI_RUNTIME', 'langgraph')
    responses = []
    def upstream(_):
        value = responses.pop(0)
        if isinstance(value, Exception): raise value
        return httpx.Response(200, json=value)
    app = create_app(tmp_path, ai_transport=httpx.MockTransport(upstream))
    auth = app.state.auth
    auth.import_accounts([('owner', 'fixture-password'), ('employee', 'fixture-password')])
    logged = [auth.login(name, 'fixture-password', False, 'test') for name in ('owner', 'employee')]
    provider = app.state.ai.save_provider(ProviderInput(name='Fixture', base_url='https://fixture.example/v1', api_key='fixture-secret', model='fixture'))
    return app, logged, responses, provider


def response(inputs=100, outputs=10, **kwargs):
    return {'choices': [{'message': {'role': 'assistant', 'content': 'fixture reply'}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': inputs, 'completion_tokens': outputs, 'total_tokens': inputs + outputs,
            'prompt_cache_hit_tokens': inputs // 2, 'prompt_cache_miss_tokens': inputs - inputs // 2}, **kwargs}


def test_calls_persist_independently_of_deleted_chat_and_sources(env):
    app, logged, responses, _ = env
    ai, auth = app.state.ai, app.state.auth
    async def run():
        replies = []
        for entry, source in zip([logged[0], logged[1], logged[0], logged[1]], ['desktop', 'wechat', 'feishu', 'schedule']):
            responses.append(response())
            value = await ai.send(entry['user'], ChatInput(message='fixture', request_id=uuid.uuid4()),
                session_token=entry['access_token'], entry_point=source)
            replies.append(value)
            assert value['conversation']['messages'][-1]['usage']['total_tokens'] == 110
            assert value['conversation']['messages'][-1]['usage']['complete']
            ai.delete_conversation(entry['user'], value['conversation']['id'])
        rows = ai.usage_store.aggregate('default')
        assert sum(row['total_tokens'] for row in rows) == 440
        assert {row['entry_point'] for row in rows} == {'desktop', 'wechat', 'feishu', 'schedule'}
        assert ai.usage_store.aggregate('another-workspace') == []
        assert sum(row['total_tokens'] for row in ai.usage_store.aggregate('default', user_id=logged[1]['user']['id'])) == 220
        await app.state.clawbot.close()
    asyncio.run(run())


def test_parallel_users_never_share_metering_context(env):
    app, logged, responses, _ = env
    async def run():
        responses.extend([response(100, 10), response(200, 20)])
        result = await asyncio.gather(*(app.state.ai.send(entry['user'], ChatInput(message='fixture', request_id=uuid.uuid4()), session_token=entry['access_token']) for entry in logged))
        totals = [r['conversation']['messages'][-1]['usage']['total_tokens'] for r in result]
        assert sorted(totals) == [110, 220]
        assert [sum(r['total_tokens'] for r in app.state.ai.usage_store.aggregate('default', user_id=e['user']['id'])) for e in logged] == totals
        assert SCOPE.get() is None
        await app.state.clawbot.close()
    asyncio.run(run())


def test_tool_roundtrip_counts_each_model_request(env):
    app, logged, responses, _ = env
    entry = logged[0]
    tool = response(100, 10)
    tool['choices'][0]['message'] = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'time-fixture', 'type': 'function', 'function': {'name': 'galaxy_cli', 'arguments': json.dumps({'args': ['time', 'now']})}}]}
    async def run():
        responses.extend([tool, response(200, 20)])
        value = await app.state.ai.send(entry['user'], ChatInput(message='查询时间', request_id=uuid.uuid4()), session_token=entry['access_token'])
        usage = value['conversation']['messages'][-1]['usage']
        assert usage['requests'] == 2 and usage['input_tokens'] == 300 and usage['output_tokens'] == 30
        assert usage['total_tokens'] == 330
        await app.state.clawbot.close()
    asyncio.run(run())


def test_truncated_and_missing_usage_are_not_lost_or_invented(env):
    app, logged, responses, provider = env
    ai = app.state.ai
    async def run():
        scope = UsageScope(ai.usage_store, logged[0]['user'], str(uuid.uuid4()), str(uuid.uuid4()), 'desktop')
        token = SCOPE.set(scope)
        try:
            truncated = response()
            truncated['choices'][0]['finish_reason'] = 'length'
            responses.append(truncated)
            with pytest.raises(AuthFailure): await ai._model_message(ai.snapshot()[0], [])
            responses.append(response(usage={}))
            await ai._model_message(ai.snapshot()[0], [])
            responses.append(httpx.ReadTimeout('fixture timeout'))
            with pytest.raises(AuthFailure): await ai._model_message(ai.snapshot()[0], [])
            usage = scope.result()
            assert usage['total_tokens'] == 110 and usage['requests'] == 3
            assert usage['unknown_requests'] == 2 and usage['failed_requests'] == 2
            assert not usage['complete']
        finally:
            SCOPE.reset(token)
        await app.state.clawbot.close()
    asyncio.run(run())


def test_native_stream_counts_final_cumulative_usage_once_and_keeps_cache(env):
    app, logged, _, _ = env
    ai = app.state.ai
    async def drain(): pass
    async def run():
        provider = ai.snapshot()[0]
        user = logged[0]['user']
        scope = UsageScope(ai.usage_store, user, str(uuid.uuid4()), str(uuid.uuid4()), 'wechat')
        token = SCOPE.set(scope)
        try:
            turn = DshTurn(ai, ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), str(uuid.uuid4()), logged[0]['access_token']), provider, run=None, progress={}, thinking=False, reasoning_effort=None)
        finally:
            SCOPE.reset(token)
        frames = [dict(type='message_start', message={'usage': {'input_tokens': 50, 'cache_read_input_tokens': 50, 'cache_creation_input_tokens': 0, 'output_tokens': 0}}),
            dict(type='message_delta', usage={'input_tokens': 60, 'cache_read_input_tokens': 70, 'cache_creation_input_tokens': 0, 'output_tokens': 20}), dict(type='message_stop')]
        ai.transport = httpx.MockTransport(lambda _: httpx.Response(200, content=''.join('data: ' + json.dumps(frame) + '\n\n' for frame in frames), headers={'content-type':'text/event-stream'}))
        await turn.native_model({'messages': [{'role':'user','content':'fixture'}]}, SimpleNamespace(write=lambda _:None, drain=drain))
        usage = scope.result()
        assert usage['requests'] == 1 and usage['input_tokens'] == 130 and usage['output_tokens'] == 20
        assert usage['total_tokens'] == 150 and usage['cache_hit_tokens'] == 70 and usage['cache_miss_tokens'] == 60
        assert usage['complete']
        await app.state.clawbot.close()
    asyncio.run(run())


def test_cancelled_stream_preserves_partial_known_usage(env):
    app, logged, _, _ = env
    ai = app.state.ai
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield ('data: ' + json.dumps({'type':'message_start','message':{'usage':{'input_tokens':100,'output_tokens':0}}}) + '\n\n').encode()
            raise asyncio.CancelledError()
    async def drain():pass
    async def run():
        user = logged[0]['user']
        scope = UsageScope(ai.usage_store, user, str(uuid.uuid4()), str(uuid.uuid4()), 'feishu')
        token = SCOPE.set(scope)
        try:turn = DshTurn(ai, ExecutionContext(user['id'],user['workspace_id'],tuple(user['permissions']),str(uuid.uuid4()),logged[0]['access_token']),ai.snapshot()[0],run=None,progress={},thinking=False,reasoning_effort=None)
        finally:SCOPE.reset(token)
        ai.transport = httpx.MockTransport(lambda _:httpx.Response(200,stream=Stream()))
        with pytest.raises(asyncio.CancelledError):
            await turn.native_model({'messages':[{'role':'user','content':'fixture'}]},SimpleNamespace(write=lambda _:None,drain=drain))
        result = scope.result()
        assert result['input_tokens'] == 100 and result['unknown_requests'] == 1 and result['failed_requests'] == 1
        assert len(ai.usage_store.aggregate('default')) == 1
        await app.state.clawbot.close()
    asyncio.run(run())


def test_company_permissions_and_personal_scope(env):
    app, logged, responses, _ = env
    async def seed():
        for entry in logged:
            responses.append(response())
            await app.state.ai.send(entry['user'], ChatInput(message='fixture',request_id=uuid.uuid4()),session_token=entry['access_token'])
        await app.state.clawbot.close()
    asyncio.run(seed())
    client = TestClient(app)
    headers = [{'Authorization':'Bearer '+entry['access_token']} for entry in logged]
    assert client.get('/api/v1/usage').status_code == 401
    assert client.get('/api/v1/usage',headers=headers[1]).status_code == 403
    assert client.get('/api/v1/usage/me',headers=headers[1]).json()['all']['total_tokens'] == 110
    company = client.get('/api/v1/usage',headers=headers[0]).json()
    assert company['totals']['total_tokens'] == 220 and len(company['users']) == 2
    assert client.get('/api/v1/usage?start=2026-13-01',headers=headers[0]).status_code == 422
    assert client.get('/api/v1/usage?start=2026-11-24&end=2026-11-01',headers=headers[0]).status_code == 422


def test_usage_normalization_never_treats_boolean_or_missing_as_measured_zero():
    assert normalize({'prompt_tokens': True, 'completion_tokens': -1})['input_tokens'] is None
    assert normalize({})['output_tokens'] is None
    assert normalize({'prompt_tokens': 100, 'completion_tokens': 20, 'prompt_tokens_details': {'cached_tokens': 80}})['cache_miss_tokens'] == 20
