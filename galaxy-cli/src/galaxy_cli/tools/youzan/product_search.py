"""The merchant multi-channel product search and scoped filter options."""
import copy
import datetime as dt
import json
import uuid
from decimal import Decimal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from jsonschema import Draft202012Validator
from ...registry import ToolError
from ...youzan_catalog import ID, IDS, OUTPUT, object_schema, invalid, client_context, must, success

MONEY={'type':'string','pattern':r'^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$','maxLength':20}
INTS={'type':'array','items':{'type':'integer'},'uniqueItems':True}
PROPS={'keyword':{'type':'string','minLength':1,'maxLength':200},'page':{'type':'integer','minimum':1},'page_size':{'type':'integer','minimum':1},
 'created_start':{'type':'string'},'created_end':{'type':'string'},'code_type':{'enum':['product_code','product_barcode','sku_code','sku_barcode']},
 'code':{'type':'string','minLength':1,'maxLength':200},'source_shop_id':{'anyOf':[ID,{'const':'0'}]},
 'product_type':{'enum':['all','physical','virtual','digital','distribution','cake']},'category_ids':IDS,'brand_ids':IDS,
 'group_ids':IDS,'group_channel':{'enum':[0,1,2]},'leaf_category_id':ID,'supplier_id':ID,
 'inventory_type':{'type':'integer'},'delivery_types':{'type':'array','uniqueItems':True,'items':{'enum':['express','cityDelivery','selfPick']}},
 'stock_deduct_mode':{'enum':[0,1]},'history_types':INTS,'property_value_ids':IDS,
 'channel_type':{'enum':[0,1,2,3]},'channels':INTS,'sale_shop_ids':IDS,'unsale_shop_ids':IDS,
 'status':{'enum':['all','on_sale','sold_out','off_sale']},'pre_sale_types':{'type':'array','uniqueItems':True,'items':{'enum':[-1,0,1]}},
 'has_images':{'type':'boolean'},'has_video':{'type':'boolean'},'has_detail':{'type':'boolean'},'min_price':MONEY,'max_price':MONEY,
 'lifecycle_ids':{'type':'array','uniqueItems':True,'items':{'enum':[1,2,3,4,5]}}}
OPTION_PROPS={'section':{'enum':['basic','brands','shops','properties','categories','groups']},'page':{'type':'integer','minimum':1},
 'page_size':{'type':'integer','minimum':1},'parent_id':ID}
INPUT={'type':'object','oneOf':[object_schema({'action':{'const':'query'},**PROPS},['action']),
 object_schema({'action':{'const':'options'},**OPTION_PROPS},['action'])]}
BASE={'scene_code':'publish_on_sale','is_sub_query':False,'need_sub_saleable':False,'channel_param':{'channel_type':3,'channels':[]},
 'pre_sale_types':[],'lifecycle_ids':[],'sale_status_param':-1,'is_virtuals':[],'item_types':[],'biz_codes':[],
 'not_exclude_ability_marks':[],'properties_values':[],'support_multi_channel':True}
TYPES={'all':{},'physical':{'is_virtuals':[0],'item_types':[0]},'virtual':{'is_virtuals':[2],'item_types':[0],'exclude_ability_marks':[40011]},
 'digital':{'is_virtuals':[3],'item_types':[0]},'distribution':{'item_types':[10]},'cake':{'is_virtuals':[0],'item_types':[0],'biz_codes':['010000000001']}}
CODES={'product_code':'spu_code_match','product_barcode':'spu_barcode_match','sku_code':'sku_code_match','sku_barcode':'sku_barcode_match'}
GATEWAY={'search':'youzan.item.lite.product.search/1.0.0','brands':'youzan.retail.product.brandnew.querypage/1.0.0',
 'properties':'youzan.item.common.get.propertiesfiltersettings/1.0.0',
 'categories':'youzan.retail.product.category.biz/1.0.0/querycategories'}
