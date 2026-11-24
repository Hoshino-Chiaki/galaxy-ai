"""Bounded current-group history, fetched as the mentioning employee, not the bot."""
import asyncio
import datetime as dt
import json
import time
import uuid
from zoneinfo import ZoneInfo

from ..auth import AuthFailure
from ..tool_context import ExecutionContext, DispatchDenied
from ..tool_cache import cache_result

ACTION = '+chat-messages-list'
MAX_MESSAGES = 30
MAX_CHARS = 12000


def message_time(value, fallback):
    try:
        return int(str(value)) / 1000 if value else fallback
    except (ValueError, TypeError):
        return fallback


def text_content(item):
    try:
        content = json.loads(item.get('body', {}).get('content', '{}'))
    except (ValueError, TypeError, AttributeError):
        return ''
    if not isinstance(content, dict):return ''
    if item.get('msg_type') == 'text':
        text = content.get('text', '')
        return text if isinstance(text, str) else ''
    if item.get('msg_type') == 'post':
        # Rich text is data; keep words only, without following links/resources.
        def words(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in ('text', 'title') and isinstance(value, str):yield value
                    elif isinstance(value, (dict, list)):yield from words(value)
            elif isinstance(node, list):
                for value in node:yield from words(value)
        return '\n'.join(words(content))
    return '[非文字消息：' + str(item.get('msg_type', 'unknown'))[:30] + '，未读取附件内容]'


def excerpts(items, message, anchor):
    rows = []
    for item in items:
        if (not isinstance(item, dict) or item.get('message_id') == message['message_id'] or item.get('deleted')
                or item.get('chat_id') not in (None, message['chat_id'])):
            continue
        created = message_time(item.get('create_time'), 0)
        if not anchor - 86400 <= created <= anchor:continue
        text = text_content(item).strip()
        if not text:continue
        sender = item.get('sender', {})
        if not isinstance(sender, dict):sender = {}
        sender_id = sender.get('id') if sender.get('id_type') == 'open_id' else sender.get('open_id')
        rows.append({'message_id': item.get('message_id'), 'sender_open_id': sender_id,
            'is_requester': sender_id == message['open_id'], 'sender_type': sender.get('sender_type'),
            'sent_at': dt.datetime.fromtimestamp(created, ZoneInfo('Asia/Shanghai')).isoformat(),
            'text': text[:1500], 'text_truncated': len(text) > 1500})
    rows.sort(key=lambda row: row['sent_at'], reverse=True)
    output, size = [], 0
    for row in rows[:MAX_MESSAGES]:
        encoded = json.dumps(row, ensure_ascii=False)
        if size + len(encoded) > MAX_CHARS:break
        output.append(row);size += len(encoded)
    output.reverse()
    return output, len(output) < len(rows)


async def load_group_context(bot, user, app, message, token, conversation_id):
    anchor = message_time(message.get('create_time'), message.get('received_at', time.time()))
    context = ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']),
        str(conversation_id or uuid.uuid5(uuid.NAMESPACE_URL, app['app_id'] + message['message_id'])), token)
    start = dt.datetime.fromtimestamp(anchor - 86400, dt.timezone.utc).isoformat()
    end = dt.datetime.fromtimestamp(anchor + 1, dt.timezone.utc).isoformat()
    body = {'action': ACTION, 'options': {'chat-id': message['chat_id'], 'order': 'desc', 'page-size': '50',
            'page-all': False, 'no-reactions': True, 'start': start, 'end': end}}
    canonical = ['feishu', 'im', '--input', json.dumps(body, ensure_ascii=False), '--format', 'json']
    dispatcher = bot.ai.dispatcher
    def validate():
        principal = dispatcher._check_command(context, 'feishu.im', canonical)
        bound, _ = bot.principal(user['workspace_id'], app, message)
        if bound['id'] != principal['id']:raise AuthFailure('forbidden', '账号绑定已变化，未读取群消息。', 403)
        return principal
    try:
        async with asyncio.timeout(10):
            validate()
            credentials = await bot.feishu.credentials(user, validate, domain='im', action=ACTION)
            status, value = await bot.feishu.application._request('GET', 'https://open.feishu.cn/open-apis/im/v1/messages',
                headers={'Authorization': 'Bearer ' + credentials['access_token']},
                params={'container_id_type': 'chat', 'container_id': message['chat_id'],
                    'start_time': str(int(anchor - 86400)), 'end_time': str(int(anchor) + 1),
                    'sort_type': 'ByCreateTimeDesc', 'page_size': 50})
            validate()
            if not bot.feishu.unchanged(user, credentials):
                raise AuthFailure('feishu_connection_changed', '本人飞书授权已变化，未使用群消息。', 409)
            data = value.get('data')
            if status != 200 or value.get('code') != 0 or not isinstance(data, dict) or not isinstance(data.get('items'), list):
                raise AuthFailure('feishu_group_history_unavailable', '当前账号暂不能读取本群上下文，请检查本人的飞书群消息授权和群成员身份。', 403)
            value = dispatcher._redact_feishu(value, credentials)
            receipt = await cache_result(dispatcher, context, 'feishu.im', canonical, None, {'ok': True, 'data': value['data']}, False)
            rows, truncated = excerpts(value['data']['items'], message, anchor)
            return {'source': 'feishu_group', 'status': 'available', 'chat_id': message['chat_id'],
                'requester_open_id': message['open_id'], 'request_message_id': message['message_id'],
                'request_sent_at': dt.datetime.fromtimestamp(anchor, ZoneInfo('Asia/Shanghai')).isoformat(),
                'window_hours': 24, 'max_messages': MAX_MESSAGES, 'truncated': truncated or bool(data.get('has_more')),
                'messages': rows, 'cache': receipt}
    except (AuthFailure, DispatchDenied, TimeoutError) as error:
        return {'source': 'feishu_group', 'status': 'unavailable', 'chat_id': message['chat_id'],
            'error_code': getattr(error, 'code', 'group_context_timeout'),
            'detail': getattr(error, 'message', '读取群上下文超时。')[:600],
            'message': '本次未取得群上下文，不能据此断言用户没有提过相关安排；需要时明确说明缺少群记录或授权。'}
