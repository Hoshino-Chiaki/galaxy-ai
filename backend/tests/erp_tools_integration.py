"""Exercises real tool dispatch and HTTP authorization against an isolated MySQL schema."""
import asyncio
import copy
import json
import secrets
from pathlib import Path
import httpx
from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from xingxu.auth import AuthService, AuthFailure, ROLES
from xingxu.database import database
from xingxu.mysql_store import MySQLRepository, TABLES
from xingxu.departments import create_router, DepartmentService, DepartmentInput
from xingxu.employees import create_router as employees_router, EmployeeService, EmployeeInput
from xingxu.tools import GalaxyDispatcher, ExecutionContext, help_arguments, required_permissions
from galaxy_cli.registry import ToolError
from galaxy_cli.tools.erp import department_create, employee_move
from xingxu.tool_context import DispatchDenied

async def main():
    assert database().options['database']=='galaxy_ai_test'
    with database().connection() as connection,connection.cursor() as cursor:
        for role,permissions in ROLES.items():
            cursor.execute('INSERT IGNORE INTO roles(code,name) VALUES(%s,%s)',(role,role))
            for permission in permissions:
                cursor.execute('INSERT IGNORE INTO permissions(code) VALUES(%s)',(permission,))
                cursor.execute('INSERT IGNORE INTO role_permissions VALUES(%s,%s)',(role,permission))
        for name in ['auth',*TABLES]:cursor.execute('INSERT IGNORE INTO repository_locks VALUES(%s)',(name,))
    auth=AuthService(MySQLRepository(Path('/tmp')))
    password=secrets.token_urlsafe(20);auth.seed('erp-tool-admin',password)
    session=auth.login('erp-tool-admin',password,False,'127.0.0.1');admin=session['user']
    app=FastAPI();bearer=HTTPBearer()
    def identity(credential=Depends(bearer)):return auth.identity(credential.credentials)
    @app.exception_handler(AuthFailure)
    async def failure(_request,error):return JSONResponse({'error':{'code':error.code}},status_code=error.status)
    app.include_router(create_router(identity));app.include_router(employees_router(identity))
    dispatcher=GalaxyDispatcher('/tmp/erp-tool-tests',authorize=auth.identity)
    dispatcher.erp_transport=httpx.ASGITransport(app=app)
    def context(s):
        user=s['user'];return ExecutionContext(user['id'],user['workspace_id'],tuple(user['permissions']),'erp-test',s['access_token'])
    ctx=context(session)
    async def invoke(name,body,ctx=ctx):
        call={'id':secrets.token_hex(8),'type':'function','function':{'name':'galaxy_cli','arguments':json.dumps({'args':['erp',name,'--input',json.dumps(body)]})}}
        return await dispatcher.execute(ctx,call)
    created=await invoke('department-create',{'name':'工具新部门'});assert created['status']=='success',created
    department=created['result'];assert department['created'] and department['employee_count']==0
    duplicate=await invoke('department-create',{'name':' 工具新部门 '});assert duplicate['result']['error']['code']=='department_exists',duplicate
    original_dept=DepartmentService().save(admin,DepartmentInput(name='原部门'))
    employee=EmployeeService().save(admin,EmployeeInput(name='同名员工',account='erp-tool-employee',department='原部门',job_title='财务',phone='123456',password=password))
    foreign_actor={**admin,'workspace_id':'foreign'}
    foreign_dept=DepartmentService().save(foreign_actor,DepartmentInput(name='其他空间'))
    foreign_employee=EmployeeService().save(foreign_actor,EmployeeInput(name='其他员工',account='erp-tool-foreign',department='其他空间',password=password))
    options=await invoke('employee-move',{'action':'options','q':'erp-tool-employee','page_size':1})
    assert options['status']=='success',options
    assert options['result']['total']==1 and options['result']['employees']==[{key:employee[key] for key in ('id','name','account','department')}]
    assert foreign_dept['id'] not in [d['id'] for d in options['result']['departments']]
    def snapshot():
        with database().connection() as con,con.cursor() as cur:
            cur.execute('SELECT e.*,u.username,u.role,u.password_hash,u.auth_version FROM employees e JOIN users u ON u.id=e.user_id WHERE e.id=%s',(employee['id'],));return cur.fetchone()
    before=snapshot()
    moved=await invoke('employee-move',{'employee_id':employee['id'],'department_id':department['id']})
    assert moved['status']=='success' and moved['result']['moved'],moved
    assert moved['result']['previous_department']=='原部门'
    after=snapshot();assert after['department']=='工具新部门'
    for key in before:
        if key not in ('department','updated_at'):assert before[key]==after[key],key
    same=await invoke('employee-move',{'employee_id':employee['id'],'department_id':department['id']})
    assert same['status']=='success' and same['result']['moved'] is False,same
    for body in [{'employee_id':employee['id'],'department_id':foreign_dept['id']}, {'employee_id':foreign_employee['id'],'department_id':department['id']}]:
        rejected=await invoke('employee-move',body);assert rejected['status']!='success',rejected
    employee_session=auth.login(employee['account'],password,False,'127.0.0.1');employee_context=context(employee_session)
    for name,body in [('department-create',{'name':'越权'}),('employee-move',{'action':'options'}),('employee-move',{'employee_id':employee['id'],'department_id':original_dept['id']})]:
        denied=await invoke(name,body,employee_context);assert denied['result']['error']['code']=='permission_denied',denied
    assert snapshot()['department']=='工具新部门'
    # Real employee-create flow is still supported after extending the ERP dispatcher.
    old=await invoke('employee-create',{'action':'options'});assert old['status']=='success' and old['result']['password_min_length']==8
    body={'name':'新增员工','account':'erp-tool-created','password':password,'department':'原部门'}
    old=await invoke('employee-create',body);assert old['status']=='success',old
    assert password not in json.dumps(old)
    assert required_permissions('youzan.order-search',help_arguments(json.dumps({'args':['youzan','order-search','query','--input','{}']})))=={'youzan:order:read'}
    for body in [{'name':'','role':'admin'},{'name':'新部门','workspace_id':'foreign'}]:
        try:department_create.normalize(body)
        except ToolError:pass
        else:raise AssertionError('Invalid department accepted')
    for body in [{'employee_id':employee['id'],'department_id':department['id'],'role':'admin'}, {'action':'options','employee_id':employee['id']}]:
        try:employee_move.normalize(body)
        except ToolError:pass
        else:raise AssertionError('Invalid employee move accepted')
    print('PASS ERP: real dispatcher/API/MySQL create, duplicate, options, minimal fields, employee move/idempotence, unchanged credentials/role/profile, ordinary employee denied, cross-workspace denied, existing employee-create preserved, order permission.')

if __name__=='__main__':asyncio.run(main())
