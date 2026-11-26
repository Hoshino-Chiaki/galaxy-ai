from ...registry import ToolError
INPUT={'type':'object','properties':{'challenge_id':{'type':'string','format':'uuid'}},'required':['challenge_id'],'additionalProperties':False}
OUTPUT={'type':'object'}
DESCRIPTION=('等待当前渠道已发送的验证码，在桌面客户端自动弹出输入框；使用 interaction verification --input {"challenge_id":"上一步实际返回的ID"}。'
 '这是各渠道通用的人机交互工具，不发送短信、不接收模型传入的验证码原值；短信已由渠道工具在触发验证时自动发送。'
 '最多等待发送成功后的120秒，前端倒计时支持粘贴、提交和取消；等待时不能继续其他业务。'
 '返回 received 时含一次性 submission_id，立即调用 interaction.verification-submit；expired/cancelled 时停止当前验证，不自行重发或重新创建导出。')
def register(registry):
 @registry.tool(name='interaction.verification',category='interaction',description=DESCRIPTION,input_schema=INPUT,output_schema=OUTPUT)
 def call(arguments):raise ToolError('chat_context_required','请通过支持验证码弹窗的桌面聊天调用。')
