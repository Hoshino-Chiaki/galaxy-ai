"""Read-only operating report options and data queries."""
import datetime as dt
import json
import time
import urllib.parse
import uuid

from jsonschema import Draft202012Validator

from ...context import category_credentials
from ...registry import ToolError
from ...youzan_http import YouzanClient

GATEWAY = 'https://gateway.youzan.com/api/'
DEPARTMENTS = 'https://carmen.youzan.com/gw/web/youzan.sam.chain.department/1.0.0/find'
SALES_UNITS = GATEWAY + 'youzan.retail.finance.org.sales.page/3.0.0'
CHANNELS = GATEWAY + 'youzan.retail.finance.statisticsreport.ordersource.query/1.0.0'
REPORT = GATEWAY + 'youzan.retail.finance.statisticsreport.shopperformance.query.asyn/1.0.3'
POLL = GATEWAY + 'youzan.retail.finance.asyn.query.result/1.0.0'
DIMENSIONS = [{'value': 4, 'label': '销售单元'}, {'value': 5, 'label': '销售渠道'}, {'value': 0, 'label': '时段(小时)'}]
PERIODS = [{'value': 'day', 'label': '日报'}, {'value': 'week', 'label': '周报'}, {'value': 'month', 'label': '月报'}, {'value': 'custom', 'label': '自定义时间'}]
OPTION_KEYS = {'departments', 'sales_units', 'sales_channels', 'time_intervals', 'dimensions', 'periods'}
ID = {'type': 'string', 'pattern': '^[1-9][0-9]{0,19}$'}
QUERY = {'type': 'object', 'properties': {
    'action': {'const': 'query'}, 'period': {'enum': ['day', 'week', 'month', 'custom'], 'default': 'custom'},
    'start_date': {'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'},
    'end_date': {'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'},
    'dimensions': {'type': 'array', 'minItems': 1, 'maxItems': 3, 'uniqueItems': True, 'items': {'type': 'integer', 'enum': [0, 4, 5]}},
    'department_id': ID,
    'sales_kdt_ids': {'type': 'array', 'maxItems': 100, 'uniqueItems': True, 'items': ID},
    'sales_channels': {'type': 'array', 'maxItems': 50, 'uniqueItems': True, 'items': {'type': 'string', 'pattern': '^[a-zA-Z0-9_-]{1,100}$'}},
    'time_intervals': {'type': 'array', 'maxItems': 24, 'uniqueItems': True, 'items': {'type': 'string', 'pattern': r'^(0[0-9]|1[0-9]|2[0-3])$'}},
    'page': {'type': 'integer', 'minimum': 1, 'maximum': 10000, 'default': 1},
    'page_size': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 20}},
    'required': ['action', 'start_date', 'end_date', 'dimensions'], 'additionalProperties': False}
OPTIONS = {'type': 'object', 'properties': {'action': {'const': 'options'}, 'include_closed': {'type': 'boolean', 'default': True}},
           'required': ['action'], 'additionalProperties': False}
INPUT = {'type': 'object', 'oneOf': [OPTIONS, QUERY]}
OUTPUT = {'type': 'object'}
DESCRIPTION = (
    '获取经营报表的筛选选项和完整数据。提供两个命令：galaxy-cli youzan business-report options 获取选项，galaxy-cli youzan business-report query --input JSON 查询数据。'
    'options 无必填参数，可选 --input {"include_closed":true}，默认包含已打烊销售单元；输出 departments（部门接口完整响应分页数组）、'
    'sales_units（销售单元接口完整响应分页数组）、sales_channels（渠道接口完整响应）、time_intervals（小时选项）、dimensions（维度选项）、periods（周期选项）。'
    '部门 ID 取 departments[].response.items[].deptId，销售单元 ID 取 sales_units[].data.items[].kdt_id，渠道代码取 sales_channels.data[].code，标为全部的选项不要填写筛选 ID。'
    'query 必填 start_date、end_date（YYYY-MM-DD，含首尾日期，开始不晚于结束，最多 366 天）和 dimensions（不重复的整数数组，至少一项：4=销售单元、5=销售渠道、0=时段小时）。'
    '可选 period 为 day/week/month/custom，默认 custom；日期范围按传入值查询，不自动扩展到整周或整月。'
    '比较今天和昨天时，今天仅包含截至实际查询时刻的数据，昨天为全天数据，分析时须说明两者统计时长不同，不能把今天当作完整一天。'
    '可选 department_id 为单个部门 ID 字符串，sales_kdt_ids 为销售单元 ID 字符串数组（最多 100 项），sales_channels 为渠道代码字符串数组（最多 50 项），'
    'time_intervals 为两位小时字符串数组（00—23，如 ["09","10"]），分别表示 09:00—09:59、10:00—10:59；四种筛选可组合，未传表示全部，数组传 [] 也表示全部。'
    'page 默认 1，page_size 默认 20、范围 1—50；只返回本页，继续读取须增加 page。'
    'query 输出接口完整原始 JSON，不裁剪字段；通常外层 code=200 且 success=true，data.status=1 表示完成，'
    'data.data.code=200 且 data.data.success=true 时，数据在 data.data.data.items，分页在 data.data.data.paginator，totalCount 为明细总数。'
    'isTotal=true 的行为合计，可能每页重复，统计或导出时不能重复累加；金额展示字段按原有元单位和字符串格式解析，百分比按原有百分号解析，空值不当作 0。'
    '工具会等待异步计算；若返回 data.status 非 1，表示尚未完成或失败，不能解释为零数据。'
    '选项中的接口错误保留原响应；本地校验或连接失败返回 error.code、error.message。'
)


