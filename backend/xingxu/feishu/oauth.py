"""Feishu device OAuth protocol; only fixed official HTTPS endpoints are used."""
from urllib.parse import urlsplit
import httpx
from ..auth import AuthFailure

ACCOUNTS = 'https://accounts.feishu.cn'
READ_SCOPES = ('offline_access', 'docx:document:readonly', 'search:docs:read',
               'drive:drive:readonly', 'drive:file:download', 'drive:drive.metadata:readonly', 'wiki:node:retrieve')


class FeishuOAuth:
    def __init__(self, transport=None):
        self.transport = transport

    async def request(self, path, *, data=None, app=None, user_token=None):
        url = 'https://open.feishu.cn/open-apis/authen/v1/user_info' if user_token else ACCOUNTS + path
        try:
            async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False, timeout=25) as client:
                if user_token:
                    response = await client.get(url, headers={'Authorization': 'Bearer ' + user_token})
                else:
                    response = await client.post(url, data=data, auth=httpx.BasicAuth(app['app_id'], app['app_secret']) if path.endswith('device_authorization') else None)
                if len(response.content) > 128 * 1024:
                    raise ValueError()
                value = response.json()
                if not isinstance(value, dict):raise ValueError()
                return response.status_code, value
        except (httpx.HTTPError, ValueError):
            raise AuthFailure('feishu_unreachable', '无法连接飞书授权服务，请稍后重试。', 502) from None

    async def start(self, app, *, scopes=None):
        status, value = await self.request('/oauth/v1/device_authorization', app=app,
            data={'client_id': app['app_id'], 'scope': ' '.join(scopes or READ_SCOPES)})
        if status != 200 or value.get('error') or not value.get('device_code'):
            raise AuthFailure('feishu_app_error', '飞书未接受应用配置，请检查 App ID、App Secret 和应用的 OAuth 权限。', 422)
        url = value.get('verification_uri_complete') or value.get('verification_uri', '')
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.hostname not in {'accounts.feishu.cn', 'passport.feishu.cn', 'open.feishu.cn'} or parts.username or parts.password or parts.port not in (None, 443):
            raise AuthFailure('feishu_oauth_error', '飞书返回了不支持的授权地址。', 502)
        try:
            lifetime, interval = int(value['expires_in']), int(value.get('interval', 5))
            if not 1 <= lifetime <= 3600 or not 1 <= interval <= 60:raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise AuthFailure('feishu_oauth_error', '飞书授权响应格式异常。', 502) from None
        return {'device_code': value['device_code'], 'url': url, 'user_code': value.get('user_code', ''),
                'expires_in': lifetime, 'interval': interval}

    async def poll(self, app, device_code):
        return await self.request('/oauth/v3/token', app=app, data={'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
            'device_code': device_code, 'client_id': app['app_id'], 'client_secret': app['app_secret']})

    async def refresh(self, app, refresh_token):
        return await self.request('/oauth/v3/token', app=app, data={'grant_type': 'refresh_token', 'refresh_token': refresh_token,
            'client_id': app['app_id'], 'client_secret': app['app_secret']})

    async def identity(self, access_token):
        status, value = await self.request('', user_token=access_token)
        identity = value.get('data')
        if status != 200 or value.get('code') != 0 or not isinstance(identity, dict) or not identity.get('open_id'):
            raise AuthFailure('feishu_identity_error', '飞书身份校验失败，请重新授权。', 502)
        return {key: str(identity.get(key) or '')[:200] for key in ('open_id', 'tenant_key', 'user_id', 'name', 'avatar_url')}
