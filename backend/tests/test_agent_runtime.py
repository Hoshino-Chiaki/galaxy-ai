"""Offline tests of the actual LangGraph runtime; no app server or credentials."""
import asyncio
import copy
import json

import pytest

from xingxu.agent_runtime import AgentFailure, ChatGraph, ContextPolicy, input_size
from xingxu.conversation_context import ConversationContext, history_messages


def call(identifier):
    return {'id': identifier, 'type': 'function', 'function': {
        'name': 'galaxy_cli', 'arguments': '{"args":["help"]}'}}


async def unchanged(messages):
    return messages


def history(turns, size=20, *, tool=False):
    result = []
    for index in range(turns):
        result.append({'id': f'user-{index}', 'role': 'user', 'content': f'目标-{index} ' + 'x' * size})
        assistant = {'id': f'assistant-{index}', 'role': 'assistant', 'content': f'结论-{index} ' + 'y' * size}
        if tool:
            assistant['model_context'] = [
                {'role': 'assistant', 'content': None, 'reasoning_content': 'private-reasoning', 'tool_calls': [call(f'call-{index}')]},
                {'role': 'tool', 'tool_call_id': f'call-{index}', 'content': json.dumps({'shop': f'shop-{index}', 'total': index})},
                {'role': 'assistant', 'content': assistant['content'], 'reasoning_content': 'confirmed-reasoning'},
            ]
        result.append(assistant)
    return result


def test_graph_runs_ordered_tool_batches_and_preserves_protocol():
    graph = ChatGraph(ContextPolicy())
    seen, executed = [], []
    batch = {'role': 'assistant', 'content': None, 'reasoning_content': 'provider-original',
             'tool_calls': [call('one'), call('two')]}

    async def model(messages):
        seen.append(copy.deepcopy(messages))
        return batch if len(seen) == 1 else {'role': 'assistant', 'content': 'done'}

    async def tool(request):
        executed.append(request['id'])
        return {'role': 'tool', 'tool_call_id': request['id'], 'content': '{"ok":true}'}

    messages = [{'role': 'user', 'content': 'question'}]
    result = asyncio.run(graph.run(messages, model=model, tool=tool, prepare=unchanged, tools=[]))
    assert result == 'done' and executed == ['one', 'two']
    assert seen[1][1] == batch
    assert [message.get('tool_call_id') for message in seen[1][2:]] == ['one', 'two']
    assert messages == [{'role': 'user', 'content': 'question'}]


def test_graph_continues_beyond_langgraph_default_recursion_limit():
    graph = ChatGraph(ContextPolicy())
    calls = []

    async def model(messages):
        calls.append(1)
        return ({'role': 'assistant', 'content': None, 'tool_calls': [call(str(len(calls)))]}
                if len(calls) <= 35 else {'role': 'assistant', 'content': 'finished'})

    async def tool(request):
        return {'role': 'tool', 'tool_call_id': request['id'], 'content': '{}'}

    assert asyncio.run(graph.run([{'role': 'user', 'content': 'long task'}],
        model=model, tool=tool, prepare=unchanged, tools=[])) == 'finished'
    assert len(calls) == 36


def test_graph_does_not_retry_completed_business_writes_after_provider_failure():
    graph = ChatGraph(ContextPolicy())
    writes = []

    async def model(messages):
        if len(messages) == 1:
            return {'role': 'assistant', 'content': None, 'tool_calls': [call('write-once')]}
        raise RuntimeError('provider failed after write')

    async def tool(request):
        writes.append(request['id'])
        return {'role': 'tool', 'tool_call_id': request['id'], 'content': '{"ok":true}'}

    with pytest.raises(RuntimeError, match='provider failed'):
        asyncio.run(graph.run([{'role': 'user', 'content': 'write'}],
            model=model, tool=tool, prepare=unchanged, tools=[]))
    assert writes == ['write-once']


def test_graph_cancellation_reaches_active_tool_and_never_starts_next_tool():
    async def scenario():
        graph = ChatGraph(ContextPolicy())
        started, cleaned = asyncio.Event(), asyncio.Event()
        executed = []

        async def model(messages):
            return {'role': 'assistant', 'content': None, 'tool_calls': [call('first'), call('second')]}

        async def tool(request):
            executed.append(request['id'])
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        task = asyncio.create_task(graph.run([{'role': 'user', 'content': 'cancel'}],
            model=model, tool=tool, prepare=unchanged, tools=[]))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set() and executed == ['first']

    asyncio.run(scenario())


def test_graph_requests_have_separate_runtime_dependencies_when_concurrent():
    graph = ChatGraph(ContextPolicy())

    async def scenario():
        async def run(owner):
            async def model(messages):
                await asyncio.sleep(0)
                assert messages[0]['content'] == owner
                return {'role': 'assistant', 'content': owner}
            return await graph.run([{'role': 'user', 'content': owner}],
                model=model, tool=None, prepare=unchanged, tools=[])
        assert await asyncio.gather(run('user-a'), run('user-b')) == ['user-a', 'user-b']

    asyncio.run(scenario())


