"""Operator-managed encrypted SQL credentials, scoped to the caller's saved book."""
import hashlib
import json

from cryptography.fernet import InvalidToken
from ..account_storage import read_json, scoped, Accounts
from ..auth import AuthFailure
from .service import owner_key

PERMISSION = 'guanjia:inventory-cost:read'


class CostSource:
    def __init__(self, directory, cipher, guanjia):
        self.directory, self.cipher, self.guanjia = directory, cipher, guanjia

    def credentials(self, user):
        if PERMISSION not in user['permissions']:
            raise AuthFailure('forbidden', '当前账号没有读取库存均价权限。', 403)
        with scoped(self.guanjia.repo, user, write=False) as state:
            record = state['connections'].get(owner_key(user), {})
            book = record.get('database')
        if not book:
            raise AuthFailure('gjp_cost_book_required', '请先在“我的账号 → 管家婆”绑定要查询的账套；数据库工具不要求网页会话有效。', 409)
        try:
            sources = read_json(self.directory / 'gjp-cost-sources.json', {'sources': {}})['sources']
            key = json.dumps([user['workspace_id'], book], separators=(',', ':'))
            encrypted = sources.get(key)
            if not encrypted:
                raise AuthFailure('gjp_cost_source_required', '本人绑定账套尚未配置库存均价数据源，请管理员检查；不会改查其他账套。', 409)
            config = json.loads(self.cipher.decrypt(encrypted.encode()))
            if (config.get('database') != book or config.get('workspace_id') != user['workspace_id']
                    or not all(isinstance(config.get(k), str) and config[k] for k in ('server', 'user', 'password'))
                    or type(config.get('port')) is not int or not 1 <= config['port'] <= 65535):
                raise ValueError()
            return {**config, 'mode': 'inventory-cost-database',
                    'source_revision': hashlib.sha256(encrypted.encode()).hexdigest()}
        except (InvalidToken, ValueError, TypeError, KeyError, RuntimeError, OSError):
            raise AuthFailure('gjp_cost_source_invalid', '库存均价数据源不可读取，请管理员检查配置。', 503) from None

    def frame(self, user, credentials, files, job_id, source_limit):
        return {'categories': {'gjp': credentials}, 'job_id': job_id,
                'job_directory': str(Accounts(self.directory).path(user, 'tasks/inventory-cost')),
                'workspace_directory': str(files.workspace(user)), 'cache_directory': str(files.cache(user)),
                'cache_identity': {'user_id': user['id'], 'workspace_id': user['workspace_id']},
                'source_limit_bytes': source_limit}
