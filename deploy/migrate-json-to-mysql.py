#!/usr/bin/env python3
"""Offline, verified import. Stop galaxy-ai.service and back up private/ first."""
import argparse
import json
import os
import sys
import uuid
from pathlib import Path


def migrate(source, config, backend):
    sys.path.insert(0,str(backend))
    os.environ['GALAXY_DATABASE_CONFIG']=str(config)
    from xingxu.database import database
    from xingxu.mysql_store import MySQLRepository,TABLES
    from xingxu.auth import ROLES
    sources={}
    for name,fields in {'auth':{'users':'users','sessions':'sessions','attempts':'attempts'},**TABLES}.items():
        value=json.loads((source/(name+'.json')).read_text())
        if value.get('schema_version')!=1 or set(value)!={'schema_version',*fields} or any(not isinstance(value.get(key),dict) for key in fields):
            raise RuntimeError(f'Invalid source schema: {name}')
        sources[name]=value
    with database().connection() as connection,connection.cursor() as cursor:
        cursor.execute('SELECT COUNT(*) total FROM users')
        if cursor.fetchone()['total']:
            raise RuntimeError('Target database already contains users; import refused')
        for code,permissions in ROLES.items():
            cursor.execute('INSERT IGNORE INTO roles (code,name) VALUES (%s,%s)',(code,'管理员' if code=='admin' else '员工'))
            for permission in permissions:
                cursor.execute('INSERT IGNORE INTO permissions (code) VALUES (%s)',(permission,))
                cursor.execute('INSERT IGNORE INTO role_permissions (role_code,permission_code) VALUES (%s,%s)',(code,permission))
        for name in sources:
            cursor.execute('INSERT IGNORE INTO repository_locks (name) VALUES (%s)',(name,))
    results={}
    for name,value in sources.items():
        fields=tuple(key for key in value if key!='schema_version')
        repository=MySQLRepository(source,name,fields)
        with repository.transaction() as state:
            for key in fields:state[key]=value[key]
        with repository.transaction(write=False) as saved:
            if saved!=value:raise RuntimeError(f'Imported data mismatch: {name}')
        results[name]={key:len(value[key]) for key in fields}
    with database().connection() as connection,connection.cursor() as cursor:
        for user in sources['auth']['users'].values():
            cursor.execute('''INSERT IGNORE INTO employees (id,workspace_id,user_id,employment_status) VALUES (%s,%s,%s,%s)''',
                (str(uuid.uuid5(uuid.NAMESPACE_URL,'galaxy-employee:'+user['id'])),user['workspace_id'],user['id'],'active' if user['active'] else 'resigned'))
    return {'verified':True,'repositories':results,'employees':len(sources['auth']['users'])}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--backend',type=Path,default=Path('/opt/galaxy-ai/backend'))
    args=parser.parse_args()
    print(json.dumps(migrate(args.source,args.config,args.backend)))
