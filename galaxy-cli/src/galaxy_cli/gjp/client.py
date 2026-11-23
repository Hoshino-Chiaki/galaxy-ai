"""Reproduce captured report initialization and paging without browser state."""
import copy
import gzip
import json
import uuid
from importlib.resources import files
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from ..context import read_context
from ..registry import ToolError
from .schemas import normalize

ORIGIN = 'http://cdmzzaetsc.szbs001.gjpfz.com'
PAGER = '/Carpa.Web/Carpa.Web.Script.DataService.ajax/GetPagerDataEx'
INVENTORY = '/CarpaServer/CarpaServer.Stock.GoodsStockListService.ajax/_GetServerContextData'
SELECTOR = '/CarpaServer/CarpaServer.Selector.ReportSelectorVService.ajax/_GetServerContextData'
ATTRIBUTES = '/CarpaServer/CarpaServer.SaleSystem.SaleBtypeOrderService.ajax/GetAssistType'
TEMPLATES = json.loads(files(__package__).joinpath('templates.json').read_text(encoding='utf-8'))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def rows(table):
    if not isinstance(table, dict) or not isinstance(table.get('fields'), list) or not isinstance(table.get('rows'), list):
        raise ToolError('gjp_invalid_response', '管家婆没有返回有效的表格数据。')
    fields = table['fields']
    if any(not isinstance(f, str) for f in fields) or len(set(fields)) != len(fields):
        raise ToolError('gjp_invalid_response', '管家婆表格字段异常。')
    if any(not isinstance(row, list) or len(row) != len(fields) for row in table['rows']):
        raise ToolError('gjp_invalid_response', '管家婆表格列数异常。')
    return [dict(zip(fields, row)) for row in table['rows']]


def page_result(table, total, body):
    result = rows(table)
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ToolError('gjp_invalid_response', '管家婆没有返回有效的记录总数。')
    first = (body['page'] - 1) * body['page_size']
    return {'rows': result, 'total': total, 'count': len(result),
            'page': body['page'], 'page_size': body['page_size'], 'has_more': first + len(result) < total}


