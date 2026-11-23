import asyncio
import copy
import hashlib
import json
import time
import uuid
from galaxy_cli.tools.clawbot.send import normalize
from ..auth import AuthFailure
from .media import snapshot, upload


def receipt(record):
    return {k: copy.deepcopy(record[k]) for k in ('delivery_id', 'status', 'parts', 'created_at', 'error') if k in record}


async def send(service, actor, body, authorize):
    body = normalize(body)
    user, _ = service.current(actor)
    required = {'clawbot:send'} | ({'file:read'} if 'path' in body else set())
    if not required.issubset(user['permissions']):
        raise AuthFailure('forbidden', '当前账号没有微信发送或文件读取权限。', 403)
    record = service.record(user)
    if not record.get('token') or record.get('status') == 'expired':
        raise AuthFailure('clawbot_connection_required', '请先在“我的账号 → ClawBot”扫码绑定微信。', 409)
    if not record.get('context_token'):
        raise AuthFailure('clawbot_context_required', '请先在微信向 ClawBot 发送一条消息，再尝试投递。', 409)
    key = hashlib.sha256(body['request_key'].encode()).hexdigest()
    fingerprint = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    lock = service.send_locks.setdefault(user['id'], asyncio.Lock())
    async with lock:
        def check():
            authorize()
            principal, raw = service.current(actor)
            current = service.record(actor)
            if (not required.issubset(principal['permissions']) or raw['auth_version'] != current.get('auth_version')
                    or current.get('binding_id') != record['binding_id']):
                raise AuthFailure('clawbot_authorization_changed', '账号权限或微信绑定已变化，投递已停止。', 403)
            return current

        check()
        with service.store.transaction(user, False) as state:
            prior = copy.deepcopy(state.get('outbox', {}).get(key))
        if prior:
            if prior['fingerprint'] != fingerprint:
                raise AuthFailure('clawbot_delivery_conflict', '投递标识已用于其他内容，请先核对实际发送记录。', 409)
            if prior['status'] == 'sending':
                prior.update(status='unknown', error={'code':'clawbot_delivery_unknown','message':'上次投递已开始但缺少完整确认；未重复发送。'})
            return receipt(prior)
        data, filename = snapshot(service.ai.dispatcher.files, user, body['path']) if 'path' in body else (None, None)
        delivery = {'delivery_id': str(uuid.uuid4()), 'fingerprint': fingerprint, 'status': 'sending',
            'created_at': time.time(), 'parts': []}
        items = [({'type': 1, 'text_item': {'text': body['text'][i:i + 3500]}}, {'type': 'text', 'status': 'pending'})
            for i in range(0, len(body.get('text', '')), 3500)]
        if data is not None:
            items.append((None, {'type': 'file', 'filename': filename, 'size': len(data), 'status': 'pending'}))
        delivery['parts'] = [meta for _, meta in items]

        def save():
            with service.store.transaction(user) as state:
                if state.get('binding_id') != record['binding_id']:
                    return
                state.setdefault('outbox', {})[key] = copy.deepcopy(delivery)

        save()
        active = None
        try:
            file_item = await upload(service.api, record, data, filename, check) if data is not None else None
            for item, meta in items:
                fresh = check()
                active = meta
                meta['status'] = 'sending'
                save()
                await service.api.request('sendmessage', base=fresh['base'], token=fresh['token'], body={
                    'msg': {'from_user_id': '', 'to_user_id': fresh['peer'],
                        'client_id': 'galaxy-' + delivery['delivery_id'] + '-' + str(delivery['parts'].index(meta)),
                        'message_type': 2, 'message_state': 2, 'context_token': fresh['context_token'],
                        'item_list': [item or file_item]},
                    'base_info': {'channel_version': '2.4.10', 'bot_agent': 'GalaxyAI/0.8.17'}})
                meta.update(status='sent', sent_at=time.time())
                save()
                active = None
            delivery['status'] = 'sent'
            with service.store.transaction(user) as state:
                if state.get('binding_id') == record['binding_id']:
                    state['last_reply_at'] = time.time()
        except (Exception, asyncio.CancelledError) as failure:
            unknown = active is not None and (not isinstance(failure, AuthFailure) or failure.code == 'clawbot_unavailable')
            if active is not None:
                active['status'] = 'unknown' if unknown else 'failed'
            delivery['status'] = 'unknown' if unknown else 'partial' if any(p['status'] == 'sent' for p in delivery['parts']) else 'failed'
            delivery['error'] = {'code':getattr(failure, 'code', 'clawbot_delivery_interrupted'), 'message':getattr(failure, 'message', '投递中断，未自动重发；请核对实际收到的消息。')}
            if isinstance(failure, asyncio.CancelledError):
                raise
        finally:
            save()
        return receipt(delivery)
