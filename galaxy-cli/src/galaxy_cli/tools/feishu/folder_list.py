from ...feishu_client import INPUTS, invoke

DESCRIPTION = '只读列出飞书云空间文件夹的直接子项。所有输入可选：folder_token 指定文件夹，省略读取当前账号的默认根目录；page_size 为 1—20，默认 20；page_token 为上一页返回的分页游标。返回官方 CLI 完整 JSON，ok=true 表示成功，按 data 的 has_more/page_token 继续翻页；不递归读取子文件夹，也不直接返回文档正文。 调用方式为 galaxy-cli feishu folder-list --input JSON。授权由服务器按当前登录员工自动提供，模型不能指定账号或 token。'


def register(registry):
    @registry.tool(name='feishu.folder-list', category="feishu", description=DESCRIPTION,
        input_schema=INPUTS['folder-list'], output_schema={"type":"object"})
    def handler(arguments):
        return invoke('folder-list', arguments)
