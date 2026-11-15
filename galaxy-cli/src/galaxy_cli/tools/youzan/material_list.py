"""Full native material list and a server-owned metadata index."""
from jsonschema import Draft202012Validator
from ...youzan_catalog import ID, OUTPUT, object_schema, invalid, client_context, must, success
from ...registry import ToolError

INPUT=object_schema({'category_id':ID,'page':{'type':'integer','minimum':1},'page_size':{'type':'integer','minimum':1},
    'keyword':{'type':'string','minLength':3,'maxLength':200}},['category_id'])
DESCRIPTION=('分页查询当前店铺素材图片，调用 galaxy-cli youzan material-list --input JSON。必填 category_id（分类 ID 字符串）；'
    '可选 page（默认 1）、page_size（默认 15）、keyword（至少 3 字符，需平台支持搜索）。返回完整有赞响应，data.items 为图片，'
    'data.paginator 为分页，attachmentId 为素材 ID，attachmentFullUrl/attachmentUrl 为原图，attachmentSize 为字节，width/height 为像素；'
    'meta 可能是 JSON 字符串。用于上传选择或商品主图变更的 ID 须来自当前店铺结果，不用文件名猜 ID。'
    '分销商品同样可以选取本店有权使用的素材，查到素材不代表可以修改供应商商品、供货价、库存或受限规格。')


def normalize(arguments):
    if not isinstance(arguments,dict) or not Draft202012Validator(INPUT).is_valid(arguments):
        invalid('素材查询必填 category_id，可选 page、page_size、keyword。')
    return {'page':1,'page_size':15,**arguments}


def run(client,args):
    args=normalize(args);client.category(args['category_id'])
    if 'keyword' in args:
        support=must(client.request('/v4/shop/api/search/support',params={'type':'image'}))
        if support is not True: raise ToolError('unsupported_search','当前店铺不支持素材关键词搜索，请按分类分页查询。',1)
    params={'categoryId':args['category_id'],'pageNum':args['page'],'pageSize':args['page_size'],'p':args['page'],'mediaType':'image'}
    if 'keyword' in args: params['keyword']=args['keyword']
    result=client.request('/v4/materials/api/upload/materials/list',params=params,host='materials.youzan.com')
    if success(result):
        data=result.get('data')
        if not isinstance(data,dict) or not isinstance(data.get('items'),list): raise ToolError('invalid_upstream','素材列表结构无效。',1)
        client.state.remember(data['items'])
    return result


def register(registry):
    @registry.tool(name='youzan.material-list',category='youzan',description=DESCRIPTION,input_schema=INPUT,output_schema=OUTPUT)
    def invoke(arguments):
        args=normalize(arguments);client,_=client_context('material-list',args);return run(client,args)