def test_context_counts_system_prompts_tool_definitions_and_tool_outputs():
    policy = ContextPolicy(input_tokens=4096)
    graph = ChatGraph(policy)
    calls = []

    async def model(messages):
        calls.append(1)
        return {'role': 'assistant', 'content': None, 'tool_calls': [call('large')]}

    async def tool(request):
        return {'role': 'tool', 'tool_call_id': request['id'], 'content': '大' * 4000}

    with pytest.raises(AgentFailure) as error:
        asyncio.run(graph.run([{'role': 'user', 'content': 'query'}],
            model=model, tool=tool, prepare=unchanged, tools=[]))
    assert error.value.code == 'context_too_large' and calls == [1]
    with pytest.raises(AgentFailure):
        asyncio.run(graph.run([{'role': 'user', 'content': 'query'}], model=model, tool=tool,
            prepare=unchanged, tools=[{'description': '大' * 4000}]))
    assert calls == [1]


def test_short_history_is_preserved_beyond_the_previous_forty_message_cutoff():
    original = history(25)
    memory = ConversationContext(original, None, ContextPolicy(), deepseek=True)

    async def should_not_summarize(messages):
        pytest.fail('Short input should not incur an extra model request')

    base = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'next'}]
    wire = asyncio.run(memory.prepare(base, [], should_not_summarize))
    assert len(wire) == 52 and wire[1]['content'] == original[0]['content']
    assert memory.snapshot() is None


def test_summary_is_incremental_persistent_and_keeps_complete_recent_tool_bundles():
    policy = ContextPolicy(input_tokens=4096, summary_tokens=128, recent_turns=1)
    original = history(12, size=600, tool=True)
    before = copy.deepcopy(original)
    memory = ConversationContext(original, None, policy, deepseek=True)
    summaries = []

    async def summarize(messages):
        assert input_size(messages, []) <= policy.input_tokens
        summaries.append(copy.deepcopy(messages))
        return '历史目标：按 shop-0 汇总，结论已确认，未执行写操作。'

    base = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'next'}]
    wire = asyncio.run(memory.prepare(base, [], summarize))
    saved = memory.snapshot()
    assert summaries and saved['covered_messages'] > 0
    assert saved['through_message_id'] == original[saved['covered_messages'] - 1]['id']
    assert wire[0] == base[0] and wire[1]['role'] == 'user' and '历史对话摘要' in wire[1]['content']
    assert wire[-1] == base[-1] and input_size(wire, []) <= policy.input_tokens
    assert original == before
    assert 'private-reasoning' not in json.dumps(summaries)
    assert any(message.get('reasoning_content') == 'private-reasoning' for message in wire)
    assert any(message.get('tool_call_id') == 'call-11' for message in wire)
    restored = ConversationContext(original, saved, policy, deepseek=True)
    count = len(summaries)
    assert asyncio.run(restored.prepare(base, [], summarize)) == wire
    assert len(summaries) == count


def test_invalid_summary_cursor_rebuilds_from_original_history():
    original = history(3)
    memory = ConversationContext(original, {'version': 1, 'covered_messages': 2,
        'through_message_id': 'wrong-id', 'summary': 'untrusted-stale-cache'}, ContextPolicy())
    assert memory.snapshot() is None
    wire = asyncio.run(memory.prepare([{'role': 'user', 'content': 'next'}], [], unchanged))
    assert wire[0]['content'] == original[0]['content']
    assert 'untrusted-stale-cache' not in str(wire)


def test_large_historical_tool_result_is_chunked_without_omitting_any_text():
    policy = ContextPolicy(input_tokens=4096, summary_tokens=128, recent_turns=1)
    original = history(3, size=600)
    original[1]['model_context'] = [
        {'role': 'assistant', 'content': None, 'tool_calls': [call('huge')]},
        {'role': 'tool', 'tool_call_id': 'huge', 'content': '边界-marker-' * 4000},
        {'role': 'assistant', 'content': 'done'},
    ]
    memory = ConversationContext(original, None, policy)
    fragments = []

    async def summarize(messages):
        assert input_size(messages, []) <= policy.input_tokens
        fragments.append(json.loads(messages[1]['content'])['history_fragment'])
        return '历史大结果已确认。'

    asyncio.run(memory.prepare([{'role': 'user', 'content': 'next'}], [], summarize))
    covered = memory.snapshot()['covered_messages']
    expected = json.dumps([{'created_at': item.get('created_at'), 'messages': history_messages([item], for_summary=True)}
        for item in original[:covered]], ensure_ascii=False, separators=(',', ':'))
    assert len(fragments) > 1 and ''.join(fragments) == expected


