import asyncio
import json
from types import SimpleNamespace

import pytest

from xingxu.auth import AuthFailure
from xingxu.chat_runs import ChatRuns
from xingxu.tool_context import ExecutionContext
from xingxu.verification import Verifications
from test_excel import setup


def context(user='u',conversation='c'):
    return ExecutionContext(user,'w',('ai:chat',),conversation,'test',verification_inputs=True)


def test_human_input_is_private_one_time_and_account_bound():
    async def run():
        calls=[]
        async def submit(code,check):calls.append(code);check();return {'status':'downloaded'}
        manager=Verifications();ctx=context()
        first=manager.issue(ctx,channel='youzan',purpose='订单导出',destination='138****0000',submitter=submit)
        other=manager.issue(ctx,channel='other',purpose='另一渠道',destination='139****0000',submitter=submit)
        assert first['channel']=='youzan' and other['channel']=='other'
        entry=manager.get(ctx,first['challenge_id'])
        ui=SimpleNamespace(key=('w','u','request'),interaction=None,stop_requested=False,transition=lambda _:None)
        waiting=asyncio.create_task(manager.wait(ui,entry));await asyncio.sleep(0)
        assert ui.interaction['kind']=='verification'
        with pytest.raises(AuthFailure):manager.answer(ui,{'id':'b','workspace_id':'w'},entry.id,code='461237')
        with pytest.raises(AuthFailure):manager.get(context(conversation='another'),entry.id)
        manager.answer(ui,{'id':'u','workspace_id':'w'},entry.id,code='461237')
        result=await waiting
        assert result['status']=='received' and '461237' not in json.dumps(result)
        assert '461237' not in repr(entry) and entry.expires>0
        output=await manager.submit(ctx,entry.id,result['submission_id'],lambda:None)
        assert output['status']=='downloaded' and calls==['461237'] and entry.code is None and entry.submitter is None
        with pytest.raises(AuthFailure):await manager.submit(ctx,entry.id,result['submission_id'],lambda:None)
    asyncio.run(run())


def test_timeout_cancel_and_wrong_code_do_not_restart_deadline():
    async def run():
        async def wrong(code,check):raise AuthFailure('verification_invalid','wrong',409)
        manager=Verifications();ctx=context()
        entry=manager.get(ctx,manager.issue(ctx,channel='any',purpose='test',destination='masked',submitter=wrong)['challenge_id'])
        ui=SimpleNamespace(key=('w','u','r'),interaction=None,stop_requested=False,transition=lambda _:None)
        task=asyncio.create_task(manager.wait(ui,entry));await asyncio.sleep(0)
        deadline=entry.deadline
        manager.answer(ui,{'id':'u','workspace_id':'w'},entry.id,code='461237')
        received=await task
        result=await manager.submit(ctx,entry.id,received['submission_id'],lambda:None)
        assert result['status']=='verification_required' and entry.deadline==deadline and entry.code is None
        task=asyncio.create_task(manager.wait(ui,entry));await asyncio.sleep(0)
        manager.answer(ui,{'id':'u','workspace_id':'w'},entry.id,cancel=True)
        assert (await task)['status']=='cancelled' and ui.interaction is None
        fresh=manager.get(ctx,manager.issue(ctx,channel='any',purpose='test',destination='masked',submitter=wrong)['challenge_id'])
        fresh.deadline=manager.monotonic()+.01
        assert (await manager.wait(ui,fresh))['status']=='expired' and ui.interaction is None
        fresh=manager.get(ctx,manager.issue(ctx,channel='any',purpose='test',destination='masked',submitter=wrong)['challenge_id'])
        task=asyncio.create_task(manager.wait(ui,fresh));await asyncio.sleep(0);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert ui.interaction is None and fresh.state=='cancelled' and fresh.code is None
    asyncio.run(run())


