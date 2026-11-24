"""Explicit, credential-free projections of the calling employee's own connections."""
import json
import time
from galaxy_cli.registry import ToolError
from galaxy_cli.tools.account.me import normalize
from .auth import AuthFailure
from .tool_context import DispatchDenied, strict_json


def arguments(args):
    if args[0] == 'account.me':
        args = ['account', 'me', *args[1:]]
    if args[0] != 'account':
        return None
    if args in (['account', 'me'], ['account', 'me', '--format', 'json']):
        return ['account', 'me', '--input', '{}', '--format', 'json']
    if len(args) not in (4, 6) or args[:3] != ['account', 'me', '--input'] or len(args) == 6 and args[4:] != ['--format', 'json']:
        raise DispatchDenied('invalid_arguments', '请使用 account me --input {}。')
    try:
        normalize(strict_json(args[3]))
    except (ValueError, TypeError, ToolError):
        raise DispatchDenied('invalid_arguments', '本人信息工具不接受身份或其他参数。') from None
    return ['account', 'me', '--input', '{}', '--format', 'json']


class AccountInfo:
    def __init__(self, **providers):
        self.providers = providers

    def summary(self, user):
        channels = {}
        now = time.time()
        for name, provider in self.providers.items():
            if not any(code.startswith(name + ':') for code in user['permissions']):
                channels[name] = {'account': '', 'status': 'not_enabled'}
                continue
            try:
                value = provider.summary(user)
                if name == 'youzan':
                    sid = value.get('sid', {})
                    # Match the channel page. Cached expiry is not a live rejection.
                    sessions = value.get('shop_sessions') or []
                    ready = sum(bool(shop.get('ready')) for shop in sessions)
                    status = ('not_configured' if not value['configured'] else 'connected' if ready
                        else 'not_connected' if not sid.get('present') else 'unknown' if sid.get('expired')
                        else 'connected' if value.get('csrf', {}).get('present') else 'partial')
                    info = {'account': value.get('account', ''), 'status': status}
                    if sessions:
                        info.update(ready_shops=ready, total_shops=len(sessions))
                elif name == 'feishu':
                    info = {'account': (value.get('identity') or {}).get('name', ''),
                        'status': 'not_configured' if not value['configured'] else 'not_connected' if not value['connected']
                            else 'connected'}
                elif name in ('guanjia', 'jushuitan'):
                    info = {'account': value.get('account', ''), 'status': 'connected' if value['connected'] else 'not_connected'}
                elif name == 'clawbot':
                    info = {'account': value.get('bot_id', ''), 'status': value.get('status', 'disconnected')}
                else:
                    info = {'account': '', 'status': 'unknown'}
                info.update(source='channel_settings', checked_live=False)
                info['status_label'] = {
                    'connected': '已连接，与客户端渠道状态一致',
                    'partial': '已登录，部分店铺会话尚未就绪，不等于账号过期',
                    'unknown': '已保存登录会话，缓存到期信息不能确认当前失效，状态待确认',
                    'not_connected': '未取得连接', 'not_configured': '未配置',
                }.get(info['status'], info['status'])
                info['message'] = '本次读取与客户端相同的渠道状态，未实时调用业务接口。不要根据缓存时间或部分店铺未就绪声称整个渠道已过期，也不要据此要求重新登录。'
                channels[name] = info
            except AuthFailure:
                channels[name] = {'account': '', 'status': 'unavailable', 'checked_live': False,
                    'status_label': '暂时无法读取状态', 'message': '读取状态失败不代表渠道登录过期，不能据此要求重新登录。'}
        return {'name': user.get('name') or user['username'], 'account': user['username'],
            'administrator': user['role'] == 'admin', 'channels': channels, 'checked_at': int(now)}
