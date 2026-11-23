import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from cryptography.fernet import Fernet

from xingxu.auth import AuthFailure
from xingxu.chat_runs import ChatRuns
from xingxu.dsh_runtime import DshTurn
from xingxu.tool_context import ExecutionContext


class LiveReasoningTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_thinking_is_visible_before_model_finishes_and_owner_scoped(self):
        visible, resume = asyncio.Event(), asyncio.Event()

        def event(text):
            return ('data: ' + json.dumps({'delta': {'type': 'thinking_delta', 'thinking': text}}, ensure_ascii=False) + '\n\n').encode()

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield event('先核对库存')
                visible.set()
                await resume.wait()
                yield event('，再确认金额。')

        user = {'id': 'user', 'workspace_id': 'workspace'}
        runs = ChatRuns()
        run = runs.begin(user, 'request')
        cipher = Fernet(Fernet.generate_key())
        ai = SimpleNamespace(cipher=cipher, advanced_settings={'model_response_mb': 2},
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream())))
        provider = {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-test',
                    'encrypted_api_key': cipher.encrypt(b'fixture-secret').decode()}
        turn = DshTurn(ai, ExecutionContext('user', 'workspace', (), 'conversation', 'session'), provider,
                       run=run, progress={}, thinking=True, reasoning_effort='high')
        turn.principal = Mock(return_value=user)
        writer = SimpleNamespace(write=lambda _: None, drain=self.drain)
        task = asyncio.create_task(turn.native_model({'messages': [{'role': 'user', 'content': 'test'}]}, writer))
        try:
            await asyncio.wait_for(visible.wait(), 2)
            self.assertFalse(task.done())
            self.assertEqual(runs.status(user, 'request')['reasoning_content'], '先核对库存')
            with self.assertRaises(AuthFailure):
                runs.status({**user, 'id': 'other-user'}, 'request')
            with self.assertRaises(AuthFailure):
                runs.status({**user, 'workspace_id': 'other-workspace'}, 'request')
            resume.set()
            await task
            self.assertEqual(run.reasoning_content, '先核对库存，再确认金额。')
            self.assertEqual(turn.reasoning, ['先核对库存，再确认金额。'])
        finally:
            resume.set()
            await task
            runs.finish(run)
        with self.assertRaises(AuthFailure):
            runs.status(user, 'request')

    async def test_current_system_policy_reaches_native_provider_with_string_and_block_prompts(self):
        cipher = Fernet(Fernet.generate_key())
        captured = []
        def upstream(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, content=b'data: {"type":"message_stop"}\n\n')
        ai = SimpleNamespace(cipher=cipher, advanced_settings={'model_response_mb': 2}, transport=httpx.MockTransport(upstream))
        provider = {'base_url':'https://api.deepseek.com','model':'deepseek-test',
                    'encrypted_api_key':cipher.encrypt(b'fixture-secret').decode()}
        turn = DshTurn(ai, ExecutionContext('user','workspace',(),'conversation','session'),provider,
                       run=None,progress={},thinking=False,reasoning_effort=None)
        turn.principal = Mock(return_value={'id':'user'})
        writer = SimpleNamespace(write=lambda _:None,drain=self.drain)
        for previous in ['DSH base instructions', [{'type':'text','text':'DSH cached prompt','cache_control':{'type':'ephemeral'}}]]:
            body = {'system':previous,'messages':[{'role':'user','content':'今天经营日报'}]}
            turn.system_prompt = 'CURRENT POLICY: use interaction.ask; shop choices A/B'
            await turn.native_model(body,writer)
            payload = captured[-1]
            if isinstance(previous,list):
                self.assertEqual(payload['system'][0],previous[0]);self.assertEqual(len(previous),1)
                self.assertEqual(payload['system'][-1]['text'],turn.system_prompt)
            else:
                self.assertTrue(payload['system'].startswith(previous));self.assertIn(turn.system_prompt,payload['system'])
            self.assertEqual(payload['messages'],body['messages'])
            self.assertNotIn('fixture-secret',json.dumps(payload))
        turn.system_prompt = 'CHANGED POLICY: selected shop B; current client supports choices'
        await turn.native_model({'messages':[{'role':'user','content':'继续'}]},writer)
        self.assertEqual(captured[-1]['system'],turn.system_prompt)
        self.assertNotIn('shop choices A/B',captured[-1]['system'])

    async def drain(self):
        pass
