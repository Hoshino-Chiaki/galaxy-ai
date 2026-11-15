"""Recognize the fixed read-only server clock command."""
import json

from galaxy_cli.registry import ToolError
from galaxy_cli.tools.time.now import normalize

from .tool_context import DispatchDenied, strict_json


def arguments(args):
    if args[0] == 'time.now':
        args = ['time', 'now', *args[1:]]
    if args[0] != 'time':
        return None
    if args[:2] != ['time', 'now']:
        raise DispatchDenied('command_denied', '时间工具不存在，请使用 galaxy-cli time now。')
    body, position, used_input, used_format = {}, 2, False, False
    while position < len(args):
        if args[position] == '--input' and not used_input and position + 1 < len(args):
            try:
                body = strict_json(args[position + 1])
            except (ValueError, TypeError):
                raise DispatchDenied('invalid_arguments', '时间工具输入必须是有效 JSON 对象。') from None
            used_input = True
            position += 2
        elif args[position:] == ['--format', 'json'] and not used_format:
            used_format = True
            position += 2
        else:
            raise DispatchDenied('invalid_arguments', '时间工具只接受可选的 --input JSON 和 --format json。')
    try:
        body = normalize(body)
    except ToolError as error:
        raise DispatchDenied(error.code, error.message) from None
    return ['time', 'now', '--input', json.dumps(body, ensure_ascii=False), '--format', 'json']
