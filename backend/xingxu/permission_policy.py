"""Department defaults and personal exceptions shared by APIs and CLI checks."""
from .database import database
from .mysql_store import load_json

PROTECTED = {'workspace:read', 'galaxy:help', 'tools:galaxy_cli'}
ADMIN_PROTECTED = {'permissions:manage', 'users:manage'}
CHANNELS = ('youzan', 'feishu', 'guanjia', 'clawbot')
LABELS = {
 'ai:usage:read':'查看员工 AI 用量统计',
 'ai:chat':'使用 AI 对话', 'ai:configure':'管理模型、提示词与工具配置',
 'users:manage':'管理人员与部门', 'excel:export':'生成 Excel', 'file:publish':'发布下载附件', 'file:read':'下载附件',
 'schedule:create':'创建本人的定时任务','schedule:read':'查看本人的定时任务','schedule:update':'修改本人任务时间','schedule:delete':'删除本人的定时任务',
 'task:upload':'上传任务文件', 'task:har:read':'解析接口记录',
 'youzan:customer:read':'读取客户资料与客户数量', 'youzan:report:read':'读取经营报表', 'youzan:order:read':'读取订单',
 'youzan:material:read':'查看素材与分类', 'youzan:material:category:create':'创建素材分类', 'youzan:material:category:update':'修改素材分类',
 'youzan:material:upload':'上传素材', 'youzan:product:read':'查看商品与商品选项', 'youzan:product:update':'修改商品资料与分组',
 'youzan:product:price:update':'修改商品价格', 'youzan:product:stock:update':'修改商品库存', 'youzan:product:display:update':'修改商品销售状态',
 'youzan:renew:configure':'设置自动续期', 'feishu:read':'读取个人授权范围内的飞书数据', 'feishu:create':'创建、发送和上传飞书内容', 'feishu:delete':'删除或移除飞书内容', 'feishu:update':'修改飞书内容与其他写入操作',
 'feishu:chat':'通过飞书机器人使用本人 Galaxy AI',
 'feishu:attendance:company:read':'读取企业授权人员与考勤', 'feishu:app:configure':'管理飞书企业应用与账号池',
 'clawbot:send':'发送文字与文件到本人微信',
 'clawbot:configure':'绑定并使用本人的微信 ClawBot',
 'guanjia:configure':'连接管家婆并查询 ERP 数据',
}
GROUPS = [('ai','AI'),('schedule','任务调度（实验）'),('youzan','有赞'),('feishu','飞书'),('guanjia','管家婆'),('clawbot','ClawBot'),('erp','ERP'),('files','文件与附件')]


def catalog():
    from .auth import ROLES
    codes = set().union(*map(set, ROLES.values())) | set(LABELS)
    return [dict(id=key, name=name, permissions=[{'code':code,'name':LABELS.get(code,code),'action':action(code)} for code in sorted(codes)
        if code in LABELS and group(code)==key]) for key,name in GROUPS]


def action(code):
    if code.endswith(':read') or code=='guanjia:configure': return '查询'
    if code == 'clawbot:send': return '新增'
    if ':create' in code or code in ('excel:export', 'task:upload', 'youzan:material:upload'): return '新增'
    if ':delete' in code: return '删除'
    if ':update' in code: return '修改'
    return '其他'


def group(code):
    if code.startswith(('schedule:', 'ai:', 'youzan:', 'feishu:', 'guanjia:', 'clawbot:')): return code.split(':')[0]
    return 'erp' if code=='users:manage' else 'files'


def normalize(permissions):
    result=set(permissions)
    result.discard('feishu:write')
    if result & {'feishu:create','feishu:delete','feishu:update'}: result.add('feishu:write')
    for channel in ('youzan','feishu'):
        config=channel+':configure'
        if any(p.startswith(channel+':') and p!=config for p in result):result.add(config)
        else:result.discard(config)
    return sorted(result)


def resolve(base, department=None, personal=None, *, admin=False):
    result=set(base)
    if admin:
        from .auth import ROLES
        return normalize(result | set().union(*map(set, ROLES.values())) | PROTECTED | ADMIN_PROTECTED)
    for policy in (department or {}, personal or {}):
        for code,allow in policy.items():
            if allow:result.add(code)
            else:result.discard(code)
    result.update(PROTECTED)
    if admin:result.update(ADMIN_PROTECTED)
    return normalize(result)


def policies(cursor, user):
    cursor.execute('''SELECT d.id FROM employees e JOIN departments d ON d.workspace_id=e.workspace_id
        AND d.name COLLATE utf8mb4_bin=e.department WHERE e.user_id=%s AND e.workspace_id=%s''', (user['id'],user['workspace_id']))
    department=cursor.fetchone()
    ids=[('user',user['id'])]+([('department',department['id'])] if department else [])
    result={}
    for kind,identifier in ids:
        cursor.execute('SELECT payload FROM permission_policies WHERE workspace_id=%s AND subject_type=%s AND subject_id=%s',
                       (user['workspace_id'],kind,identifier))
        row=cursor.fetchone();result[kind]=load_json(row['payload']) if row else {}
    return result


def effective(cursor,user,base):
    if user['role']=='admin':
        cursor.execute('SELECT code FROM permissions')
        return resolve([row['code'] for row in cursor.fetchall()],admin=True)
    policy=policies(cursor,user)
    return resolve(base,policy.get('department'),policy.get('user'),admin=user['role']=='admin')


def check_assignment(cursor, actor, role, department, user_id=None, *, credentials=False):
    """Personnel edits cannot grant stronger accounts through department or passwords."""
    from .auth import AuthFailure
    cursor.execute('SELECT permission_code FROM role_permissions WHERE role_code=%s', (role,))
    base=[r['permission_code'] for r in cursor.fetchall()]
    personal={}
    if user_id:
        personal=policies(cursor,{'id':user_id,'workspace_id':actor['workspace_id']}).get('user',{})
    cursor.execute('''SELECT p.payload FROM permission_policies p JOIN departments d ON d.id=p.subject_id
        AND d.workspace_id=p.workspace_id WHERE p.workspace_id=%s AND p.subject_type='department' AND d.name COLLATE utf8mb4_bin=%s''',
        (actor['workspace_id'],department))
    row=cursor.fetchone()
    assigned=resolve(base,load_json(row['payload']) if row else {},personal,admin=role=='admin')
    if set(assigned)-set(actor['permissions']):
        raise AuthFailure('assignment_forbidden','该操作会涉及超出你权限范围的账号或部门，需由权限管理员操作。',403)


def require_live(cursor, actor, permission):
    from .auth import AuthFailure
    cursor.execute('SELECT id,workspace_id,role,active FROM users WHERE id=%s AND workspace_id=%s', (actor['id'],actor['workspace_id']))
    raw=cursor.fetchone()
    if not raw or not raw['active']:raise AuthFailure('forbidden','当前账号不可用。',403)
    cursor.execute('SELECT DISTINCT rp.permission_code FROM user_roles ur JOIN role_permissions rp ON rp.role_code=ur.role_code WHERE ur.user_id=%s',(actor['id'],))
    current=effective(cursor,raw,[row['permission_code'] for row in cursor.fetchall()])
    if permission not in current:raise AuthFailure('forbidden','当前操作权限已变更。',403)
    return {**actor,'permissions':current}
