from ...contracts.youzan import INPUT, normalize, request_body
from ...context import category_credentials
from ...youzan_http import YouzanClient, CUSTOMERS
from ...registry import ToolError

OUTPUT = {"type": "object"}

DESCRIPTION = '只读搜索服务器为本次调用选定的有赞店铺客户，支持零售、微商城及 CRM 客户列表版本，调用方式为 galaxy-cli youzan customer-search --input JSON。所有业务参数均可选，省略某项就不使用该项筛选：keyword 为字符串，统一搜索手机号、姓名、昵称或渠道 ID，不保证精确匹配；last_trade_start、last_trade_end 为上次消费时间的开始和结束，customer_created_start、customer_created_end 为成为客户时间的开始和结束，member_created_start、member_created_end 为成为会员时间的开始和结束，三个时间范围可分别使用，也可组合，单边界也允许填写。时间统一按北京时间，格式为 YYYY-MM-DD 或 YYYY-MM-DD HH:mm:ss；只写日期时，开始补 00:00:00，结束补 23:59:59，同一范围开始不能晚于结束。branch_names 为归属分店名称数组，branch_ids 为归属分店 ID 字符串数组，最多各 20 项；名称优先精确匹配，否则仅接受唯一的包含匹配，有歧义时需明确名称或 ID；只允许当前账号有数据权限的分店，多个分店取并集。mobile_authorized 为布尔值，true 表示已授权手机号，false 表示未授权，省略表示全部；wecom_friend 为布尔值，true 对应页面中企微好友的“是”，false 对应页面中的“否”，省略表示全部，这不是企业微信授权状态。page 为从 1 开始的整数页码，默认 1，最大 10000；page_size 为每页数量，默认 20，范围 1—50，可调整 page 继续查询。不同筛选条件可组合使用，不传任何筛选条件时查询所选店铺下当前账号可访问的客户。输出保留接口完整 JSON，包括 code、msg、data；code=0 表示成功，data.list 为本页客户，data.total 为匹配总数。客户列表中的 accountId/accountType 可作为 customer-get 输入，但客户详情工具目前仅适配零售 CRM；其他版本会明确提示不支持。非零售版本暂不支持 branch_names/branch_ids 分店筛选，需要按店铺范围选择店铺。字段按原值和原单位解析，不把空值或缺失字段当成 0。'


def register(registry):
    @registry.tool(name="youzan.customer-search", category="youzan", description=DESCRIPTION,
        input_schema=INPUT, output_schema=OUTPUT,
        examples=('galaxy-cli youzan customer-search --input \'{"keyword":"客户姓名","page_size":20}\'',
                  'galaxy-cli youzan customer-search --input \'{"last_trade_start":"2026-11-01","last_trade_end":"2026-11-08","mobile_authorized":true}\'',
                  'galaxy-cli youzan customer-search --input \'{"branch_names":["分店名称"],"wecom_friend":true}\''))
    def customer_search(arguments):
        return YouzanClient(category_credentials("youzan"), customer_search_only=True).search(arguments)


def search(client, arguments):
    arguments = normalize(arguments)
    selected = []
    if arguments.get("branch_ids") or arguments.get("branch_names"):
        branches = client.branches()
        for identifier in arguments.get("branch_ids", []):
            match = next((row for row in branches if row["id"] == identifier), None)
            if not match:
                raise ToolError("branch_denied", "分店 ID 不在当前账号有数据权限的列表内。", 1)
            selected.append(match)
        for name in arguments.get("branch_names", []):
            matches = [row for row in branches if row["name"] == name]
            if not matches:
                matches = [row for row in branches if name in row["name"]]
            if len(matches) != 1:
                raise ToolError("branch_ambiguous", "分店名称未匹配到唯一结果，请提供更完整的名称或明确的分店 ID。", 1)
            selected.append(matches[0])
    selected = list({row["id"]: row for row in selected}.values())
    data = client._json(CUSTOMERS, request_body(arguments, [row["id"] for row in selected]))
    result = data
    return client.redact(result)
