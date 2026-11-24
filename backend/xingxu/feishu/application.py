"""Fixed application-identity endpoints and short-lived, application-isolated cache."""
import asyncio
import time
from urllib.parse import quote

import httpx

from ..auth import AuthFailure

APPLICATION_SCOPES = ('contact:contact.base:readonly', 'contact:user.employee:readonly',
                      'contact:user.employee_id:readonly', 'attendance:task:readonly',
                      'im:message.p2p_msg:readonly', 'im:message.group_at_msg:readonly', 'im:message:send_as_bot')


class FeishuApplication:
    def __init__(self, transport=None):
        self.transport = transport
        self.tokens = {}
        self.locks = {}

    async def _request(self, method, url, **kwargs):
        try:
            async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                                         follow_redirects=False, timeout=25) as client:
                response = await client.request(method, url, **kwargs)
                if len(response.content) > 512 * 1024:
                    raise ValueError()
                value = response.json()
                if not isinstance(value, dict):
                    raise ValueError()
                return response.status_code, value
        except (httpx.HTTPError, ValueError):
            raise AuthFailure('feishu_unreachable', '无法连接飞书身份校验服务，请稍后重试。', 502) from None

    async def token(self, app, *, force=False):
        key = (app['app_id'], app['revision'])
        async with self.locks.setdefault(key, asyncio.Lock()):
            previous = self.tokens.get(key)
            if not force and previous and previous['expires_at'] > time.time() + 60:
                return dict(previous)
            status, value = await self._request('POST', 'https://accounts.feishu.cn/oauth/v3/token',
                data={'grant_type': 'client_credentials', 'client_id': app['app_id'],
                      'client_secret': app['app_secret']})
            try:
                seconds = int(value['expires_in'])
                access_token = value['access_token']
                if status != 200 or value.get('error') or value.get('code', 0) != 0 or seconds <= 0:
                    raise ValueError()
                if not isinstance(access_token, str) or not access_token:
                    raise ValueError()
                scope = value.get('scope', '')
                if not isinstance(scope, str):
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise AuthFailure('feishu_application_token_failed', '无法取得飞书应用凭证，请管理员检查应用配置。', 409) from None
            result = {'access_token': access_token, 'expires_at': time.time() + seconds,
                      'scope': scope, 'scope_known': 'scope' in value}
            self.tokens[key] = result
            return dict(result)

    async def inspect(self, app, identity):
        """Verify membership and observe platform role; caller grants are separate."""
        result = {'status': 'unknown', 'is_tenant_manager': None,
                  'identity_verified': False,
                  'employee_id': identity.get('user_id', ''), 'employee_no': '',
                  'checked_at': int(time.time()), 'reason': 'verification_unavailable'}
        try:
            for attempt in range(2):
                token = await self.token(app, force=attempt > 0)
                status, value = await self._request('GET',
                    'https://open.feishu.cn/open-apis/contact/v3/users/' + quote(identity['open_id'], safe=''),
                    params={'user_id_type': 'open_id'},
                    headers={'Authorization': 'Bearer ' + token['access_token']})
                if value.get('code') != 99991672:
                    break
        except AuthFailure as error:
            result['reason'] = error.code
            return result
        if status != 200 or value.get('code') != 0:
            result['reason'] = 'contact_permission_required' if value.get('code') == 99991672 else 'contact_verification_failed'
            return result
        data = value.get('data')
        contact = data.get('user', {}) if isinstance(data, dict) else {}
        if not isinstance(contact, dict) or contact.get('open_id') != identity.get('open_id'):
            result['reason'] = 'identity_mismatch'
            return result
        if identity.get('user_id') and contact.get('user_id') and contact['user_id'] != identity['user_id']:
            result['reason'] = 'identity_mismatch'
            return result
        state = contact.get('status', {})
        if isinstance(state, dict) and any(state.get(k) is True for k in ('is_frozen', 'is_resigned', 'is_exited', 'is_unjoin')):
            result.update(status='inactive', reason='employee_inactive', employee_id='', employee_no='')
            return result
        result['identity_verified'] = True
        result['employee_id'] = contact.get('user_id') if isinstance(contact.get('user_id'), str) else result['employee_id']
        result['employee_no'] = contact.get('employee_no') if isinstance(contact.get('employee_no'), str) else ''
        flag = contact.get('is_tenant_manager')
        if type(flag) is bool:
            result.update(status='tenant_manager' if flag else 'tenant_member',
                          is_tenant_manager=flag, reason=None)
        else:
            result['reason'] = 'manager_field_unavailable'
        return result
