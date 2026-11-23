import json
from galaxy_cli.tools.schedule import create, list as listing, update_time, delete
from galaxy_cli.registry import ToolError
from ..tool_context import DispatchDenied, strict_json
from ..auth import AuthFailure
MODULES={'create':create,'list':listing,'update-time':update_time,'delete':delete}
PERMISSIONS={'create':'schedule:create','list':'schedule:read','update-time':'schedule:update','delete':'schedule:delete'}


def arguments(args):
    if args[0].startswith('schedule.'):args=['schedule',args[0].split('.',1)[1],*args[1:]]
    if args[0]!='schedule':return None
    if len(args) not in (4,6) or args[1] not in MODULES or args[2]!='--input' or len(args)==6 and args[4:]!=['--format','json']:
        raise DispatchDenied('invalid_arguments','请使用 schedule 工具名 --input JSON。')
    try:body=MODULES[args[1]].normalize(strict_json(args[3]))
    except (ValueError,TypeError,ToolError):raise DispatchDenied('invalid_arguments','任务调度参数无效，请查看 help。') from None
    return ['schedule',args[1],'--input',json.dumps(body,ensure_ascii=False),'--format','json']


async def run(dispatcher,context,command,canonical):
    user=dispatcher._check_command(context,command,canonical);service=dispatcher.schedule_provider
    if not service:raise DispatchDenied('schedule_unavailable','任务调度未启用。')
    body=strict_json(canonical[3]);name=canonical[1]
    try:
        if name=='create':result=service.create(user,body)
        elif name=='list':result=service.list(user,body.get('id'))
        elif name=='update-time':result=service.update_time(user,body)
        else:result=await service.delete(user,body)
    except AuthFailure as e:raise DispatchDenied(e.code,e.message) from None
    return result