def test_desktop_api_accepts_only_owner_and_never_exposes_code(setup):
    import uuid
    import httpx
    app,admin,employee,_=setup
    async def scenario():
        actor=employee['user'];runs=app.state.ai.runs;request_id=str(uuid.uuid4());run=runs.begin(actor,request_id)
        ctx=ExecutionContext(actor['id'],actor['workspace_id'],tuple(actor['permissions']),'conversation',employee['access_token'],verification_inputs=True)
        async def submit(code,check):check();return {'status':'verified'}
        manager=runs.verifications;entry=manager.get(ctx,manager.issue(ctx,channel='example',purpose='测试渠道',destination='138****0000',submitter=submit)['challenge_id'])
        wait=asyncio.create_task(manager.wait(run,entry));await asyncio.sleep(0)
        headers={'Authorization':'Bearer '+employee['access_token']}
        body={'request_id':request_id,'challenge_id':entry.id,'code':'461237'}
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                assert (await client.post('/api/v1/chat/verification',json=body)).status_code==401
                assert (await client.post('/api/v1/chat/verification',headers={'Authorization':'Bearer '+admin['access_token']},json=body)).status_code==409
                response=await client.get('/api/v1/chat/runs/'+request_id,headers=headers)
                assert response.json()['interaction']['kind']=='verification' and '461237' not in response.text
                response=await client.post('/api/v1/chat/verification',headers=headers,json=body)
                assert response.status_code==200 and response.json()=={'accepted':True}
            result=await wait
            assert '461237' not in json.dumps(result)
            from xingxu.tools import GalaxyDispatcher
            dispatcher=app.state.ai.dispatcher
            call={'id':'v','type':'function','function':{'name':'galaxy_cli','arguments':json.dumps({'args':['interaction','verification-submit','--input',json.dumps({'challenge_id':entry.id,'submission_id':result['submission_id']})]})}}
            event=await dispatcher.execute(ctx,call)
            assert event['status']=='success' and event['result']['status']=='verified'
            assert '461237' not in json.dumps(event)
        finally:
            if not wait.done():wait.cancel()
            runs.finish(run)
    asyncio.run(scenario())


def test_export_opens_verification_without_an_extra_model_turn(setup):
    import uuid
    from contextlib import asynccontextmanager
    app,_,employee,_=setup
    async def scenario():
        actor=employee['user'];dispatcher=app.state.ai.dispatcher
        ctx=ExecutionContext(actor['id'],actor['workspace_id'],tuple(actor['permissions']),'auto-dialog',employee['access_token'],verification_inputs=True)
        guarded=False
        @asynccontextmanager
        async def guard(user):
            nonlocal guarded
            guarded=True
            try:yield
            finally:guarded=False
        async def shops(*args,**kwargs):return [{'credentials':{'kdt_id':'94598170'}}]
        async def submit(code,check):return {'status':'downloaded'}
        async def export(*args,**kwargs):
            assert guarded and kwargs['interactive']
            return {**dispatcher.verifications.issue(ctx,channel='youzan',purpose='导出',destination='masked',submitter=submit),'job_id':'fixture','template':'AI财务订单表1'}
        async def interaction(body):
            assert not guarded and body['kind']=='verification'
            return {'status':'received','challenge_id':body['entry'].id,'submission_id':'one-time-reference','next_tool':'interaction.verification-submit'}
        dispatcher.operation_guard=guard;dispatcher.shop_resolver=shops
        dispatcher.order_export_provider=SimpleNamespace(run=export)
        body={'start_time':'2026-11-16 00:00:00','end_time':'2026-11-22 23:59:59'}
        call={'id':'auto-dialog','type':'function','function':{'name':'galaxy_cli','arguments':json.dumps({'shops':['94598170'],'args':['youzan','order-export','--input',json.dumps(body)]})}}
        event=await dispatcher.execute(ctx,call,interaction=interaction)
        assert event['status']=='success' and event['result']['status']=='received'
        assert event['result']['submission_id']=='one-time-reference'
    asyncio.run(scenario())
