"""Shared identity and temporary execution sessions for external AI entry points."""
import copy
import secrets
import time
import asyncio
from .auth import AuthFailure, digest


def execution_slot(ai):
    if not hasattr(ai, '_channel_execution_slot'):
        ai._channel_execution_slot = asyncio.Lock()
    return ai._channel_execution_slot


def current(auth, actor, required):
    with auth.repository.transaction(write=False) as state:
        raw = copy.deepcopy(state['users'].get(actor['id']))
    if not raw or not raw.get('active') or raw['workspace_id'] != actor['workspace_id']:
        raise AuthFailure('forbidden', '绑定的 Galaxy 账号已停用。', 403)
    user = auth._public(raw)
    if not set(required).issubset(user.get('permissions', [])):
        raise AuthFailure('forbidden', '当前 Galaxy 账号没有该入口所需的使用权限。', 403)
    return user, raw


def issue_session(auth, user, raw):
    token, now = secrets.token_urlsafe(32), int(time.time())
    with auth.repository.transaction() as state:
        latest = state['users'].get(raw['id'])
        if (not latest or not latest.get('active') or latest['auth_version'] != raw['auth_version']
                or latest['workspace_id'] != user['workspace_id']):
            raise AuthFailure('forbidden', '账号授权已失效。', 403)
        state['sessions'][digest(token)] = {'user_id': raw['id'], 'auth_version': raw['auth_version'],
            'created_at': now, 'expires_at': now + auth.policy.session_seconds, 'remember': False}
    return token
