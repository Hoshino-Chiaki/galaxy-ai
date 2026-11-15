"""Persistent per-owner refresh schedules; background jobs never reuse login tokens."""
import asyncio
import copy
import time
import uuid
from dataclasses import dataclass

from ..auth import AuthFailure, public_user

PERMISSION = 'youzan:renew:configure'
KINDS = ('sid', 'csrf')
DAY = 86400
RETRY = 1800
MANUAL = {'sms_verification_required', 'human_verification_required', 'login_rejected',
          'profile_account_mismatch', 'credentials_missing', 'login_unconfirmed'}


def public_schedule(record):
    policy = (record or {}).get('renewal', {})
    return {kind: {**{'enabled': False, 'interval_days': 2},
                   **{k: v for k, v in policy.get(kind, {}).items() if k in
                      ('enabled', 'interval_days', 'next_at', 'last_attempt_at', 'last_success_at', 'error', 'paused')}}
            for kind in KINDS}


@dataclass(frozen=True)
class RefreshGrant:
    owner: str
    user_id: str
    workspace_id: str
    auth_version: int
    policy_revision: str
    kind: str


class YouzanRenewal:
    def __init__(self, service, auth):
        self.service, self.auth = service, auth
        self.worker = None
        self.wake = asyncio.Event()

    def authorize(self, grant):
        with self.auth.repository.transaction(write=False) as state:
            raw = copy.deepcopy(state['users'].get(grant.user_id))
        if (not raw or not raw.get('active') or raw.get('workspace_id') != grant.workspace_id
                or raw.get('auth_version') != grant.auth_version):
            raise AuthFailure('forbidden', '自动刷新授权已失效，请管理员重新保存设置。', 403)
        user = public_user(raw)
        if PERMISSION not in user['permissions'] or 'youzan:configure' not in user['permissions']:
            raise AuthFailure('forbidden', '当前账号无权执行自动刷新。', 403)
        from .service import owner_key
        if owner_key(user) != grant.owner:
            raise AuthFailure('forbidden', '自动刷新身份不匹配。', 403)
        with self.service.repo.transaction(write=False) as state:
            policy = state['connections'].get(grant.owner, {}).get('renewal', {})
        if policy.get('revision') != grant.policy_revision or not policy.get(grant.kind, {}).get('enabled'):
            raise AuthFailure('forbidden', '自动刷新已关闭或设置已变更。', 403)
        return user

    def configure(self, user, body):
        if PERMISSION not in user['permissions']:
            raise AuthFailure('forbidden', '只有管理员可以配置自动刷新。', 403)
        from .service import owner_key
        key = owner_key(user)
        with self.auth.repository.transaction(write=False) as state:
            raw = state['users'].get(user['id'])
            if not raw or not raw.get('active'):
                raise AuthFailure('forbidden', '账号不可用。', 403)
            auth_version = raw['auth_version']
        now = int(time.time())
        with self.service.repo.transaction() as state:
            record = state['connections'].get(key)
            if not record:
                raise AuthFailure('credentials_missing', '请先保存有赞账号和密码。', 422)
            policy = {'revision': str(uuid.uuid4()), 'user_id': user['id'],
                      'workspace_id': user['workspace_id'], 'auth_version': auth_version}
            for kind in KINDS:
                settings = getattr(body, kind).model_dump()
                old = record.get('renewal', {}).get(kind, {})
                base = old.get('last_success_at') or record.get(kind, {}).get('updated_at') or now
                policy[kind] = {**settings, **{k: old[k] for k in ('last_attempt_at', 'last_success_at') if k in old},
                                'next_at': max(now, base + settings['interval_days'] * DAY) if settings['enabled'] else None,
                                'paused': False, 'error': None}
            record['renewal'] = policy
        self.wake.set()
        return self.service.summary(user)

    def start(self):
        if self.worker is None:
            self.worker = asyncio.create_task(self._loop(), name='youzan-renewal')

    async def close(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None

    async def _loop(self):
        while True:
            self.wake.clear()
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Transient scheduler errors never print credentials or stop future checks.
                pass
            try:
                await asyncio.wait_for(self.wake.wait(), 60)
            except TimeoutError:
                pass

    def _record_attempt(self, key, revision, kind, *, error=None, success=False, pause=False):
        now = int(time.time())
        with self.service.repo.transaction() as state:
            policy = state['connections'].get(key, {}).get('renewal', {})
            if policy.get('revision') != revision:
                return
            entry = policy[kind]
            entry.update(last_attempt_at=now, error=error, paused=pause,
                         next_at=None if pause else now + (entry['interval_days'] * DAY if success else RETRY))
            if success:
                entry['last_success_at'] = now

    async def tick(self):
        with self.service.repo.transaction(write=False) as state:
            records = copy.deepcopy(state['connections'])
        for key, record in records.items():
            policy = record.get('renewal', {})
            if not policy.get('revision') or key in self.service.tasks:
                continue
            if policy.get('sid', {}).get('paused') and policy.get('csrf', {}).get('enabled'):
                with self.service.repo.transaction() as state:
                    current = state['connections'].get(key, {}).get('renewal', {})
                    if current.get('revision') == policy['revision']:
                        current['csrf'].update(paused=True, next_at=None, error={
                            'code': 'sid_refresh_required', 'message': 'SID 自动刷新已暂停，请先完成登录验证，再刷新店铺会话。'})
                continue
            for kind in KINDS:
                entry = policy.get(kind, {})
                if not entry.get('enabled') or entry.get('paused') or not entry.get('next_at') or entry['next_at'] > time.time():
                    continue
                grant = RefreshGrant(key, policy['user_id'], policy['workspace_id'],
                                     policy['auth_version'], policy['revision'], kind)
                try:
                    user = self.authorize(grant)
                    # SID always goes first when both are due. CRM never starts a second login.
                    result = await self.service.start(user, 'sid' if kind == 'sid' else 'all', grant)
                    task = self.service.tasks.get(key)
                    if task:
                        await task
                    with self.service.repo.transaction(write=False) as state:
                        current = state['connections'].get(key, {})
                        job = copy.deepcopy(current.get('job', {}))
                    if job.get('id') != result['job']['id']:
                        raise AuthFailure('connection_changed', '刷新状态已变化，请重新检查。', 409)
                    if job.get('status') != 'success':
                        raise AuthFailure(job.get('code', 'failed'), job.get('message', '刷新失败。'), 409)
                    if kind == 'csrf' and job.get('code') == 'all_partial':
                        raise AuthFailure('all_partial', '部分店铺刷新失败，请检查店铺会话。', 409)
                    self._record_attempt(key, policy['revision'], kind, success=True)
                    if kind == 'sid' and job.get('code') in ('all_ready', 'success'):
                        self._record_attempt(key, policy['revision'], 'csrf', success=True)
                        break
                except AuthFailure as error:
                    if error.code == 'busy':
                        break  # Active business calls take priority; no attempt was started.
                    self._record_attempt(key, policy['revision'], kind,
                        error={'code': error.code, 'message': error.message},
                        pause=error.code in MANUAL or error.code == 'forbidden')
                    if kind == 'sid':
                        break
