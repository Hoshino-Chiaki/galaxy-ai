import datetime as dt
import re
from jsonschema import Draft202012Validator
from ...registry import ToolError
INPUT={'type':'object','properties':{'action':{'enum':['create','resume']},'start_time':{'type':'string'},'end_time':{'type':'string'},'job_id':{'type':'string','pattern':'^[a-f0-9]{32}$'}},'additionalProperties':False}
OUTPUT={'type':'object'}
def normalize(value):
 if not isinstance(value,dict) or not Draft202012Validator(INPUT).is_valid(value):raise ToolError('invalid_input','请填写起止时间或已返回的job_id。')
 value=dict(value);value.setdefault('action','create')
 if value['action']=='resume':
  if set(value)!={'action','job_id'}:raise ToolError('invalid_input','续接只接受 action=resume 和 job_id。')
 else:
  if set(value)!={'action','start_time','end_time'}:raise ToolError('invalid_input','创建导出需要 start_time 和 end_time，不能混入 job_id。')
  try:
   times=[dt.datetime.strptime(value[k],'%Y-%m-%d %H:%M:%S') for k in ('start_time','end_time')]
   if times[0]>times[1]:raise ValueError()
  except ValueError:raise ToolError('invalid_input','时间须为有效的 YYYY-MM-DD HH:mm:ss，开始不得晚于结束。') from None
 return value
DESCRIPTION=('按北京时间下单时间批量导出订单CSV，固定使用当前店铺名为“AI财务订单表1”的商品维度模板，不使用系统标准模板；youzan order-export --input JSON。'
 '创建必填 start_time/end_time（YYYY-MM-DD HH:mm:ss），可选 action=create；店铺用 galaxy_cli 的 shops 指定明确一家，不支持 all 或多家同时导出。'
 '服务端解析各店独立模板ID并核验6列：订单号、商品名称、订单商品状态、订单状态、付款方式、订单类型。程序创建导出、查询进度并下载，模型不用逐页查询订单。'
 '遇短信验证先自动发送短信，发送成功立即弹出客户端输入框并等待，期间不需要模型另发一轮请求。返回 received 含 challenge_id、submission_id、job_id；取消或超时分别返回 cancelled/expired。若渠道返回 verification_required，再调用通用 interaction.verification 等待，不要仅回复让用户发验证码。'
 '收到 submission_id 后调用通用 interaction.verification-submit，服务器验证并继续下载。等待最多120秒，超时/取消应停止。'
 '需要官方安全验证时明确提示人工完成，不重复发送；不更改登录配置、不代替用户完成滑块。'
 '已有导出任务用 action=resume 和真实 job_id 续接，不重复创建。成功返回 status=downloaded、本人缓存 sandbox_path/path、format=csv、rows、headers、时间范围和模板；完整数据不进入对话。'
 '此商品维度一单多行，补订单级字段须先核实同单一致性，避免多对多放大；导出时间范围不能覆盖所有跨期退款原单。')
def register(registry):
 @registry.tool(name='youzan.order-export',category='youzan',description=DESCRIPTION,input_schema=INPUT,output_schema=OUTPUT)
 def call(arguments):normalize(arguments);raise ToolError('server_context_required','请通过服务器 galaxy_cli 调用导出。')
