"""Per-user encrypted 管家婆 connections, available to later server-side tools."""
import copy
import hashlib
import json
import time
import threading
import httpx
from cryptography.fernet import InvalidToken
from ..auth import AuthFailure
from ..store import JsonRepository
from .login import login, list_databases, LoginError


def owner_key(user):
    return hashlib.sha256(json.dumps([user['workspace_id'], user['id']]).encode()).hexdigest()


class GuanjiaService:
    def __init__(self, directory, cipher, *, login_runner=None, database_runner=None):
        self.repo = JsonRepository(directory, 'guanjia', ('connections',))
        self.cipher = cipher
        self.login_runner = login_runner or login
        self.database_runner = database_runner or list_databases
        self.slots = threading.BoundedSemaphore(2)
        self.active = set()
        self.guard = threading.Lock()

    def _encrypt(self, value):
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()

    def _decrypt(self, value):
        try:
            return json.loads(self.cipher.decrypt(value.encode()))
        except (InvalidToken, ValueError, TypeError):
            raise AuthFailure('guanjia_credential_error', '管家婆凭证无法读取，请重新输入账号密码登录。', 503) from None

    def _record(self, user):
        with self.repo.transaction(write=False) as state:
            return copy.deepcopy(state['connections'].get(owner_key(user), {}))

    def summary(self, user):
        value = self._record(user)
        present = bool(value.get('session'))
        return {'account': value.get('account', ''), 'has_password': bool(value.get('password') and self._decrypt(value['password'])),
                'connected': present, 'cookie': {'present': present}, 'sid': {'present': present},
                'database': value.get('database', ''), 'database_name': value.get('database_name', ''),
                'last_login_at': value.get('last_login_at')}

    def databases(self, user):
        if 'guanjia:configure' not in user.get('permissions', []):
            raise AuthFailure('forbidden', '没有访问权限。', 403)
        try:
            return self.database_runner()
        except LoginError as error:
            raise AuthFailure('guanjia_databases_failed', str(error), 502) from None
        except httpx.HTTPError:
            raise AuthFailure('guanjia_unavailable', '暂时无法读取管家婆账套，请稍后刷新。', 502) from None

    def connect(self, user, account, password, database=None):
        key = owner_key(user)
        with self.guard:
            if key in self.active or not self.slots.acquire(blocking=False):
                raise AuthFailure('guanjia_busy', '正在登录，请稍后再试。', 409)
            self.active.add(key)
        try:
            old = self._record(user)
            selected = database or (old.get('database') if old.get('account') == account else None)
            if password is None:
                password = self._decrypt(old['password']) if old.get('account') == account and old.get('password') else ''
            try:
                client = self.login_runner(account, password, **({'database': selected} if selected else {}))
            except LoginError as error:
                raise AuthFailure('guanjia_login_failed', str(error), 400) from None
            except httpx.HTTPError:
                raise AuthFailure('guanjia_unavailable', '无法连接管家婆，请稍后重新登录。', 502) from None
            try:
                session = {'domain': client.domain, 'sid': client.sid, 'cookie': client.cookie,
                           'cookies': [{'name': c.name, 'value': c.value, 'domain': c.domain,
                                        'path': c.path, 'secure': c.secure} for c in client.session.cookies.jar]}
                if not session['sid'] or not session['cookie']:
                    raise AuthFailure('guanjia_login_failed', '登录没有返回完整凭证，请重新登录。', 502)
                if selected and client.database != selected:
                    raise AuthFailure('guanjia_database_mismatch', '返回的账套与所选账套不一致，未覆盖原有会话。', 502)
                record = {'account': account, 'password': self._encrypt(password),
                          'session': self._encrypt(session), 'database': client.database,
                          'database_name': getattr(client, 'database_name', ''),
                          'last_login_at': int(time.time())}
                with self.repo.transaction() as state:
                    state['connections'][key] = record
            finally:
                client.session.close()
            return self.summary(user)
        finally:
            with self.guard:
                self.active.discard(key)
                self.slots.release()

    def credentials(self, user):
        """Server-only accessor; no raw Cookie/SID is returned by public routes."""
        if 'guanjia:configure' not in user.get('permissions', []):
            raise AuthFailure('forbidden', '没有访问权限。', 403)
        record = self._record(user)
        if not record.get('session'):
            raise AuthFailure('guanjia_login_required', '请先在控制台登录管家婆。', 409)
        return self._decrypt(record['session'])