def normalize(arguments):
    if not isinstance(arguments, dict) or not Draft202012Validator(INPUT).is_valid(arguments):
        raise ToolError('invalid_input', '报表需选择 options 或 query；query 必填 start_date、end_date 和 dimensions，其他参数请参照说明。')
    result = dict(arguments)
    if result['action'] == 'options':
        result.setdefault('include_closed', True)
    else:
        try:
            start, end = (dt.date.fromisoformat(result[k]) for k in ('start_date', 'end_date'))
            if start > end or (end - start).days >= 366:
                raise ValueError()
        except ValueError:
            raise ToolError('invalid_input', '开始和结束日期必须有效、顺序正确，且包含首尾不超过 366 天。') from None
        result.setdefault('period', 'custom')
        result.setdefault('page', 1)
        result.setdefault('page_size', 20)
    return result


def request(client, endpoint, params=None):
    values = {'source': 'WEB_BACK_END', 'retail_source': 'WEB-RETAIL-AJAX', 'idempotent_no': uuid.uuid4().hex}
    cookies = client.credentials.get('cookies', {})
    if endpoint.startswith(GATEWAY):
        access = cookies.get('access_token')
        if not access:
            raise ToolError('connection_required', '报表需要店铺业务凭证，请在控制台手动刷新当前用户的有赞连接。', 1)
        values['access_token'] = access
    for key in ('carmen_csrf_token', 'bifrost_csrf_token'):
        if cookies.get(key):
            values[key] = cookies[key]
    values.update(params or {})
    values = {k: json.dumps(v, separators=(',', ':')) if isinstance(v, (list, bool)) else v for k, v in values.items()}
    return client._json(endpoint + '?' + urllib.parse.urlencode(values))


def pages(client, endpoint, params, *, department=False):
    responses = []
    for page in range(1, 21):
        response = request(client, endpoint, {**params, 'page_no': page})
        responses.append(response)
        payload = response.get('response') if department else response.get('data')
        if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
            return responses  # Keep a native error response intact.
        if not department and (response.get('code') != 200 or response.get('success') is not True):
            return responses
        rows, paginator = payload['items'], payload.get('paginator', {})
        total = paginator.get('totalCount' if department else 'total_count')
        size = paginator.get('pageSize' if department else 'page_size')
        if not rows or isinstance(total, int) and isinstance(size, int) and page * size >= total:
            return responses
        if not isinstance(total, int) or not isinstance(size, int) or size <= 0:
            raise ToolError('invalid_options', '选项分页信息异常，不能把部分选项当作全部。', 1)
    raise ToolError('options_limit', '选项超过单次分页上限，未返回不完整选项。', 1)


def departments(client):
    return pages(client, DEPARTMENTS, {'org_id': client.credentials['kdt_id']}, department=True)


def sales_units(client, include_closed=True):
    result = []
    statuses = [['try', 'valid', 'protect'], ['close']] if include_closed else [['try', 'valid', 'protect']]
    for selected in statuses:
        result += pages(client, SALES_UNITS, {'query_type': 2, 'page_size': 100, 'fill_all': True, 'force_fill': True,
            'none_need_ability': True, 'store_statuses': selected})
    return result


