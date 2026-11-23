from datetime import date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Query
from .auth import AuthFailure
from .database import database
from .usage import TOKEN_FIELDS

FIELDS = (*TOKEN_FIELDS, 'requests', 'unknown_requests', 'failed_requests')


def totals(rows):
    rows = list(rows)
    return {field: sum(row.get(field) or 0 for row in rows) for field in FIELDS}


def dates(start=None, end=None):
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    try:
        first = date.fromisoformat(start) if start else today.replace(day=1)
        last = date.fromisoformat(end) if end else today
        if first > last:
            raise ValueError()
        zone = ZoneInfo('Asia/Shanghai')
        return first, last, datetime.combine(first, time.min, zone).timestamp(), datetime.combine(last + timedelta(days=1), time.min, zone).timestamp()
    except (ValueError, OverflowError):
        raise AuthFailure('invalid_input', '请选择有效的开始与结束日期。', 422) from None


def report(store, auth, user, start=None, end=None, *, model=None, entry_point=None):
    if 'ai:usage:read' not in user['permissions']:
        raise AuthFailure('forbidden', '当前账号没有查看员工 AI 用量的权限。', 403)
    first, last, since, until = dates(start, end)
    rows = store.aggregate(user['workspace_id'], start=since, end=until, model=model, entry_point=entry_point)
    with auth.repository.transaction(write=False) as state:
        accounts = [dict(raw) for raw in state['users'].values() if raw['workspace_id'] == user['workspace_id']]
    # Obtain avatars and names once, not a separate MySQL connection per employee.
    profiles = {}
    import os
    if os.environ.get('GALAXY_DATABASE_CONFIG'):
        with database().connection() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT user_id,name,avatar,department FROM employees WHERE workspace_id=%s', (user['workspace_id'],))
            profiles = {row['user_id']: row for row in cursor.fetchall()}
    users = []
    for account in accounts:
        profile = profiles.get(account['id'], {})
        users.append(dict(user_id=account['id'], account=account['username'], name=profile.get('name') or account['username'],
            avatar=profile.get('avatar'), department=profile.get('department', ''),
            **totals(r for r in rows if r['user_id'] == account['id'])))
    by_day, by_source, by_model = {}, {}, {}
    for row in rows:
        by_day.setdefault(row['day'], []).append(row)
        by_source.setdefault(row['entry_point'], []).append(row)
        by_model.setdefault(row['model'], []).append(row)
    return dict(start=first.isoformat(), end=last.isoformat(), timezone='Asia/Shanghai', totals=totals(rows),
        users=sorted(users, key=lambda row: (-row['total_tokens'], row['account'])),
        daily=[dict(date=key, **totals(value)) for key, value in sorted(by_day.items())],
        sources=[dict(source=key, **totals(value)) for key, value in sorted(by_source.items())],
        models=[dict(model=key, **totals(value)) for key, value in sorted(by_model.items())], checked_at=datetime.now().timestamp())


def personal(store, user):
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    rows = store.aggregate(user['workspace_id'], user_id=user['id'])
    return {'today': totals(r for r in rows if r['day'] == today.isoformat()),
        'month': totals(r for r in rows if r['day'][:7] == today.isoformat()[:7]), 'all': totals(rows)}


def create_router(store, auth, identity):
    router = APIRouter(prefix='/api/v1/usage')
    @router.get('/me')
    def mine(value: Annotated[dict, Depends(identity)]):
        return personal(store, value['user'])

    @router.get('')
    def company(value: Annotated[dict, Depends(identity)],
            start: str | None = Query(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$'),
            end: str | None = Query(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$'),
            model: str | None = Query(default=None, max_length=160),
            entry_point: str | None = Query(default=None, pattern=r'^(desktop|wechat|feishu|schedule|test)$')):
        return report(store, auth, value['user'], start, end, model=model, entry_point=entry_point)
    return router
