"""One durable usage row per provider request, independent of chat retention."""
import asyncio
import functools
import os
import time
import uuid
from contextvars import ContextVar
from datetime import datetime
from zoneinfo import ZoneInfo
from .account_storage import folder, locked, read_json, atomic_json
from .database import database

SCOPE = ContextVar('galaxy_usage_scope', default=None)
REQUEST = ContextVar('galaxy_usage_request', default=None)
TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'total_tokens', 'cache_hit_tokens', 'cache_miss_tokens')


def normalize(value, native=False):
    value = value if isinstance(value, dict) else {}
    def number(key):
        result = value.get(key)
        return result if type(result) is int and 0 <= result <= 2**63 - 1 else None
    if native:
        miss, hit, create = number('input_tokens'), number('cache_read_input_tokens'), number('cache_creation_input_tokens')
        inputs = miss + (hit or 0) + (create or 0) if miss is not None else None
        output = number('output_tokens')
        return dict(input_tokens=inputs, output_tokens=output,
            total_tokens=(inputs or 0) + (output or 0) if inputs is not None or output is not None else None,
            cache_hit_tokens=hit, cache_miss_tokens=miss + (create or 0) if miss is not None else None)
    inputs, output = number('prompt_tokens'), number('completion_tokens')
    hit, miss = number('prompt_cache_hit_tokens'), number('prompt_cache_miss_tokens')
    if hit is None and isinstance(value.get('prompt_tokens_details'), dict):
        detail = value['prompt_tokens_details'].get('cached_tokens')
        hit = detail if type(detail) is int and 0 <= detail <= (inputs or 0) else None
    if miss is None and inputs is not None and hit is not None and hit <= inputs:
        miss = inputs - hit
    return dict(input_tokens=inputs, output_tokens=output, total_tokens=number('total_tokens')
        if number('total_tokens') is not None else (inputs or 0) + (output or 0) if inputs is not None or output is not None else None,
        cache_hit_tokens=hit, cache_miss_tokens=miss)


def sample(value, *, native=False, final=True):
    request = REQUEST.get()
    if request is not None:
        request.update(normalize(value, native))
        request['usage_complete'] = bool(final and request['input_tokens'] is not None and request['output_tokens'] is not None)
        request['_store'].save(request)


def summary(rows):
    rows = list(rows)
    return {**{field: sum(row.get(field) or 0 for row in rows) for field in TOKEN_FIELDS},
        'requests': len(rows), 'unknown_requests': sum(not r.get('usage_complete') for r in rows),
        'failed_requests': sum(r.get('status') in ('failed', 'interrupted') for r in rows)}


class UsageScope:
    def __init__(self, store, user, conversation_id, turn_id, entry_point):
        self.store, self.user = store, user
        self.conversation_id, self.turn_id, self.entry_point = conversation_id, turn_id, entry_point
        self.rows = []

    def result(self):
        result = summary(self.rows)
        result['complete'] = not result['unknown_requests'] and bool(self.rows)
        return result


def inherit_scope(function):
    @functools.wraps(function)
    async def invoke(self, *args, **kwargs):
        token = SCOPE.set(self.usage_scope)
        try:
            return await function(self, *args, **kwargs)
        finally:
            SCOPE.reset(token)
    return invoke


def metered(*, native=False):
    def decorate(function):
        @functools.wraps(function)
        async def invoke(self, *args, **kwargs):
            scope = getattr(self, 'usage_scope', None) if native else SCOPE.get()
            if scope is None:
                return await function(self, *args, **kwargs)
            provider = self.provider if native else args[0]
            row = {'id': str(uuid.uuid4()), 'user_id': scope.user['id'], 'workspace_id': scope.user['workspace_id'],
                'conversation_id': scope.conversation_id, 'turn_id': scope.turn_id, 'entry_point': scope.entry_point,
                'provider_id': provider['id'], 'model': provider['model'], 'started_at': time.time(),
                'finished_at': None, 'status': 'pending', 'usage_complete': False,
                **{field: None for field in TOKEN_FIELDS}, '_store': scope.store}
            scope.store.save(row)
            scope.rows.append(row)
            token = REQUEST.set(row)
            try:
                value = await function(self, *args, **kwargs)
                row['status'] = 'success'
                return value
            except asyncio.CancelledError:
                row['status'] = 'interrupted'
                raise
            except Exception:
                row['status'] = 'failed'
                raise
            finally:
                row['finished_at'] = time.time()
                try:
                    scope.store.save(row)
                finally:
                    REQUEST.reset(token)
        return invoke
    return decorate


class UsageStore:
    def __init__(self, directory):
        self.directory = directory

    def save(self, row):
        record = {k: v for k, v in row.items() if not k.startswith('_')}
        if os.environ.get('GALAXY_DATABASE_CONFIG'):
            columns = list(record)
            with database().connection() as connection, connection.cursor() as cursor:
                cursor.execute('INSERT INTO ai_usage (' + ','.join('`' + k + '`' for k in columns) + ') VALUES ('
                    + ','.join(['%s'] * len(columns)) + ') ON DUPLICATE KEY UPDATE '
                    + ','.join('`' + k + '`=VALUES(`' + k + '`)' for k in columns if k != 'id'), tuple(record.values()))
            return
        # Offline test repository only; production always uses MySQL.
        base = folder(self.directory / 'usage')
        with locked(base / '.lock', True):
            state = read_json(base / 'records.json', {})
            state[record['id']] = record
            atomic_json(base / 'records.json', state)

    def aggregate(self, workspace, *, user_id=None, start=None, end=None, model=None, entry_point=None):
        if os.environ.get('GALAXY_DATABASE_CONFIG'):
            where, args = ['workspace_id=%s'], [workspace]
            for field, value, operation in (('user_id', user_id, '='), ('started_at', start, '>='), ('started_at', end, '<'), ('model', model, '='), ('entry_point', entry_point, '=')):
                if value is not None:
                    where.append('`' + field + '`' + operation + '%s'); args.append(value)
            with database().connection() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT user_id,entry_point,model,DATE(TIMESTAMPADD(SECOND,FLOOR(started_at)+28800,'1970-01-01')) day,"
                    + ','.join('COALESCE(SUM(`' + f + '`),0) `' + f + '`' for f in TOKEN_FIELDS)
                    + ",COUNT(*) requests,SUM(NOT usage_complete) unknown_requests,SUM(status IN ('failed','interrupted')) failed_requests"
                    + ' FROM ai_usage WHERE ' + ' AND '.join(where) + ' GROUP BY user_id,entry_point,model,day', args)
                return [{k: str(v) if k == 'day' else int(v) if k in (*TOKEN_FIELDS, 'requests', 'unknown_requests', 'failed_requests') else v for k, v in row.items()} for row in cursor.fetchall()]
        base = folder(self.directory / 'usage')
        with locked(base / '.lock', False):
            rows = read_json(base / 'records.json', {}).values()
        grouped = {}
        for row in rows:
            if (row['workspace_id'] != workspace or user_id and row['user_id'] != user_id
                    or start is not None and row['started_at'] < start or end is not None and row['started_at'] >= end
                    or model and row['model'] != model or entry_point and row['entry_point'] != entry_point):
                continue
            day = datetime.fromtimestamp(row['started_at'], ZoneInfo('Asia/Shanghai')).date().isoformat()
            grouped.setdefault((row['user_id'], row['entry_point'], row['model'], day), []).append(row)
        return [dict(user_id=k[0], entry_point=k[1], model=k[2], day=k[3], **summary(v)) for k, v in grouped.items()]