DESCRIPTION=('查询当前店铺商品，调用 galaxy-cli youzan product-search options/query，可选 --input JSON。'
 'options 可选 section=basic/brands/shops/properties/categories/groups（默认 basic），page、page_size 用于对应分页，groups 可选 parent_id；返回实际候选项，ID 不可臆造。'
 'query 所有筛选均可选：keyword 是商品筛选词，可用名称或 P 开头编号；page 默认 1，page_size 默认 20；created_start/created_end 为北京时间 YYYY-MM-DD 或 YYYY-MM-DD HH:mm:ss，日期结束包含当天。'
 '精确编条码筛选用 code 与 code_type=product_code/product_barcode/sku_code/sku_barcode，不能把 P 编号直接当商品内部 ID。'
 '可选 source_shop_id（来源店铺，0 为本店）、product_type=all/physical/virtual/digital/distribution/cake、category_ids（零售商品分类）、brand_ids、group_ids 和 group_channel（0全部/1网店/2门店）、leaf_category_id（行业末级类目）、supplier_id、inventory_type。'
 'delivery_types 可填 express/cityDelivery/selfPick，stock_deduct_mode 按 options 枚举，history_types 和 property_value_ids 为实际历史类型/属性值 ID；不接受任意属性 JSON、请求地址、凭证或其他账号身份。'
 'channel_type/channels 为销售渠道，sale_shop_ids/unsale_shop_ids 为互斥的可售/不可售店铺集合；status=all/on_sale/sold_out/off_sale；pre_sale_types 可填 -1非预售/0全款/1定金。'
 'has_images/has_video/has_detail 为布尔；min_price/max_price 输入元单位十进制字符串，工具转换为分；lifecycle_ids 为 1新品/2正常/3停购/4停售/5淘汰。'
 '结果保留完整上游 JSON，code=200 且 success 不为 false 表示查询成功，data.items 为商品，data.paginator.total_count 为总数；一次只返回指定页，不代表全部结果。'
 '搜索返回 price/product_price 等金额为分，不能按元直接使用。读取或编辑网店商品使用 id_summary.online_item_id（或 item_id），商品库 spu_id、门店 offline_item_id 不是同一种 ID。'
 '分销商品可查询完整信息，但供货价、供应商身份/库存/规格及受限物流不能修改；零售价受实时区间约束。搜索命中不代表该账号有写权限，触及受限项目时应按 product-update 描述提示用户。'
 '部分扩展筛选依据页面参数映射实现，尚未逐项实测；类目参数多值筛选、未定位的供应商候选接口不宣称已支持，不能静默忽略未开放字段。')


def date_value(value,end=False):
 try:
  if len(value)==10:
   stamp=dt.datetime.strptime(value,'%Y-%m-%d')
   if end:stamp+=dt.timedelta(days=1,milliseconds=-1)
  else:stamp=dt.datetime.strptime(value,'%Y-%m-%d %H:%M:%S')
  return int(stamp.replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000)
 except (TypeError,ValueError,OverflowError):invalid('时间须为北京时间 YYYY-MM-DD 或 YYYY-MM-DD HH:mm:ss。')


def normalize(arguments):
 if not isinstance(arguments,dict) or not Draft202012Validator(INPUT).is_valid(arguments):
  invalid('商品搜索请使用 options/query 及工具列出的筛选字段，不接受凭证、任意接口或未开放筛选。')
 result=copy.deepcopy(arguments)
 if result['action']=='query':
  result.setdefault('page',1);result.setdefault('page_size',20)
  if ('code' in result)!=('code_type' in result):invalid('code 与 code_type 必须同时填写。')
  if result.get('group_ids') and 'group_channel' not in result:invalid('商品分组筛选还需提供 group_channel，不能混用网店和门店分组 ID。')
  if set(result.get('sale_shop_ids',[])) & set(result.get('unsale_shop_ids',[])):invalid('同一店铺不能同时作为可售和不可售条件。')
  for key in ('created_start','created_end'):
   if key in result:date_value(result[key],end=key=='created_end')
  if 'created_start' in result and 'created_end' in result and date_value(result['created_start'])>date_value(result['created_end'],True):invalid('创建时间开始不能晚于结束。')
  if 'min_price' in result and 'max_price' in result and Decimal(result['min_price'])>Decimal(result['max_price']):invalid('最低价不能大于最高价。')
 else:
  section=result.get('section','basic')
  if 'parent_id' in result and section!='groups':invalid('parent_id 仅用于商品分组子目录查询。')
  result.setdefault('section','basic')
 return result


def gateway(client,name,params=None):
 access=client.credentials.get('cookies',{}).get('access_token')
 if not access:raise ToolError('connection_required','商品搜索需要当前店铺业务 access_token，请刷新当前有赞分类凭证。',1)
 values={'access_token':access,'source':'WEB_BACK_END','retail_source':'WEB-RETAIL-AJAX','idempotent_no':uuid.uuid4().hex}
 for key in ('carmen_csrf_token','bifrost_csrf_token'):
  token=client.credentials.get('cookies',{}).get(key)
  if token:values[key]=token
 for key,value in (params or {}).items():
  values[key]=json.dumps(value,ensure_ascii=False,separators=(',',':')) if isinstance(value,(dict,list,bool)) else value
 return client.redact(client._json('https://gateway.youzan.com/api/'+GATEWAY[name]+'?'+urlencode(values)))


