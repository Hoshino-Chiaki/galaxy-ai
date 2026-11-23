"""Structured clarification; only the authenticated chat server can wait for answers."""
import copy
from jsonschema import Draft202012Validator
from ...registry import ToolError
KEY={'type':'string','pattern':r'^[a-zA-Z0-9_-]{1,64}$'}
INPUT={'type':'object','properties':{'questions':{'type':'array','minItems':1,'maxItems':3,'items':{
 'type':'object','properties':{'id':KEY,'title':{'type':'string','minLength':1,'maxLength':500},'options':{
 'type':'array','minItems':2,'maxItems':30,'items':{'type':'object','properties':{'id':KEY,'label':{'type':'string','minLength':1,'maxLength':160},
 'description':{'type':'string','maxLength':400}},'required':['id','label'],'additionalProperties':False}}},
 'required':['id','title','options'],'additionalProperties':False}}},'required':['questions'],'additionalProperties':False}
OUTPUT={'type':'object','properties':{'interaction_id':{'type':'string'},'answers':{'type':'array','items':{'type':'object',
 'properties':{'question_id':KEY,'question':{'type':'string'},'option_id':KEY,'answer':{'type':'string'}},
 'required':['question_id','question','answer'],'additionalProperties':False}}},'required':['interaction_id','answers'],'additionalProperties':False}
DESCRIPTION=('向当前聊天用户弹出底部选择框：galaxy-cli interaction ask --input JSON。仅当缺少影响执行的范围、目标或其他关键条件时使用，先复用当前请求和对话中已明确的信息，避免重复询问。'
 '输入只接受必填 questions 数组（1至3个问题），每项必填 id（唯一英文/数字/下划线/连字符标识）、title（完整问题）和 options（2至30个选项）；选项必填唯一 id、label，可选 description。'
 '例如 {"questions":[{"id":"shop","title":"查询哪家店铺的订单？","options":[{"id":"all","label":"全部已授权店铺"},{"id":"shop_94598170","label":"妈咪挚爱旗舰店"}]}]}。店铺名称和ID应来自当前授权信息，不虚构候选，不包含不可访问店铺。'
 '界面自动提供“自行填写”，不要额外添加“其他”选项；不要给选项传默认值。调用会暂停等待真实用户提交，未提交、超时、取消和默认高亮均不是用户答案，不能擅自继续业务查询或写入。'
 '成功输出 interaction_id 和 answers；每项包含 question_id、question、answer，点选时还包含 option_id，自行填写时 answer 为用户原文。收到答案后结合原请求继续执行；用户取消则停止当前任务。此工具不扩大任何平台权限。')

def normalize(value):
    if not isinstance(value,dict) or not Draft202012Validator(INPUT).is_valid(value):
        raise ToolError('invalid_input','询问需 questions，每个问题有 id/title 和2至30个 id/label 选项。')
    value=copy.deepcopy(value)
    ids=set()
    for question in value['questions']:
        if question['id'] in ids:raise ToolError('invalid_input','问题ID不能重复。')
        ids.add(question['id']);choices=set()
        for record,key in [(question,'title'),*[(item,'label') for item in question['options']]]:
            record[key]=record[key].strip()
            if not record[key] or any(ord(c)<32 for c in record[key]):raise ToolError('invalid_input','问题和选项不能为空或含控制字符。')
        for option in question['options']:
            if option['id'] == '__custom__' or option['id'] in choices:raise ToolError('invalid_input','同一问题的选项ID不能重复。')
            choices.add(option['id'])
    return value

def register(registry):
    @registry.tool(name='interaction.ask',category='interaction',description=DESCRIPTION,input_schema=INPUT,output_schema=OUTPUT)
    def invoke(arguments):
        normalize(arguments)
        raise ToolError('server_context_required','请通过已登录的 Galaxy AI 对话调用选择框。')
