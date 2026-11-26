from ...registry import ToolError
INPUT={'type':'object','properties':{'challenge_id':{'type':'string','format':'uuid'},'submission_id':{'type':'string','minLength':1}},'required':['challenge_id','submission_id'],'additionalProperties':False}
OUTPUT={'type':'object'}
DESCRIPTION=('提交本人当前对话刚收到的一次性验证码引用，适用于所有已接入渠道；interaction verification-submit --input JSON，必填 challenge_id、submission_id，均使用 interaction.verification 实际返回值。'
 '不传验证码原值、手机号、账号、接口URL或凭证。服务器使用临时保存的验证码验证并继续原操作；有赞订单导出成功会直接返回CSV文件回执。'
 '若返回 verification_required，在原剩余时间内再次调用 interaction.verification 等待重填；不能重新计时或自动重发短信。超时、取消或权限变化时停止。')
def register(registry):
 @registry.tool(name='interaction.verification-submit',category='interaction',description=DESCRIPTION,input_schema=INPUT,output_schema=OUTPUT)
 def call(arguments):raise ToolError('chat_context_required','请通过当前账号的聊天调用。')
