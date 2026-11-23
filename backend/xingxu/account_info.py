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
                    status = ('not_configured' if not value['configured'] else 'login_required' if not sid.get('present')
                        else 'expired' if sid.get('expired') else 'connected' if value.get('csrf', {}).get('present') else 'partial')
                    info = {'account': value.get('account', ''), 'status': status}
                elif name == 'feishu':
                    expired = value.get('expires_at') and value['expires_at'] <= now
                    refreshable = value.get('refresh_expires_at') and value['refresh_expires_at'] > now
                    info = {'account': (value.get('identity') or {}).get('name', ''),
                        'status': 'not_configured' if not value['configured'] else 'not_connected' if not value['connected']
                            else 'refreshable' if expired and refreshable else 'expired' if expired else 'connected'}
                elif name == 'guanjia':
                    info = {'account': value.get('account', ''), 'status': 'credentials_saved' if value['connected'] else 'not_connected'}
                elif name == 'clawbot':
                    info = {'account': value.get('bot_id', ''), 'status': value.get('status', 'disconnected')}
                else:
                    info = {'account': '', 'status': 'unknown'}
                channels[name] = info
            except AuthFailure:
                channels[name] = {'account': '', 'status': 'unavailable'}
        return {'name': user.get('name') or user['username'], 'account': user['username'],
            'administrator': user['role'] == 'admin', 'channels': channels, 'checked_at': int(now)}
