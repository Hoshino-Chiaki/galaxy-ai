"""Disposable MySQL schema plus private temp files; never mutates production records."""
import configparser
import copy
import json
import os
import secrets
import tempfile
import uuid
from pathlib import Path

import pymysql

from xingxu.account_storage import Accounts, AccountRepository, scoped, owner_key
from xingxu.auth import AuthService, ROLES
from xingxu.database import _pools
from xingxu.migrate_accounts import migrate, FIELDS
from xingxu.mysql_store import MySQLRepository, TABLES
from xingxu.store import JsonRepository


def main():
    schema = 'galaxy_storage_test_' + uuid.uuid4().hex[:12]
    config = configparser.ConfigParser()
    config.read('/etc/galaxy-ai/mysql-root.cnf')
    options = dict(config['client'])
    root = pymysql.connect(host=options.get('host', '127.0.0.1'), user=options.get('user', 'root'),
        password=options.get('password', ''), port=int(options.get('port', 3306)), autocommit=True)
    config_path = None
    try:
        with root.cursor() as cursor:
            cursor.execute('CREATE DATABASE `' + schema + '` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
            cursor.execute('USE `' + schema + '`')
            migration = Path(__file__).resolve().parents[1] / 'migrations/001_mysql.sql'
            for statement in migration.read_text().split(';'):
                if statement.strip():cursor.execute(statement)
            for name in TABLES:
                for table in TABLES[name].values():
                    cursor.execute('CREATE TABLE IF NOT EXISTS `' + table + '` (record_id VARCHAR(191) COLLATE utf8mb4_bin PRIMARY KEY,payload JSON NOT NULL) ENGINE=InnoDB')
            for name in ['auth', *TABLES]:
                cursor.execute('INSERT IGNORE INTO repository_locks (name) VALUES (%s)', (name,))
            for role, permissions in ROLES.items():
                cursor.execute('INSERT IGNORE INTO roles (code,name) VALUES (%s,%s)', (role,role))
                for permission in permissions:
                    cursor.execute('INSERT IGNORE INTO permissions (code) VALUES (%s)', (permission,))
                    cursor.execute('INSERT IGNORE INTO role_permissions (role_code,permission_code) VALUES (%s,%s)', (role,permission))
            for extra in sorted(migration.parent.glob('*.sql')):
                if extra.name == '001_mysql.sql':continue
                for statement in extra.read_text().split(';'):
                    if statement.strip():cursor.execute(statement)
        with tempfile.TemporaryDirectory(prefix='galaxy-storage-integration-') as temporary:
            directory = Path(temporary)
            config_path = directory / 'database.json'
            config_path.write_text(json.dumps({'host': options.get('host', '127.0.0.1'),
                'port': int(options.get('port',3306)), 'user': options.get('user','root'),
                'password': options.get('password',''), 'database': schema}))
            config_path.chmod(0o600)
            os.environ['GALAXY_DATABASE_CONFIG'] = str(config_path)
            os.environ.pop('GALAXY_ACCOUNT_STORAGE', None)
            auth = AuthService(JsonRepository(directory))
            password = secrets.token_urlsafe(24)
            auth.import_accounts([('chiaki-fixture', password), ('yuzhuo-fixture', password)])
            first = auth.login('chiaki-fixture',password,False,'fixture')['user']
            second = auth.login('yuzhuo-fixture',password,False,'fixture')['user']
            chat = {'id':str(uuid.uuid4()), 'user_id':first['id'], 'workspace_id':first['workspace_id'],
                'title':'fixture', 'messages':[{'role':'user','content':'fixture original'}]}
            with MySQLRepository(directory,'chats',('conversations',)).transaction() as state:
                state['conversations'][chat['id']] = chat
            with MySQLRepository(directory,'youzan',('connections',)).transaction() as state:
                state['connections'][owner_key(first)] = {'id':'fixture','profile_revision':'fixture','account':'fixture','password':'fixture-cipher'}
            original_chat = copy.deepcopy(chat)
            assert migrate(directory,directory/'hosted',apply=True)['records']['chats'] == 1
            os.environ['GALAXY_ACCOUNT_STORAGE'] = 'files-v1'
            assert isinstance(JsonRepository(directory),MySQLRepository)
            repository = JsonRepository(directory,'chats',('conversations',))
            assert isinstance(repository,AccountRepository)
            with scoped(repository,first) as state:
                state['conversations'][chat['id']]['messages'].append({'role':'assistant','content':'fixture new file-only reply'})
            with MySQLRepository(directory,'chats',('conversations',)).transaction(write=False) as state:
                assert state['conversations'][chat['id']] == original_chat
            with scoped(repository,second,write=False) as state:
                assert not state['conversations']
            channel = JsonRepository(directory,'youzan',('connections',))
            with channel.transaction() as state:
                state['connections'][owner_key(first)]['password'] = 'fixture-new-cipher'
            with MySQLRepository(directory,'youzan',('connections',)).transaction(write=False) as state:
                assert state['connections'][owner_key(first)]['password'] == 'fixture-cipher'
            feishu = JsonRepository(directory,'feishu',('apps','connections'))
            with feishu.transaction() as state:
                state['apps'][first['workspace_id']] = {'secret':'fixture-company-app'}
                state['connections'][owner_key(first)] = {'secret':'fixture-personal-token'}
            with MySQLRepository(directory,'feishu',('apps','connections')).transaction(write=False) as state:
                assert state['apps'][first['workspace_id']]['secret'] == 'fixture-company-app'
                assert not state['connections']
            assert (Accounts(directory).path(first,'channels/feishu')/'connection.json').is_file()
            print(json.dumps({'mysql_authentication':True,'verified_migration':True,'chat_sql_unchanged':True,
                'personal_channels_sql_unchanged':True,'company_apps_still_mysql':True,'cross_user_isolation':True}))
    finally:
        if config_path is not None:
            pool = _pools.pop(str(config_path),None)
            if pool:
                while not pool.idle.empty():pool.idle.get_nowait().close()
        with root.cursor() as cursor:cursor.execute('DROP DATABASE IF EXISTS `' + schema + '`')
        root.close()


if __name__ == '__main__':main()
