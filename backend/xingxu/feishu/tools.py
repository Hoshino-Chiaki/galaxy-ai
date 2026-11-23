"""Fixed Feishu command validation for the Galaxy tool gateway."""
import json
from galaxy_cli.feishu_client import INPUTS, normalize
from galaxy_cli.registry import ToolError
from galaxy_cli.feishu_capabilities import DOMAINS, is_write
from ..tool_context import DispatchDenied, strict_json

PERMISSIONS = {'feishu.' + name: 'feishu:read' for name in INPUTS}


def permissions(command, canonical):
    name=command.split('.',1)[1]
    if name=='authorized-users':return {'feishu:read','feishu:attendance:company:read'}
    body=strict_json(canonical[canonical.index('--input')+1])
    if name in DOMAINS and is_write(name,body):
        action=body.get('action','').split('.')[-1]
        if any(word in action for word in ('delete','remove','revoke','destroy')):kind='delete'
        elif any(word in action for word in ('create','add','copy','import','send','reply','upload')):kind='create'
        else:kind='update'
        return {'feishu:write','feishu:'+kind}
    return {'feishu:read'}


def arguments(args):
    if args[0] in PERMISSIONS:args = ['feishu', args[0].split('.',1)[1], *args[1:]]
    if args[0] != 'feishu':return None
    if len(args) < 2 or args[1] not in INPUTS:
        raise DispatchDenied('command_denied', '飞书工具不存在，请先运行 galaxy-cli help feishu。')
    body, position, used_input, used_format = {}, 2, False, False
    while position < len(args):
        if args[position] == '--input' and not used_input and position + 1 < len(args):
            try:body = strict_json(args[position+1])
            except (ValueError, TypeError):raise DispatchDenied('invalid_arguments','飞书输入必须是有效 JSON。') from None
            used_input = True;position += 2
        elif args[position:] == ['--format', 'json'] and not used_format:
            used_format = True;position += 2
        else:raise DispatchDenied('invalid_arguments','飞书工具只接受 --input JSON 和 --format json。')
    try:body = normalize(args[1], body)
    except ToolError as error:raise DispatchDenied(error.code,error.message) from None
    return ['feishu',args[1],'--input',json.dumps(body,ensure_ascii=False),'--format','json']
