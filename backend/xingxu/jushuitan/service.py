"""Personal encrypted supply-chain credentials; no shared browser or HAR dependency."""
import asyncio
import copy
import hashlib
import json
import re
import time

import httpx
from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..auth import AuthFailure
from ..account_storage import scoped
from ..store import JsonRepository
from .captcha import Captchas

ORIGIN = 'https://gyl.scm121.com'
KEY = ORIGIN + '/api/auth/open/getEncryptKey'
LOGIN = ORIGIN + '/api/auth/open/v2/login/gyl/accountLogin'
CAPTCHA = ORIGIN + '/api/auth/open/v2/login/getCheckCode'


def owner(user):
    return hashlib.sha256(json.dumps([user['workspace_id'], user['id']]).encode()).hexdigest()


class JushuitanService:
    def __init__(self, directory, cipher, *, transport=None, login_runner=None):
        self.repo = JsonRepository(directory, 'jushuitan', ('connections',))
        self.cipher, self.transport = cipher, transport
        self.login_runner = login_runner or self.login
        self.locks = {}
        self.slots = asyncio.Semaphore(2)
        self.captchas = Captchas()

    def operation_guard(self, user):
        return self.locks.setdefault(owner(user), asyncio.Lock())

    def encrypt(self, value):
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()

    def decrypt(self, value):
        try:return json.loads(self.cipher.decrypt(value.encode()))
        except (InvalidToken, ValueError, TypeError):
            raise AuthFailure('jushuitan_credential_error', '聚水潭凭证无法读取，请重新登录。', 503) from None

    def record(self, user):
        with scoped(self.repo, user, write=False) as state:
            return copy.deepcopy(state['connections'].get(owner(user), {}))

    def summary(self, user):
        record = self.record(user)
        return {'account': record.get('account', ''), 'has_password': bool(record.get('password') and self.decrypt(record['password'])),
                'connected': bool(record.get('session')), 'identity': record.get('identity'),
                'last_login_at': record.get('last_login_at'), 'expires_at': record.get('expires_at')}

    @staticmethod
    def check(value, *, secrets=()):
        if not isinstance(value, dict) or value.get('success') is not True or str(value.get('code')) != '0000':
            code = str(value.get('code')) if isinstance(value, dict) else ''
            code = code if re.fullmatch(r'[A-Za-z0-9_-]{1,24}', code) else 'unknown'
            detail = str(value.get('message') or '') if isinstance(value, dict) else ''
            for secret in sorted((item for item in secrets if item), key=len, reverse=True):
                detail = detail.replace(secret, '[已隐藏]')
            detail = re.sub(r'[A-Za-z0-9_./+=-]{24,}', '[已隐藏]', detail)
            detail = re.sub(r'[\x00-\x1f]', ' ', detail).strip()[:200]
            reason = f'（业务码 {code}）' + (f' 平台提示：{detail}' if detail else '')
            if re.search(r'用户名或密码|账号或密码|帐号或密码', detail):
                raise AuthFailure('jushuitan_login_failed', '聚水潭账号或密码不正确，原有登录会话已保留。' + reason, 400)
            if code == '20001' and re.search(r'图形|验证码', detail):
                raise AuthFailure('jushuitan_captcha_required', '请填写图形验证码后再次登录，原会话未覆盖。' + reason, 409)
            if code in ('20001', '20002', '1007', '3333'):
                raise AuthFailure('jushuitan_verification_required', '聚水潭要求额外安全验证，请在官方网页确认账号状态；原会话未覆盖。' + reason, 409)
            raise AuthFailure('jushuitan_login_failed', '聚水潭未接受本次登录，原会话未覆盖。' + reason, 400)
        return value.get('data')

    async def captcha(self, user, account):
        lock = self.operation_guard(user)
        if lock.locked():raise AuthFailure('jushuitan_busy', '聚水潭正在处理当前账号的请求，请稍后再试。', 409)
        async with lock, self.slots:
            self.captchas.discard(owner(user))
            try:
                # Each new image starts with an empty cookie jar. Only this
                # challenge's cookies can accompany its human-entered answer.
                async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                        timeout=30, headers={'appcode': 'gyl.scm121.com', 'Origin': ORIGIN, 'Referer': ORIGIN + '/user/login'}) as client:
                    response = await client.get(CAPTCHA, params={'loginName': account})
                    response.raise_for_status()
                    data = self.check(response.json(), secrets=(account,))
                    return self.captchas.issue(owner(user), account, data, httpx.Cookies(client.cookies))
            except httpx.HTTPError:
                raise AuthFailure('jushuitan_unavailable', '无法获取聚水潭验证码，请点击刷新重试。', 502) from None
            except ValueError:
                raise AuthFailure('jushuitan_invalid_response', '聚水潭验证码图片无效，请点击刷新重试。', 502) from None

    async def login(self, account, password, *, verification=None):
        headers = {'Content-Type': 'application/json', 'Origin': ORIGIN, 'Referer': ORIGIN + '/user/login', 'appcode': 'gyl.scm121.com'}
        try:
            async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False, timeout=30,
                    headers=headers, cookies=verification['cookies'] if verification else None) as client:
                response = await client.post(KEY, params={'username': account})
                response.raise_for_status()
                key = self.check(response.json(), secrets=(account, password))
                if not isinstance(key, str) or len(key.encode()) not in (16, 24, 32):
                    raise AuthFailure('jushuitan_invalid_response', '聚水潭登录加密参数无效，原会话未覆盖。', 502)
                padder = padding.PKCS7(128).padder()
                encryptor = Cipher(algorithms.AES(key.encode()), modes.ECB()).encryptor()
                encoded = (encryptor.update(padder.update(password.encode()) + padder.finalize()) + encryptor.finalize()).hex()
                body = {'loginName': account, 'password': encoded}
                if verification:
                    body.update(preToken=verification['pre_token'], verifyCode=verification['verify_code'])
                response = await client.post(LOGIN, json=body)
                response.raise_for_status()
                result = self.check(response.json(), secrets=(account, password, encoded))
        except httpx.HTTPError:
            raise AuthFailure('jushuitan_unavailable', '无法连接聚水潭登录服务，原会话未覆盖。', 502) from None
        except ValueError:
            raise AuthFailure('jushuitan_invalid_response', '聚水潭登录响应无效，原会话未覆盖。', 502) from None
        if (not isinstance(result, dict) or not isinstance(result.get('access_token'), str) or not result['access_token']
                or not all(str(result.get(k, '')).isdecimal() for k in ('uid', 'coId'))):
            raise AuthFailure('jushuitan_invalid_response', '聚水潭没有签发完整的身份和访问凭证，原会话未覆盖。', 502)
        return result

    async def connect(self, user, account, password, *, challenge_id=None, verify_code=None):
        lock = self.operation_guard(user)
        if lock.locked():raise AuthFailure('jushuitan_busy', '聚水潭正在处理当前账号的请求，请稍后再试。', 409)
        async with lock, self.slots:
            verification = None
            if challenge_id is not None:
                verification = self.captchas.consume(owner(user), account, challenge_id)
                verification['verify_code'] = verify_code
            else:
                self.captchas.discard(owner(user))
            old = self.record(user)
            if password is None:
                password = self.decrypt(old['password']) if old.get('account') == account and old.get('password') else ''
            result = await self.login_runner(account, password, **({'verification': verification} if verification else {}))
            identity = {key: result.get(key) for key in ('uid', 'coId', 'user_name')}
            expires = result.get('expires_in')
            expires_at = int(time.time()) + expires if type(expires) is int and expires > 0 else None
            record = {'account': account, 'password': self.encrypt(password), 'session': self.encrypt(result),
                      'identity': identity, 'last_login_at': int(time.time()), 'expires_at': expires_at}
            with scoped(self.repo, user) as state:
                state['connections'][owner(user)] = record
            return self.summary(user)

    def credentials(self, user):
        if 'jushuitan:order:read' not in user.get('permissions', []):
            raise AuthFailure('forbidden', '没有聚水潭订单查询权限。', 403)
        record = self.record(user)
        if not record.get('session'):
            raise AuthFailure('jushuitan_login_required', '请先在“我的账号 → 聚水潭”登录。', 409)
        session = self.decrypt(record['session'])
        # Only the access token goes to the fixed query process; never passwords
        # or refresh tokens, and never channel roles as Galaxy permissions.
        return {key: session[key] for key in ('access_token', 'uid', 'coId')}
