"""Department creation is executed by the authenticated Galaxy server."""
from jsonschema import Draft202012Validator
from ...registry import ToolError

INPUT = {'type':'object','properties':{'name':{'type':'string','minLength':1,'maxLength':100}},'required':['name'],'additionalProperties':False}
OUTPUT = {'type':'object','properties':{'created':{'const':True},'id':{'type':'string'},'name':{'type':'string'},'employee_count':{'type':'integer','minimum':0}},'required':['created','id','name','employee_count'],'additionalProperties':False}
DESCRIPTION = ('ERP 创建部门：galaxy-cli erp department-create --input JSON，仅当前工作空间有 users:manage 人员管理权限的账号可用。'
 '必填 name（部门名称，1至100字），例如 {"name":"新零售部"}；不能传用户、角色、工作空间、任意接口或凭证。'
 '名称去除首尾空格，不能含控制字符；已存在同名部门时返回 department_exists，不覆盖、不合并。'
 '成功返回 created=true、部门 id、name、employee_count。创建部门不会创建账号或移动员工；移动已有员工请使用 erp.employee-move。'
 '只有成功结果才能说明已创建；网络中断等结果未确认时先用 employee-move 的 options 或人员管理核对，不重复创建。')


def normalize(arguments):
    if not isinstance(arguments,dict) or not Draft202012Validator(INPUT).is_valid(arguments):
        raise ToolError('invalid_input','创建部门只接受必填 name，名称为1至100字。')
    name=arguments['name'].strip()
    if not name or any(ord(c)<32 for c in name):raise ToolError('invalid_input','部门名称不能为空或包含控制字符。')
    return {'name':name}


def register(registry):
    @registry.tool(name='erp.department-create',category='erp',description=DESCRIPTION,input_schema=INPUT,output_schema=OUTPUT)
    def invoke(arguments):
        normalize(arguments)
        raise ToolError('server_context_required','请在已登录 Galaxy AI 的对话中调用，由服务器校验人员管理权限。')
