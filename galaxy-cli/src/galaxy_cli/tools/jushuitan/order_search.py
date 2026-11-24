from ...jushuitan.orders import Client, INPUT, OUTPUT

DESCRIPTION = ('查询当前员工绑定的聚水潭供应链订单（供应商视角），只读。使用 jushuitan order-search --input JSON。'
    '全部筛选可选；online_order_nos 为线上订单号字符串数组（优先用它精确定位），distribution_online_order_nos 为分销线上订单号数组，'
    'order_ids 为聚水潭内部订单号数组；三者不能混用。product_name 商品名称，shop_ids 店铺内部ID数组，supplier_ids 供应商内部ID数组；'
    'buyer_nicknames 买家昵称数组，phones 手机号数组，receivers 收货人数组，tracking_nos 快递单号数组。'
    'sku_codes 商品编码数组、style_codes 款式编码数组，对应 sku_match/style_match 可选 exact精确（默认）或 fuzzy模糊。'
    'start_time/end_time 须同时填写，格式 YYYY-MM-DD HH:mm:ss，北京时间；time_type 为 OrderDate下单（默认）、PayDate付款、DeliveryDate发货、ConfirmDate推单。'
    'provinces 为省份完整名称数组，例如 ["湖南省"]；exclude_provinces=true 排除这些省份，默认包含。'
    'flags 包含旗帜、exclude_flags 排除旗帜，均为页面真实旗帜值的字符串数组，不知道值时不要猜。'
    'after_sale_guarantees 为分销售后保障/分销无忧售后/无分销售后保障组成的数组；order_statuses 只能传实际接口状态值。'
    'action=query（默认）查一页并核对总数，action=count只查总数；page默认1，page_size默认50、范围1—50，ascending默认false。'
    '省略条件表示不加该筛选，不擅自加日期、状态或店铺限制。输出保留完整上游JSON：query.data为本页订单及商品明细，count.total为真实总数，'
    'page/page_size/has_more说明分页；query.total=-1表示列表未带总数，count.data=[]是正常计数响应，均不代表零订单。'
    '金额、数量及空值按原值保留，不套用有赞的分/千分位规则。大型结果由服务器写入本人缓存，返回sandbox_path后用Python读取。'
    '店铺与供应商ID来自真实数据，不能把公司身份ID作为筛选自行覆盖；不接受账号、密码、token、Cookie、网址、任意HTTP请求或其他员工身份。')


def register(registry):
    registry.register(name='jushuitan.order-search', category='jushuitan', description=DESCRIPTION,
        input_schema=INPUT, output_schema=OUTPUT, handler=lambda body: Client().orders(body),
        examples=('galaxy-cli jushuitan order-search --input \'{"online_order_nos":["线上订单号"]}\'',))
