"""Short-lived, owner/account-bound state for a human-entered image challenge."""
import base64
import time
import uuid

from ..auth import AuthFailure


def image_url(encoded):
    if not isinstance(encoded, str) or len(encoded) > 2 * 1024 * 1024:
        raise ValueError('Invalid captcha image')
    if encoded.startswith('data:'):
        encoded = encoded.partition(',')[2]
    try:data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):raise ValueError('Invalid captcha image') from None
    if not 0 < len(data) <= 1024 * 1024:
        raise ValueError('Invalid captcha image')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):mime = 'image/png'
    elif data.startswith(b'\xff\xd8'):mime = 'image/jpeg'
    elif data.startswith((b'GIF87a', b'GIF89a')):mime = 'image/gif'
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':mime = 'image/webp'
    else:raise ValueError('Unsupported captcha image')
    return 'data:' + mime + ';base64,' + base64.b64encode(data).decode()


class Captchas:
    def __init__(self):
        self.pending = {}

    def prune(self):
        now = time.time()
        self.pending = {key: value for key, value in self.pending.items() if value['expires_at'] > now}

    def discard(self, owner):
        self.prune()
        self.pending.pop(owner, None)

    def issue(self, owner, account, data, cookies):
        self.discard(owner)
        token = data.get('preToken') if isinstance(data, dict) else None
        if not isinstance(token, str) or not token or len(token) > 8192:
            raise ValueError('Invalid captcha challenge')
        image = image_url(data.get('img'))
        value = {'id': str(uuid.uuid4()), 'account': account, 'pre_token': token,
                 'cookies': cookies, 'expires_at': int(time.time()) + 300}
        self.pending[owner] = value
        return {'id': value['id'], 'image': image, 'expires_at': value['expires_at']}

    def consume(self, owner, account, identifier):
        self.prune()
        value = self.pending.get(owner)
        if value is None or value['id'] != str(identifier) or value['account'] != account:
            raise AuthFailure('jushuitan_captcha_expired', '验证码已过期、已刷新或不属于当前账号，请刷新验证码后重试。', 409)
        self.pending.pop(owner)
        return value
