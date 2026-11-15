"""Full merchant product data; no lossy projection."""
from jsonschema import Draft202012Validator
from ...youzan_catalog import ID, OUTPUT, object_schema, invalid, client_context
INPUT=object_schema({'product_id':ID},['product_id'])
DESCRIPTION=('获取当前店铺单个商品完整信息，调用 galaxy-cli youzan product-get --input JSON，必填 product_id（有赞内部商品 ID 字符串）。'
    '商品编码、条码、SKU ID 或 P 开头编号不能直接代替商品 ID。返回完整 code/msg/data；基本信息在 data，picture 为有序主图，content 为详情 HTML，'
    'tag/actualTag 为商品分组，itemPriceParam 为价格，itemStockParam.stock 为 SKU 及库存，updateTime 用于修改版本核对；金额按原始元单位解析。'
    '不要将顶层 num=0 当作所有 SKU 已售罄。分销信息在 extraParam.fenXiaoParam：供货价、供应商身份、供应商库存不能由分销商修改；'
    '零售价须遵守各 SKU 实时 min/max 区间，供应商禁用的 SKU 不能重新启用，规格、运费/配送等上游禁止项目不能通过普通编辑绕过。'
    '取得完整字段不代表每个字段可写，请按 product-update 的描述调用，触及分销受限项时向用户解释限制。')


def normalize(arguments):
    if not isinstance(arguments,dict) or not Draft202012Validator(INPUT).is_valid(arguments):invalid('商品详情必填内部 product_id，不接受商品编码、条码或其他店铺参数。')
    return dict(arguments)


def register(registry):
    @registry.tool(name='youzan.product-get',category='youzan',description=DESCRIPTION,input_schema=INPUT,output_schema=OUTPUT)
    def invoke(arguments):
        args=normalize(arguments);client,_=client_context('product-get',args);return client.product(args['product_id'])
