"""Transactional MySQL adapters for existing module record contracts.

Accounts/sessions and personnel are relational. Complex provider/platform records
retain their JSON structure in separate module tables; no JSON file fallback is used.
"""
import copy
import json
from contextlib import contextmanager
from pathlib import Path
from .database import database

TABLES = {
    'jushuitan': {'connections': 'jushuitan_connections'},
    'guanjia': {'connections': 'guanjia_connections'},
    'ai': {'providers': 'ai_providers', 'settings': 'ai_settings'},
    'chats': {'conversations': 'chat_conversations'},
    'youzan': {'connections': 'youzan_connections'},
    'feishu': {'apps': 'feishu_apps', 'connections': 'feishu_connections'},
    'files': {'files': 'file_assets'},
    'tool_audit': {'records': 'tool_audit'},
    'tool-prompts': {'workspaces': 'tool_prompts'},
    'uploads': {'requests': 'upload_requests'},
}


def load_json(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


class MySQLRepository:
    def __init__(self, directory, name='auth', fields=('users', 'sessions', 'attempts')):
        if name != 'auth' and (name not in TABLES or not fields or not set(fields) <= set(TABLES[name])):
            raise ValueError('Unknown repository')
        self.directory, self.name, self.fields = Path(directory), name, fields

    @contextmanager
    def transaction(self, *, write=True):
        with database().connection() as connection, connection.cursor() as cursor:
            # The module lock preserves the old atomic read/modify/write contract.
            if write:
                cursor.execute('SELECT name FROM repository_locks WHERE name=%s FOR UPDATE', (self.name,))
                if cursor.fetchone() is None:
                    raise RuntimeError('Database migration incomplete')
            state = self._load(cursor)
            before = copy.deepcopy(state) if write else None
            yield state
            if write:
                self._save(cursor, state, before)

    def _load(self, cursor):
        state = {'schema_version': 1}
        if self.name != 'auth':
            for field in self.fields:
                table = TABLES[self.name][field]
                cursor.execute(f'SELECT record_id,payload FROM `{table}`')
                state[field] = {row['record_id']: load_json(row['payload']) for row in cursor.fetchall()}
            return state
        cursor.execute('SELECT id,username,password_hash,role,workspace_id,active,auth_version,created_at FROM users')
        state['users'] = {row['id']: {**row, 'active': bool(row['active'])} for row in cursor.fetchall()}
        cursor.execute('SELECT token_hash,user_id,created_at,expires_at,remember,auth_version FROM sessions')
        state['sessions'] = {row.pop('token_hash'): {**row, 'remember': bool(row['remember'])} for row in cursor.fetchall()}
        cursor.execute('SELECT bucket,attempt_at FROM login_attempts ORDER BY id')
        state['attempts'] = {}
        for row in cursor.fetchall():
            state['attempts'].setdefault(row['bucket'], []).append(row['attempt_at'])
        return state

    def _save(self, cursor, state, before):
        if state.get('schema_version') != 1 or any(not isinstance(state.get(field), dict) for field in self.fields):
            raise ValueError('Invalid repository state')
        if self.name != 'auth':
            for field in self.fields:
                table = TABLES[self.name][field]
                for key in before[field].keys() - state[field].keys():
                    cursor.execute(f'DELETE FROM `{table}` WHERE record_id=%s', (key,))
                for key, value in state[field].items():
                    if key not in before[field] or before[field][key] != value:
                        cursor.execute(f'INSERT INTO `{table}` (record_id,payload) VALUES (%s,%s) ON DUPLICATE KEY UPDATE payload=VALUES(payload)',
                                       (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))
            return
        # Drop sessions before users to satisfy their foreign key.
        for key in before['sessions'].keys() - state['sessions'].keys():
            cursor.execute('DELETE FROM sessions WHERE token_hash=%s', (key,))
        for key in before['users'].keys() - state['users'].keys():
            cursor.execute('DELETE FROM users WHERE id=%s', (key,))
        for key, user in state['users'].items():
            if key not in before['users'] or user != before['users'][key]:
                cursor.execute('''INSERT INTO users (id,username,password_hash,role,workspace_id,active,auth_version,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE username=VALUES(username),
                    password_hash=VALUES(password_hash),role=VALUES(role),workspace_id=VALUES(workspace_id),
                    active=VALUES(active),auth_version=VALUES(auth_version),created_at=VALUES(created_at)''',
                    tuple(user[field] for field in ('id','username','password_hash','role','workspace_id','active','auth_version','created_at')))
                if key not in before['users'] or before['users'][key]['role'] != user['role']:
                    cursor.execute('DELETE FROM user_roles WHERE user_id=%s', (key,))
                    cursor.execute('INSERT INTO user_roles (user_id,role_code) VALUES (%s,%s)', (key,user['role']))
        for key, session in state['sessions'].items():
            if key not in before['sessions'] or session != before['sessions'][key]:
                cursor.execute('''INSERT INTO sessions (token_hash,user_id,created_at,expires_at,remember,auth_version)
                    VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE user_id=VALUES(user_id),created_at=VALUES(created_at),
                    expires_at=VALUES(expires_at),remember=VALUES(remember),auth_version=VALUES(auth_version)''',
                    (key, *(session[field] for field in ('user_id','created_at','expires_at','remember','auth_version'))))
        for key in before['attempts'].keys() | state['attempts'].keys():
            if before['attempts'].get(key) != state['attempts'].get(key):
                cursor.execute('DELETE FROM login_attempts WHERE bucket=%s', (key,))
                if state['attempts'].get(key):
                    cursor.executemany('INSERT INTO login_attempts (bucket,attempt_at) VALUES (%s,%s)', [(key,value) for value in state['attempts'][key]])

    def permissions(self, user):
        with database().connection() as connection, connection.cursor() as cursor:
            cursor.execute('''SELECT DISTINCT rp.permission_code FROM user_roles ur
                JOIN role_permissions rp ON rp.role_code=ur.role_code JOIN users u ON u.id=ur.user_id
                WHERE u.id=%s AND u.workspace_id=%s AND u.active=1''', (user['id'],user['workspace_id']))
            base = [row['permission_code'] for row in cursor.fetchall()]
            from .permission_policy import effective
            return effective(cursor, user, base)

    def profile(self, user):
        with database().connection() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT name,avatar FROM employees WHERE user_id=%s AND workspace_id=%s', (user['id'],user['workspace_id']))
            return cursor.fetchone() or {}
