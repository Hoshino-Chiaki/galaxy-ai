"""Recognize only registered task tools, never general shell commands or local paths."""
import json
import re

from galaxy_cli.registry import ToolError
from galaxy_cli.tools.task.har import normalize as har_arguments
from galaxy_cli.tools.task.request_upload import normalize as upload_arguments
from .tool_context import DispatchDenied, strict_json


def arguments(args):
    if args[0] not in ('task', 'task.har', 'task.request-upload'):
        return None
    if args[0] != 'task':
        args = ['task', args[0].split('.', 1)[1], *args[1:]]
    if len(args) not in (4, 6) or args[1] not in ('har', 'request-upload') or args[2] != '--input' or len(args) == 6 and args[4:] != ['--format', 'json']:
        raise DispatchDenied('invalid_arguments', '请使用 task har/request-upload --input JSON。')
    try:
        body = (har_arguments if args[1] == 'har' else upload_arguments)(strict_json(args[3]))
        if args[1] == 'har' and 'path' in body:
            match = re.fullmatch(r'uploads/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/payload', body['path'])
            if not match:
                raise DispatchDenied('path_denied', '请使用上传工具实际返回的 path 或 file_id，不能传入其他磁盘路径或网址。')
            # Treat an uploaded address as an opaque reference. Never open the
            # model's path; ownership, conversation and expiry are resolved below.
            body = {key: value for key, value in body.items() if key != 'path'}
            body['file_id'] = match.group(1)
    except ToolError as error:
        raise DispatchDenied(error.code, error.message) from None
    except (ValueError, TypeError):
        raise DispatchDenied('invalid_arguments', '任务工具输入必须是有效 JSON 对象。') from None
    return ['task', args[1], '--input', json.dumps(body, ensure_ascii=False), '--format', 'json']
