"""Current-user profile operations. Neither route accepts a target user identifier."""
import json
import time
import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from argon2.exceptions import VerificationError, InvalidHashError
from .auth import AuthFailure, AuthPolicy, HASHER, PASSWORD_WORKERS, digest
from .database import database
from .employees import EmployeeInput


class ProfileInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=80)
    phone:str=Field(default='',max_length=32)
    avatar:str|None=Field(default=None,max_length=64000)
    @field_validator('name')
    @classmethod
    def name_format(cls,value):return EmployeeInput.required_text(value)
    @field_validator('avatar')
    @classmethod
    def avatar_format(cls,value):return EmployeeInput.raster_avatar(value)


class PasswordInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    current_password:str=Field(min_length=1,max_length=256)
    new_password:str=Field(min_length=8,max_length=256)


def get_profile(user):
    with database().connection() as connection,connection.cursor() as cursor:
        cursor.execute('''SELECT u.username account,COALESCE(e.name,'') name,e.avatar,
            COALESCE(e.department,'') department,COALESCE(e.job_title,'') job_title,COALESCE(e.phone,'') phone,
            COALESCE(e.employment_status,'active') employment_status
            FROM users u LEFT JOIN employees e ON e.user_id=u.id AND e.workspace_id=u.workspace_id
            WHERE u.id=%s AND u.workspace_id=%s AND u.active=1''',(user['id'],user['workspace_id']))
        value=cursor.fetchone()
        if not value:raise AuthFailure('session_expired','登录已失效，请重新登录。')
        return value


def save_profile(user,body):
    with database().connection() as connection,connection.cursor() as cursor:
        cursor.execute("SELECT name FROM repository_locks WHERE name='auth' FOR UPDATE")
        cursor.execute('SELECT id FROM users WHERE id=%s AND workspace_id=%s AND active=1 FOR UPDATE',(user['id'],user['workspace_id']))
        if not cursor.fetchone():raise AuthFailure('session_expired','登录已失效，请重新登录。')
        cursor.execute('SELECT id FROM employees WHERE user_id=%s AND workspace_id=%s FOR UPDATE',(user['id'],user['workspace_id']))
        row=cursor.fetchone()
        employee_id=row['id'] if row else str(uuid.uuid5(uuid.NAMESPACE_URL,'galaxy-employee:'+user['id']))
        if row:
            cursor.execute('UPDATE employees SET name=%s,phone=%s,avatar=%s WHERE id=%s AND workspace_id=%s',
                (body.name,body.phone,body.avatar,employee_id,user['workspace_id']))
        else:
            cursor.execute('INSERT INTO employees (id,workspace_id,user_id,name,phone,avatar) VALUES (%s,%s,%s,%s,%s,%s)',
                (employee_id,user['workspace_id'],user['id'],body.name,body.phone,body.avatar))
        cursor.execute('''INSERT INTO employee_audit (workspace_id,actor_user_id,employee_id,operation,changed_fields)
            VALUES (%s,%s,%s,'profile_update',%s)''',(user['workspace_id'],user['id'],employee_id,json.dumps(['avatar','name','phone'])))
    return get_profile(user)


def change_password(user,bearer,body,policy=AuthPolicy()):
    now=int(time.time());bucket='password:'+digest(user['id'])
    with database().connection() as connection,connection.cursor() as cursor:
        cursor.execute("SELECT name FROM repository_locks WHERE name='auth' FOR UPDATE")
        cursor.execute('SELECT COUNT(*) total FROM login_attempts WHERE bucket=%s AND attempt_at>%s',(bucket,now-policy.attempt_window))
        if cursor.fetchone()['total']>=policy.account_attempts:
            raise AuthFailure('rate_limited','尝试次数过多，请稍后再试。',429,policy.attempt_window)
        cursor.execute('INSERT INTO login_attempts (bucket,attempt_at) VALUES (%s,%s)',(bucket,now))
        cursor.execute('SELECT password_hash,auth_version FROM users WHERE id=%s AND workspace_id=%s AND active=1',(user['id'],user['workspace_id']))
        snapshot=cursor.fetchone()
        if not snapshot:raise AuthFailure('session_expired','登录已失效，请重新登录。')
    with PASSWORD_WORKERS:
        try:verified=HASHER.verify(snapshot['password_hash'],body.current_password)
        except (VerificationError,InvalidHashError):verified=False
        if not verified:raise AuthFailure('incorrect_password','当前密码不正确。',422)
        encoded=HASHER.hash(body.new_password)
    token_hash=digest(bearer)
    with database().connection() as connection,connection.cursor() as cursor:
        cursor.execute("SELECT name FROM repository_locks WHERE name='auth' FOR UPDATE")
        cursor.execute('SELECT password_hash,auth_version FROM users WHERE id=%s AND workspace_id=%s AND active=1 FOR UPDATE',(user['id'],user['workspace_id']))
        current=cursor.fetchone()
        cursor.execute('SELECT expires_at,auth_version FROM sessions WHERE token_hash=%s AND user_id=%s FOR UPDATE',(token_hash,user['id']))
        session=cursor.fetchone()
        if not current or not session or session['expires_at']<=time.time() or session['auth_version']!=current['auth_version']:
            raise AuthFailure('session_expired','登录已失效，请重新登录。')
        if current!=snapshot:raise AuthFailure('account_changed','账户信息已变化，请重新操作。',409)
        version=current['auth_version']+1
        cursor.execute('UPDATE users SET password_hash=%s,auth_version=%s WHERE id=%s',(encoded,version,user['id']))
        cursor.execute('DELETE FROM sessions WHERE user_id=%s AND token_hash<>%s',(user['id'],token_hash))
        cursor.execute('UPDATE sessions SET auth_version=%s WHERE token_hash=%s',(version,token_hash))
        cursor.execute('DELETE FROM login_attempts WHERE bucket=%s',(bucket,))
        cursor.execute('SELECT id FROM employees WHERE user_id=%s AND workspace_id=%s',(user['id'],user['workspace_id']))
        row=cursor.fetchone()
        if row:
            cursor.execute('''INSERT INTO employee_audit (workspace_id,actor_user_id,employee_id,operation,changed_fields)
                VALUES (%s,%s,%s,'password_update','["password"]')''',(user['workspace_id'],user['id'],row['id']))
    return {'ok':True,'message':'密码已修改，其他登录会话已失效。'}


def create_router(identity,token):
    router=APIRouter(prefix='/api/v1/me',tags=['personal-center'])
    @router.get('/profile')
    def profile(session=Depends(identity)):return get_profile(session['user'])
    @router.put('/profile')
    def update(body:ProfileInput,session=Depends(identity)):return save_profile(session['user'],body)
    @router.put('/password')
    def password(body:PasswordInput,session=Depends(identity),bearer=Depends(token)):
        return change_password(session['user'],bearer,body)
    return router
