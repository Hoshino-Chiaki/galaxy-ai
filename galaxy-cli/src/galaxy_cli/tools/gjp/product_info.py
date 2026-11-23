from ...gjp.client import Client
from ...gjp.schemas import INPUTS, OUTPUT

DESCRIPTION = ('管家婆商品信息获取，用于了解库存可选商品信息；只读。使用 gjp product-info --input JSON。'
    'action=attributes（默认）：调用 GetAssistType 返回当前账号的16类辅助属性 id/title（品牌、季节、年份等）；'
    '这些ID是属性类别，不是具体品牌/年份值，不能直接当库存筛选值。'
    'action=products：按抓包商品选择接口列商品分类/商品选项，省略 parent_id 列根分类，'
    '传上一层返回的真实 parent_id 逐层展开；page 默认1、page_size 默认100最大200。'
    '输出 options 含 id/name/child_count/parent_id，以及原始 rows 和分页信息；用返回的 id 作为库存 product_id。'
    '不把辅助属性类别表称为商品明细，不猜测尚未提供的辅助属性具体选项或筛选参数。凭证由服务器提供。')


def register(registry):
    registry.register(name='gjp.product-info', category='gjp', description=DESCRIPTION,
        input_schema=INPUTS['product-info'], output_schema=OUTPUT, handler=lambda body: Client().product_info(body),
        examples=('galaxy-cli gjp product-info --input \'{}\'',
                  'galaxy-cli gjp product-info --input \'{"action":"products"}\''))
