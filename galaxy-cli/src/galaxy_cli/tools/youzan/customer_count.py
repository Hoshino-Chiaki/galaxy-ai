"""Read the customer-list total without returning customer records."""
from ...context import category_credentials
from ...registry import ToolError
from ...youzan_http import YouzanClient

INPUT = {'type': 'object', 'properties': {}, 'additionalProperties': False}
OUTPUT = {'type': 'object', 'properties': {'total': {'type': 'integer', 'minimum': 0}},
          'required': ['total'], 'additionalProperties': False}
DESCRIPTION = ('只读获取有赞店铺的客户总数，命令为 galaxy-cli youzan customer-count，可省略 --input 或传入空对象 {}，'
    '不接受客户筛选、分页、凭证或店铺 ID 等业务参数。通过 galaxy_cli 调用时，在顶层 shops 使用 all 查询当前账号全部授权店铺，'
    'current 查询默认店铺，或使用店铺 ID/名称数组查询指定范围；服务器逐店提供会话并执行。'
    '支持零售、微商城和 CRM 客户列表版本，每店只请求第一页 1 条，读取接口总数，不遍历客户，不返回任何客户个人资料。'
    '单店输出仅包含 total，表示查询时当前账号在该店铺可见的客户总数，为非负整数；total=0 表示接口明确返回零。'
    '多店调用由网关返回 shops 数组，各项的 kdt_id、shop_name 标明店铺，data.total 为该店数量，error 表示该店失败，'
    'partial=true 表示未全部完成；权限不足、会话失效或接口缺少有效总数都按失败处理，不能解释为零。'
    '各店分别统计，总部与分店客户可能重叠，不能直接相加为跨店去重人数。')


def normalize(arguments):
    if not isinstance(arguments, dict) or arguments:
        raise ToolError('invalid_input', '客户总数工具只接受空对象 {}，店铺范围通过 galaxy_cli 顶层 shops 指定。')
    return {}


def count(client, arguments):
    normalize(arguments)
    result = client.search({'page': 1, 'page_size': 1})
    if not isinstance(result, dict) or type(result.get('code')) is not int or result['code'] != 0:
        raise ToolError('upstream_error', '有赞客户总数查询失败，请检查店铺权限或刷新会话。', 1)
    data = result.get('data')
    total = data.get('total') if isinstance(data, dict) else None
    if type(total) is not int or total < 0:
        raise ToolError('invalid_upstream_response', '有赞未返回有效的客户总数，不能将本次结果视为零。', 1)
    return {'total': total}


def register(registry):
    @registry.tool(name='youzan.customer-count', category='youzan', description=DESCRIPTION,
        input_schema=INPUT, output_schema=OUTPUT,
        examples=('galaxy-cli youzan customer-count', 'galaxy-cli youzan customer-count --input \'{}\''))
    def customer_count(arguments):
        return count(YouzanClient(category_credentials('youzan'), customer_search_only=True), arguments)
