"""Workspace-scoped, versioned permission editing with explicit inheritance."""
import json
import uuid
from typing import Literal
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from .auth import AuthFailure
from .database import database
from .mysql_store import load_json
from .permission_policy import catalog, policies, resolve, ADMIN_PROTECTED


class PolicyInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=0)
    effects:dict[str,StrictBool]
    management:StrictBool | None = None


class PermissionService:
    def target(self,cursor,actor,kind,identifier):
        if kind=='department':
            cursor.execute('SELECT id,name FROM departments WHERE id=%s AND workspace_id=%s', (identifier,actor['workspace_id']))
            row=cursor.fetchone()
            if row:return {'id':row['id'],'subject_id':row['id'],'name':row['name'],'role':'employee'}
        else:
            cursor.execute('''SELECT e.id,e.user_id,e.name,u.username,u.role FROM employees e JOIN users u
                ON u.id=e.user_id AND u.workspace_id=e.workspace_id WHERE e.id=%s AND e.workspace_id=%s''', (identifier,actor['workspace_id']))
            row=cursor.fetchone()
            if row:return {'id':row['id'],'subject_id':row['user_id'],'name':row['name'] or row['username'],'role':row['role']}
        raise AuthFailure('not_found','未找到当前工作空间中的部门或员工。',404)

    def snapshot(self,cursor,actor,kind,target):
        cursor.execute('SELECT permission_code FROM role_permissions WHERE role_code=%s', ('employee',))
        base=[r['permission_code'] for r in cursor.fetchall()]
        inherited=resolve(base)
        if kind=='user':
            current=policies(cursor,{'id':target['subject_id'],'workspace_id':actor['workspace_id']})
            inherited=resolve(base,current.get('department'))
        cursor.execute('SELECT payload,revision FROM permission_policies WHERE workspace_id=%s AND subject_type=%s AND subject_id=%s',
            (actor['workspace_id'],kind,target['subject_id']))
        row=cursor.fetchone();effects=load_json(row['payload']) if row else {}
        categories=catalog()
        if kind=='department':
            initial=resolve(inherited,personal=effects)
            effects={p['code']:p['code'] in initial for category in categories for p in category['permissions']}
            inherited=[]
        management=kind=='user' and target['role']=='admin'
        effective=resolve(inherited,personal=effects,admin=management)
        if management:
            cursor.execute('SELECT code FROM permissions')
            effective=resolve([r['code'] for r in cursor.fetchall()],admin=True)
        source='账号默认'
        if kind=='user':
            cursor.execute('SELECT department FROM employees WHERE user_id=%s AND workspace_id=%s',(target['subject_id'],actor['workspace_id']))
            member=cursor.fetchone()
            if member and member['department']:source=member['department']
        return {'target':{'id':target['id'],'name':target['name'],'kind':kind},'revision':row['revision'] if row else 0,
            'effects':effects,'inherited':inherited,'effective':effective,'management':management,'inherited_source':source,
            'protected':[p['code'] for category in categories for p in category['permissions']] if management else [],
            'categories':categories}

    def get(self,actor,kind,identifier):
        with database().connection() as connection,connection.cursor() as cursor:
            return self.snapshot(cursor,actor,kind,self.target(cursor,actor,kind,str(identifier)))

    def save(self,actor,kind,identifier,body):
        if kind=='department' and body.management is not None:raise AuthFailure('invalid_permissions','管理员权限只能设置给个人。',422)
        valid={p['code'] for category in catalog() for p in category['permissions']}
        if set(body.effects)-valid:raise AuthFailure('invalid_permissions','包含尚未支持的权限。',422)
        with database().connection() as connection,connection.cursor() as cursor:
            cursor.execute("SELECT name FROM repository_locks WHERE name='auth' FOR UPDATE")
            # Recheck management/grant authority inside the write transaction.
            cursor.execute('SELECT id,workspace_id,role,active FROM users WHERE id=%s AND workspace_id=%s FOR UPDATE',(actor['id'],actor['workspace_id']))
            raw=cursor.fetchone()
            if not raw or not raw['active']:raise AuthFailure('forbidden','当前账号不可用。',403)
            cursor.execute('SELECT DISTINCT rp.permission_code FROM user_roles ur JOIN role_permissions rp ON rp.role_code=ur.role_code WHERE ur.user_id=%s',(actor['id'],))
            from .permission_policy import effective
            authority=effective(cursor,raw,[r['permission_code'] for r in cursor.fetchall()])
            if 'permissions:manage' not in authority:raise AuthFailure('forbidden','没有权限管理权限。',403)
            target=self.target(cursor,actor,kind,str(identifier));before=self.snapshot(cursor,actor,kind,target)
            if body.revision!=before['revision']:raise AuthFailure('policy_changed','权限已被其他人修改，请重新读取后再保存。',409)
            effects={**before['effects'],**body.effects} if kind=='department' else body.effects
            management=before['management'] if body.management is None else body.management
            if before['management'] and not management:
                cursor.execute("SELECT COUNT(*) n FROM users WHERE workspace_id=%s AND active=1 AND role='admin'",(actor['workspace_id'],))
                if cursor.fetchone()['n']<=1:raise AuthFailure('last_admin','必须保留至少一位拥有管理员权限的在职人员。',422)
            after = resolve(before['inherited'], personal=effects, admin=management)
            if (set(after)-set(before['effective']))-set(authority):
                raise AuthFailure('grant_forbidden','不能通过恢复继承授予超出自己范围的权限。',403)
            for code,allow in effects.items():
                if allow and code not in authority and before['effects'].get(code) is not True:
                    raise AuthFailure('grant_forbidden','不能授予超出自己可用范围的权限。',403)
            if kind=='user' and management!=before['management']:
                role='admin' if management else 'employee'
                cursor.execute('UPDATE users SET role=%s WHERE id=%s AND workspace_id=%s',(role,target['subject_id'],actor['workspace_id']))
                cursor.execute('DELETE FROM user_roles WHERE user_id=%s',(target['subject_id'],))
                cursor.execute('INSERT INTO user_roles(user_id,role_code) VALUES(%s,%s)',(target['subject_id'],role))
                target['role']=role
            cursor.execute('''INSERT INTO permission_policies(workspace_id,subject_type,subject_id,payload,revision)
                VALUES(%s,%s,%s,%s,1) ON DUPLICATE KEY UPDATE payload=VALUES(payload),revision=revision+1''',
                (actor['workspace_id'],kind,target['subject_id'],json.dumps(effects)))
            changed=sorted(k for k in set(before['effects'])|set(effects) if before['effects'].get(k)!=effects.get(k))
            if management!=before['management']:changed.append('__management__')
            cursor.execute('INSERT INTO permission_audit(workspace_id,actor_user_id,subject_type,subject_id,changed_permissions) VALUES(%s,%s,%s,%s,%s)',
                (actor['workspace_id'],actor['id'],kind,target['subject_id'],json.dumps(changed)))
            return self.snapshot(cursor,actor,kind,target)


def create_router(identity):
    router=APIRouter(prefix='/api/v1/permissions',tags=['permissions']);service=PermissionService()
    def manager(session=Depends(identity)):
        user=session['user']
        if 'permissions:manage' not in user['permissions']:raise AuthFailure('forbidden','没有权限管理权限。',403)
        return user
    @router.get('/{kind}/{identifier}')
    def get(kind:Literal['department','user'],identifier:uuid.UUID,user=Depends(manager)):
        return service.get(user,kind,identifier)
    @router.put('/{kind}/{identifier}')
    def save(kind:Literal['department','user'],identifier:uuid.UUID,body:PolicyInput,user=Depends(manager)):
        return service.save(user,kind,identifier,body)
    return router
