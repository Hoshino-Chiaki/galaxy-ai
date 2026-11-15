"""Options for editing a product already verified to belong to this shop."""
from jsonschema import Draft202012Validator
from ...youzan_catalog import ID, OUTPUT, PRODUCT, object_schema, invalid, client_context, success
INPUT={'type':'object','oneOf':[
    object_schema({'action':{'const':'groups'},'product_id':ID,'page':{'type':'integer','minimum':1},'parent_id':ID,'keyword':{'type':'string','maxLength':200}},['action','product_id']),
    object_schema({'action':{'const':'sku-values'},'product_id':ID,'spec_id':ID},['action','product_id','spec_id']),
    object_schema({'action':{'const':'detail-templates'},'product_id':ID,'page':{'type':'integer','minimum':1},'page_size':{'type':'integer','minimum':1}},['action','product_id']),
    object_schema({'action':{'enum':['delivery','shops']},'product_id':ID},['action','product_id'])]}
DESCRIPTION=('查询商品编辑选项，调用 galaxy-cli youzan product-options groups/sku-values/detail-templates/delivery/shops --input JSON。'
    '所有子命令必填 product_id；groups 可选 page（默认 1）、parent_id、keyword；sku-values 额外必填 spec_id；'
    'detail-templates 可选 page（默认 1）、page_size（默认 100），其他子命令无额外参数。返回对应接口完整 JSON，'
    '商品分组和模板通常在 data.items，分页在 data.paginator，规格、配送和分店按实际 data 解析；素材分类和商品分组不能混用。'
    '这是读取选项，不会修改商品。分销商品的规格、库存和配送可能由供应商控制；选项存在不等于允许修改。供货价、供应商库存、供应商身份不可修改，'
    '定时、预售、规格重建和复杂组件详情未开放为写参数；触及上游受限项目时应说明限制，不用其他字段绕过。')


def normalize(arguments):
    if not isinstance(arguments,dict) or not Draft202012Validator(INPUT).is_valid(arguments):invalid('请使用 groups/sku-values/detail-templates/delivery/shops，并填写 product_id 及对应可选参数。')
    return dict(arguments)


def run(client,args):
    args=normalize(args);product=client.product(args['product_id'])
    if not success(product):return product
    action=args['action'];shop=client.credentials['kdt_id']
    if action=='groups':
        if 'parent_id' in args:
            return client.request('/v4/goods/tag/querySubGroupList.json',params={'upperId':args['parent_id'],'kdtId':shop})
        params={'p':args.get('page',1),'kdtId':shop,'filterHiddenGroup':'true','groupQueryScene':2}
        if 'keyword' in args:params['keyword']=args['keyword']
        return client.request('/v4/goods/tag/listMultiLevelGroupPage.json',params=params)
    if action=='sku-values':return client.request('/shop/v2/showcase/skuProperty/skuValue.json',params={'id':args['spec_id']})
    if action=='detail-templates':return client.request(PRODUCT+'pageQueryDetailTemplate',params={
        'page':args.get('page',1),'pageSize':args.get('page_size',100),'kdtId':shop,'isQueryHq':'false'})
    if action=='delivery':return client.request(PRODUCT+'delivery-setting')
    return client.request(PRODUCT+'sub-shop-list')


def register(registry):
    @registry.tool(name='youzan.product-options',category='youzan',description=DESCRIPTION,input_schema=INPUT,output_schema=OUTPUT)
    def invoke(arguments):
        args=normalize(arguments);client,_=client_context('product-options',args);return run(client,args)
