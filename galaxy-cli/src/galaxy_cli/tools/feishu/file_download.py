from ...feishu_client import INPUTS, invoke

DESCRIPTION = '下载当前员工有权限访问的飞书云空间普通文件到服务器的当前账号工作目录。必填 file 为飞书 file/wiki 链接或普通文件 token，filename 为保存文件名（含正确扩展名），不接受目录、绝对路径或凭证。输出仅包含真实 path、filename、size，size 单位为字节；该 path 可交给 file.publish 托管成聊天附件，不能当下载链接。仅下载文件不代表已解析正文；在线 docx/sheet/base/slides 不属于普通文件，应使用相应读取或导出能力。失败不回退为管理员身份，不会请求额外写权限。 调用方式为 galaxy-cli feishu file-download --input JSON。授权由服务器按当前登录员工自动提供，模型不能指定账号或 token。'


def register(registry):
    @registry.tool(name='feishu.file-download', category="feishu", description=DESCRIPTION,
        input_schema=INPUTS['file-download'], output_schema={"type":"object"})
    def handler(arguments):
        return invoke('file-download', arguments)
