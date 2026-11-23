import asyncio
import json
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from pathlib import Path
from galaxy_cli.registry import ToolError, load_registry
from galaxy_cli.tools.interaction.ask import normalize
from xingxu.chat_runs import ChatRuns
from xingxu.auth import AuthFailure
from xingxu.tools import GalaxyDispatcher, ExecutionContext
from xingxu.dsh_runtime import DshTurn

BODY={'questions':[{'id':'shop','title':'查询哪家店铺？','options':[{'id':'all','label':'全部已授权店铺'},{'id':'one','label':'旗舰店'}]}]}
USER={'id':'owner','workspace_id':'workspace','permissions':['ai:chat','tools:galaxy_cli','galaxy:help']}

class ClarificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runs=ChatRuns();self.identifier=uuid.uuid4();self.ready=asyncio.Event();self.run=None
    async def start(self,body=BODY):
        async def worker():
            self.run=self.runs.begin(USER,self.identifier);self.ready.set()
            try:return await self.runs.ask(self.run,body)
            finally:self.runs.finish(self.run)
        task=asyncio.create_task(worker());await self.ready.wait();await asyncio.sleep(0);return task
    def answer(self,values):
        return self.runs.answer(USER,self.identifier,self.run.interaction['id'],values)
    async def test_no_default_owner_isolation_and_real_selected_answer(self):
        task=await self.start()
        state=self.runs.status(USER,self.identifier)
        self.assertEqual(state['phase'],'waiting');self.assertEqual(state['status'],'awaiting_input');self.assertFalse(task.done())
        for stranger in [{**USER,'id':'other'},{**USER,'workspace_id':'other'}]:
            with self.assertRaises(AuthFailure):self.runs.answer(stranger,self.identifier,self.run.interaction['id'],[{'question_id':'shop','option_id':'all'}])
            with self.assertRaises(AuthFailure):self.runs.status(stranger,self.identifier)
        self.assertFalse(task.done())
        self.answer([{'question_id':'shop','option_id':'one'}])
        result=await task;self.assertEqual(result['answers'][0]['answer'],'旗舰店');self.assertNotIn(self.runs.key(USER,self.identifier),self.runs.active)
    async def test_bad_answers_do_not_resume_and_duplicate_post_is_idempotent(self):
        task=await self.start();qid=self.run.interaction['id']
        for answers in [[],[{'question_id':'wrong','option_id':'one'}],[{'question_id':'shop','option_id':'forged'}],
                        [{'question_id':'shop','option_id':'one','text':'other'}],[{'question_id':'shop','text':'   '}]]:
            with self.assertRaises(AuthFailure):self.answer(answers)
            self.assertFalse(task.done())
        with self.assertRaises(AuthFailure):self.runs.answer(USER,self.identifier,uuid.uuid4(),[{'question_id':'shop','option_id':'all'}])
        answers=[{'question_id':'shop','text':'指定两个店铺'}]
        self.assertTrue(self.answer(answers)['accepted']);self.assertTrue(self.runs.answer(USER,self.identifier,qid,answers)['accepted'])
        with self.assertRaises(AuthFailure):self.runs.answer(USER,self.identifier,qid,[{'question_id':'shop','option_id':'all'}])
        self.assertEqual((await task)['answers'][0]['answer'],'指定两个店铺')
    async def test_stop_cleans_question_and_never_supplies_an_answer(self):
        task=await self.start();qid=self.run.interaction['id']
        await self.runs.stop(USER,self.identifier)
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertIsNone(self.run.interaction)
        with self.assertRaises(AuthFailure):self.runs.answer(USER,self.identifier,qid,[{'question_id':'shop','option_id':'all'}])
    async def test_two_conversations_do_not_mix(self):
        first=await self.start();first_run=self.run
        identifier=uuid.uuid4();second_run=None
        async def worker():
            nonlocal second_run
            second_run=self.runs.begin(USER,identifier)
            try:return await self.runs.ask(second_run,BODY)
            finally:self.runs.finish(second_run)
        second=asyncio.create_task(worker());await asyncio.sleep(0)
        with self.assertRaises(AuthFailure):self.runs.answer(USER,identifier,first_run.interaction['id'],[{'question_id':'shop','option_id':'all'}])
        self.runs.answer(USER,identifier,second_run.interaction['id'],[{'question_id':'shop','option_id':'all'}])
        self.assertEqual((await second)['answers'][0]['answer'],'全部已授权店铺');self.assertFalse(first.done())
        self.runs.answer(USER,self.identifier,first_run.interaction['id'],[{'question_id':'shop','option_id':'one'}]);await first
    async def test_dsh_gateway_waits_without_occupying_business_slot(self):
        with tempfile.TemporaryDirectory() as folder:
            dispatcher=GalaxyDispatcher(Path(folder),authorize=lambda _: {'user':USER});dispatcher.slots=asyncio.Semaphore(0)
            context=ExecutionContext(USER['id'],USER['workspace_id'],tuple(USER['permissions']),'conversation','private-test-session')
            ai=SimpleNamespace(dispatcher=dispatcher,runs=self.runs)
            async def worker():
                self.run=self.runs.begin(USER,self.identifier);self.run.interactive_questions=True
                try:
                    turn=DshTurn(ai,context,{'model':'fixture'},run=self.run,progress={},thinking=False,reasoning_effort=None)
                    return await turn.cli({'args':['interaction','ask','--input',json.dumps(BODY)]})
                finally:self.runs.finish(self.run)
            task=asyncio.create_task(worker())
            for _ in range(50):
                if self.run and self.run.interaction:break
                await asyncio.sleep(.01)
            self.assertIsNotNone(self.run.interaction);self.answer([{'question_id':'shop','option_id':'one'}])
            result=await asyncio.wait_for(task,1);self.assertEqual(result['answers'][0]['option_id'],'one')
            call={'id':'test','type':'function','function':{'name':'galaxy_cli','arguments':json.dumps({'args':['interaction','ask','--input',json.dumps(BODY)]})}}
            result=await dispatcher.execute(context,call)
            self.assertEqual(result['result']['error']['code'],'chat_context_required')
    async def test_real_chat_http_pauses_and_resumes_with_validated_answer(self):
        import httpx
        from xingxu.app import create_app
        from xingxu.ai import ProviderInput
        with tempfile.TemporaryDirectory() as folder:
            calls=[]
            def model(request):
                wire=json.loads(request.content);calls.append(wire)
                if len(calls)==1:
                    message={'role':'assistant','content':None,'tool_calls':[{'id':'question-call','type':'function','function':{'name':'galaxy_cli','arguments':json.dumps({'args':['interaction','ask','--input',json.dumps(BODY)]})}}]}
                else:
                    result=json.loads(next(m['content'] for m in reversed(wire['messages']) if m['role']=='tool'))
                    assert result['answers'][0]['answer']=='旗舰店'
                    message={'role':'assistant','content':'已确认只查询旗舰店。'}
                return httpx.Response(200,json={'choices':[{'message':message,'finish_reason':'tool_calls' if len(calls)==1 else 'stop'}]})
            app=create_app(Path(folder),ai_transport=httpx.MockTransport(model));auth=app.state.auth
            auth.import_accounts([('test-owner','test-only-password'),('test-other','other-only-password')])
            own=auth.login('test-owner','test-only-password',False,'127.0.0.1')
            other=auth.login('test-other','other-only-password',False,'127.0.0.1')
            ai=app.state.ai;ai.runtime_name='langgraph'
            ai.save_provider(ProviderInput(name='fixture',base_url='https://fixture.example/v1',api_key='disposable-fixture-key',model='fixture'))
            identifier=str(uuid.uuid4());headers={'Authorization':'Bearer '+own['access_token']}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                send=asyncio.create_task(client.post('/api/v1/chat/send',headers=headers,json={'message':'查今天订单','request_id':identifier,'interactive_questions':True}))
                state=None
                for _ in range(100):
                    response=await client.get('/api/v1/chat/runs/'+identifier,headers=headers)
                    if response.status_code==200 and response.json().get('interaction'):
                        state=response.json();break
                    await asyncio.sleep(.01)
                assert state is not None
                self.assertFalse(send.done());self.assertEqual(len(calls),1)
                body={'request_id':identifier,'interaction_id':state['interaction']['id'],'answers':[{'question_id':'shop','option_id':'one'}]}
                denied=await client.post('/api/v1/chat/answer',headers={'Authorization':'Bearer '+other['access_token']},json=body)
                self.assertEqual(denied.status_code,404)
                wrong=await client.post('/api/v1/chat/answer',headers=headers,json={**body,'answers':[{'question_id':'shop','option_id':'forged'}]})
                self.assertEqual(wrong.status_code,422);self.assertFalse(send.done())
                accepted=await client.post('/api/v1/chat/answer',headers=headers,json=body)
                self.assertEqual(accepted.status_code,200)
                completed=await asyncio.wait_for(send,5);self.assertEqual(completed.status_code,200,completed.text)
                message=completed.json()['conversation']['messages'][-1]
                self.assertEqual(message['content'],'已确认只查询旗舰店。');self.assertEqual(len(calls),2)
                self.assertEqual(message['tool_events'][0]['result']['answers'][0]['option_id'],'one')
                self.assertEqual((await client.get('/api/v1/chat/runs/'+identifier,headers=headers)).status_code,404)

    def test_schema_and_help(self):
        self.assertEqual(normalize(BODY),BODY)
        for body in [{'questions':BODY['questions']*2}, {'questions':[dict(BODY['questions'][0],options=[{'id':'x','label':'one'},{'id':'x','label':'two'}])]}, {**BODY,'default':'all'}]:
            with self.assertRaises(ToolError):normalize(body)
        registry=load_registry();self.assertEqual(registry.get('interaction.ask').category,'interaction')
        self.assertNotIn('HAR',registry.get('interaction.ask').description)

if __name__=='__main__':unittest.main()
