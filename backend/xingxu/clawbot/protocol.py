"""Tencent iLink protocol, based on @tencent-weixin/openclaw-weixin 2.4.10 (MIT)."""
import base64
import secrets
from urllib.parse import urlsplit
import httpx
from ..auth import AuthFailure

BASE = 'https://ilinkai.weixin.qq.com'


def origin(value):
    parsed = urlsplit(value)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or not host.endswith('.weixin.qq.com') or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise AuthFailure('clawbot_protocol_error', '微信返回的服务地址无效。', 502)
    return 'https://' + host


class ILink:
    def __init__(self, transport=None):
        self.client = httpx.AsyncClient(transport=transport, timeout=40, follow_redirects=False, trust_env=False)

    async def close(self):
        await self.client.aclose()

    async def request(self, endpoint, *, base=BASE, token=None, body=None, params=None):
        headers = {'iLink-App-Id': 'bot', 'iLink-App-ClientVersion': str((2 << 16) | (4 << 8) | 10)}
        if body is not None:
            headers.update({'AuthorizationType': 'ilink_bot_token', 'X-WECHAT-UIN':
                base64.b64encode(str(secrets.randbits(32)).encode()).decode()})
        if token:
            headers['Authorization'] = 'Bearer ' + token
        try:
            response = await self.client.request('POST' if body is not None else 'GET',
                origin(base) + '/ilink/bot/' + endpoint, headers=headers, params=params, json=body)
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError()
        except (httpx.HTTPError, ValueError):
            raise AuthFailure('clawbot_unavailable', '暂时无法连接微信 ClawBot，请稍后重试。', 502) from None
        code = value.get('ret') or value.get('errcode') or 0
        if code:
            if code == -14:
                raise AuthFailure('clawbot_expired', '微信连接已失效，请重新扫码。', 401)
            raise AuthFailure('clawbot_rejected', '微信未接受本次请求，请检查连接状态后重试。', 502)
        return value
