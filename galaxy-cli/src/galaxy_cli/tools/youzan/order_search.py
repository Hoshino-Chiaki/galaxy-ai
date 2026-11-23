"""Read merchant orders with fixed endpoints and native response preservation."""
import copy
import hashlib
import json
import os
import uuid
from urllib.parse import urlencode
from jsonschema import Draft202012Validator
from ...registry import ToolError
from ...file_scope import root_directory, child_directory
from ...youzan_catalog import IDS, ID, OUTPUT, object_schema, invalid, client_context
from .product_search import date_value

SEARCH = {'order_nos':'订单编号','outer_transaction_number':'外部单号','user_name':'收货人姓名','tel':'收货人手机号',
 'offline_order_no':'离线订单编号','buyer_phone':'买家手机号','fulfill_no':'发货单号','express_no':'物流单号',
 'sku_no':'商品编码','bar_code':'商品条码','buyer_phone_last_4':'买家手机号后四位','receiver_phone_last_4':'收货人手机号后四位','out_pay_no':'支付流水号'}
TIMES = {'book_time':'下单时间','ship_time':'发货时间','pay_time':'支付时间','success_time':'交易完成时间'}
STATES = {3:'待付款',5:'待发货/待接单',6:'已发货/已接单',50:'待成团/审核中/待接单',100:'已成功',99:'已关闭',600001:'售后中',600002:'退款结束'}
FEEDBACK = {600001:'售后中',250:'售后结束'}
DELIVERY = {0:'快递',1:'自提',2:'同城配送',600001:'快递或同城配送'}
CHANNELS = {0:'网店/小程序',1:'门店',200:'美团',201:'饿了么'}
MARKETING = {1:'有赞客推广',2:'三方应用推广',3:'分销员推广',4:'导购员推广'}
INTEGER = {'type':'integer','minimum':0}
PAGE = {'page':{'type':'integer','minimum':1},'page_size':{'type':'integer','minimum':1}}
STAMP = {'anyOf':[{'type':'string','minLength':10,'maxLength':19},{'type':'integer','minimum':1000000000000,'maximum':9999999999999}]}
FILTERS = {**PAGE,'search_label':{'enum':list(SEARCH)},'search_field':{'type':'string','minLength':1,'maxLength':10000},
 'time_type':{'enum':list(TIMES)},'start_time':STAMP,'end_time':STAMP,'sort':{'enum':[0,1]},
 'goods_title':{'type':'string','minLength':1,'maxLength':200},'brand_id_list':IDS,'sale_way':INTEGER,'shop_kdt_ids':IDS,
 'order_type':INTEGER,'order_state':{'enum':list(STATES)},'order_state_list':{'type':'array','minItems':1,'uniqueItems':True,'items':{'enum':list(STATES)}},
 'feedback_state':{'enum':list(FEEDBACK)},'express_type':{'enum':list(DELIVERY)},'buy_way':INTEGER,
 'is_reminder_status':{'type':'boolean'},'has_star':{'type':'boolean'},'marketing_type':{'enum':list(MARKETING)},'table_id':ID,
 'order_sources':{'type':'array','uniqueItems':True,'items':{'type':'string','pattern':r'^[0-9]+$'}},
 'real_pay_min':{'type':'integer','minimum':0,'maximum':99999999},'real_pay_max':{'type':'integer','minimum':0,'maximum':99999999}}
SECTIONS = ['basic','shops','brands','sales_channels','order_types','payment_methods','order_sources','tables']
INPUT = {'type':'object','oneOf':[object_schema({'action':{'const':'query'},**FILTERS,'all_pages':{'type':'boolean'}},['action']),
 object_schema({'action':{'const':'options'},'section':{'enum':SECTIONS},**PAGE,'shop_kdt_ids':IDS},['action'])]}
ENDPOINTS = {'search':'youzan.retail.trademanager.order.list.search/1.0.1',
 'conditions':'youzan.retail.trade.manager.ordersearch.condition.fetch/1.0.0',
 'brands':'youzan.retail.product.brandnew.querypage/1.0.0','tables':'youzan.retail.shop.global.retail.table.list/1.0.0'}
