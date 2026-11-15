"""Expose only the company directory result prepared by the authenticated server."""
import copy

from jsonschema import Draft202012Validator

from ...context import read_context
from ...feishu_errors import raise_upstream
from ...registry import ToolError


PAGES = {'page_size': {'type': 'integer', 'minimum': 1, 'maximum': 50},
         'page_token': {'type': 'string', 'minLength': 1, 'maxLength': 2048}}
DEPARTMENT = {'type': 'string', 'pattern': r'^(0|od-[A-Za-z0-9_-]{1,128})$'}
USER = {'type': 'string', 'pattern': r'^[A-Za-z0-9_-]{1,128}$'}


def shape(action, fields, required):
    return {'type': 'object', 'properties': {'action': {'const': action}, **fields},
            'required': required, 'additionalProperties': False}


INPUT = {'type': 'object', 'oneOf': [
    shape('scope', PAGES, []),
    shape('user', {'user_id': USER}, ['action', 'user_id']),
    shape('users', {'department_id': DEPARTMENT, **PAGES}, ['action', 'department_id']),
    shape('departments', {'department_id': DEPARTMENT, 'fetch_child': {'type': 'boolean'}, **PAGES},
          ['action', 'department_id'])]}
OUTPUT = {'type': 'object', 'properties': {'code': {'const': 0}, 'data': {'type': 'object'},
    'coverage': {'const': 'application_authorized_scope'}}, 'required': ['code', 'data', 'coverage']}
DESCRIPTION = (
    '读取当前企业应用实际授权的人员、部门及分页信息，调用 galaxy-cli feishu authorized-users --input JSON，输入可省略。'
    'action 默认 scope：返回原始 data.user_ids、department_ids、group_ids、has_more/page_token；user_ids 是可直接用于考勤 employee_type=employee_id 的真实员工 ID，无需个人 contact:user:search 授权。'
    '可选 page_size 为 1—50，默认 50；page_token 必须用上一页实际返回的值。scope 返回部门不代表已展开部门成员：'
    'action=departments 必填 department_id（scope 实际返回的 od- 标识；根部门是 0，只有实际获准访问时可用），可选 fetch_child 默认 false；'
    'action=users 必填 department_id，返回该部门直属员工；两者均支持分页。按实际部门逐个展开、逐页读取并按 user_id 去重。'
    'action=user 必填 user_id，获取该授权用户资料及姓名、工号；该动作不接受分页字段。'
    '成功返回官方完整 JSON，额外 coverage=application_authorized_scope 表示仅当前应用授权范围，不代表全公司；has_more=false 只代表本次列表分页结束，仍需展开部门。'
    'group_ids 本工具不展开，存在未展开用户组时须说明覆盖范围未完整。公司范围读取需要 Galaxy 公司考勤读取权限，当前默认管理员可用，普通员工不获此权限。'
    '失败保留上游 upstream_code 和具体说明；不要用猜测的 ID 探测管理身份，也不要把关键词搜索当成全员枚举。'
    '取得真实名单后再调用 feishu.attendance，每次最多 50 人；旧会话中的“必须企业超级管理员”结论已不适用。'
)


def normalize(value):
    if not isinstance(value, dict) or not Draft202012Validator(INPUT).is_valid(value):
        raise ToolError('invalid_input', '授权名单输入无效，请按 action 的必填字段和分页结构调用。')
    result = dict(value)
    result.setdefault('action', 'scope')
    if any(isinstance(v, str) and (v.startswith('@') or any(ord(c) < 32 for c in v)) for v in result.values()):
        raise ToolError('invalid_input', '授权名单参数不接受本地文件引用或控制字符。')
    return result


def invoke(value):
    normalize(value)
    credentials = read_context().get('categories', {}).get('feishu', {})
    result = credentials.get('directory_result')
    if credentials.get('directory_access') != 'application' or not isinstance(result, dict):
        raise ToolError('server_context_required', '授权名单须通过已登录并获公司读取权限的 galaxy_cli 调用。', 1)
    status = credentials.get('directory_http_status')
    if type(result.get('code')) is not int or result['code'] != 0 or status not in range(200, 300):
        raise_upstream(result, http_status=status)
    if not isinstance(result.get('data'), dict):
        raise ToolError('feishu_invalid_response', '飞书未返回有效的授权名单数据。', 1)
    result = copy.deepcopy(result)
    result['coverage'] = 'application_authorized_scope'
    return result


def register(registry):
    registry.register(name='feishu.authorized-users', category='feishu', description=DESCRIPTION,
                      input_schema=INPUT, output_schema=OUTPUT, handler=invoke)
