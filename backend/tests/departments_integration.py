"""Department lifecycle, employee filtering/paging and workspace permission tests."""
import concurrent.futures
import secrets
import uuid
from pathlib import Path
from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from fastapi.testclient import TestClient
from xingxu.auth import AuthService, AuthFailure, ROLES, HASHER
from xingxu.database import database
from xingxu.mysql_store import MySQLRepository, TABLES
from xingxu.departments import create_router, DepartmentService, DepartmentInput
from xingxu.employees import create_router as employees_router, EmployeeService, EmployeeInput


def main():
    assert database().options['database'] == 'galaxy_ai_test'
    with database().connection() as connection, connection.cursor() as cursor:
        for code, permissions in ROLES.items():
            cursor.execute('INSERT IGNORE INTO roles(code,name) VALUES(%s,%s)', (code, code))
            for permission in permissions:
                cursor.execute('INSERT IGNORE INTO permissions(code) VALUES(%s)', (permission,))
                cursor.execute('INSERT IGNORE INTO role_permissions VALUES(%s,%s)', (code, permission))
        for name in ['auth', *TABLES]: cursor.execute('INSERT IGNORE INTO repository_locks VALUES(%s)', (name,))
    auth = AuthService(MySQLRepository(Path('/tmp')))
    password = secrets.token_urlsafe(20)
    auth.seed('department-test-admin', password)
    session = auth.login('department-test-admin', password, False, '127.0.0.1')
    admin = session['user']; headers = {'Authorization': 'Bearer ' + session['access_token']}
    app = FastAPI(); bearer = HTTPBearer()
    def identity(credential=Depends(bearer)): return auth.identity(credential.credentials)
    @app.exception_handler(AuthFailure)
    async def failure(_request, error): return JSONResponse({'code': error.code}, status_code=error.status)
    app.include_router(create_router(identity)); app.include_router(employees_router(identity))
    client = TestClient(app)
    def create(name): return client.post('/api/v1/erp/departments', headers=headers, json={'name': name})
    first = create('财务部'); assert first.status_code == 200
    department = first.json(); assert department['employee_count'] == 0
    assert create(' 财务部 ').status_code == 409
    for body in [{'name': '   '}, {'name': 'x\ny'}, {'name': 'x' * 101}, {'name': '研发部', 'workspace_id': 'foreign'}]:
        assert client.post('/api/v1/erp/departments', headers=headers, json=body).status_code == 422
    create('市场部')
    employee = EmployeeService().save(admin, EmployeeInput(name='财务员工', account='department-test-employee', department='财务部', password=password))
    employee_session = auth.login(employee['account'], password, False, '127.0.0.1')
    employee_headers = {'Authorization': 'Bearer ' + employee_session['access_token']}
    assert client.get('/api/v1/erp/departments', headers=employee_headers).status_code == 403
    assert client.post('/api/v1/erp/departments', headers=employee_headers, json={'name': '越权部门'}).status_code == 403
    assert client.put('/api/v1/erp/departments/' + department['id'], headers=employee_headers, json={'name': '越权部门'}).status_code == 403
    assert client.delete('/api/v1/erp/departments/' + department['id'], headers=employee_headers).status_code == 403
    # More than one page verifies that a department does not silently lose members after page 1.
    encoded = HASHER.hash(secrets.token_urlsafe(20))
    with database().connection() as connection, connection.cursor() as cursor:
        for index in range(51):
            uid, eid = str(uuid.uuid4()), str(uuid.uuid4())
            cursor.execute("INSERT INTO users VALUES(%s,%s,%s,'employee',%s,1,1,1)", (uid, 'department-member-' + str(index), encoded, admin['workspace_id']))
            cursor.execute("INSERT INTO employees(id,user_id,workspace_id,name,department) VALUES(%s,%s,%s,%s,'财务部')", (eid, uid, admin['workspace_id'], '分页员工' + str(index)))
    page1 = client.get('/api/v1/erp/employees?department=财务部', headers=headers).json()
    page2 = client.get('/api/v1/erp/employees?department=财务部&page=2', headers=headers).json()
    assert page1['total'] == 52 and len(page1['employees']) == 50 and len(page2['employees']) == 2
    assert client.get('/api/v1/erp/employees?department=市场部', headers=headers).json()['total'] == 0
    assert client.get('/api/v1/erp/employees?department=财务部&q=财务员工', headers=headers).json()['total'] == 1
    renamed = client.put('/api/v1/erp/departments/' + department['id'], headers=headers, json={'name': '财务中心'})
    assert renamed.status_code == 200 and renamed.json()['employee_count'] == 52
    assert client.get('/api/v1/erp/employees?department=财务部', headers=headers).json()['total'] == 0
    assert client.get('/api/v1/erp/employees?department=财务中心', headers=headers).json()['total'] == 52
    conflict = client.put('/api/v1/erp/departments/' + department['id'], headers=headers, json={'name': '市场部'})
    assert conflict.status_code == 409
    assert client.get('/api/v1/erp/employees?department=财务中心', headers=headers).json()['total'] == 52
    other = {**admin, 'workspace_id': 'other-workspace'}
    assert DepartmentService().list(other)['departments'] == []
    foreign = DepartmentService().save(other, DepartmentInput(name='财务中心'))
    try: DepartmentService().save(other, DepartmentInput(name='跨空间修改'), department['id'])
    except AuthFailure as error: assert error.status == 404
    else: raise AssertionError('Cross-workspace department edit accepted')
    assert foreign['id'] != department['id']
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
        codes = list(workers.map(lambda _: create('并发部门').status_code, range(2)))
    assert sorted(codes) == [200, 409]
    data = client.get('/api/v1/erp/departments', headers=headers).json()
    assert next(row for row in data['departments'] if row['id'] == department['id'])['employee_count'] == 52
    assert all('password' not in row and 'password_hash' not in row for row in page1['employees'])
    try: DepartmentService().delete(other, department['id'])
    except AuthFailure as error: assert error.status == 404
    else: raise AssertionError('Cross-workspace department deletion accepted')
    deleted = client.delete('/api/v1/erp/departments/' + department['id'], headers=headers)
    assert deleted.status_code == 200 and deleted.json()['reassigned'] == 52
    assert client.delete('/api/v1/erp/departments/' + department['id'], headers=headers).status_code == 404
    assert client.get('/api/v1/erp/employees?department=财务中心', headers=headers).json()['total'] == 0
    assert client.get('/api/v1/erp/employees?department=', headers=headers).json()['total'] >= 52
    assert auth.identity(employee_session['access_token'])['user']['id'] == employee['user_id']
    print('PASS: deletion with employee/account retention, empty department creation, duplicate/concurrent names, rename with membership retention, rollback, permissions, workspace isolation, exact department filter/search, 52-member pagination, no password disclosure.')


if __name__ == '__main__': main()
