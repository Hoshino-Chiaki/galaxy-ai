import datetime as dt
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from jsonschema import Draft202012Validator
from ..context import read_context
from ..registry import ToolError

ENDPOINT = 'https://innerapi.scm121.com/api/inner/order/list'
TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 500, 'pattern': r'^[^\x00-\x1f]+$'}
LIST = {'type': 'array', 'items': TEXT, 'minItems': 1, 'uniqueItems': True}
ARRAY_FIELDS = {'online_order_nos': 'soIdList', 'distribution_online_order_nos': 'originalSoIds',
    'order_ids': 'oidList', 'shop_ids': 'shopIds', 'supplier_ids': 'supplierCoIds',
    'buyer_nicknames': 'buyerAccounts', 'phones': 'phones', 'receivers': 'receivers',
    'tracking_nos': 'trackNoList', 'flags': 'flags', 'exclude_flags': 'noFlags', 'order_statuses': 'orderStatus'}
INPUT = {'type': 'object', 'additionalProperties': False, 'properties': {
    **{key: LIST for key in ARRAY_FIELDS}, 'action': {'enum': ['query', 'count']},
    'product_name': TEXT, 'sku_codes': LIST, 'style_codes': LIST,
    'sku_match': {'enum': ['exact', 'fuzzy']}, 'style_match': {'enum': ['exact', 'fuzzy']},
    'start_time': {'type': 'string'}, 'end_time': {'type': 'string'},
    'time_type': {'enum': ['OrderDate', 'PayDate', 'DeliveryDate', 'ConfirmDate']},
    'provinces': LIST, 'exclude_provinces': {'type': 'boolean'},
    'after_sale_guarantees': {'type': 'array', 'minItems': 1, 'uniqueItems': True,
        'items': {'enum': ['分销售后保障', '分销无忧售后', '无分销售后保障']}},
    'page': {'type': 'integer', 'minimum': 1}, 'page_size': {'type': 'integer', 'minimum': 1, 'maximum': 50},
    'ascending': {'type': 'boolean'}}}
OUTPUT = {'type': 'object', 'required': ['count'], 'properties': {
    'count': {'type': 'object'}, 'query': {'type': 'object'}, 'page': {'type': 'integer'},
    'page_size': {'type': 'integer'}, 'has_more': {'type': 'boolean'}}, 'additionalProperties': False}


def normalize(body):
    if not isinstance(body, dict) or not Draft202012Validator(INPUT).is_valid(body):
        raise ToolError('invalid_input', '聚水潭订单参数无效，请查看 jushuitan.order-search 的输入说明。')
    for key in ('start_time', 'end_time'):
        if key in body:
            try:dt.datetime.strptime(body[key], '%Y-%m-%d %H:%M:%S')
            except ValueError:raise ToolError('invalid_input', '时间格式为 YYYY-MM-DD HH:mm:ss（北京时间）。') from None
    if ('start_time' in body) != ('end_time' in body) or body.get('start_time', '') > body.get('end_time', ''):
        raise ToolError('invalid_input', '起止时间需同时填写，且开始时间不能晚于结束时间。')
    for prefix in ('sku', 'style'):
        if prefix + '_match' in body and prefix + '_codes' not in body:
            raise ToolError('invalid_input', '编码匹配方式必须与相应编码一起提供。')
    return body


def payload(body):
    body = normalize(body)
    value = {'dateQueryType': body.get('time_type', 'OrderDate'), 'orderTypeEnum': 'ALL',
             'noteType': 'NOFILTER', 'orderByKey': 0, 'ascOrDesc': body.get('ascending', False),
             'pageNum': body.get('page', 1), 'pageSize': body.get('page_size', 50)}
    value.update({target: body[key] for key, target in ARRAY_FIELDS.items() if key in body})
    for key, target in [('product_name', 'itemName'), ('start_time', 'startTime'), ('end_time', 'endTime')]:
        if key in body:value[target] = body[key]
    for prefix, target in [('sku', 'skuIds'), ('style', 'styleCode')]:
        if prefix + '_codes' in body:
            exact = body.get(prefix + '_match', 'exact') == 'exact'
            value[target if exact else target + 'Vague'] = body[prefix + '_codes'] if exact else ','.join(body[prefix + '_codes'])
    if 'provinces' in body:
        value.update(receiveAddressJson=json.dumps({name: '' for name in body['provinces']}, ensure_ascii=False),
                     isExceptAddress='Except' if body.get('exclude_provinces') else 'Contain')
    if 'after_sale_guarantees' in body:value['orderLabels'] = body['after_sale_guarantees']
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):return None


class Client:
    def __init__(self, credentials=None, *, opener=None):
        self.credentials = credentials if credentials is not None else read_context().get('categories', {}).get('jushuitan', {})
        if not isinstance(self.credentials.get('access_token'), str) or not self.credentials['access_token']:
            raise ToolError('jushuitan_login_required', '请先在“我的账号 → 聚水潭”登录。')
        if any(c in self.credentials['access_token'] for c in '\r\n\x00'):
            raise ToolError('invalid_context', '聚水潭凭证格式无效。')
        self.opener = opener or build_opener(ProxyHandler({}), NoRedirect())

    def post(self, body):
        request = Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode(), method='POST', headers={
            'Content-Type': 'application/json;charset=UTF-8', 'Authorization': self.credentials['access_token'],
            'appcode': 'gyl.scm121.com', 'source': 'SUPPLIER', 'Origin': 'https://innerorder.scm121.com',
            'Referer': 'https://innerorder.scm121.com/distribute', 'User-Agent': 'Mozilla/5.0'})
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(32 * 1024 * 1024 + 1)
            if len(raw) > 32 * 1024 * 1024:raise ToolError('response_limit', '订单单页返回过大，请减少 page_size。', 1)
            value = json.loads(raw)
        except HTTPError as error:
            raise ToolError('jushuitan_http_error', '聚水潭返回 HTTP ' + str(error.code) + '，请检查渠道会话。', 1) from None
        except (URLError, OSError):
            raise ToolError('jushuitan_network_error', '聚水潭连接失败，未自动重试。', 1) from None
        except (ValueError, UnicodeError):
            raise ToolError('jushuitan_invalid_response', '聚水潭没有返回有效 JSON。', 1) from None
        if not isinstance(value, dict) or value.get('success') is not True or str(value.get('code')) != '0000':
            code = str(value.get('code')) if isinstance(value, dict) else ''
            message = ('聚水潭要求重新登录。' if code == '1006' else
                       '聚水潭要求安全检查，请在官方网页确认账号状态；未自动重复登录。' if code in ('1007', '3333') else
                       '聚水潭拒绝订单查询，请检查当前账号的供应链订单权限。')
            raise ToolError('jushuitan_upstream_rejected', message, 1)
        if not isinstance(value.get('data'), list):
            raise ToolError('jushuitan_invalid_response', '聚水潭订单返回结构异常，不能当作零订单。', 1)
        return value

    def orders(self, body):
        query = payload(body)
        count = self.post({**query, 'searchType': 2})
        total = count.get('total')
        if type(total) is not int or total < 0:
            raise ToolError('jushuitan_invalid_response', '聚水潭没有返回有效订单总数。', 1)
        if body.get('action') == 'count':return {'count': count}
        result = self.post({**query, 'searchType': 1})
        return {'query': result, 'count': count, 'page': query['pageNum'], 'page_size': query['pageSize'],
                'has_more': query['pageNum'] * query['pageSize'] < total}
