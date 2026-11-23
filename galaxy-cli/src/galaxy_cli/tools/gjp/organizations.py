from ...gjp.client import Client
from ...gjp.schemas import INPUTS, OUTPUT

DESCRIPTION = ('管家婆机构信息获取，作为库存筛选的机构选项工具；只读。使用 gjp organizations --input JSON。'
    '省略 parent_id 列根分类；指定上次返回的真实 parent_id 列下一级，可逐层找到零售板块或门店。'
    'page 默认1、page_size 默认100且最大200。输出 options 含 id/name/parent_id/child_count，及 rows/total/count/has_more。'
    'child_count 大于0可继续展开；筛选库存时使用返回的 id 作为 organization_id，不把显示名称或商家编码当内部ID。'
    '这是机构树，不是仓库或供应商清单。由服务器自动提供当前用户管家婆凭证。')


def register(registry):
    registry.register(name='gjp.organizations', category='gjp', description=DESCRIPTION,
        input_schema=INPUTS['organizations'], output_schema=OUTPUT, handler=lambda body: Client().selection(body),
        examples=('galaxy-cli gjp organizations --input \'{}\'',))
