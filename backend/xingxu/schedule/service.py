"""Server scheduling above channel integrations; no desktop or provider credentials stored here."""
import asyncio
import copy
import fcntl
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from ..auth import AuthFailure, digest
from .rules import normalize, occurrences
from .storage import ScheduleStore

log=logging.getLogger(__name__)
PERMISSIONS={name:'schedule:'+name for name in ('create','read','update','delete')}


def identifier(value):
    try:return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):raise AuthFailure('invalid_input','任务编号无效。',422) from None


class ScheduleService:
    def __init__(self,directory,auth,ai,clock=time.time):
        self.store=ScheduleStore(directory);self.auth=auth;self.ai=ai;self.clock=clock
        self.worker=None;self.lease=None;self.active={};self.wake=asyncio.Event();self.event_loop=None
        self.execution_slot=asyncio.Semaphore(1)

    def notify(self):
        if self.event_loop and self.event_loop.is_running():self.event_loop.call_soon_threadsafe(self.wake.set)
        else:self.wake.set()

    def check(self,user,permission):
        if permission not in user.get('permissions',[]):raise AuthFailure('forbidden','当前账号没有该任务调度权限。',403)

    def current_user(self,user):
        with self.auth.repository.transaction(write=False) as state:raw=copy.deepcopy(state['users'].get(user['id']))
        if not raw or not raw.get('active') or raw['workspace_id']!=user['workspace_id']:
            raise AuthFailure('forbidden','任务所属账号已停用或不再可用。',403)
        current=self.auth._public(raw)
        for permission in ('ai:chat','tools:galaxy_cli','schedule:create'):
            self.check(current,permission)
        return current,raw

    @staticmethod
    def owns(record,user):return record.get('user_id')==user['id'] and record.get('workspace_id')==user['workspace_id']

    def get_record(self,state,user,value):
        record=state['tasks'].get(identifier(value))
        if not record or record.get('deleted_at') or not self.owns(record,user):raise AuthFailure('not_found','任务不存在。',404)
        return record

    def public(self,task):
        result={k:copy.deepcopy(task.get(k)) for k in ('id','revision','name','instruction','time_description','rule','status','next_at','created_at','updated_at','last_run')}
        now=self.clock()
        upcoming=occurrences(task['rule'],now) if task.get('next_at') else []
        if task.get('next_at') and task['next_at']<=now:upcoming=[task['next_at'],*upcoming][:3]
        result.update(next_runs=upcoming,experimental=True,delivery='galaxy')
        return result

    def list(self,user,task_id=None):
        self.check(user,PERMISSIONS['read'])
        with self.store.transaction(user,False) as state:
            if task_id:
                task=self.get_record(state,user,task_id)
                runs=sorted([copy.deepcopy(r) for r in state['runs'].values() if r['task_id']==task['id']],key=lambda r:r['scheduled_at'],reverse=True)[:20]
                return {'task':self.public(task),'runs':runs}
            return {'tasks':[self.public(t) for t in sorted(state['tasks'].values(),key=lambda t:t['created_at'],reverse=True) if self.owns(t,user) and not t.get('deleted_at')],'experimental':True}

    def create(self,user,body):
        self.check(user,PERMISSIONS['create']);_,raw=self.current_user(user)
        fingerprint=hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        with self.store.transaction(user) as state:
            old=next((t for t in state['tasks'].values() if t.get('request_key')==body['request_key'] and self.owns(t,user)),None)
            if old:
                if old.get('request_fingerprint')!=fingerprint or old.get('deleted_at'):raise AuthFailure('request_conflict','创建标识已用于其他或已删除任务，请先核对现有任务。',409)
                return {'created':False,'task':self.public(old)}
            now=self.clock();rule=normalize(body['rule'],now)
            for field in ('name','instruction','time_description'):
                if not body[field].strip():raise AuthFailure('invalid_input','任务名称、内容及时间描述不能为空。',422)
            task={'id':str(uuid.uuid4()),'revision':str(uuid.uuid4()),'user_id':user['id'],'workspace_id':user['workspace_id'],
                'auth_version':raw['auth_version'],'name':body['name'].strip(),'instruction':body['instruction'].strip(),
                'time_description':body['time_description'].strip(),'rule':rule,'status':'scheduled','next_at':occurrences(rule,now)[0],
                'created_at':now,'updated_at':now,'request_key':body['request_key'],'request_fingerprint':fingerprint,'last_run':None}
            state['tasks'][task['id']]=task
        self.notify();return {'created':True,'task':self.public(task)}

    def update_time(self,user,body):
        self.check(user,PERMISSIONS['update']);_,raw=self.current_user(user);now=self.clock();rule=normalize(body['rule'],now)
        with self.store.transaction(user) as state:
            task=self.get_record(state,user,body['id'])
            if task['revision']!=identifier(body['revision']):raise AuthFailure('schedule_conflict','任务已变化，请重新读取后修改。',409)
            description=body['time_description'].strip()
            if not description:raise AuthFailure('invalid_input','时间描述不能为空。',422)
            task.update(rule=rule,time_description=description,next_at=occurrences(rule,now)[0],revision=str(uuid.uuid4()),
                status='running' if task.get('active_run') else 'scheduled',updated_at=now,auth_version=raw['auth_version'])
            result=self.public(task)
        self.notify();return {'updated':True,'task':result}

    async def delete(self,user,body):
        self.check(user,PERMISSIONS['delete'])
        with self.store.transaction(user) as state:
            task=self.get_record(state,user,body['id'])
            if task['revision']!=identifier(body['revision']):raise AuthFailure('schedule_conflict','任务已变化，请重新读取后删除。',409)
            task.update(deleted_at=self.clock(),next_at=None,status='deleted',revision=str(uuid.uuid4()))
            active=task.get('active_run')
        if active in self.active:self.active[active].cancel()
        self.notify();return {'deleted':True,'id':task['id']}

    def users(self):
        with self.auth.repository.transaction(write=False) as state:return [copy.deepcopy(u) for u in state['users'].values()]

    def recover(self):
        # A previous process may have performed side effects. Never automatically replay that run.
        for raw in self.users():
            with self.store.transaction(raw) as state:
                for run in state['runs'].values():
                    if run['status']=='running':run.update(status='interrupted',finished_at=self.clock(),error='服务重启，执行结果未确认；本次不自动重试。')
                for task in state['tasks'].values():
                    active=task.pop('active_run',None)
                    if active and active in state['runs']:
                        task['last_run']=copy.deepcopy(state['runs'][active])
                        if not task.get('deleted_at'):task['status']='scheduled' if task.get('next_at') else 'completed'

    def start(self):
        if self.worker:return
        path=self.store.directory/'.schedule-worker.lock'
        self.lease=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:fcntl.flock(self.lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.lease);self.lease=None;return
        self.event_loop=asyncio.get_running_loop()
        self.recover();self.worker=asyncio.create_task(self.loop(),name='galaxy-schedule')

    async def close(self):
        if self.worker:self.worker.cancel();await asyncio.gather(self.worker,return_exceptions=True);self.worker=None
        tasks=list(self.active.values())
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        if self.lease is not None:os.close(self.lease);self.lease=None

    async def loop(self):
        while True:
            self.wake.clear()
            try:await self.tick()
            except asyncio.CancelledError:raise
            except Exception:log.error('Schedule scan failed; retrying on the next scan')
            try:await asyncio.wait_for(self.wake.wait(),2)
            except TimeoutError:pass

    async def tick(self):
        now=self.clock()
        for raw in self.users():
            with self.store.transaction(raw) as state:
                claimed=[]
                for task in state['tasks'].values():
                    if task.get('deleted_at') or task.get('active_run') or task.get('next_at') is None or task['next_at']>now:continue
                    run={'id':str(uuid.uuid4()),'task_id':task['id'],'scheduled_at':task['next_at'],'started_at':now,'finished_at':None,'status':'running',
                        'conversation_id':str(uuid.uuid4()),'result_saved':False,'error':None}
                    state['runs'][run['id']]=run;next_runs=occurrences(task['rule'],now)
                    task.update(active_run=run['id'],status='running',next_at=next_runs[0] if next_runs else None,last_run=copy.deepcopy(run))
                    claimed.append((copy.deepcopy(task),copy.deepcopy(run)))
            for task,run in claimed:
                future=asyncio.create_task(self.execute(raw,task,run),name='schedule-'+run['id'])
                self.active[run['id']]=future
                future.add_done_callback(lambda _done,key=run['id']:self.active.pop(key,None))

    def issue_session(self,user,raw):
        token=secrets.token_urlsafe(32);now=int(self.clock())
        with self.auth.repository.transaction() as state:
            current=state['users'].get(raw['id'])
            if not current or not current.get('active') or current['auth_version']!=raw['auth_version']:raise AuthFailure('forbidden','任务授权已失效。',403)
            state['sessions'][digest(token)]={'user_id':raw['id'],'created_at':now,'expires_at':now+self.auth.policy.session_seconds,'remember':False,'auth_version':raw['auth_version']}
        return token

    async def execute(self,raw,task,run):
        token=None;status='failed';error=None;result_saved=False;acquired=False
        try:
            # The existing DSH worker has one active slot. Queue scheduled work instead of losing it to dsh_busy.
            await self.execution_slot.acquire();acquired=True
            if getattr(self.ai,'runtime_name',None)=='dsh':
                from ..dsh_runtime import worker_request
                while True:
                    runtime=await worker_request({'command':'status'})
                    if runtime.get('ok') and runtime.get('active')==0 and not self.ai.running:break
                    await asyncio.sleep(1)
            user,current=self.current_user(raw)
            if current['auth_version']!=task['auth_version']:raise AuthFailure('schedule_authorization_expired','账号安全设置已变化，请修改任务时间以重新授权。',403)
            # A task can be deleted while its coroutine waits to start.
            with self.store.transaction(user,False) as state:
                latest=self.get_record(state,user,task['id'])
                if latest.get('active_run')!=run['id']:raise AuthFailure('not_found','任务已停止。',404)
            token=self.issue_session(user,current)
            from ..ai import ChatInput
            result=await self.ai.send(user,ChatInput(message=task['instruction'],request_id=uuid.uuid4()),session_token=token,
                scheduled_run={'task_id':task['id'],'run_id':run['id'],'conversation_id':run['conversation_id'],'name':task['name'],'rule':task['rule'],'scheduled_at':run['scheduled_at']})
            result_saved=True
            message=result['conversation']['messages'][-1]
            status='partial' if any(e.get('status')!='success' for e in message.get('tool_events',[])) else 'success'
            if result.get('interrupted'):status='interrupted'
        except asyncio.CancelledError:
            status='interrupted';error='任务已停止；已经完成的业务操作不会撤回。'
        except AuthFailure as failure:
            error=failure.message
        except Exception:
            error='任务执行失败，请检查当前模型与渠道授权；未自动重复执行。'
        finally:
            if acquired:self.execution_slot.release()
            if token:self.auth.logout(token)
            with self.store.transaction(raw) as state:
                record=state['runs'].get(run['id'])
                if record:
                    record.update(status=status,finished_at=self.clock(),error=error,result_saved=result_saved)
                    task=state['tasks'].get(task['id'])
                    if task and task.get('active_run')==run['id']:
                        task.pop('active_run',None);task['last_run']=copy.deepcopy(record)
                        if not task.get('deleted_at'):task['status']='scheduled' if task.get('next_at') else 'completed'
            self.notify()
