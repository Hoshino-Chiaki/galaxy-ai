from jsonschema import Draft202012Validator
from ...registry import ToolError
from ...file_scope import relative_parts

INPUT = {'type': 'object', 'properties': {
    'text': {'type': 'string', 'minLength': 1, 'maxLength': 20000},
    'path': {'type': 'string', 'minLength': 1, 'maxLength': 1024},
    'request_key': {'type': 'string', 'minLength': 1, 'maxLength': 100}},
    'required': ['request_key'], 'anyOf': [{'required': ['text']}, {'required': ['path']}], 'additionalProperties': False}
OUTPUT = {'type': 'object'}
DESCRIPTION = (
    '发送文字或文件到当前 Galaxy 账号已绑定的 ClawBot 微信，命令 galaxy-cli clawbot send --input JSON。'
    '必填 request_key 为本次投递唯一标识（如任务运行标识加用途），重试必须沿用同值；相同标识不会重复投递，不能换标识盲目重发。'
    '可选 text（1至20000字，纯文字内容）、path（当前账号工作目录内的相对文件路径），两者至少一个，也可同时传入；'
    '发送 Excel 时直接用 excel.create 实际返回的 path，例如 {"path":"generated/日报.xlsx","text":"今天的经营日报","request_key":"本次唯一投递标识"}。'
    '文件必须是当前账号工作目录内1字节至20MB的普通文件；不接受绝对路径、..、URL、隐藏路径、链接、其他账号路径。'
    '无需先 file.publish；如还需在 Galaxy AI 聊天框下载，可另行托管。只发送给当前账号扫码绑定者，不支持传收件人、群号、身份或凭证。'
    '可以在已获授权的定时任务中调用；任务 instruction 应包含生成报表及调用本工具发送的要求，创建任务不等于已经发送。'
    '需要 clawbot:send 权限，发送文件还需 file:read；未绑定、会话失效或未曾收到微信消息时会明确失败，需要在“我的账号→ClawBot”绑定并先向机器人发送一条消息。'
    '返回 delivery_id、status、parts、created_at 及可选 error；parts 为每段文字/文件的实际提交状态及文件名、大小。'
    '只有 status=sent 才表示微信服务已接受全部消息，不代表用户已读；partial 表示仅部分发送，unknown 表示网络中断后结果不确定，不能当作未发送并重复投递。'
    '定时投递受微信连接及平台实际允许的消息上下文有效性影响，失败应如实报告，不能承诺必达。'
)


def normalize(value):
    if not isinstance(value, dict) or not Draft202012Validator(INPUT).is_valid(value):
        raise ToolError('invalid_input', '请提供 request_key，以及 text 或 path。')
    if not value['request_key'].strip() or 'text' in value and not value['text'].strip():
        raise ToolError('invalid_input', '投递标识与文字不能仅为空格。')
    if 'path' in value:
        relative_parts(value['path'])
    return dict(value)


def register(registry):
    @registry.tool(name='clawbot.send', category='clawbot', description=DESCRIPTION, input_schema=INPUT, output_schema=OUTPUT)
    def invoke(arguments):
        normalize(arguments)
        raise ToolError('server_context_required', '请在 Galaxy AI 登录会话中调用微信发送工具。')
