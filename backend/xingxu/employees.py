"""Workspace-scoped personnel and login-account administration."""
import base64
import hashlib
import json
import time
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .auth import AuthFailure, HASHER, PASSWORD_WORKERS
from .database import database
from .mysql_store import load_json
from .permission_policy import check_assignment, require_live


class EmployeeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    account: str = Field(min_length=1, max_length=160)
    avatar: str | None = Field(default=None, max_length=64000)
    department: str = Field(default='', max_length=100)
    job_title: str = Field(default='', max_length=100)
    phone: str = Field(default='', max_length=32)
    employment_status: Literal['active', 'resigned'] = 'active'
    password: str | None = Field(default=None, max_length=256)

    @field_validator('name', 'account')
    @classmethod
    def required_text(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError('Invalid text')
        return value.strip()

    @field_validator('password')
    @classmethod
    def password_length(cls, value):
        if value and len(value) < 8:
            raise ValueError('Password must contain at least 8 characters')
        return value or None

    @field_validator('avatar')
    @classmethod
    def raster_avatar(cls, value):
        if not value:
            return None
        try:
            prefix, content = value.split(',', 1)
            if prefix not in ('data:image/jpeg;base64', 'data:image/png;base64', 'data:image/webp;base64'):
                raise ValueError()
            data = base64.b64decode(content, validate=True)
            if not (data.startswith(b'\xff\xd8\xff') if prefix.startswith('data:image/jpeg') else
                    data.startswith(b'\x89PNG\r\n\x1a\n') if prefix.startswith('data:image/png') else
                    data.startswith(b'RIFF') and data[8:12] == b'WEBP'):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Invalid raster avatar') from None
        return value


class EmployeeDepartmentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    department_id: uuid.UUID


def linked_accounts(cursor, user_id, workspace_id):
    key = hashlib.sha256(json.dumps([workspace_id, user_id]).encode()).hexdigest()
    result = []
    for platform, table in [('有赞','youzan_connections'), ('飞书','feishu_connections')]:
        cursor.execute(f'SELECT payload FROM {table} WHERE record_id=%s', (key,))
        row = cursor.fetchone()
        if row:
            record = load_json(row['payload'])
            account = record.get('account') if platform == '有赞' else (record.get('identity') or {}).get('name')
            if account:
                result.append({'platform': platform, 'account': str(account)})
    return result


def employee_output(row, cursor):
    return {'id':row['id'], 'user_id':row['user_id'], 'name':row['name'], 'account':row['username'],
            'avatar':row['avatar'], 'department':row['department'], 'job_title':row['job_title'],
            'phone':row['phone'], 'employment_status':row['employment_status'],
            'linked_accounts':linked_accounts(cursor,row['user_id'],row['workspace_id'])}


class EmployeeService:
    def list(self, user, q='', status=None, page=1, page_size=50, department=None, summary=False):
        where='e.workspace_id=%s AND u.workspace_id=e.workspace_id'
        args=[user['workspace_id']]
        if q:
            where+=' AND (e.name LIKE %s OR u.username LIKE %s OR e.phone LIKE %s OR e.department LIKE %s)'
            args += ['%'+q+'%']*4
        if department is not None:
            where+=' AND e.department COLLATE utf8mb4_bin=%s';args.append(department)
        if status:
            where+=' AND e.employment_status=%s';args.append(status)
        with database().connection() as connection, connection.cursor() as cursor:
            cursor.execute(f'SELECT COUNT(*) total FROM employees e JOIN users u ON u.id=e.user_id WHERE {where}', args)
            total=cursor.fetchone()['total']
            cursor.execute(f'''SELECT e.*,u.username FROM employees e JOIN users u ON u.id=e.user_id
                WHERE {where} ORDER BY e.created_at,e.id LIMIT %s OFFSET %s''', [*args,page_size,(page-1)*page_size])
            rows=cursor.fetchall()
            return {'employees':[({'id':row['id'],'name':row['name'],'account':row['username'],'department':row['department']} if summary else employee_output(row,cursor)) for row in rows], 'total':total, 'page':page, 'page_size':page_size}

    def move(self, actor, identifier, department_id):
        with database().connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT name FROM repository_locks WHERE name='auth' FOR UPDATE")
            actor = require_live(cursor, actor, 'users:manage')
            cursor.execute('SELECT name FROM departments WHERE id=%s AND workspace_id=%s FOR UPDATE', (str(department_id),actor['workspace_id']))
            department=cursor.fetchone()
            if not department:raise AuthFailure('department_not_found','部门不存在。',404)
            cursor.execute('SELECT e.id,e.name,e.department,e.user_id,u.username,u.role FROM employees e JOIN users u ON u.id=e.user_id AND u.workspace_id=e.workspace_id WHERE e.id=%s AND e.workspace_id=%s FOR UPDATE', (str(identifier),actor['workspace_id']))
            employee=cursor.fetchone()
            if not employee:raise AuthFailure('employee_not_found','员工不存在。',404)
            previous=employee['department'];changed=previous!=department['name']
            if changed:
                check_assignment(cursor,actor,employee['role'],department['name'],employee['user_id'])
                cursor.execute('UPDATE employees SET department=%s WHERE id=%s AND workspace_id=%s', (department['name'],str(identifier),actor['workspace_id']))
                cursor.execute("INSERT INTO employee_audit(workspace_id,actor_user_id,employee_id,operation,changed_fields) VALUES(%s,%s,%s,'move','[\"department\"]')", (actor['workspace_id'],actor['id'],str(identifier)))
            return {'moved':changed,'employee':{'id':employee['id'],'name':employee['name'],'account':employee['username'],'department':department['name']},'department_id':str(department_id),'previous_department':previous}

    def save(self, actor, body, identifier=None):
        encoded = None
        if body.password:
            with PASSWORD_WORKERS:
                encoded = HASHER.hash(body.password)
        if identifier is None and encoded is None:
            raise AuthFailure('password_required','新增人员时请输入至少 8 位密码。',422)
        creating = identifier is None
        with database().connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT name FROM repository_locks WHERE name='auth' FOR UPDATE")
            actor = require_live(cursor, actor, 'users:manage')
            row=None
            if identifier:
                cursor.execute('''SELECT e.*,u.username,u.role FROM employees e JOIN users u ON u.id=e.user_id
                    WHERE e.id=%s AND e.workspace_id=%s FOR UPDATE''',(str(identifier),actor['workspace_id']))
                row=cursor.fetchone()
                if not row:raise AuthFailure('not_found','人员不存在。',404)
                if row['user_id']==actor['id'] and body.employment_status=='resigned':
                    raise AuthFailure('self_disable_denied','不能将当前登录账号设为离职。',422)
                if row['role']=='admin' and body.employment_status=='resigned':
                    cursor.execute("SELECT COUNT(*) total FROM users WHERE workspace_id=%s AND role='admin' AND active=1",(actor['workspace_id'],))
                    if cursor.fetchone()['total']<=1:raise AuthFailure('last_admin','必须保留至少一个在职管理员。',422)
            if body.department:
                cursor.execute('SELECT id FROM departments WHERE workspace_id=%s AND name=%s',(actor['workspace_id'],body.department))
                if not cursor.fetchone():raise AuthFailure('department_not_found','请选择已创建的部门。',422)
            user_id=row['user_id'] if row else str(uuid.uuid4())
            cursor.execute('SELECT id FROM users WHERE username=%s AND id<>%s',(body.account,user_id))
            if cursor.fetchone():raise AuthFailure('account_exists','登录账户已存在。',409)
            if not row or body.department != row['department'] or row['user_id'] != actor['id'] and (encoded or body.account != row['username'] or body.employment_status != row['employment_status']):
                check_assignment(cursor,actor,row['role'] if row else 'employee',body.department,row['user_id'] if row else None)
            if row:
                cursor.execute('''UPDATE users SET username=%s,active=%s,auth_version=auth_version+%s WHERE id=%s''',
                    (body.account,body.employment_status=='active',int(bool(encoded) or body.employment_status!=row['employment_status']),user_id))
                if encoded:cursor.execute('UPDATE users SET password_hash=%s WHERE id=%s',(encoded,user_id))
                cursor.execute('''UPDATE employees SET name=%s,avatar=%s,department=%s,job_title=%s,phone=%s,employment_status=%s
                    WHERE id=%s AND workspace_id=%s''',(body.name,body.avatar,body.department,body.job_title,body.phone,body.employment_status,str(identifier),actor['workspace_id']))
            else:
                identifier=str(uuid.uuid4())
                cursor.execute('''INSERT INTO users (id,username,password_hash,role,workspace_id,active,auth_version,created_at)
                    VALUES (%s,%s,%s,'employee',%s,%s,1,%s)''',(user_id,body.account,encoded,actor['workspace_id'],body.employment_status=='active',int(time.time())))
                cursor.execute("INSERT INTO user_roles (user_id,role_code) VALUES (%s,'employee')",(user_id,))
                cursor.execute('''INSERT INTO employees (id,workspace_id,user_id,name,avatar,department,job_title,phone,employment_status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(identifier,actor['workspace_id'],user_id,body.name,body.avatar,body.department,body.job_title,body.phone,body.employment_status))
            changed = [key for key in body.model_fields_set if key != 'password' or encoded]
            cursor.execute('''INSERT INTO employee_audit (workspace_id,actor_user_id,employee_id,operation,changed_fields)
                VALUES (%s,%s,%s,%s,%s)''',(actor['workspace_id'],actor['id'],str(identifier),'create' if creating else 'update',json.dumps(sorted(changed))))
            cursor.execute('SELECT e.*,u.username FROM employees e JOIN users u ON u.id=e.user_id WHERE e.id=%s',(str(identifier),))
            return employee_output(cursor.fetchone(),cursor)


def create_router(identity):
    router=APIRouter(prefix='/api/v1/erp/employees',tags=['employees'])
    service=EmployeeService()
    def manager(session=Depends(identity)):
        user=session['user']
        if 'users:manage' not in user['permissions']:raise AuthFailure('forbidden','当前账号没有人员管理权限。',403)
        return user
    @router.get('')
    def employees(q:str=Query(default='',max_length=80),status:Literal['active','resigned']|None=None,
                  page:int=Query(default=1,ge=1),page_size:int=Query(default=50,ge=1,le=100),department:str|None=Query(default=None,max_length=100),summary:bool=False,user=Depends(manager)):
        return service.list(user,q,status,page,page_size,department,summary)
    @router.post('')
    def create(body:EmployeeInput,user=Depends(manager)):
        return service.save(user,body)
    @router.put('/{identifier}/department')
    def move(identifier:uuid.UUID,body:EmployeeDepartmentInput,user=Depends(manager)):
        return service.move(user,identifier,body.department_id)
    @router.put('/{identifier}')
    def update(identifier:uuid.UUID,body:EmployeeInput,user=Depends(manager)):
        return service.save(user,body,identifier)
    return router
