"""The only model-callable 管家婆 commands; no session overrides."""
import json
from galaxy_cli.gjp.schemas import normalize, INPUTS
from galaxy_cli.registry import ToolError
from ..tool_context import DispatchDenied, strict_json


def arguments(args):
    if args[0].startswith('gjp.'):
        args = ['gjp', args[0][4:], *args[1:]]
    if args[0] != 'gjp':
        return None
    if len(args) < 2 or args[1] not in INPUTS:
        raise DispatchDenied('command_denied', '管家婆工具不存在，请查看 gjp 分类说明。')
    body, position, used_input, used_format = {}, 2, False, False
    while position < len(args):
        if args[position] == '--input' and not used_input and position + 1 < len(args):
            try:
                body = strict_json(args[position + 1])
            except (ValueError, TypeError):
                raise DispatchDenied('invalid_arguments', '管家婆工具输入必须是有效 JSON 对象。') from None
            used_input = True
            position += 2
        elif args[position:] == ['--format', 'json'] and not used_format:
            used_format = True
            position += 2
        else:
            raise DispatchDenied('invalid_arguments', '管家婆工具只接受 --input JSON 和 --format json。')
    try:
        body = normalize(args[1], body)
    except ToolError as error:
        raise DispatchDenied(error.code, error.message) from None
    return ['gjp', args[1], '--input', json.dumps(body, ensure_ascii=False), '--format', 'json']


def redact(value, credentials):
    secrets = [credentials.get('cookie'), credentials.get('sid')]
    secrets.extend(c.get('value') for c in credentials.get('cookies', []) if isinstance(c, dict) and len(c.get('value', '')) >= 8)
    def clean(item):
        if isinstance(item, dict):
            return {k: clean(v) for k, v in item.items()}
        if isinstance(item, list):
            return [clean(v) for v in item]
        if isinstance(item, str):
            for secret in secrets:
                if secret:
                    item = item.replace(secret, '[已隐藏凭证]')
        return item
    return clean(value)