def all_rows(responses, department=False):
    result = []
    for response in responses:
        payload = response.get('response') if department else response.get('data')
        if not isinstance(payload, dict) or not isinstance(payload.get('items'), list) or not department and (response.get('code') != 200 or response.get('success') is not True):
            raise ToolError('options_unavailable', '无法读取筛选选项，请检查当前有赞连接及报表权限。', 1)
        result.extend(payload['items'])
    return result


def options(client, arguments):
    return client.redact({'departments': departments(client), 'sales_units': sales_units(client, arguments['include_closed']),
        'sales_channels': request(client, CHANNELS),
        'time_intervals': [{'value': f'{hour:02}', 'label': f'{hour:02}:00 ~ {hour:02}:59'} for hour in range(24)],
        'dimensions': DIMENSIONS, 'periods': PERIODS})


def query(client, arguments):
    if arguments.get('department_id'):
        identifiers = {str(x.get('deptId')) for x in all_rows(departments(client), True)}
        if arguments['department_id'] not in identifiers:
            raise ToolError('department_denied', '部门不在当前账号的选项中，请先获取选项。', 1)
    if arguments.get('sales_kdt_ids'):
        identifiers = {str(x.get('kdt_id')) for x in all_rows(sales_units(client))}
        if not set(arguments['sales_kdt_ids']) <= identifiers:
            raise ToolError('sales_unit_denied', '销售单元不在当前账号的选项中，请先获取选项。', 1)
    if arguments.get('sales_channels'):
        response = request(client, CHANNELS)
        if response.get('code') != 200 or response.get('success') is not True or not isinstance(response.get('data'), list):
            raise ToolError('options_unavailable', '无法读取当前账号的销售渠道。', 1)
        if not set(arguments['sales_channels']) <= {x.get('code') for x in response['data']}:
            raise ToolError('channel_denied', '销售渠道不在当前选项中，请先获取选项。', 1)
    params = {'query_type': 2, 'force_ck': True, 'business_time_type': 0,
        'start_date': arguments['start_date'], 'end_date': arguments['end_date'], 'dimensions': sorted(arguments['dimensions']),
        'sales_kdt_ids': [int(x) for x in arguments.get('sales_kdt_ids', [])], 'sales_channels': arguments.get('sales_channels', []),
        'time_intervals': arguments.get('time_intervals', []), 'page_no': arguments['page'], 'page_size': arguments['page_size']}
    if arguments['period'] != 'custom':
        params['report_type'] = {'day': 1, 'week': 2, 'month': 3}[arguments['period']]
    if arguments.get('department_id'):
        params['department_id'] = arguments['department_id']
    response = request(client, REPORT, params)
    deadline = time.monotonic() + 25
    while response.get('code') == 200 and response.get('success') is True:
        payload = response.get('data')
        if not isinstance(payload, dict) or payload.get('status') in (1, -1):
            break
        key = payload.get('queryKey')
        if not isinstance(key, str) or not 1 <= len(key) <= 300 or time.monotonic() >= deadline:
            break
        time.sleep(1)
        response = request(client, POLL, {'query_key': key})
    return client.redact(response)


def succeeded(action, result):
    if not isinstance(result, dict) or 'error' in result:
        return False
    if action == 'options':
        if set(result) != OPTION_KEYS:
            return False
        try:
            all_rows(result['departments'], True)
            all_rows(result['sales_units'])
            channels = result['sales_channels']
            return channels.get('code') == 200 and channels.get('success') is True and isinstance(channels.get('data'), list)
        except (ToolError, TypeError, AttributeError):
            return False
    payload = result.get('data')
    inner = payload.get('data') if isinstance(payload, dict) else None
    return (result.get('code') == 200 and result.get('success') is True and payload.get('status') == 1
        and isinstance(inner, dict) and inner.get('code') == 200 and inner.get('success') is True and isinstance(inner.get('data'), dict)) if isinstance(payload, dict) else False


def register(registry):
    @registry.tool(name='youzan.business-report', category='youzan', description=DESCRIPTION, input_schema=INPUT, output_schema=OUTPUT)
    def business_report(arguments):
        arguments = normalize(arguments)
        # CRM discovery origin identifies a page, not report API compatibility.
        # Reports use only the fixed merchant gateway/Carmen endpoints above.
        client = YouzanClient({**category_credentials('youzan'), 'origin': 'https://store.youzan.com'})
        return options(client, arguments) if arguments['action'] == 'options' else query(client, arguments)
