"""Run only against the isolated galaxy_ai_test database on the server."""
import concurrent.futures
import os
import secrets
import tempfile
import uuid
from pathlib import Path
from fastapi import FastAPI,Depends
from fastapi.testclient import TestClient
from xingxu.auth import AuthService,AuthFailure,ROLES
from xingxu.database import database
from xingxu.mysql_store import MySQLRepository,TABLES
from xingxu.employees import create_router,EmployeeService,EmployeeInput


def main():
    assert database().options['database']=='galaxy_ai_test','Test database required'
    with database().connection() as connection,connection.cursor() as cursor:
        for code,permissions in ROLES.items():
            cursor.execute('INSERT IGNORE INTO roles (code,name) VALUES (%s,%s)',(code,code))
            for permission in permissions:
                cursor.execute('INSERT IGNORE INTO permissions (code) VALUES (%s)',(permission,))
                cursor.execute('INSERT IGNORE INTO role_permissions (role_code,permission_code) VALUES (%s,%s)',(code,permission))
        for name in ['auth',*TABLES]:cursor.execute('INSERT IGNORE INTO repository_locks (name) VALUES (%s)',(name,))
    repo=MySQLRepository(Path('/tmp'),'ai',('providers','settings'))
    try:
        with repo.transaction() as state:
            state['settings']['rollback-check']='must not exist'
            raise RuntimeError('intentional rollback')
    except RuntimeError:pass
    with repo.transaction(write=False) as state:assert 'rollback-check' not in state['settings']
    with repo.transaction() as state:state['settings']['counter']=0
    def increment(_):
        for _ in range(5):
            with repo.transaction() as state:state['settings']['counter']+=1
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:list(workers.map(increment,range(4)))
    with repo.transaction(write=False) as state:assert state['settings']['counter']==20
    auth=AuthService(MySQLRepository(Path('/tmp')))
    password=secrets.token_urlsafe(20)
    auth.seed('mysql-test-admin',password)
    admin_session=auth.login('mysql-test-admin',password,False,'127.0.0.1')
    admin=admin_session['user']
    assert 'users:manage' in admin['permissions']
    with auth.repository.transaction(write=False) as state:
        assert admin_session['access_token'] not in state['sessions']
        assert next(iter(state['users'].values()))['password_hash'].startswith('$argon2id$')
    app=FastAPI()
    from fastapi.security import HTTPBearer
    bearer=HTTPBearer()
    def identity(credential=Depends(bearer)):return auth.identity(credential.credentials)
    app.include_router(create_router(identity))
    @app.exception_handler(AuthFailure)
    async def error(_request,error):
        from fastapi.responses import JSONResponse
        return JSONResponse({'code':error.code},status_code=error.status)
    client=TestClient(app)
    headers={'Authorization':'Bearer '+admin_session['access_token']}
    from xingxu.departments import DepartmentService, DepartmentInput
    DepartmentService().save(admin, DepartmentInput(name='测试部门'))
    employee_password=secrets.token_urlsafe(20)
    body={'name':'数据库测试人员','account':'mysql-test-employee','department':'测试部门','job_title':'测试岗位','phone':'','employment_status':'active','password':employee_password}
    created=client.post('/api/v1/erp/employees',json=body,headers=headers)
    assert created.status_code==200,created.status_code
    employee=created.json();assert 'password' not in employee and 'password_hash' not in employee
    employee_session=auth.login(body['account'],employee_password,False,'127.0.0.1')
    denied=client.get('/api/v1/erp/employees',headers={'Authorization':'Bearer '+employee_session['access_token']})
    assert denied.status_code==403
    rows=client.get('/api/v1/erp/employees?q=数据库测试',headers=headers).json()
    assert rows['total']==1 and rows['employees'][0]['name']==body['name']
    duplicate=client.post('/api/v1/erp/employees',json=body,headers=headers)
    assert duplicate.status_code==409
    foreign_actor={**admin,'workspace_id':'another-workspace'}
    assert EmployeeService().list(foreign_actor)['total']==0
    try:EmployeeService().save(foreign_actor,EmployeeInput(**body),employee['id'])
    except AuthFailure as error:assert error.status==404
    else:raise AssertionError('Cross-workspace edit accepted')
    body['password']=None;body['job_title']='更新岗位'
    assert client.put('/api/v1/erp/employees/'+employee['id'],json=body,headers=headers).status_code==200
    assert auth.login(body['account'],employee_password,False,'127.0.0.1')['user']['id']==employee['user_id']
    assert 'ai:chat' in auth.identity(employee_session['access_token'])['user']['permissions']
    with database().connection() as connection,connection.cursor() as cursor:
        cursor.execute("DELETE FROM role_permissions WHERE role_code='employee' AND permission_code='ai:chat'")
    assert 'ai:chat' not in auth.identity(employee_session['access_token'])['user']['permissions']
    body['employment_status']='resigned'
    assert client.put('/api/v1/erp/employees/'+employee['id'],json=body,headers=headers).status_code==200
    try:auth.identity(employee_session['access_token'])
    except AuthFailure as error:assert error.status==401
    else:raise AssertionError('Resigned session still accepted')
    with database().connection() as connection,connection.cursor() as cursor:
        cursor.execute("DELETE FROM role_permissions WHERE role_code='employee' AND permission_code='ai:chat'")
    assert 'ai:chat' not in auth.repository.permissions(employee_session['user'])
    print('MySQL tests passed: rollback, concurrent writes, account hashes, employee CRUD, password retention, permission denial, tenant isolation, session revocation.')


if __name__=='__main__':main()
