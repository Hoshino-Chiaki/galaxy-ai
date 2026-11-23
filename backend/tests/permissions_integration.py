"""Isolated MySQL permission boundaries; no production users or platform calls."""
import secrets,uuid
from pathlib import Path
from xingxu.database import database
from xingxu.auth import AuthService,AuthFailure,ROLES
from xingxu.mysql_store import MySQLRepository,TABLES
from xingxu.departments import DepartmentService,DepartmentInput
from xingxu.employees import EmployeeService,EmployeeInput
from xingxu.permissions import PermissionService,PolicyInput


def main():
    assert database().options['database']=='galaxy_ai_test'
    with database().connection() as c,c.cursor() as q:
        for role,codes in ROLES.items():
            q.execute('INSERT IGNORE INTO roles VALUES(%s,%s)',(role,role))
            for code in codes:q.execute('INSERT IGNORE INTO permissions VALUES(%s)',(code,));q.execute('INSERT IGNORE INTO role_permissions VALUES(%s,%s)',(role,code))
        for name in ['auth',*TABLES]:q.execute('INSERT IGNORE INTO repository_locks VALUES(%s)',(name,))
    auth=AuthService(MySQLRepository(Path('/tmp')));pw=secrets.token_urlsafe(20)
    auth.seed('permission-admin',pw);admin=auth.login('permission-admin',pw,False,'fixture')['user']
    ds=DepartmentService();es=EmployeeService();ps=PermissionService()
    d=ds.save(admin,DepartmentInput(name='测试部门'))
    employee=es.save(admin,EmployeeInput(name='权限测试员工',account='permission-employee',department=d['name'],password=pw))
    token=auth.login('permission-employee',pw,False,'fixture')['access_token']
    before=ps.get(admin,'department',d['id']);assert before['revision']==0
    assert before['inherited']==[] and all(type(v) is bool for v in before['effects'].values())
    assert len(before['effects'])==sum(len(c['permissions']) for c in before['categories'])
    dept=ps.save(admin,'department',d['id'],PolicyInput(revision=0,effects={'youzan:order:read':False,'feishu:read':False,'feishu:create':False,'feishu:delete':False,'feishu:update':False}))
    current=auth.identity(token)['user']['permissions']
    assert 'youzan:order:read' not in current and 'feishu:configure' not in current
    one=ps.get(admin,'user',employee['id']);assert 'youzan:order:read' not in one['effective']
    result=ps.save(admin,'user',employee['id'],PolicyInput(revision=0,effects={'youzan:order:read':True}))
    current=auth.identity(token)['user']['permissions'];assert 'youzan:order:read' in current and 'youzan:configure' in current
    ps.save(admin,'user',employee['id'],PolicyInput(revision=result['revision'],effects={}))
    assert 'youzan:order:read' not in auth.identity(token)['user']['permissions']
    try:ps.save(admin,'department',d['id'],PolicyInput(revision=0,effects={}))
    except AuthFailure as e:assert e.code=='policy_changed'
    else:raise AssertionError('Lost update allowed')
    try:ps.get({**admin,'workspace_id':'other-workspace'},'department',d['id'])
    except AuthFailure as e:assert e.status==404
    else:raise AssertionError('Cross workspace allowed')
    # Admin toggle changes the actual account role, observes live sessions and bypasses department denials.
    ordinary=ps.get(admin,'user',employee['id'])
    promoted=ps.save(admin,'user',employee['id'],PolicyInput(revision=ordinary['revision'],effects=ordinary['effects'],management=True))
    assert promoted['management'] and 'youzan:order:read' in promoted['effective'] and 'ai:configure' in promoted['effective']
    assert auth.identity(token)['user']['role']=='admin'
    demoted=ps.save(admin,'user',employee['id'],PolicyInput(revision=promoted['revision'],effects={},management=False))
    assert not demoted['management'] and auth.identity(token)['user']['role']=='employee'
    assert 'youzan:order:read' not in auth.identity(token)['user']['permissions']
    with database().connection() as c,c.cursor() as q:
        q.execute('SELECT id FROM employees WHERE user_id=%s',(admin['id'],));admin_employee=q.fetchone()
    # The seed identity has no personnel profile; create just that profile inside the isolated schema.
    if not admin_employee:
        with database().connection() as c,c.cursor() as q:
            identifier=str(uuid.uuid4());q.execute('INSERT INTO employees(id,workspace_id,user_id,name) VALUES(%s,%s,%s,%s)',(identifier,admin['workspace_id'],admin['id'],'测试管理员'))
        admin_employee={'id':identifier}
    try:ps.save(admin,'user',admin_employee['id'],PolicyInput(revision=0,effects={},management=False))
    except AuthFailure as e:assert e.code=='last_admin'
    else:raise AssertionError('Last administrator removed')
    # A delegated personnel manager cannot move themselves into a stronger department.
    privileged=ds.save(admin,DepartmentInput(name='高权限部门'))
    ps.save(admin,'user',employee['id'],PolicyInput(revision=demoted['revision'],effects={'users:manage':True,'ai:configure':False}))
    actor=auth.identity(token)['user']
    try:es.move(actor,employee['id'],privileged['id'])
    except AuthFailure as e:assert e.code=='assignment_forbidden'
    else:raise AssertionError('Department move granted stronger capabilities')
    # An active run must observe revocation through the existing CLI identity boundary.
    from xingxu.tools import GalaxyDispatcher
    from xingxu.tool_context import ExecutionContext,DispatchDenied
    dispatcher=object.__new__(GalaxyDispatcher);dispatcher.authorize=auth.identity
    context=ExecutionContext(actor['id'],actor['workspace_id'],tuple(actor['permissions']),'fixture',token)
    try:dispatcher._check_command(context,'youzan.order-search',['youzan','order-search','query','--input','{}'])
    except DispatchDenied as e:assert e.code=='permission_denied'
    else:raise AssertionError('CLI ignored current department denial')
    with database().connection() as c,c.cursor() as q:
        q.execute('SELECT COUNT(*) n FROM permission_audit');assert q.fetchone()['n']>=4
    print('PASS: binary department defaults, personal override/reset, full administrator toggle and last-admin protection, live session and CLI revocation, derived channel visibility, revision conflicts, workspace isolation, department escalation prevention, audit.')

if __name__=='__main__':main()
