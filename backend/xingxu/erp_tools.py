"""ERP gateway: reuses the personnel API with the current user's bearer token."""
import json
import httpx
from jsonschema import Draft202012Validator
from galaxy_cli.registry import ToolError
from galaxy_cli.tools.erp import employee_create, department_create, employee_move
MODULES = {"employee-create": employee_create, "department-create": department_create, "employee-move": employee_move}
from .tool_context import DispatchDenied, strict_json

BASE_URL = 'http://127.0.0.1:18443'


def arguments(args):
    if args[0].startswith('erp.'):
        args = ['erp', args[0].split('.', 1)[1], *args[1:]]
    if args[0] != 'erp':
        return None
    if (len(args) not in (4, 6) or args[1] not in MODULES or args[2] != '--input'
            or len(args) == 6 and args[4:] != ['--format', 'json']):
        raise DispatchDenied('invalid_arguments', '请使用 erp 工具名 --input JSON。')
    try:
        body = MODULES[args[1]].normalize(strict_json(args[3]))
    except ToolError as error:
        raise DispatchDenied(error.code, error.message) from None
    except (ValueError, TypeError):
        raise DispatchDenied('invalid_arguments', 'ERP 输入必须是有效 JSON 对象。') from None
    return ['erp', args[1], '--input', json.dumps(body, ensure_ascii=False), '--format', 'json']


async def run(dispatcher, context, command, canonical):
    dispatcher._check_command(context, command, canonical)
    body = strict_json(canonical[3])
    name = canonical[1]
    action = body.pop('action', 'create')
    options = action == 'options'
    path = '/api/v1/erp/departments' if options or name == 'department-create' else '/api/v1/erp/employees'
    method = 'GET' if options else 'POST'
    if name == 'employee-move' and not options:
        method = 'PUT'
        path += '/' + body['employee_id'] + '/department'
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(30, connect=3), transport=getattr(dispatcher, 'erp_transport', None)) as client:
            headers = {'Authorization': 'Bearer ' + context.session_token}
            response = await client.request(method, path, headers=headers,
                **({} if options else {'json': {'department_id': body['department_id']} if name == 'employee-move' else body}))
            employee_page = None
            if options and name == 'employee-move' and response.status_code == 200:
                dispatcher._check_command(context, command, canonical)
                employee_response = await client.get('/api/v1/erp/employees', headers=headers,
                    params={**body, 'summary': 'true'})
                if employee_response.status_code != 200 or len(employee_response.content) > 128 * 1024:
                    raise DispatchDenied('erp_unavailable', '员工候选查询失败，请检查登录及人员管理权限。')
                try:
                    employee_page = employee_response.json()
                except ValueError:
                    raise DispatchDenied('erp_unavailable', '员工候选返回结构异常，请重试。') from None
    except httpx.RequestError:
        raise DispatchDenied('erp_unavailable' if options else 'erp_result_unconfirmed',
            'ERP 查询暂时不可用，请稍后重试。' if options else '操作结果未确认，请先到 ERP 人员管理核对，勿盲目重复提交。') from None
    dispatcher._check_command(context, command, canonical)
    if response.status_code in (401, 403):
        raise DispatchDenied('session_expired' if response.status_code == 401 else 'permission_denied',
            '登录已失效，请重新登录。' if response.status_code == 401 else '当前账号没有人员管理权限。')
    if len(response.content) > 128 * 1024:
        raise DispatchDenied('erp_result_unconfirmed', 'ERP 返回内容异常，请到人员管理核对操作结果。')
    try:
        data = response.json()
    except ValueError:
        raise DispatchDenied('erp_result_unconfirmed', 'ERP 返回内容异常，请到人员管理核对操作结果。') from None
    if response.status_code not in (200, 201):
        # Validation errors may contain the original password in `input`.
        # Never forward upstream bodies or exception text into tool output.
        code = data.get('error', {}).get('code') if isinstance(data, dict) and isinstance(data.get('error'), dict) else None
        errors = {'account_exists': '登录账号已存在，未覆盖已有账号或密码。',
                  'department_not_found': '部门不存在，请先用 action=options 查询已有部门。',
                  'password_required': '创建账号需要提供至少8位密码。', 'department_exists': '当前工作空间已有同名部门。', 'employee_not_found': '员工不存在或不属于当前工作空间。'}
        if code in errors:
            raise DispatchDenied(code, errors[code])
        if response.status_code == 409:
            code = 'department_exists' if name == 'department-create' else 'account_exists'
            raise DispatchDenied(code, errors[code])
        raise DispatchDenied('invalid_input' if response.status_code == 422 else 'erp_result_unconfirmed',
            'ERP 参数校验未通过，请核对字段及密码要求。' if response.status_code == 422 else '操作结果未确认，请到 ERP 人员管理核对。')
    try:
        if name == 'department-create':
            assert data['name'] == body['name']
            result = {'created': True, **{key: data[key] for key in ('id','name','employee_count')}}
        elif name == 'employee-move':
            if options:
                result = {'action': 'options', 'departments': [row for row in data['departments'] if row['id'] is not None], **{key: employee_page[key] for key in ('employees','total','page','page_size')}}
            else:
                assert data['employee']['id'].lower() == body['employee_id'].lower() and data['department_id'].lower() == body['department_id'].lower()
                result = {'action': 'move', **data}
        elif action == 'options':
            result = {'action': action, 'created': False, 'password_min_length': 8, 'default_role': 'employee',
                      'departments': [{'id': row['id'], 'name': row['name']} for row in data['departments'] if row['id'] is not None]}
        else:
            assert data['account'] == body['account'] and data['department'] == body['department']
            result = {'action': action, 'created': True, 'employee': {
                **{key: data[key] for key in ('id', 'user_id', 'name', 'account', 'department', 'job_title', 'phone', 'employment_status')},
                'role': 'employee'}}
        if not Draft202012Validator(MODULES[name].OUTPUT).is_valid(result):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError, AssertionError):
        raise DispatchDenied('erp_result_unconfirmed', 'ERP 返回结构异常，请到人员管理核对结果。') from None
    return result
