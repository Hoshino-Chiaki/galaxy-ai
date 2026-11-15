from ...feishu_client import INPUTS, invoke

DESCRIPTION = '搜索当前账号有权限访问的飞书云文档、知识库和云空间对象。可选 query 为最多 30 字的关键词，省略或空字符串按可见范围浏览；page_size 为 1—20，默认 15；page_token 可继续翻页。输出为官方 CLI 完整 JSON，成功 ok=true，data 是实际搜索结果，按其中分页信息继续读取，不能将第一页当成全部。只搜索当前员工授权范围，结果不会自动读取正文。 调用方式为 galaxy-cli feishu search --input JSON。授权由服务器按当前登录员工自动提供，模型不能指定账号或 token。'


def register(registry):
    @registry.tool(name='feishu.search', category="feishu", description=DESCRIPTION,
        input_schema=INPUTS['search'], output_schema={"type":"object"})
    def handler(arguments):
        return invoke('search', arguments)
