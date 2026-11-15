import re
from datetime import datetime

from jsonschema import Draft202012Validator
from ..registry import ToolError

DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?$",
        "description": "北京时间，YYYY-MM-DD HH:mm:ss；只写日期时，开始为 00:00:00、结束为 23:59:59。"}
INPUT = {"type": "object", "properties": {
    "keyword": {"type": "string", "maxLength": 160, "description": "统一关键词：手机号、姓名、昵称或渠道 ID；不保证精确匹配。"},
    "last_trade_start": {**DATE, "description": "上次消费时间下限。" + DATE["description"]},
    "last_trade_end": {**DATE, "description": "上次消费时间上限。" + DATE["description"]},
    "customer_created_start": {**DATE, "description": "成为客户时间下限。" + DATE["description"]},
    "customer_created_end": {**DATE, "description": "成为客户时间上限。" + DATE["description"]},
    "member_created_start": {**DATE, "description": "成为会员时间下限。" + DATE["description"]},
    "member_created_end": {**DATE, "description": "成为会员时间上限。" + DATE["description"]},
    "branch_ids": {"type": "array", "maxItems": 20, "uniqueItems": True,
                   "items": {"type": "string", "pattern": r"^[0-9]{1,16}$"}, "description": "归属分店 ID，多个分店取并集；只能选择当前有赞账号有数据权限的分店。"},
    "branch_names": {"type": "array", "maxItems": 20, "uniqueItems": True,
                     "items": {"type": "string", "minLength": 1, "maxLength": 160}, "description": "归属分店名称，先精确匹配，只有唯一候选时才接受包含匹配；不会猜测分店 ID。"},
    "mobile_authorized": {"type": "boolean", "description": "是否已授权手机号；省略为全部。"},
    "wecom_friend": {"type": "boolean", "description": "企微好友：true=页面中的‘是’，false=页面中的‘否’；这是好友筛选，不代表企微授权状态。省略为全部。"},
    "page": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 1},
    "page_size": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
}, "additionalProperties": False}

for _name, _title in {'keyword': '手机号 / 姓名 / 昵称 / 渠道 ID', 'last_trade_start': '上次消费开始', 'last_trade_end': '上次消费结束', 'customer_created_start': '成为客户开始', 'customer_created_end': '成为客户结束', 'member_created_start': '成为会员开始', 'member_created_end': '成为会员结束', 'branch_ids': '归属分店 ID', 'branch_names': '归属分店名称', 'mobile_authorized': '是否授权手机号', 'wecom_friend': '是否企微好友', 'page': '页码', 'page_size': '每页数量，最多 50'}.items():
    INPUT["properties"][_name]["title"] = _title + ("，北京时间 YYYY-MM-DD 或 YYYY-MM-DD HH:mm:ss" if _name.endswith(("_start", "_end")) else "")

TIME_FIELDS = {
    "last_trade_start": "lastTradeTimeStart", "last_trade_end": "lastTradeTimeEnd",
    "customer_created_start": "createdAtStart", "customer_created_end": "createdAtEnd",
    "member_created_start": "memberCreatedAtStart", "member_created_end": "memberCreatedAtEnd",
}


def normalize(arguments):
    if not isinstance(arguments, dict) or not Draft202012Validator(INPUT).is_valid(arguments):
        raise ToolError("invalid_input", "客户搜索参数无效，请运行 help youzan.customer-search。")
    result = dict(arguments)
    for key in TIME_FIELDS:
        if key in result:
            value = result[key]
            if len(value) == 10:
                value += " 23:59:59" if key.endswith("_end") else " 00:00:00"
            try:
                datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                raise ToolError("invalid_input", "请输入有效的日期和时间。") from None
            result[key] = value
    for prefix in ("last_trade", "customer_created", "member_created"):
        if prefix + "_start" in result and prefix + "_end" in result and result[prefix + "_start"] > result[prefix + "_end"]:
            raise ToolError("invalid_input", "时间范围的开始不能晚于结束。")
    if "keyword" in result:
        result["keyword"] = result["keyword"].strip()
    if "branch_names" in result:
        result["branch_names"] = [name.strip() for name in result["branch_names"]]
        if any(not name for name in result["branch_names"]):
            raise ToolError("invalid_input", "分店名称不能为空。")
    result.setdefault("page", 1)
    result.setdefault("page_size", 20)
    return result


def request_body(arguments, branch_ids=()):
    arguments = normalize(arguments)
    query = {"keyword": arguments.get("keyword", ""), "tagSearchType": 0,
             "current": arguments["page"], "isAuthMobile": arguments.get("mobile_authorized")}
    for key, field in TIME_FIELDS.items():
        if key in arguments:
            query[field] = arguments[key]
    if branch_ids:
        query["relatedKdtIds"] = [int(value) for value in branch_ids]
    if "wecom_friend" in arguments:
        query["wecomStaffRelation"] = 2 if arguments["wecom_friend"] else 0
    return {"page": arguments["page"], "pageSize": arguments["page_size"], "crowd": -1,
            "query": query, "needChange": True}
