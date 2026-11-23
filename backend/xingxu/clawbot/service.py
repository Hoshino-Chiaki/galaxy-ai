"""Personal WeChat binding and text bridge into the existing Galaxy AI runtime."""
import asyncio
import copy
import time
import uuid
from ..auth import AuthFailure
from ..account_storage import owner_key
from .protocol import ILink, BASE, origin
from .storage import ClawbotStore


class ClawbotService:
    def __init__(self, directory, auth, ai, transport=None):
        self.store = ClawbotStore(directory, ai.cipher)
        self.auth, self.ai, self.api = auth, ai, ILink(transport)
        self.pending, self.workers, self.send_locks = {}, {}, {}
        self.monitor = None
        from ..channel_identity import execution_slot
        self.bind_lock, self.ai_slot = asyncio.Lock(), execution_slot(ai)

    async def send(self, user, body, authorize):
        from .outbound import send
        return await send(self, user, body, authorize)

    @staticmethod
    def check(user):
        if 'clawbot:configure' not in user.get('permissions', []):
            raise AuthFailure('forbidden', '当前账号没有 ClawBot 使用权限。', 403)

    def current(self, actor):
        from ..channel_identity import current
        return current(self.auth, actor, ('clawbot:configure', 'ai:chat'))

    def users(self):
        with self.auth.repository.transaction(write=False) as state:
            return copy.deepcopy(list(state['users'].values()))

    def record(self, user):
        with self.store.transaction(user, False) as record:
            return copy.deepcopy(record)

    def summary(self, user):
        record = self.record(user)
        return {'connected': bool(record.get('token')), 'status': record.get('status', 'disconnected'),
            'bot_id': record.get('bot_id', ''), 'connected_at': record.get('connected_at'),
            'last_message_at': record.get('last_message_at'), 'last_reply_at': record.get('last_reply_at'),
            'error': record.get('error'), 'chat_types': ['direct'], 'content_types': ['text']}

    async def connect(self, user):
        self.current(user)
        cancelled = []
        for key, pending in list(self.pending.items()):
            if pending['owner'] == owner_key(user) or pending['expires_at'] < time.time():
                pending['task'].cancel()
                cancelled.append(pending['task'])
                self.pending.pop(key, None)
        await asyncio.gather(*cancelled, return_exceptions=True)
        old = self.record(user)
        value = await self.api.request('get_bot_qrcode', params={'bot_type': '3'},
            body={'local_token_list': [old['token']] if old.get('token') else []})
        if not value.get('qrcode') or not isinstance(value.get('qrcode_img_content'), str):
            raise AuthFailure('clawbot_protocol_error', '微信没有返回绑定二维码，请稍后重试。', 502)
        key = str(uuid.uuid4())
        pending = {'owner': owner_key(user), 'id': key, 'qrcode': value['qrcode'],
            'qr_content': value['qrcode_img_content'], 'expires_at': time.time() + 300,
            'status': 'wait', 'base': BASE, 'error': None}
        self.pending[key] = pending
        pending['task'] = asyncio.create_task(self.wait_login(user, pending))
        return {k: pending[k] for k in ('id', 'qr_content', 'expires_at', 'status')}

    def poll(self, user, key, code=None):
        pending = self.pending.get(key)
        if not pending or pending['owner'] != owner_key(user):
            raise AuthFailure('not_found', '绑定请求不存在，请重新扫码。', 404)
        if code is not None:
            if pending['status'] != 'need_verifycode':
                raise AuthFailure('invalid_input', '当前无需输入配对码。', 409)
            pending['verify_code'] = code
        return {'id': key, 'status': pending['status'], 'error': pending['error'],
            'expires_at': pending['expires_at'], 'connection': self.summary(user)}

    async def wait_login(self, user, pending):
        try:
            while time.time() < pending['expires_at']:
                self.current(user)
                params = {'qrcode': pending['qrcode']}
                if pending.get('verify_code'):
                    params['verify_code'] = pending.pop('verify_code')
                try:
                    value = await self.api.request('get_qrcode_status', base=pending['base'], params=params)
                except AuthFailure as failure:
                    if failure.code != 'clawbot_unavailable':
                        raise
                    await asyncio.sleep(2)
                    continue
                status = pending['status'] = value.get('status', 'wait')
                if status == 'scaned_but_redirect':
                    pending['base'] = origin('https://' + value.get('redirect_host', ''))
                elif status == 'confirmed':
                    pending['status'] = 'connecting'
                    await self.bind(user, value)
                    pending['status'] = 'confirmed'
                    return
                elif status in ('expired', 'verify_code_blocked'):
                    return
                elif status == 'binded_redirect':
                    pending['status'] = 'confirmed' if self.record(user).get('token') else 'failed'
                    if pending['status'] == 'failed':
                        pending['error'] = '微信显示已绑定，但此账号没有对应凭证，请在微信中解除旧绑定后重新连接。'
                    return
                await asyncio.sleep(1)
            pending['status'] = 'expired'
        except asyncio.CancelledError:
            raise
        except AuthFailure as failure:
            pending.update(status='failed', error=failure.message)
        except Exception:
            pending.update(status='failed', error='微信绑定未完成，请稍后重试。')

    async def bind(self, user, value):
        _, raw = self.current(user)
        if not all(isinstance(value.get(k), str) and value[k] for k in ('bot_token', 'ilink_bot_id', 'ilink_user_id')):
            raise AuthFailure('clawbot_protocol_error', '微信未返回完整身份信息，请重新扫码。', 502)
        base = origin(value.get('baseurl') or BASE)
        async with self.bind_lock:
            for actor in self.users():
                if owner_key(actor) != owner_key(user) and self.record(actor).get('bot_id') == value['ilink_bot_id']:
                    raise AuthFailure('clawbot_bound', '此 ClawBot 已绑定另一个 Galaxy 账号，请先解除原绑定。', 409)
            await self.stop_worker(user)
            with self.store.transaction(user) as record:
                record.clear()
                record.update(token=value['bot_token'], bot_id=value['ilink_bot_id'], peer=value['ilink_user_id'],
                    base=base, auth_version=raw['auth_version'], binding_id=str(uuid.uuid4()),
                    status='connected', connected_at=time.time(), cursor='', seen=[], conversation_id=None)
            if self.monitor:
                self.ensure_worker(user)

    async def disconnect(self, user):
        cancelled = []
        for key, pending in list(self.pending.items()):
            if pending['owner'] == owner_key(user):
                pending['task'].cancel()
                cancelled.append(pending['task'])
                self.pending.pop(key, None)
        await asyncio.gather(*cancelled, return_exceptions=True)
        await self.stop_worker(user)
        async with self.bind_lock:
            with self.store.transaction(user) as record:
                record.clear()
        return self.summary(user)

    async def stop_worker(self, user):
        worker = self.workers.pop(owner_key(user), None)
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    def ensure_worker(self, user):
        key = owner_key(user)
        if key not in self.workers or self.workers[key].done():
            self.workers[key] = asyncio.create_task(self.receive(user))

    def start(self):
        self.monitor = asyncio.create_task(self.watch())

    async def close(self):
        tasks = [*self.workers.values(), *(p['task'] for p in self.pending.values())]
        if self.monitor:
            tasks.append(self.monitor)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.api.close()

    async def watch(self):
        while True:
            try:
                for raw in self.users():
                    if self.record(raw).get('token'):
                        try:
                            self.current(raw)
                            self.ensure_worker(raw)
                        except AuthFailure:
                            await self.stop_worker(raw)
            except Exception:
                pass  # No credential or upstream response enters application logs.
            await asyncio.sleep(15)

    async def receive(self, actor):
        while True:
            try:
                user, raw = self.current(actor)
                record = self.record(user)
                if not record.get('token') or record.get('status') == 'expired':
                    return
                if record['auth_version'] != raw['auth_version']:
                    raise AuthFailure('clawbot_expired', '账号安全设置已变化，请重新扫码绑定微信。', 401)
                value = await self.api.request('getupdates', base=record['base'], token=record['token'],
                    body={'get_updates_buf': record.get('cursor', ''), 'base_info': {'channel_version': '2.4.10', 'bot_agent': 'GalaxyAI/0.8.17'}})
                with self.store.transaction(user) as state:
                    state.update(cursor=value.get('get_updates_buf', state.get('cursor', '')),
                        status='connected', error=None)
                for message in value.get('msgs') or []:
                    await self.handle(user, record, message)
                await asyncio.sleep(.2)
            except asyncio.CancelledError:
                raise
            except AuthFailure as failure:
                with self.store.transaction(actor) as state:
                    state.update(status='expired' if failure.code == 'clawbot_expired' else 'error', error=failure.message)
                if failure.code in ('clawbot_expired', 'forbidden'):
                    return
                await asyncio.sleep(10)
            except Exception:
                with self.store.transaction(actor) as state:
                    state.update(status='error', error='微信消息处理失败，请检查模型与渠道连接。')
                await asyncio.sleep(10)

    def session(self, user, raw):
        from ..channel_identity import issue_session
        return issue_session(self.auth, user, raw)

    async def handle(self, actor, connection, message):
        if (message.get('message_type') != 1 or message.get('group_id')
                or message.get('from_user_id') != connection['peer'] or not message.get('context_token')):
            return
        text = '\n'.join(item.get('text_item', {}).get('text', '') for item in message.get('item_list', []) if item.get('type') == 1).strip()
        key = str(message.get('message_id') or '')
        if not text or not key:
            return
        # Persist before invoking tools: a restart must not replay a write operation.
        with self.store.transaction(actor) as state:
            if state.get('binding_id') != connection['binding_id'] or key in state.get('seen', []):
                return
            state.update(seen=[*state.get('seen', []), key][-2000:], last_message_at=time.time(),
                context_token=message['context_token'], processing=True)
        token = None
        try:
            async with self.ai_slot:
                if getattr(self.ai, 'runtime_name', None) == 'dsh':
                    from ..dsh_runtime import worker_request
                    while True:
                        runtime = await worker_request({'command': 'status'})
                        if runtime.get('ok') and not runtime.get('active') and not self.ai.running:
                            break
                        await asyncio.sleep(1)
                user, raw = self.current(actor)
                fresh = self.record(user)
                if fresh.get('binding_id') != connection['binding_id'] or raw['auth_version'] != fresh['auth_version']:
                    raise AuthFailure('clawbot_expired', '微信绑定已变化，请重新连接。', 401)
                token = self.session(user, raw)
                from ..ai import ChatInput
                body = ChatInput(message=text, request_id=uuid.uuid4(), conversation_id=fresh.get('conversation_id'), interactive_questions=False)
                result = await self.ai.send(user, body, session_token=token, entry_point="wechat")
                conversation = result['conversation']
                with self.store.transaction(user) as state:
                    state['conversation_id'] = conversation['id']
                content = conversation['messages'][-1].get('content', '')
                _, authorized = self.current(actor)
                fresh = self.record(user)
                if (fresh.get('binding_id') != connection['binding_id']
                        or authorized['auth_version'] != fresh.get('auth_version')):
                    return
                # Only the scanning owner may talk to this account; no external recipient parameter.
                for index in range(0, len(content), 3500):
                    await self.api.request('sendmessage', base=fresh['base'], token=fresh['token'], body={
                        'msg': {'from_user_id': '', 'to_user_id': fresh['peer'], 'client_id': 'galaxy-' + str(uuid.uuid4()),
                            'message_type': 2, 'message_state': 2, 'context_token': message['context_token'],
                            'item_list': [{'type': 1, 'text_item': {'text': content[index:index + 3500]}}]},
                        'base_info': {'channel_version': '2.4.10', 'bot_agent': 'GalaxyAI/0.8.17'}})
                with self.store.transaction(user) as state:
                    state.update(last_reply_at=time.time(), error=None, status='connected')
        finally:
            if token:
                self.auth.logout(token)
            with self.store.transaction(actor) as state:
                if state.get('binding_id') == connection['binding_id']:
                    state['processing'] = False