DESCRIPTION = ('查询有赞订单：galaxy-cli youzan order-search options/query --input JSON，分类 youzan，需 youzan:order:read，服务器自动使用当前员工、目标店铺的会话，不接受凭证或任意接口。'
 'options 可选 section=basic/shops/brands/sales_channels/order_types/payment_methods/order_sources/tables（默认 basic）；basic 返回固定筛选枚举；sales_channels 返回页面支持的渠道值，不代表当前店铺已开通；其余项从当前店铺获取真实候选，品牌可 page/page_size，桌号可传 shop_kdt_ids。ID、支付方式、订单类型、来源和渠道值从真实候选选择，不根据名称猜值。'
 '候选结果解析：shops 的 data 是 id/name 数组；订单类型、支付方式、订单来源的 data 是 display/paramValue/displayAtSaleWay 数组，paramValue 分别转为 order_type、buy_way 的整数或 order_sources 的字符串；销售渠道 data 的 channelId 用于 sale_way，enabled_status=unknown 表示未核验开通状态；品牌保留 data.items/paginator。'
 'query 所有筛选可选，不填写代表不限制：page 默认1、page_size默认20；time_type=book_time下单/ship_time发货/pay_time支付/success_time交易完成，默认book_time；start_time/end_time 必须同时提供，接受北京时间 YYYY-MM-DD、YYYY-MM-DD HH:mm:ss 或13位毫秒时间戳，日期结束包含全天；sort=0由近到远/1由远到近，仅下单时间支持。'
 '订单搜索用 search_field 和可选 search_label（默认order_nos）：order_nos订单编号（最多100个，逗号分隔）、outer_transaction_number外部单号、user_name收货人姓名、tel收货人手机号、offline_order_no离线单号、buyer_phone买家手机号、fulfill_no发货单号、express_no物流单号、sku_no商品编码、bar_code商品条码、buyer_phone_last_4/receiver_phone_last_4手机号后四位、out_pay_no支付流水号。商品编码/条码的适用性以当前店铺版本为准。'
 '其他可选：goods_title商品名称、brand_id_list品牌ID字符串数组、sale_way销售渠道、shop_kdt_ids有数据权限的分店ID字符串数组、order_type订单类型、order_state单个状态或互斥的order_state_list状态数组（3待付款/5待发货/6已发货/50待成团等/100成功/99关闭/600001售后中/600002退款结束）。'
 'feedback_state售后状态600001售后中/250售后结束；express_type配送0快递/1自提/2同城/600001快递或同城；buy_way支付方式；is_reminder_status是否催单、has_star是否加星为布尔；marketing_type推广1有赞客/2三方应用/3分销员/4导购员；table_id桌号ID；order_sources订单来源值字符串数组；real_pay_min/real_pay_max为实付金额上下限，按网页输入整数元。所有条件同时生效，全部选项应省略字段，不传-1。'
 '默认返回完整原始单页订单JSON，保留所有业务字段；仅隐藏会话凭证。code=200且success不为false才成功；data.data为当前页订单，data.total为满足全部条件的总数，data.page_no/page_size为分页。总数不是本页数量。'
 '需要全部订单时，query 传 all_pages=true（page只能省略或为1，page_size默认50），程序自动按total连续翻页，筛选保持不变，无需AI逐页调用或另写脚本。每页完整原始响应写入当前账号工作目录的JSONL文件，每行一个响应；仅返回data中的path、filename、size、total、count、pages、complete，不把订单明细重复塞进聊天。path可供后续程序读取、分析或制表，本工具不生成Excel。只有complete=true才表示全部取得；中途接口失败、空页、重复页或总数变化时返回error及已保存部分的路径和数量，不得当成完整结果。未传all_pages或为false仍返回原始单页数据。'
 '时间字段按接口原值解析；payment_info中的real_pay/payment/postage/total_discount等为分，输入金额筛选仍是元，其他金额字段以实际定义为准。只读查询，不创建、修改、退款或发货。跨店范围可在galaxy_cli外层shops传all/current/店铺ID或名称数组；shop_kdt_ids仅是当前店铺内的分店筛选，不是切换授权店铺。')


def timestamp(value,end=False):
    return value if type(value) is int else date_value(value,end)


