"""Owner-scoped, idempotent chat submission detached from the HTTP connection."""
import asyncio
import hashlib
import json
import logging
import time
import uuid

from .account_storage import atomic_json, read_json
from .auth import AuthFailure
from .tool_context import ExecutionContext, DispatchDenied

LIVE = {'queued', 'running'}


class ChatJobs:
    def __init__(self, ai):
        self.ai = ai
        self.tasks = {}
        self.lock = asyncio.Lock()
        self.closing = False

    def directory(self, user):
        from .account_storage import child
        return child(self.ai.dispatcher.files.account_root(user), 'tasks/chat-runs')

    def path(self, user, identifier):
        return self.directory(user)/(str(uuid.UUID(str(identifier)))+'.json')

    @staticmethod
    def key(user, identifier):return user['workspace_id'], user['id'], str(identifier)

    def read(self, user, identifier):
        path = self.path(user, identifier)
        if path.exists() and path.stat().st_size > 65536:
            raise AuthFailure('invalid_run', '任务状态文件异常。', 502)
        value = read_json(path)
        if not value or (value['user_id'],value['workspace_id']) != (user['id'],user['workspace_id']):
            raise AuthFailure('not_found', '任务不存在。', 404)
        if value['status'] in LIVE and self.key(user,identifier) not in self.tasks:
            value.update(status='interrupted',finished_at=time.time(),error={'code':'service_restarted',
                'message':'服务器执行已中断，未自动重跑。请核对已完成结果后继续。'})
            atomic_json(path,value)
        return value

    def view(self, user, value):
        result = {k:value.get(k) for k in ('request_id','conversation_id','status','created_at','finished_at','error','interrupted')}
        result.update(message=value['message'],phase='preparing',elapsed_ms=max(0,round(((value.get('finished_at') or time.time())-value['created_at'])*1000)))
        if value['status'] in LIVE:
            try:result.update(self.ai.runs.status(user,value['request_id']))
            except AuthFailure:pass
        return result

    async def submit(self, user, body, token):
        if self.closing:raise AuthFailure('service_stopping','服务器正在维护，请稍后提交。',503)
        identifier = str(body.request_id or uuid.uuid4())
        body = body.model_copy(update={'request_id':uuid.UUID(identifier)})
        encoded=json.dumps(body.model_dump(mode='json'),sort_keys=True,separators=(',',':')).encode()
        fingerprint=hashlib.sha256(encoded).hexdigest()
        async with self.lock:
            path=self.path(user,identifier)
            if path.exists():
                value=self.read(user,identifier)
                if value['fingerprint']!=fingerprint:raise AuthFailure('request_conflict','同一请求编号不能提交不同内容。',409)
                return self.view(user,value)
            if len(self.tasks)>=8:raise AuthFailure('chat_queue_full','等待的任务较多，请稍后提交。',429)
            conversation=str(body.conversation_id or uuid.uuid4())
            for key,task in self.tasks.items():
                if key[:2]==(user['workspace_id'],user['id']):
                    active=self.read(user,key[2])
                    if active['conversation_id']==conversation:raise AuthFailure('conversation_busy','当前对话正在处理。',409)
            if body.conversation_id:self.ai.conversation(user,conversation)
            value={'request_id':identifier,'user_id':user['id'],'workspace_id':user['workspace_id'],
                'conversation_id':conversation,'fingerprint':fingerprint,'message':body.message,
                'created_at':time.time(),'status':'queued','acknowledged':False}
            atomic_json(path,value)
            key=self.key(user,identifier)
            self.tasks[key]=asyncio.create_task(self.run(user,body,token,value))
            return self.view(user,value)

    async def run(self, user, body, token, value):
        path=self.path(user,value['request_id'])
        try:
            context=ExecutionContext(user['id'],user['workspace_id'],tuple(user['permissions']),value['conversation_id'],token)
            user=self.ai.dispatcher._principal(context)
            if 'ai:chat' not in user['permissions']:raise AuthFailure('forbidden','当前账号没有聊天权限。',403)
            value['status']='running';atomic_json(path,value)
            result=await self.ai.send(user,body,session_token=token,new_conversation_id=value['conversation_id'])
            value.update(status='completed',conversation_id=result['conversation']['id'],interrupted=result.get('interrupted',False))
        except asyncio.CancelledError:
            value.update(status='interrupted',error={'code':'service_restarted','message':'服务器执行已中断，未自动重跑；请核对已完成结果后继续。'})
            raise
        except (AuthFailure,DispatchDenied) as error:
            value.update(status='failed',error={'code':error.code,'message':error.message})
        except Exception as error:
            logging.getLogger(__name__).error('chat_job_failed request_id=%s exception_type=%s',value['request_id'],type(error).__name__)
            value.update(status='failed',error={'code':'chat_execution_failed','message':'任务执行失败，请提供任务编号排查。'})
        finally:
            value['finished_at']=time.time()
            try:atomic_json(path,value)
            finally:self.tasks.pop(self.key(user,value['request_id']),None)

    def status(self, user, identifier):return self.view(user,self.read(user,identifier))

    def pending(self, user):
        rows=[]
        for path in self.directory(user).glob('*.json'):
            value=self.read(user,path.stem)
            if not value.get('acknowledged'):rows.append(value)
        rows.sort(key=lambda x:x['created_at'],reverse=True)
        return {'jobs':[self.view(user,v) for v in rows[:20]]}

    def result(self, user, identifier):
        value=self.read(user,identifier)
        if value['status'] in LIVE:raise AuthFailure('request_busy','任务仍在执行。',409)
        if value['status']!='completed':
            error=value.get('error',{});raise AuthFailure(error.get('code','chat_interrupted'),error.get('message','任务中断。'),502)
        return {'conversation':self.ai.conversation(user,value['conversation_id']),
            'interrupted':value.get('interrupted',False),'request_id':value['request_id']}

    def acknowledge(self, user, identifier):
        value=self.read(user,identifier)
        if value['status'] in LIVE:raise AuthFailure('request_busy','任务尚未结束。',409)
        value['acknowledged']=True;atomic_json(self.path(user,identifier),value)
        return {'acknowledged':True}

    async def close(self):
        self.closing=True
        tasks=list(self.tasks.values())
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        self.tasks.clear()
