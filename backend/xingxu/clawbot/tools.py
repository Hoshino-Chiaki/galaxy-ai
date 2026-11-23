import json
from galaxy_cli.registry import ToolError
from galaxy_cli.tools.clawbot.send import normalize
from ..auth import AuthFailure
from ..tool_context import DispatchDenied, strict_json


def arguments(args):
    if args[0] == 'clawbot.send':
        args = ['clawbot', 'send', *args[1:]]
    if args[0] != 'clawbot':
        return None
    if len(args) not in (4, 6) or args[:3] != ['clawbot', 'send', '--input'] or len(args) == 6 and args[4:] != ['--format', 'json']:
        raise DispatchDenied('invalid_arguments', '请使用 clawbot send --input JSON。')
    try:
        body = normalize(strict_json(args[3]))
    except (ValueError, TypeError, ToolError):
        raise DispatchDenied('invalid_arguments', '微信发送参数无效，请查看 clawbot.send 的 help。') from None
    return ['clawbot', 'send', '--input', json.dumps(body, ensure_ascii=False), '--format', 'json']


def permissions(canonical):
    body = strict_json(canonical[3])
    return {'clawbot:send'} | ({'file:read'} if 'path' in body else set())


async def run(dispatcher, context, command, canonical):
    user = dispatcher._check_command(context, command, canonical)
    if dispatcher.clawbot_provider is None:
        raise DispatchDenied('clawbot_unavailable', '微信发送服务未启用。')
    try:
        return await dispatcher.clawbot_provider.send(user, strict_json(canonical[3]),
            lambda: dispatcher._check_command(context, command, canonical))
    except AuthFailure as failure:
        raise DispatchDenied(failure.code, failure.message) from None