def normalize(arguments):
    if not isinstance(arguments,dict) or not Draft202012Validator(INPUT).is_valid(arguments):
        invalid('订单查询请使用 options/query 和已说明字段；全部筛选省略，不接受 -1、凭证、任意地址或未支持字段。')
    result=copy.deepcopy(arguments)
    if result['action']=='options':
        result.setdefault('section','basic')
        if ('page' in result or 'page_size' in result) and result['section']!='brands':invalid('page/page_size 仅用于品牌候选分页。')
        if 'shop_kdt_ids' in result and result['section']!='tables':invalid('options 的 shop_kdt_ids 仅用于桌号候选。')
        return result
    result.setdefault('page',1);result.setdefault('page_size',50 if result.get('all_pages') else 20);result.setdefault('time_type','book_time')
    if result.get('all_pages') and result['page']!=1:invalid('all_pages 从第1页开始，page请省略或填1。')
    if ('start_time' in result)!=('end_time' in result):invalid('start_time 与 end_time 需同时提供。')
    if 'start_time' in result:
        if timestamp(result['start_time'])>timestamp(result['end_time'],True):invalid('开始时间不能晚于结束时间。')
    if 'sort' in result and result['time_type']!='book_time':invalid('sort 仅支持下单时间排序。')
    if 'order_state' in result and 'order_state_list' in result:invalid('order_state 与 order_state_list 不能同时填写。')
    if 'search_label' in result and 'search_field' not in result:invalid('search_label 需同时填写 search_field。')
    if 'search_field' in result:
        result.setdefault('search_label','order_nos')
        value=result['search_field'].strip()
        if not value or any(ord(c)<32 for c in value):invalid('订单搜索值不能为空或含控制字符。')
        if result['search_label']=='order_nos':
            identifiers=[item.strip() for item in value.replace('，',',').split(',')]
            if not all(identifiers) or len(identifiers)>100:invalid('订单编号用逗号分隔，最多100个，不能含空编号。')
            value=','.join(identifiers)
        if result['search_label'].endswith('_last_4') and (len(value)!=4 or not value.isascii() or not value.isdigit()):invalid('手机号后四位必须为4位数字。')
        result['search_field']=value
    if result.get('real_pay_min',0)>result.get('real_pay_max',99999999):invalid('实付金额最小值不能大于最大值。')
    return result


def gateway(client,name,params=None):
    cookies=client.credentials.get('cookies',{})
    if not cookies.get('access_token'):raise ToolError('connection_required','订单查询需要当前店铺业务 access_token，请刷新对应有赞分类凭证。',1)
    values={'access_token':cookies['access_token'],'source':'WEB_BACK_END','retail_source':'WEB-RETAIL-AJAX','idempotent_no':uuid.uuid4().hex}
    for key in ('carmen_csrf_token','bifrost_csrf_token'):
        if cookies.get(key):values[key]=cookies[key]
    for key,value in (params or {}).items():
        values[key]=json.dumps(value,ensure_ascii=False,separators=(',',':')) if isinstance(value,(dict,list,bool)) else value
    return client.redact(client._json('https://gateway.youzan.com/api/'+ENDPOINTS[name]+'?'+urlencode(values)))


def query_params(args):
    values={'page_no':args['page'],'page_size':args['page_size'],'time_type':args['time_type'],'search_label':args.get('search_label','order_nos'),'support_product':True}
    for key in FILTERS:
        if key in args and key not in ('page','page_size','time_type','search_label'):
            value=args[key]
            if key in ('start_time','end_time'):value=timestamp(value,key=='end_time')
            elif key in ('brand_id_list','shop_kdt_ids'):value=[int(item) for item in value]
            elif key=='table_id':value=int(value)
            values[key]=value
    return values


def check_shops(client,ids):
    if not ids:return
    allowed={row['id'] for row in client.branches()}|{client.credentials['kdt_id']}
    if not set(ids).issubset(allowed):raise ToolError('shop_scope_denied','分店不在当前账号有数据权限的店铺列表中，请先查询 shops 候选。',1)


def query_page(client,args):
    result=gateway(client,'search',query_params(args))
    if result.get('code')==200 and result.get('success') is not False:
        data=result.get('data')
        if not isinstance(data,dict) or type(data.get('total')) is not int or data['total']<0 or not isinstance(data.get('data'),list):
            raise ToolError('invalid_upstream','订单返回结构异常，不能视为成功或零订单。',1)
        expected=client.credentials['kdt_id']
        for row in data['data']:
            main=row.get('main_order_info',{}) if isinstance(row,dict) else {}
            if expected not in (str(main.get('kdt_id')),str(main.get('head_kdt_id'))):
                raise ToolError('shop_scope_mismatch','订单业务 token 与目标店铺不一致，请检查对应店铺会话。',1)
    return result