def query_params(args):
 result=copy.deepcopy(BASE);result.update({'page':args['page'],'page_size':args['page_size']})
 if 'keyword' in args:result['keyword']=args['keyword']
 if 'code' in args:result[CODES[args['code_type']]]=args['code']
 result.update(TYPES[args.get('product_type','all')])
 mapping={'category_ids':'category_ids','brand_ids':'brand_ids','group_ids':'second_group_ids','group_channel':'group_channel',
  'leaf_category_id':'leaf_category_id','source_shop_id':'organization_id','supplier_id':'default_vendor_id','inventory_type':'inventory_type',
  'stock_deduct_mode':'stock_deduct_mode','history_types':'prod_item_migration_tags','property_value_ids':'prop_text_ids',
  'sale_shop_ids':'sale_sub_kdt_ids','unsale_shop_ids':'no_sale_sub_kdt_ids','pre_sale_types':'pre_sale_types','lifecycle_ids':'lifecycle_ids'}
 for key,upstream in mapping.items():
  if key in args:
   value=args[key];result[upstream]=[int(x) for x in value] if isinstance(value,list) else int(value)
 for key,upstream in [('created_start','min_create_time'),('created_end','max_create_time')]:
  if key in args:result[upstream]=date_value(args[key],key=='created_end')
 for key,upstream in [('min_price','min_product_price'),('max_price','max_product_price')]:
  if key in args:result[upstream]=int(Decimal(args[key])*100)
 if 'delivery_types' in args:result['distribution_search']={key:True for key in args['delivery_types']}
 if 'channel_type' in args or 'channels' in args:
  result['channel_param']={'channel_type':args.get('channel_type',3),'channels':args.get('channels',[])}
 for key,upstream in [('has_images','contain_picture'),('has_detail','contain_content')]:
  if key in args:result[upstream]=int(args[key])
 if 'has_video' in args:result['have_video']=1 if args['has_video'] else 2
 status=args.get('status','all');result['sale_status_param']={'all':-1,'on_sale':0,'sold_out':1,'off_sale':2}[status]
 if status!='all':result[{'on_sale':'sub_kdt_saleable','sold_out':'sub_kdt_sold_out','off_sale':'sub_kdt_unshelve'}[status]]=True
 return result


def options(client,args):
 section=args['section']
 if section=='basic':return {'data':{
  'product_type':list(TYPES),'code_type':CODES,'status':{'all':-1,'on_sale':0,'sold_out':1,'off_sale':2},
  'lifecycle_ids':{'1':'新品','2':'正常','3':'停购','4':'停售','5':'淘汰'},'pre_sale_types':{'-1':'非预售','0':'全款预售','1':'定金预售'},
  'delivery_types':['express','cityDelivery','selfPick'],'stock_deduct_mode':{'0':'拍下减库存','1':'付款减库存'},
  'money_input':'元，十进制字符串','money_output':'原始接口金额字段通常为分；按原字段定义解析',
  'unavailable_filters':['类目参数多值筛选','未确定枚举的其他商品类型'],
  'id_note':'网店编辑使用 id_summary.online_item_id，不用 spu_id 或 offline_item_id 替代'}}
 if section=='brands':return gateway(client,'brands',{'page_no':args.get('page',1),'page_size':args.get('page_size',20)})
 if section=='properties':return gateway(client,'properties')
 if section=='categories':return gateway(client,'categories')
 if section=='shops':return client.request('/v2/goods/api/store/getAllStores',params={'requestContent':'{}'})
 params={'kdtId':client.credentials['kdt_id']}
 if 'parent_id' in args:
  params['upperId']=args['parent_id'];path='/v4/goods/tag/querySubGroupList.json'
 else:params.update({'p':args.get('page',1),'filterHiddenGroup':'true','groupQueryScene':2});path='/v4/goods/tag/listMultiLevelGroupPage.json'
 return client.request(path,params=params)


def run(client,args):
 args=normalize(args)
 if args['action']=='options':return options(client,args)
 result=gateway(client,'search',query_params(args))
 if result.get('code')==200 and result.get('success') is not False:
  data=result.get('data')
  if not isinstance(data,dict) or not isinstance(data.get('items'),list):raise ToolError('invalid_upstream','商品搜索结果结构无效。',1)
  if any(str(row.get('kdt_id'))!=client.credentials['kdt_id'] for row in data['items']):
   raise ToolError('shop_scope_mismatch','商品业务 token 与当前选定店铺不一致，请刷新对应店铺的分类凭证。',1)
 return result


def register(registry):
 @registry.tool(name='youzan.product-search',category='youzan',description=DESCRIPTION,input_schema=INPUT,output_schema=OUTPUT)
 def invoke(arguments):
  args=normalize(arguments);client,_=client_context('product-search',args);return run(client,args)
