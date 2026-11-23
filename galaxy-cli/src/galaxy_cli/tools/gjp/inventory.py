from ...gjp.client import Client
from ...gjp.schemas import INPUTS, OUTPUT

DESCRIPTION = ('管家婆库存信息获取，只读网页报表接口，参考价格固定库存均价。使用 gjp inventory --input JSON。'
    'action=query（默认）查询，options 返回已确认的筛选选项。可选 organization_id/warehouse_id/product_id/supplier_id '
    '均为接口返回的内部ID，barcode 为商品条码；省略这些条件表示全部，不擅自限定零售板块。'
    'include_zero 默认 false（不包含零库存，与网页未勾选“显示无库存数量的商品”一致）；用户明确要求包含零库存时才传 true。'
    '“所有零售机构”“全部机构”描述机构范围，不代表包含零库存。unique_barcode 默认 false，stock_type 为 0全部/1普通/3物料（默认0）。'
    'page 默认1，page_size 默认100、最大200；只返回该页 rows 和 total/count/has_more，不能把一页称为全部数据。'
    '按机构筛选前可用 gjp organizations 查询真实选项；按商品分类可用 gjp product-info --input {"action":"products"}。'
    '未知筛选值不要猜。输出原始字段含商品条码 ptypeid_barcode、库存均价 avgtotal、数量 qty，保留空值。'
    '登录凭证由服务器按当前用户提供，不能传 Cookie/SID/网址/SQL。')


def register(registry):
    registry.register(name='gjp.inventory', category='gjp', description=DESCRIPTION,
        input_schema=INPUTS['inventory'], output_schema=OUTPUT, handler=lambda body: Client().inventory(body),
        examples=('galaxy-cli gjp inventory --input \'{}\'',))