def query_all(client,args,context):
    if not isinstance(context,dict) or not isinstance(context.get('workspace_directory'),str):
        raise ToolError('invalid_context','全部订单需使用当前账号的服务器工作目录。',1)
    name='orders-'+uuid.uuid4().hex+'.jsonl'
    metadata={'path':'order-exports/'+name,'filename':name,'size':0,'total':None,'count':0,'pages':0,'complete':False}
    fingerprints=set()
    with root_directory(context['workspace_directory']) as root, child_directory(root,['order-exports'],create=True) as directory:
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=directory)
        with os.fdopen(fd,'wb') as stream:
            while True:
                try:
                    result=query_page(client,args)
                    if result.get('code')!=200 or result.get('success') is False:
                        raise ToolError('pagination_upstream_failed','订单接口未成功，自动翻页已停止；已保存部分不代表全部订单。',1)
                    data=result['data'];rows=data['data']
                    if metadata['total'] is None:metadata['total']=data['total']
                    if data['total']!=metadata['total']:
                        raise ToolError('pagination_changed','查询期间订单总数发生变化，已停止；请按固定时间范围重新查询。',1)
                    if 'page_no' in data and data['page_no']!=args['page']:
                        raise ToolError('pagination_stalled','订单接口返回的页码不符，已停止，不能视为全部结果。',1)
                    fingerprint=hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).digest()
                    if fingerprint in fingerprints or not rows and metadata['count']<metadata['total']:
                        raise ToolError('pagination_stalled','订单接口返回重复页或提前空页，已停止，不能视为全部结果。',1)
                    if metadata['count']+len(rows)>metadata['total']:
                        raise ToolError('pagination_changed','订单数量与接口总数不符，已停止，不能视为全部结果。',1)
                except ToolError as error:
                    return {'code':502,'success':False,'error':{'code':error.code,'message':error.message},'data':metadata}
                encoded=json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()+b'\n'
                stream.write(encoded);stream.flush();os.fsync(stream.fileno())
                fingerprints.add(fingerprint)
                metadata.update(size=metadata['size']+len(encoded),count=metadata['count']+len(rows),pages=metadata['pages']+1)
                if metadata['count']==metadata['total']:
                    metadata['complete']=True
                    return {'code':200,'success':True,'data':metadata}
                args={**args,'page':args['page']+1}


def run(client,args,context=None):
    args=normalize(args)
    check_shops(client,args.get('shop_kdt_ids',[]))
    if args['action']=='options':
        section=args['section']
        if section=='basic':return {'data':{'search_label':SEARCH,'time_type':TIMES,'sort':{'0':'由近到远','1':'由远到近'},
            'order_state':{str(k):v for k,v in STATES.items()},'feedback_state':{str(k):v for k,v in FEEDBACK.items()},
            'express_type':{str(k):v for k,v in DELIVERY.items()},'marketing_type':{str(k):v for k,v in MARKETING.items()},
            'sale_way':{str(k):v for k,v in CHANNELS.items()},'sections':SECTIONS,'money_filter_unit':'整数元','payment_info_money_unit':'分','all':'省略筛选字段'}}
        if section=='shops':return {'data':client.branches()}
        if section=='brands':return gateway(client,'brands',{'page_no':args.get('page',1),'page_size':args.get('page_size',20)})
        if section=='sales_channels':return {'data':[{'channelId':k,'channelName':v} for k,v in CHANNELS.items()],'enabled_status':'unknown'}
        if section=='tables':return gateway(client,'tables',{'sub_kdt_ids':[int(x) for x in args.get('shop_kdt_ids',[])]})
        return gateway(client,'conditions',{'type':{'order_types':'ordertype','payment_methods':'payway','order_sources':'ordersource'}[section]})
    return query_all(client,args,context) if args.get('all_pages') else query_page(client,args)


def register(registry):
    @registry.tool(name='youzan.order-search',category='youzan',description=DESCRIPTION,input_schema=INPUT,output_schema=OUTPUT)
    def invoke(arguments):
        args=normalize(arguments);client,context=client_context('order-search',args);return run(client,args,context)
