from ...registry import ToolError

INPUT = {'type': 'object', 'properties': {}, 'additionalProperties': False}
OUTPUT = {'type': 'object'}
DESCRIPTION = (
    '查看当前调用者自己的 Galaxy 账号与渠道连接状态，命令 galaxy-cli account me --input {}。无业务参数，不接受他人账号、用户ID或凭证。'
    '返回 name（个人显示姓名）、account（Galaxy 登录账号）、administrator（是否拥有管理员权限）、channels（有赞、飞书、管家婆、ClawBot 等渠道的账号标识与状态摘要）、checked_at（本次读取的Unix秒时间戳）。'
    'channels 中 not_enabled 表示当前账号未获该渠道权限；未配置、待登录、部分就绪、已连接、可续期、已过期等状态按渠道实际记录返回。'
    '结果只反映服务器保存的本人连接状态，不主动重新登录、不修改授权、不代表已实时验证所有平台接口。'
    '不返回密码、Cookie、SID、CSRF、access_token、签名密钥或他人信息；遇到“我是谁、当前登录哪个账号、各渠道是否已登录”等问题可调用。'
)


def normalize(value):
    if not isinstance(value, dict) or value:
        raise ToolError('invalid_input', 'account.me 只接受空对象 {}，身份由服务器确定。')
    return {}


def register(registry):
    @registry.tool(name='account.me', category='account', description=DESCRIPTION, input_schema=INPUT, output_schema=OUTPUT)
    def invoke(arguments):
        normalize(arguments)
        raise ToolError('server_context_required', '请在 Galaxy AI 登录会话中查看本人信息。')
