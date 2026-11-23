"""Discover the personnel creation contract without exposing database credentials."""
from jsonschema import Draft202012Validator
from ...registry import ToolError

INPUT = {'type': 'object', 'properties': {
    'action': {'enum': ['options', 'create'], 'default': 'create'},
    'name': {'type': 'string', 'minLength': 1, 'maxLength': 80},
    'account': {'type': 'string', 'minLength': 1, 'maxLength': 160},
    'password': {'type': 'string', 'minLength': 8, 'maxLength': 256},
    'department': {'type': 'string', 'minLength': 1, 'maxLength': 100},
    'job_title': {'type': 'string', 'maxLength': 100},
    'phone': {'type': 'string', 'maxLength': 32}},
    'additionalProperties': False, 'oneOf': [
        {'properties': {'action': {'const': 'options'}}, 'required': ['action'], 'maxProperties': 1},
        {'properties': {'action': {'const': 'create'}}, 'required': ['name', 'account', 'password', 'department']}]}
EMPLOYEE = {'type': 'object', 'properties': {
    'id': {'type': 'string'}, 'user_id': {'type': 'string'},
    'name': {'type': 'string'}, 'account': {'type': 'string'},
    'department': {'type': 'string'}, 'job_title': {'type': 'string'}, 'phone': {'type': 'string'},
    'employment_status': {'const': 'active'}, 'role': {'const': 'employee'}},
    'required': ['id', 'user_id', 'name', 'account', 'department', 'job_title', 'phone', 'employment_status', 'role'],
    'additionalProperties': False}
OUTPUT = {'type': 'object', 'properties': {
    'action': {'enum': ['options', 'create']}, 'created': {'type': 'boolean'},
    'departments': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'id': {'type': 'string'}, 'name': {'type': 'string'}}, 'required': ['id', 'name'], 'additionalProperties': False}},
    'password_min_length': {'const': 8}, 'default_role': {'const': 'employee'}, 'employee': EMPLOYEE},
    'required': ['action', 'created'], 'additionalProperties': False,
    'oneOf': [
        {'properties': {'action': {'const': 'options'}, 'created': {'const': False}},
         'required': ['departments', 'password_min_length', 'default_role'], 'not': {'required': ['employee']}},
        {'properties': {'action': {'const': 'create'}, 'created': {'const': True}},
         'required': ['employee'], 'not': {'required': ['departments']}}]}
DESCRIPTION = (
    'ERP：在当前登录工作空间创建人员及其 Galaxy AI 登录账号，仅有 users:manage 人员管理权限的账号可用。'
    '命令 galaxy-cli erp employee-create --input JSON。'
    '先传 {"action":"options"} 查询本工作空间真实部门名称和密码要求；不猜部门，不自行新建相似部门。'
    '创建传 {"name":"姓名","account":"登录账号","password":"至少8位密码","department":"已有部门名称",'
    '"job_title":"岗位","phone":"手机号"}；action 默认 create。'
    'name、account、password、department 必填；job_title 和 phone 可省略。姓名最多80字，账号160字，部门和岗位100字，手机号32字。'
    '用户已明确要求创建且资料齐全时直接执行；缺少必填内容再追问。密码不满足现有8位规则时说明要求，不擅自改写密码。'
    '账号默认为在职普通员工，不能传 role、权限、用户ID或工作空间ID。管理员授权需要独立管理操作。'
    '已有同名账号返回 account_exists，不覆盖密码、不修改已有人员。结果未确认时先到 ERP 人员管理核对，不盲目重复提交。'
    '成功返回 created=true 和 employee 的姓名、账号、部门、岗位、手机号、人员ID及用户ID；只在成功后称已创建。'
    '密码不会在结果中返回，不在普通回复中重复密码，也不要要求用户提供数据库 root 或其他账号的登录凭据。'
)


def normalize(arguments):
    if not isinstance(arguments, dict) or not Draft202012Validator(INPUT).is_valid(arguments):
        raise ToolError('invalid_input', '请提供姓名、账号、至少8位密码和已有部门；可选岗位、手机号。查询部门只传 action=options。')
    if arguments.get('action') == 'options':
        return {'action': 'options'}
    result = {**arguments, 'action': 'create'}
    for key in ('name', 'account', 'department', 'job_title', 'phone'):
        if key in result:
            result[key] = result[key].strip()
            if any(ord(c) < 32 for c in result[key]) or key in ('name', 'account', 'department') and not result[key]:
                raise ToolError('invalid_input', '姓名、账号、部门不能为空，文本字段不能包含控制字符。')
    return result


def register(registry):
    @registry.tool(name='erp.employee-create', category='erp', description=DESCRIPTION,
                   input_schema=INPUT, output_schema=OUTPUT)
    def employee_create(arguments):
        normalize(arguments)
        raise ToolError('server_context_required', '请在已登录 Galaxy AI 的对话中调用，由服务器校验人员管理权限并创建账号。')
