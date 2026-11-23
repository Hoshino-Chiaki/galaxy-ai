"""Move an existing employee without changing any other account fields."""
from jsonschema import Draft202012Validator
from ...registry import ToolError
UUID={'type':'string','pattern':r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'}
INPUT={'type':'object','properties':{'action':{'enum':['options','move']},'q':{'type':'string','maxLength':80},'page':{'type':'integer','minimum':1},'page_size':{'type':'integer','minimum':1,'maximum':100},'employee_id':UUID,'department_id':UUID},'additionalProperties':False,'oneOf':[
 {'properties':{'action':{'const':'options'}},'required':['action'],'not':{'anyOf':[{'required':['employee_id']},{'required':['department_id']}]}},
 {'properties':{'action':{'const':'move'}},'required':['employee_id','department_id'],'not':{'anyOf':[{'required':['q']},{'required':['page']},{'required':['page_size']}]}}]}
PERSON={'type':'object','properties':{key:{'type':'string'} for key in ('id','name','account','department')},'required':['id','name','account','department'],'additionalProperties':False}
DEPT={'type':'object','properties':{'id':{'type':'string'},'name':{'type':'string'},'employee_count':{'type':'integer','minimum':0}},'required':['id','name','employee_count'],'additionalProperties':False}
OUTPUT={'type':'object','properties':{'action':{'enum':['options','move']},'departments':{'type':'array','items':DEPT},'employees':{'type':'array','items':PERSON},'total':{'type':'integer','minimum':0},'page':{'type':'integer','minimum':1},'page_size':{'type':'integer','minimum':1},'moved':{'type':'boolean'},'employee':PERSON,'department_id':UUID,'previous_department':{'type':'string'}},'required':['action'],'additionalProperties':False,'oneOf':[
 {'properties':{'action':{'const':'options'}},'required':['departments','employees','total','page','page_size']},
 {'properties':{'action':{'const':'move'}},'required':['moved','employee','department_id','previous_department']}]}
DESCRIPTION=('ERP 移动已有员工到部门：galaxy-cli erp employee-move --input JSON，仅当前工作空间有 users:manage 人员管理权限的账号可用。'
 '先传 {"action":"options"} 获取真实部门ID和员工ID；可选 q（姓名/账户/手机/部门关键词，最多80字）、page（默认1）、page_size（默认20，最多100）筛选、翻页员工；结果 departments 为全部已建部门，employees 为指定页员工，total 为筛选后的员工总数。'
 '移动传 {"employee_id":"实际员工ID","department_id":"实际部门ID"}，action 可省略或填 move；两个ID均必填，不能根据姓名猜造ID，同名员工先按账户确认。'
 '只更新已有员工的部门，不创建员工、不修改姓名、岗位、密码、在职状态或角色权限，不触碰平台授权。'
 '成功返回 action=move、moved、employee（id/name/account/department）、department_id 和 previous_department；moved=false 表示原本已在目标部门。'
 '员工和部门必须都属于当前工作空间；无权限或不存在时失败，不能跨工作空间操作。结果未确认时重新用 options 核对归属，不能假称成功。')


def normalize(arguments):
    if not isinstance(arguments,dict) or not Draft202012Validator(INPUT).is_valid(arguments):
        raise ToolError('invalid_input','查询候选用 action=options，可选 q/page/page_size；移动需 employee_id 和 department_id，action 默认 move。')
    result={**arguments,'action':arguments.get('action','move')}
    if result['action']=='options':
        result.setdefault('page',1);result.setdefault('page_size',20)
        if 'q' in result:
            result['q']=result['q'].strip()
            if any(ord(c)<32 for c in result['q']):raise ToolError('invalid_input','搜索关键词不能含控制字符。')
    return result


def register(registry):
    @registry.tool(name='erp.employee-move',category='erp',description=DESCRIPTION,input_schema=INPUT,output_schema=OUTPUT)
    def invoke(arguments):
        normalize(arguments)
        raise ToolError('server_context_required','请在已登录 Galaxy AI 的对话中调用，由服务器校验人员管理权限。')
