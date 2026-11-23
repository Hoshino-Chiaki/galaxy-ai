"""Department names and counts are scoped to the current workspace."""
import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .auth import AuthFailure
from .database import database
from .permission_policy import require_live, check_assignment


class DepartmentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)

    @field_validator('name')
    @classmethod
    def valid_name(cls, value):
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError('Invalid department name')
        return value.strip()


class DepartmentService:
    def list(self, user):
        with database().connection() as connection, connection.cursor() as cursor:
            cursor.execute('''SELECT d.id,d.name,COUNT(u.id) employee_count FROM departments d
                LEFT JOIN employees e ON e.workspace_id=d.workspace_id AND e.department COLLATE utf8mb4_bin=d.name
                LEFT JOIN users u ON u.id=e.user_id AND u.workspace_id=e.workspace_id
                WHERE d.workspace_id=%s GROUP BY d.id,d.name ORDER BY d.created_at,d.id''', (user['workspace_id'],))
            rows = list(cursor.fetchall())
            cursor.execute('''SELECT COUNT(*) total FROM employees e JOIN users u ON u.id=e.user_id
                AND u.workspace_id=e.workspace_id WHERE e.workspace_id=%s AND e.department='' ''', (user['workspace_id'],))
            unassigned = cursor.fetchone()['total']
            if unassigned:
                rows.append({'id': None, 'name': '未分配部门', 'employee_count': unassigned})
            return {'departments': rows, 'total': sum(row['employee_count'] for row in rows)}

    def save(self, user, body, identifier=None):
        with database().connection() as connection, connection.cursor() as cursor:
            # The same lock as personnel edits keeps department renames and membership consistent.
            cursor.execute("SELECT name FROM repository_locks WHERE name='auth' FOR UPDATE")
            user = require_live(cursor, user, 'users:manage')
            previous = None
            if identifier:
                cursor.execute('SELECT name FROM departments WHERE id=%s AND workspace_id=%s FOR UPDATE',
                               (str(identifier), user['workspace_id']))
                previous = cursor.fetchone()
                if not previous:
                    raise AuthFailure('not_found', '部门不存在。', 404)
            cursor.execute('SELECT id FROM departments WHERE workspace_id=%s AND name=%s AND id<>%s',
                           (user['workspace_id'], body.name, str(identifier or '')))
            if cursor.fetchone():
                raise AuthFailure('department_exists', '当前工作空间已有同名部门。', 409)
            if previous:
                cursor.execute('UPDATE departments SET name=%s WHERE id=%s AND workspace_id=%s',
                               (body.name, str(identifier), user['workspace_id']))
                cursor.execute('UPDATE employees SET department=%s WHERE workspace_id=%s AND department COLLATE utf8mb4_bin=%s',
                               (body.name, user['workspace_id'], previous['name']))
            else:
                identifier = str(uuid.uuid4())
                cursor.execute('INSERT INTO departments(id,workspace_id,name) VALUES(%s,%s,%s)',
                               (identifier, user['workspace_id'], body.name))
            cursor.execute('''SELECT COUNT(*) total FROM employees e JOIN users u ON u.id=e.user_id
                AND u.workspace_id=e.workspace_id WHERE e.workspace_id=%s AND e.department COLLATE utf8mb4_bin=%s''',
                           (user['workspace_id'], body.name))
            return {'id': str(identifier), 'name': body.name, 'employee_count': cursor.fetchone()['total']}

    def delete(self, user, identifier):
        with database().connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT name FROM repository_locks WHERE name='auth' FOR UPDATE")
            user = require_live(cursor, user, 'users:manage')
            cursor.execute('SELECT name FROM departments WHERE id=%s AND workspace_id=%s FOR UPDATE',
                           (str(identifier), user['workspace_id']))
            row = cursor.fetchone()
            if not row:
                raise AuthFailure('not_found', '部门不存在。', 404)
            cursor.execute("SELECT u.id,u.role FROM employees e JOIN users u ON u.id=e.user_id AND u.workspace_id=e.workspace_id WHERE e.workspace_id=%s AND e.department COLLATE utf8mb4_bin=%s", (user['workspace_id'],row['name']))
            for member in cursor.fetchall():check_assignment(cursor,user,member['role'],'',member['id'])
            cursor.execute("UPDATE employees SET department='' WHERE workspace_id=%s AND department COLLATE utf8mb4_bin=%s",
                           (user['workspace_id'], row['name']))
            moved = cursor.rowcount
            cursor.execute("DELETE FROM permission_policies WHERE subject_type='department' AND subject_id=%s AND workspace_id=%s", (str(identifier),user['workspace_id']))
            cursor.execute('DELETE FROM departments WHERE id=%s AND workspace_id=%s', (str(identifier), user['workspace_id']))
            return {'ok': True, 'reassigned': moved}


def create_router(identity):
    router = APIRouter(prefix='/api/v1/erp/departments', tags=['departments'])
    service = DepartmentService()

    def manager(session=Depends(identity)):
        user = session['user']
        if 'users:manage' not in user['permissions']:
            raise AuthFailure('forbidden', '当前账号没有人员管理权限。', 403)
        return user

    @router.get('')
    def departments(user=Depends(manager)):
        return service.list(user)

    @router.post('')
    def create(body: DepartmentInput, user=Depends(manager)):
        return service.save(user, body)

    @router.put('/{identifier}')
    def update(identifier: uuid.UUID, body: DepartmentInput, user=Depends(manager)):
        return service.save(user, body, identifier)

    @router.delete('/{identifier}')
    def delete(identifier: uuid.UUID, user=Depends(manager)):
        return service.delete(user, identifier)

    return router
