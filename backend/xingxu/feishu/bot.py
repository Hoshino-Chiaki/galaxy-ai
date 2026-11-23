"""Feishu private-message entry into the same Galaxy AI, permissions and personal channels."""
import asyncio
import copy
import hashlib
import json
import time
import uuid
from pathlib import Path
from urllib.parse import quote
from ..auth import AuthFailure
from ..channel_identity import current, issue_session, execution_slot
from .service import owner_key
from .bot_storage import BotStore

BOT_SCOPES = ('im:message.p2p_msg:readonly', 'im:message:send_as_bot')


class FeishuBot:
    def __init__(self, directory, auth, ai, feishu):
        self.auth, self.ai, self.feishu = auth, ai, feishu
        self.store = BotStore(directory, feishu)
        self.workers, self.jobs, self.principals, self.status = {}, {}, {}, {}
        self.monitor = None
        self.ai_slot = execution_slot(ai)

    def apps(self):
        with self.feishu.repo.transaction(write=False) as state:
            return copy.deepcopy(state['apps'])

    def summary(self, user):
        app, record = self.feishu._snapshot(user)
        bound = bool(app and record.get('revision') == app['revision'] and record.get('identity')
            and (record.get('token') or record.get('grants')))
        status = self.status.get(user['workspace_id'], {})
        return {'configured': bool(app), 'status': status.get('status', 'starting' if app else 'not_configured'),
            'identity_bound': bound, 'allowed': 'feishu:chat' in user['permissions'],
            'last_message_at': status.get('last_message_at'), 'last_reply_at': status.get('last_reply_at'),
            'error': status.get('error'), 'required_scopes': list(BOT_SCOPES),
            'event': 'im.message.receive_v1', 'transport': 'websocket', 'chat_types': ['p2p']}

    def start(self):
        self.monitor = asyncio.create_task(self.watch())

    async def close(self):
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        for worker in list(self.workers.values()):
            await self.stop_worker(worker)
        for task in list(self.jobs.values()):
            task.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)

    async def stop_worker(self, worker):
        worker['reader'].cancel()
        process = worker['process']
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        await asyncio.gather(worker['reader'], return_exceptions=True)

    async def watch(self):
        while True:
            try:
                apps = self.apps()
                for workspace, worker in list(self.workers.items()):
                    if (workspace not in apps or worker['revision'] != apps[workspace]['revision']
                            or worker['process'].returncode is not None):
                        await self.stop_worker(worker)
                        self.workers.pop(workspace, None)
                for workspace, app in apps.items():
                    if workspace not in self.workers:
                        try:
                            await self.launch(workspace, app)
                        except Exception:
                            self.status.setdefault(workspace, {}).update(status='error', error='机器人连接未建立，请检查依赖及应用配置。')
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(15)

    async def launch(self, workspace, app):
        self.status[workspace] = {'status': 'starting'}
        process = await asyncio.create_subprocess_exec(
            str(Path(__file__).resolve().parents[2] / 'feishu/bot-venv/bin/python'), str(Path(__file__).with_name('bot_worker.py')),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd=Path(__file__).resolve().parents[2], limit=256 * 1024)
        process.stdin.write((json.dumps({'app_id': app['app_id'], 'app_secret': self.feishu._decrypt(app['secret'])}) + '\n').encode())
        await process.stdin.drain()
        reader = asyncio.create_task(self.read(workspace, app, process))
        self.workers[workspace] = {'process': process, 'reader': reader, 'revision': app['revision']}
        with self.store.transaction(workspace, app['app_id']) as state:
            for message in state['messages'].values():
                key = (workspace, app['app_id'], message['message_id'])
                if message['status'] in ('running', 'replying') and (key not in self.jobs or self.jobs[key].done()):
                    message.update(status='interrupted', error='服务已重启，未自动重复执行。')
            queued = [copy.deepcopy(m) for m in state['messages'].values() if m['status'] == 'queued']
        for message in queued:
            self.enqueue(workspace, app, message)

    async def read(self, workspace, app, process):
        try:
            while line := await process.stdout.readline():
                value = json.loads(line)
                if value['type'] == 'message':
                    accepted = self.accept(workspace, app, value)
                    process.stdin.write((json.dumps({'ack': value.get('message_id'), 'accepted': accepted}) + '\n').encode())
                    await process.stdin.drain()
                elif value['type'] == 'connected':
                    self.status.setdefault(workspace, {}).update(status='connected', error=None)
                elif value['type'] in ('disconnected', 'error'):
                    self.status.setdefault(workspace, {}).update(status='reconnecting', error=value.get('message'))
        except asyncio.CancelledError:
            raise
        except Exception:
            self.status.setdefault(workspace, {}).update(status='error', error='飞书消息连接中断，正在重新连接。')
        finally:
            if process.returncode is None:
                process.terminate()

    def accept(self, workspace, app, event):
        if (event.get('app_id') != app['app_id'] or not isinstance(event.get('message_id'), str)
                or not event['message_id'] or not event.get('open_id') or not event.get('tenant_key')):
            return False
        if event.get('sender_type') != 'user' or event.get('chat_type') != 'p2p':
            return True
        with self.store.transaction(workspace, app['app_id']) as state:
            if event['message_id'] in state['messages']:
                return True
            message = {k: event.get(k) for k in ('message_id', 'chat_id', 'open_id', 'tenant_key', 'message_type', 'content')}
            message.update(status='queued', received_at=time.time(), revision=app['revision'])
            state['messages'][message['message_id']] = message
        self.status.setdefault(workspace, {}).update(last_message_at=time.time())
        self.enqueue(workspace, app, message)
        return True

    def enqueue(self, workspace, app, message):
        key = (workspace, app['app_id'], message['message_id'])
        if key not in self.jobs or self.jobs[key].done():
            task = asyncio.create_task(self.process(workspace, app, message))
            self.jobs[key] = task
            task.add_done_callback(lambda finished: self.jobs.pop(key, None) if self.jobs.get(key) is finished else None)

    def principal(self, workspace, app, message):
        if message.get('revision') != app['revision']:
            raise AuthFailure('feishu_connection_changed', '飞书应用配置已变化，请重新发送消息。', 409)
        with self.feishu.repo.transaction(write=False) as state:
            latest = state['apps'].get(workspace)
            connections = copy.deepcopy(state['connections'])
        if not latest or latest['revision'] != app['revision']:
            raise AuthFailure('feishu_connection_changed', '飞书应用已变化，请重新发送消息。', 409)
        with self.auth.repository.transaction(write=False) as state:
            users = [copy.deepcopy(u) for u in state['users'].values() if u['workspace_id'] == workspace and u.get('active')]
        matches = []
        for raw in users:
            record = connections.get(owner_key(raw), {})
            identity = record.get('identity', {})
            if (record.get('revision') == app['revision'] and (record.get('token') or record.get('grants'))
                    and identity.get('open_id') == message['open_id'] and identity.get('tenant_key') == message['tenant_key']):
                matches.append(raw)
        if len(matches) != 1:
            raise AuthFailure('feishu_galaxy_binding_required', '请在 Galaxy AI 的“我的账号 → 飞书”连接本人的飞书账号；同一飞书账号需只绑定一个启用中的 Galaxy 账号。', 403)
        return current(self.auth, matches[0], ('ai:chat', 'feishu:chat'))

    async def reply(self, workspace, app, message, text, authorize=lambda: None):
        for index in range(0, len(text), 3500):
            authorize()
            for attempt in range(2):
                token = await self.feishu.application.token({**app, 'app_secret': self.feishu._decrypt(app['secret'])}, force=attempt > 0)
                authorize()
                status, value = await self.feishu.application._request('POST',
                    'https://open.feishu.cn/open-apis/im/v1/messages/' + quote(message['message_id'], safe='') + '/reply',
                    headers={'Authorization': 'Bearer ' + token['access_token']},
                    json={'msg_type': 'text', 'content': json.dumps({'text': text[index:index + 3500]}, ensure_ascii=False),
                        'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, app['app_id'] + message['message_id'] + str(index)))})
                if value.get('code') not in (99991672, 99991663):
                    break
            if status != 200 or value.get('code') != 0:
                raise AuthFailure('feishu_bot_reply_failed', '飞书未接受回复，请检查应用的机器人发消息权限。', 502)
        self.status.setdefault(workspace, {}).update(last_reply_at=time.time())

    async def process(self, workspace, app, message):
        token, result, error = None, None, None
        claimed = False
        state_name = 'failed'
        key = (workspace, app['app_id'], message['message_id'])
        try:
            with self.store.transaction(workspace, app['app_id']) as state:
                record = state['messages'][message['message_id']]
                if record['status'] != 'queued':
                    return
                record['status'] = 'running'
                claimed = True
            user, raw = self.principal(workspace, app, message)
            self.principals[key] = user['id']
            if message.get('message_type') != 'text':
                raise AuthFailure('feishu_text_required', '当前入口先支持文字消息，请发送文字问题。', 422)
            text = json.loads(message['content']).get('text', '').strip()
            if not text or len(text) > 8000:
                raise AuthFailure('invalid_input', '请输入1至8000字的文字问题。', 422)
            scope = hashlib.sha256(json.dumps([user['id'], message['open_id'], message['chat_id']]).encode()).hexdigest()
            async with self.ai_slot:
                if getattr(self.ai, 'runtime_name', None) == 'dsh':
                    from ..dsh_runtime import worker_request
                    while True:
                        runtime = await worker_request({'command': 'status'})
                        if runtime.get('ok') and not runtime.get('active') and not self.ai.running:
                            break
                        await asyncio.sleep(1)
                user, latest = self.principal(workspace, app, message)
                if latest['auth_version'] != raw['auth_version']:
                    raise AuthFailure('forbidden', '账号安全设置已变化，请重新发送消息。', 403)
                with self.store.transaction(workspace, app['app_id'], False) as state:
                    conversation_id = state['conversations'].get(scope)
                if conversation_id:
                    try:
                        self.ai.conversation(user, conversation_id)
                    except AuthFailure as failure:
                        if failure.code != 'not_found':
                            raise
                        conversation_id = None
                token = issue_session(self.auth, user, raw)
                from ..ai import ChatInput
                result = await self.ai.send(user, ChatInput(message=text, request_id=uuid.uuid4(),
                    conversation_id=conversation_id, interactive_questions=False), session_token=token, entry_point="feishu")
                conversation = result['conversation']
                with self.store.transaction(workspace, app['app_id']) as state:
                    state['conversations'][scope] = conversation['id']
                    state['messages'][message['message_id']].update(status='replying', conversation_id=conversation['id'])
                def guard():
                    principal, active = self.principal(workspace, app, message)
                    if principal['id'] != user['id'] or active['auth_version'] != raw['auth_version']:
                        raise AuthFailure('forbidden', '账号绑定或安全设置已变化，未发送本次结果。', 403)
                    return principal
                guard()
                await self.reply(workspace, app, message, conversation['messages'][-1]['content'],
                    guard)
                state_name = 'completed'
        except asyncio.CancelledError:
            state_name, error = 'interrupted', '当前处理已停止；已经完成的业务操作不会撤回。'
        except AuthFailure as failure:
            error = failure.message
            if result is None:
                try:
                    await self.reply(workspace, app, message, '本次未完成：' + error)
                except AuthFailure:
                    pass
        except Exception:
            error = '消息处理未完成，请检查模型或渠道授权。'
        finally:
            if token:
                self.auth.logout(token)
            if claimed:
                with self.store.transaction(workspace, app['app_id']) as state:
                    record = state['messages'].get(message['message_id'])
                    if record:
                        record.update(status=state_name, error=error, finished_at=time.time())
            self.principals.pop(key, None)

    async def disconnect(self, user):
        tasks = [task for key, task in self.jobs.items() if self.principals.get(key) == user['id']]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
