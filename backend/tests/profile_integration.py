"""Self-service profile/password checks; only run in the isolated MySQL test schema."""
import secrets
from pathlib import Path
from fastapi import FastAPI, Depends
from fastapi.security import HTTPBearer
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from xingxu.auth import AuthService, AuthFailure, ROLES
from xingxu.database import database
from xingxu.mysql_store import MySQLRepository, TABLES
from xingxu.employees import EmployeeInput, EmployeeService
from xingxu.profile import create_router


def main():
    assert database().options['database'] == 'galaxy_ai_test'
    with database().connection() as connection, connection.cursor() as cursor:
        for code, permissions in ROLES.items():
            cursor.execute('INSERT IGNORE INTO roles (code,name) VALUES (%s,%s)', (code, code))
            for permission in permissions:
                cursor.execute('INSERT IGNORE INTO permissions (code) VALUES (%s)', (permission,))
                cursor.execute('INSERT IGNORE INTO role_permissions VALUES (%s,%s)', (code, permission))
        for name in ['auth', *TABLES]:
            cursor.execute('INSERT IGNORE INTO repository_locks VALUES (%s)', (name,))
    auth = AuthService(MySQLRepository(Path('/tmp')))
    admin_password, employee_password, new_password = [secrets.token_urlsafe(20) for _ in range(3)]
    auth.seed('profile-test-admin', admin_password)
    admin = auth.login('profile-test-admin', admin_password, False, '127.0.0.1')['user']
    from xingxu.departments import DepartmentService, DepartmentInput
    DepartmentService().save(admin, DepartmentInput(name='原部门'))
    employee = EmployeeService().save(admin, EmployeeInput(name='本人', account='profile-test-employee', department='原部门', job_title='原岗位', password=employee_password))
    current = auth.login(employee['account'], employee_password, True, '127.0.0.1')
    other = auth.login(employee['account'], employee_password, False, '127.0.0.1')
    app = FastAPI()
    bearer = HTTPBearer()
    def token(credential=Depends(bearer)): return credential.credentials
    def identity(value=Depends(token)): return auth.identity(value)
    @app.exception_handler(AuthFailure)
    async def failure(_request, error): return JSONResponse({'code': error.code}, status_code=error.status)
    app.include_router(create_router(identity, token))
    client = TestClient(app)
    headers = {'Authorization': 'Bearer ' + current['access_token']}
    assert client.get('/api/v1/me/profile').status_code == 401 or client.get('/api/v1/me/profile').status_code == 403
    original = client.get('/api/v1/me/profile', headers=headers).json()
    assert original['account'] == employee['account'] and original['department'] == '原部门'
    avatar = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII='
    body = {'name': '新的姓名', 'phone': '13800000000', 'avatar': avatar}
    assert client.put('/api/v1/me/profile', headers=headers, json=body).status_code == 200
    saved = client.get('/api/v1/me/profile', headers=headers).json()
    assert saved == {**original, **body}
    for field, value in [('user_id', admin['id']), ('workspace_id', 'foreign'), ('department', '提权修改'), ('role', 'admin'), ('employment_status', 'resigned')]:
        assert client.put('/api/v1/me/profile', headers=headers, json={**body, field: value}).status_code == 422
    for bad in ['data:image/svg+xml;base64,PHN2Zz4=', 'https://example.invalid/avatar.png', 'data:image/png;base64,INVALID']:
        assert client.put('/api/v1/me/profile', headers=headers, json={**body, 'avatar': bad}).status_code == 422
    assert auth.identity(current['access_token'])['user']['name'] == body['name']
    assert auth.identity(current['access_token'])['user']['role'] == 'employee'
    from xingxu.profile import get_profile, save_profile, ProfileInput
    assert get_profile(admin)['name'] != body['name']
    try: save_profile({**current['user'], 'workspace_id': 'foreign'}, ProfileInput(**body))
    except AuthFailure: pass
    else: raise AssertionError('Cross-workspace profile update accepted')
    wrong = client.put('/api/v1/me/password', headers=headers, json={'current_password': 'wrong-password', 'new_password': new_password})
    assert wrong.status_code == 422 and wrong.json()['code'] == 'incorrect_password'
    assert client.put('/api/v1/me/password', headers=headers, json={'current_password': employee_password, 'new_password': 'short'}).status_code == 422
    changed = client.put('/api/v1/me/password', headers=headers, json={'current_password': employee_password, 'new_password': new_password})
    assert changed.status_code == 200
    assert new_password not in changed.text and employee_password not in changed.text
    assert auth.identity(current['access_token'])['user']['id'] == employee['user_id']
    try: auth.identity(other['access_token'])
    except AuthFailure: pass
    else: raise AssertionError('Other session retained after password change')
    try: auth.login(employee['account'], employee_password, False, '127.0.0.1')
    except AuthFailure: pass
    else: raise AssertionError('Old password still valid')
    assert auth.login(employee['account'], new_password, False, '127.0.0.1')['user']['id'] == employee['user_id']
    with database().connection() as connection, connection.cursor() as cursor:
        cursor.execute('SELECT operation,changed_fields FROM employee_audit WHERE actor_user_id=%s', (employee['user_id'],))
        audit = str(cursor.fetchall())
        assert new_password not in audit and employee_password not in audit
    print('PASS: own profile/avatar persistence; immutable account/role/department; tenant isolation; current password verification; current session retained; other sessions revoked; no plaintext password responses/audit.')


if __name__ == '__main__': main()