def test_partial_summary_failure_or_cancellation_never_advances_saved_cursor():
    policy = ContextPolicy(input_tokens=4096, summary_tokens=128, recent_turns=1)
    original = history(10, size=2000)

    for failure in (RuntimeError('provider unavailable'), asyncio.CancelledError()):
        memory = ConversationContext(original, None, policy)
        calls = []

        async def summarize(messages):
            calls.append(1)
            if len(calls) > 1:
                raise failure
            return '已处理第一段。'

        with pytest.raises(type(failure)):
            asyncio.run(memory.prepare([{'role': 'user', 'content': 'next'}], [], summarize))
        assert len(calls) == 2 and memory.snapshot() is None


def test_new_summary_only_processes_uncovered_history_and_preserves_record_dates():
    policy = ContextPolicy(input_tokens=4096, summary_tokens=128, recent_turns=1)
    original = history(10, size=900)
    original[0]['created_at'] = 1794787200
    memory = ConversationContext(original, None, policy)
    fragments = []

    async def summarize(messages):
        fragments.append(json.loads(messages[1]['content']))
        return '历史目标与日期已保存。'

    base = [{'role': 'user', 'content': 'next'}]
    asyncio.run(memory.prepare(base, [], summarize))
    saved = memory.snapshot()
    assert str(original[0]['created_at']) in ''.join(fragment['history_fragment'] for fragment in fragments)
    more = history(10, size=900)
    for message in more:
        message['id'] = 'new-' + message['id']
        message['content'] = '新增记录 ' + message['content']
    all_history = original + more
    restored = ConversationContext(all_history, saved, policy)
    fragments.clear()
    asyncio.run(restored.prepare(base, [], summarize))
    new_cursor = restored.snapshot()['covered_messages']
    newly_covered = all_history[saved['covered_messages']:new_cursor]
    expected = json.dumps([{'created_at': item.get('created_at'), 'messages': history_messages([item], for_summary=True)}
        for item in newly_covered], ensure_ascii=False, separators=(',', ':'))
    assert ''.join(fragment['history_fragment'] for fragment in fragments) == expected
    assert fragments[0]['existing_summary'] == saved['summary']


def test_tool_growth_reclaims_older_history_without_changing_the_current_turn():
    policy = ContextPolicy(input_tokens=4096, summary_tokens=128, recent_turns=1)
    memory = ConversationContext(history(4, size=900), None, policy)
    graph = ChatGraph(policy)
    prepared, summaries, requests = False, [], []
    result_text = 'current-result-' + 'x' * 7000

    async def summarize(messages):
        summaries.append(messages)
        return '较早的目标与约束已保存。'

    async def prepare(messages):
        nonlocal prepared
        result = await (memory.fit(messages, [], summarize) if prepared else memory.prepare(messages, [], summarize))
        prepared = True
        return result

    async def model(messages):
        requests.append(copy.deepcopy(messages))
        return ({'role': 'assistant', 'content': None, 'tool_calls': [call('current')]}
                if len(requests) == 1 else {'role': 'assistant', 'content': 'done'})

    async def tool(request):
        assert not summaries  # The initial history fits; growth triggers compaction.
        return {'role': 'tool', 'tool_call_id': request['id'], 'content': result_text}

    assert asyncio.run(graph.run([{'role': 'user', 'content': 'current question'}],
        model=model, tool=tool, prepare=prepare, tools=[])) == 'done'
    assert summaries and memory.snapshot()['covered_messages'] > 0
    assert requests[-1][-1] == {'role': 'tool', 'tool_call_id': 'current', 'content': result_text}
    assert requests[-1][-3]['content'] == 'current question'
    assert input_size(requests[-1], []) <= policy.input_tokens


def test_oversized_model_tool_arguments_are_rejected_before_executing_a_tool():
    graph = ChatGraph(ContextPolicy(input_tokens=4096))

    async def model(messages):
        request = call('oversized')
        request['function']['arguments'] = '大' * 4000
        return {'role': 'assistant', 'content': None, 'tool_calls': [request]}

    async def tool(request):
        pytest.fail('An oversized call must be rejected before execution')

    with pytest.raises(AgentFailure) as error:
        asyncio.run(graph.run([{'role': 'user', 'content': 'question'}],
            model=model, tool=tool, prepare=unchanged, tools=[]))
    assert error.value.code == 'context_too_large'


@pytest.mark.parametrize('value', ['invalid', '0', '4095', '1000001'])
def test_context_configuration_rejects_invalid_budgets(monkeypatch, value):
    monkeypatch.setenv('GALAXY_AI_INPUT_TOKEN_BUDGET', value)
    with pytest.raises(ValueError, match='GALAXY_AI_INPUT_TOKEN_BUDGET'):
        ContextPolicy.from_environment()
