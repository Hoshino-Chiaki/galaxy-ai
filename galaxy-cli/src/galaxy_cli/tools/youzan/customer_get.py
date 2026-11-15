"""Complete single-customer data from the three captured read-only endpoints."""
import urllib.parse

from jsonschema import Draft202012Validator

from ...context import category_credentials
from ...registry import ToolError
from ...youzan_http import YouzanClient

INPUT = {"type": "object", "properties": {
    "account_id": {"type": "string", "minLength": 1, "maxLength": 160, "pattern": r"^[^\s\x00-\x1f\x7f]+$"},
    "account_type": {"type": "integer", "minimum": 0, "default": 1}},
    "required": ["account_id"], "additionalProperties": False}
OUTPUT = {"type": "object", "properties": {name: {"type": "object"} for name in ("customer", "relations", "behavior")},
          "required": ["customer"], "additionalProperties": False}
DESCRIPTION = (
    '获取单个客户的详细资料，调用 galaxy-cli youzan customer-get --input JSON。'
    '必填 account_id（字符串），取 customer-search 返回记录的 accountId；可选 account_type（非负整数，默认 1），取同一记录的 accountType。'
    '手机号、姓名和昵称不能直接代替 account_id，需先搜索确定客户。'
    '输出为 {customer,relations,behavior}，各部分保留接口完整 JSON，通常包含 code、msg、data；code=0 表示成功。'
    'customer.data 包含 baseInfo（基本信息）、customerInfo（自定义资料）、tagList（标签）、benefitList（权益）、'
    'assetInfo（资产）、tradeInfo（交易统计）等字段；relations.data 为关系人列表；behavior.data.allBehavior 为行为记录，'
    'recommendSwitch 为推荐开关。金额和时间按原字段单位解析，不自行猜测或换算。'
    '行为记录没有分页或时间筛选参数，不保证覆盖全部历史。主详情失败时仅返回 customer；附加部分失败时保留其他部分，'
    '失败部分返回上游错误或 {error:{code,message}}。'
)

CUSTOMER_INFO = 'https://store.youzan.com/v4/scrm/api/customer/manage/customerInfo'
RELATIONS = 'https://store.youzan.com/crm/member/customer/api/manage/get-relation-person-list'
BEHAVIOR = 'https://store.youzan.com/crm/member/customer/api/manage/get-personal-recommend-config-and-all-behavior'


def normalize(arguments):
    if not isinstance(arguments, dict) or not Draft202012Validator(INPUT).is_valid(arguments):
        raise ToolError('invalid_input', '客户详情需要 account_id，可选 account_type；其他参数不支持。')
    return {'account_id': arguments['account_id'], 'account_type': arguments.get('account_type', 1)}


def customer_get(client, arguments):
    arguments = normalize(arguments)
    query = urllib.parse.urlencode({'accountId': arguments['account_id'], 'accountType': arguments['account_type']})
    main = client._json(CUSTOMER_INFO + '?' + query)
    result = {'customer': main}
    if main.get('code') != 0:
        return client.redact(result)
    payload = main.get('data')
    base = payload.get('baseInfo', {}) if isinstance(payload, dict) else {}
    user_id = base.get('userId') if isinstance(base, dict) else None
    if user_id is None and arguments['account_type'] == 1:
        user_id = arguments['account_id']
    for name, url in [('relations', RELATIONS + '?' + urllib.parse.urlencode({'userId': user_id}) if user_id is not None else None),
                      ('behavior', BEHAVIOR + '?' + query)]:
        if url is None:
            result[name] = {'error': {'code': 'customer_id_unavailable', 'message': '主详情未提供查询关系人所需的 userId。'}}
            continue
        try:
            result[name] = client._json(url)
        except ToolError as error:
            result[name] = {'error': {'code': error.code, 'message': error.message}}
    return client.redact(result)


def register(registry):
    @registry.tool(name='youzan.customer-get', category='youzan', description=DESCRIPTION, input_schema=INPUT, output_schema=OUTPUT)
    def get(arguments):
        return YouzanClient(category_credentials('youzan')).customer_get(arguments)
