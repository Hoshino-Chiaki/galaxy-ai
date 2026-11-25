"""DSH must see original task rules after a failed or cancelled checkpoint."""
import asyncio
import copy
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from xingxu.ai import AiService
from xingxu.agent_runtime import ContextPolicy, input_size
from xingxu.conversation_context import ConversationContext
from xingxu.dsh_runtime import DshTurn
from xingxu.tool_context import ExecutionContext


def history():
    workflow = '数量为“-”必须查询原订单并以负数计算；表6保留完整激励明细，禁止汇总。'
    return [
        {'id':'u1','role':'user','content':workflow,'created_at':1},
        {'id':'a1','role':'assistant','content':'执行中断，已完成的操作不能重跑。',
         'model_context':[{'role':'assistant','content':'历史工具结果：'+('x'*180000)}], 'created_at':2}]


def test_restore_ignores_legacy_summary_budget_and_retains_original_rules():
    original = history();before = copy.deepcopy(original)
    memory = ConversationContext(original, {'version':1,'summary':'旧摘要漏掉了业务规则',
        'covered_messages':2,'through_message_id':'a1'}, ContextPolicy(input_tokens=48000),deepseek=True)
    current=[{'role':'system','content':'本轮权限与系统提示'}, {'role':'user','content':'不要重新开始，继续'}]
    assert input_size(memory.restore_for_dsh(current),[]) > 48000
    restored = memory.restore_for_dsh(current)
    assert restored[0] == current[0] and restored[-1] == current[-1]
    assert original[0]['content'] in [m['content'] for m in restored]
    assert not any('旧摘要' in str(m) for m in restored)
    assert restored[2]['content']==original[1]['model_context'][0]['content']
    restored[1]['content']='changed'
    assert original==before


def test_dsh_complete_never_calls_legacy_summary_or_dispatches_history(tmp_path):
    ai=AiService(tmp_path,authorize=lambda _: {'user':{}})
    ai.runtime_name='dsh'
    ai._model_message=AsyncMock(side_effect=AssertionError('legacy summary/model called'))
    ai.dispatcher.execute=AsyncMock(side_effect=AssertionError('historical tool replayed'))
    memory=ConversationContext(history(),None,ContextPolicy(input_tokens=4096),deepseek=True)
    memory.prepare=AsyncMock(side_effect=AssertionError('legacy prepare called'))
    memory.fit=AsyncMock(side_effect=AssertionError('legacy fit called'))
    seen={}
    async def execute(turn,messages,*,prepare,checkpoint):
        restored=await prepare(messages)
        seen['restored']=restored;seen['checkpoint']=checkpoint
        assert input_size(restored,[])>48000
        return ('已恢复',{},'',[],[{'role':'assistant','content':'已恢复'}],{'run_id':'new-checkpoint'})
    checkpoint={}
    with patch.object(DshTurn,'execute',execute):
        result=asyncio.run(ai.complete({'model':'deepseek-flash'},[{'role':'user','content':'继续'}],
            context=ExecutionContext('u','w',(),'c','s'),memory=memory,dsh_commit=checkpoint))
    assert result[0]=='已恢复' and checkpoint=={'run_id':'new-checkpoint'}
    assert history()[0]['content'] in [m['content'] for m in seen['restored']]
    ai._model_message.assert_not_awaited();ai.dispatcher.execute.assert_not_awaited()
    memory.prepare.assert_not_awaited();memory.fit.assert_not_awaited()


def test_valid_dsh_checkpoint_sends_only_current_message(tmp_path):
    async def exercise():
        captured=[]
        async def worker(reader,writer):
            body=json.loads(await reader.readline());captured.append(body)
            writer.write(json.dumps({'type':'result','run_id':body['run_id'],'content':'已续接'}).encode()+b'\n')
            await writer.drain();await reader.read();writer.close();await writer.wait_closed()
        server=await asyncio.start_unix_server(worker,str(tmp_path/'worker.sock'))
        ai=SimpleNamespace(dispatcher=SimpleNamespace(directory=tmp_path,files=SimpleNamespace(root=tmp_path)),
                           context_policy=SimpleNamespace(input_tokens=4096))
        turn=DshTurn(ai,ExecutionContext('u','w',(),'c','s'),{'base_url':'https://api.deepseek.com','model':'deepseek-flash'},
            run=None,progress={},thinking=True,reasoning_effort='high')
        prepare=AsyncMock(side_effect=AssertionError('valid checkpoint rebuilt history'))
        try:
            with tempfile.TemporaryDirectory(prefix='dsh-history-') as broker, \
                    patch.dict(os.environ,{'GALAXY_DSH_BROKER_DIR':broker}), \
                    patch('xingxu.dsh_runtime.SOCKET',str(tmp_path/'worker.sock')), \
                    patch.object(turn,'principal',return_value={'permissions':[]}):
                result=await turn.execute([{'role':'user','content':'继续当前任务'}],prepare=prepare,checkpoint={'run_id':'previous-checkpoint'})
            assert result[0]=='已续接';prepare.assert_not_awaited()
            assert captured[0]['previous']=='previous-checkpoint'
            assert captured[0]['input']['resume'] is True and captured[0]['input']['prompt']=='继续当前任务'
            assert captured[0]['input']['context_window']==1048576
        finally:
            server.close();await server.wait_closed()
    asyncio.run(exercise())
