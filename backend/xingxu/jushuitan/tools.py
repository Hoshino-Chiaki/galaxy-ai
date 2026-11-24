import json
from galaxy_cli.jushuitan.orders import normalize
from galaxy_cli.registry import ToolError
from ..tool_context import DispatchDenied, strict_json


def arguments(args):
    if args[0] == 'jushuitan.order-search':args = ['jushuitan', 'order-search', *args[1:]]
    if args[0] != 'jushuitan':return None
    if len(args) < 2 or args[1] != 'order-search':
        raise DispatchDenied('command_denied', '请通过 help 查看真实的聚水潭工具名称。')
    tail = args[2:]
    if not tail or tail == ['--format', 'json']:body = {}
    elif len(tail) in (2, 4) and tail[0] == '--input' and (len(tail) == 2 or tail[2:] == ['--format', 'json']):
        try:body = strict_json(tail[1])
        except (ValueError, TypeError):raise DispatchDenied('invalid_arguments', '输入必须是有效 JSON 对象。') from None
    else:raise DispatchDenied('invalid_arguments', '聚水潭工具仅接受 --input JSON 和 --format json。')
    try:normalize(body)
    except ToolError as error:raise DispatchDenied(error.code, error.message) from None
    return ['jushuitan', 'order-search', '--input', json.dumps(body, ensure_ascii=False), '--format', 'json']


def redact(value, credentials):
    if isinstance(value, dict):return {key: redact(item, credentials) for key, item in value.items()}
    if isinstance(value, list):return [redact(item, credentials) for item in value]
    if isinstance(value, str):return value.replace(credentials['access_token'], '[已隐藏凭证]')
    return value
