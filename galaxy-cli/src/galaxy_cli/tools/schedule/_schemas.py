from jsonschema import Draft202012Validator
from ...registry import ToolError

RULE={'type':'object','properties':{'type':{'enum':['once','cron','interval']},'timezone':{'type':'string','maxLength':80},
    'expression':{'type':'string','maxLength':160},'at':{'type':'string','maxLength':80},'seconds':{'type':'integer','minimum':1},'anchor':{'type':'string','maxLength':80}},'required':['type'],'additionalProperties':False}
ID={'type':'string','format':'uuid'}
CREATE={'type':'object','properties':{'name':{'type':'string','minLength':1,'maxLength':100},'instruction':{'type':'string','minLength':1,'maxLength':6000},
    'time_description':{'type':'string','minLength':1,'maxLength':300},'rule':RULE,'request_key':{'type':'string','minLength':1,'maxLength':100}},
    'required':['name','instruction','time_description','rule','request_key'],'additionalProperties':False}
UPDATE={'type':'object','properties':{'id':ID,'revision':ID,'time_description':CREATE['properties']['time_description'],'rule':RULE},'required':['id','revision','time_description','rule'],'additionalProperties':False}
DELETE={'type':'object','properties':{'id':ID,'revision':ID},'required':['id','revision'],'additionalProperties':False}
LIST={'type':'object','properties':{'id':ID},'additionalProperties':False}
OUTPUT={'type':'object'}


def normalize(value,schema):
    if not isinstance(value,dict) or not Draft202012Validator(schema).is_valid(value):raise ToolError('invalid_input','任务参数无效，请查看 schedule 分类 help。')
    return value


def register_tool(registry,name,schema,description):
    @registry.tool(name='schedule.'+name,category='schedule',description=description,input_schema=schema,output_schema=OUTPUT)
    def invoke(arguments):
        normalize(arguments,schema)
        raise ToolError('server_context_required','请在 Galaxy AI 登录会话中调用任务调度工具。')