class Client:
    def __init__(self, credentials=None):
        credentials = credentials or read_context().get('categories', {}).get('gjp', {})
        if (credentials.get('domain', '').rstrip('/') != ORIGIN or not credentials.get('sid')
                or not isinstance(credentials.get('cookie'), str) or not credentials['cookie']
                or '\r' in credentials['cookie'] or '\n' in credentials['cookie']):
            raise ToolError('guanjia_login_required', '请先在“我的账号 → 管家婆”登录。')
        self.cookie = credentials['cookie']
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def post(self, path, payload):
        if path not in (PAGER, INVENTORY, SELECTOR, ATTRIBUTES):
            raise ToolError('gjp_request_denied', '管家婆请求地址未获准。')
        req = Request(ORIGIN + path, data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(), headers={
            'Accept': '*/*', 'Content-Type': 'application/json; charset=utf-8',
            'Referer': ORIGIN + '/ClientBin/CarpaClient.xap?v=475612175',
            'X-ClientType': 'SLJson', 'X-JSONFormat': 'true', 'Cookie': self.cookie, 'Pragma': 'no-cache',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64; Trident/7.0; rv:11.0) like Gecko'})
        try:
            with self.opener.open(req, timeout=15) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ToolError('gjp_response_limit', '管家婆单页响应过大，请缩小 page_size。')
                if response.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
        except HTTPError as error:
            raise ToolError('gjp_http_error', f'管家婆接口返回 HTTP {error.code}。') from None
        except (URLError, TimeoutError, OSError):
            raise ToolError('gjp_unavailable', '管家婆接口连接失败或超时。') from None
        try:
            value = json.loads(raw.decode('utf-8-sig'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError):
            raise ToolError('gjp_invalid_response', '管家婆接口没有返回有效 JSON。') from None
        message = (value.get('Message') or value.get('message') or '') if isinstance(value, dict) else value if isinstance(value, str) else ''
        if any(word in message for word in ('会话超时', '重新登录', '未登录', '登录超时', '没有指定连接字符串')):
            raise ToolError('guanjia_session_expired', '管家婆会话已失效，请到“我的账号 → 管家婆”重新登录。')
        if isinstance(value, dict) and any(k in value for k in ('ExceptionType', 'StackTrace', 'Message')):
            raise ToolError('gjp_api_error', '管家婆接口返回业务错误，未获得查询数据。')
        return value

    def initialize(self, name, path, overrides=None):
        payload = copy.deepcopy(TEMPLATES[name])
        payload['formId'] = uuid.uuid4().hex[:8]
        payload['clientInfo']['__FormGuid'] = str(uuid.uuid4())
        if overrides:
            payload['clientInfo']['__Params'].update(overrides)
        value = self.post(path, payload)
        if not isinstance(value, dict):
            raise ToolError('gjp_invalid_context', '管家婆没有返回报表上下文。')
        return value

    def pager(self, source, query, body):
        pager_id = source.get('PagerId')
        if not isinstance(pager_id, str) or not pager_id:
            raise ToolError('gjp_invalid_context', '管家婆上下文没有返回分页标识。')
        value = self.post(PAGER, {'pagerId': pager_id, 'queryParams': query, 'orders': None, 'filter': None,
                                 'first': (body['page'] - 1) * body['page_size'],
                                 'count': body['page_size'], 'isFirst': False})
        if not isinstance(value, dict):
            raise ToolError('gjp_invalid_response', '管家婆没有返回分页数据。')
        return page_result(value.get('itemList'), value.get('itemCount'), body)

    def inventory(self, body):
        body = normalize('inventory', body)
        query = copy.deepcopy(TEMPLATES['inventory_query'])
        query.update(ctypeid=body['organization_id'], cfullname='', ktypeid=body['warehouse_id'],
                     ptypeid=body['product_id'], btypeid=body['supplier_id'], ptypebar=body['barcode'],
                     Showzero=body['include_zero'], isunique=body['unique_barcode'], stocktype=body['stock_type'],
                     pricetypeid='', FilterIndex='0')
        context = self.initialize('inventory_init', INVENTORY, query)
        if body['action'] == 'options':
            return {'data': {'reference_price': '库存均价', 'stock_types': context.get('stocktype', []),
                             'product_status': [{'value': '0', 'text': '全部'}],
                             'note': '机构用 organizations 查询，商品分类用 product-info 的 products 查询；辅助属性暂只支持类别清单。'}}
        source = context.get('GoodsStockListDataSource', {})
        data = self.pager(source, query, body)
        data.update(reference_price='库存均价', filters={k: v for k, v in body.items() if k not in ('page', 'page_size', 'action')},
                    field_meanings={'ptypeid_barcode': '商品条码', 'ptypeid_pusercode': '货号',
                                    'ptypeid_pfullname': '商品全名', 'avgtotal': '库存均价',
                                    'qty': '实际数量', 'retailprice': '机构零售价'})
        return {'data': data}

    def selection(self, body, *, product=False):
        name = 'product-info' if product else 'organizations'
        body = normalize(name, body)
        # Both captured selector root tables identify their parent as 00000.
        # Explicitly reset it: this server otherwise remembers the last browser selection.
        parent_id = body['parent_id'] or '00000'
        context = self.initialize('product_init' if product else 'organization_init', SELECTOR, {'parid': parent_id})
        source = context.get('typeds', {})
        query = copy.deepcopy(source.get('QueryParams', {}))
        query['parid'] = parent_id
        if not product:
            query['bc_limit'] = True
        data = self.pager(source, query, body)
        id_key, name_key, count_key = ('ptypeid', 'pfullname', 'psonnum') if product else ('bctypeid', 'bcfullname', 'bcsonnum')
        data['options'] = [{'id': row.get(id_key), 'name': row.get(name_key), 'child_count': row.get(count_key),
                            'parent_id': row.get('parid')} for row in data['rows']]
        data['parent_id'] = body['parent_id']
        return {'data': data}

    def product_info(self, body):
        body = normalize('product-info', body)
        if body['action'] == 'products':
            return self.selection(body, product=True)
        value = self.post(ATTRIBUTES, {})
        if not isinstance(value, list) or any(not isinstance(v, dict) or not {'id', 'title'} <= v.keys() for v in value):
            raise ToolError('gjp_invalid_response', '管家婆辅助属性列表结构异常。')
        return {'data': {'attributes': value, 'count': len(value),
                         'note': '这是辅助属性类别，不是品牌等类别下的具体选项，也不是商品条码明细。'}}
