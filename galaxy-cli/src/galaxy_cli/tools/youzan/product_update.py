"""Explicit product patches, upstream restrictions, and verified writes."""
import copy
import json
import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urlsplit
from jsonschema import Draft202012Validator
from ...registry import ToolError
from ...youzan_catalog import ID, IDS, BOOL, TEXT, OUTPUT, PRODUCT, object_schema, invalid, client_context, must, success, write_lock

MONEY={'type':'string','pattern':r'^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$','maxLength':20}
SKU=object_schema({'sku_id':ID,'price':MONEY,'is_sell':BOOL,'stock_num':{'type':'integer','minimum':0},'code':TEXT,'barcode':TEXT},['sku_id'])
CHANGES=object_schema({'goods_no':TEXT,'barcode':TEXT,'title':{'type':'string','minLength':1,'maxLength':200},
    'append_image_ids':{**IDS,'minItems':1},'image_ids':{**IDS,'minItems':1},'group_ids':IDS,'price':MONEY,
    'origin_price':{'anyOf':[MONEY,{'type':'null'}]},'sku_updates':{'type':'array','minItems':1,'items':SKU},
    'hide_stock':BOOL,'detail_html':{'type':'string','maxLength':500000},'is_display':BOOL,
    'sale_scope':object_schema({'on_shop_ids':IDS,'off_shop_ids':IDS},['on_shop_ids','off_shop_ids'])})
CHANGES['minProperties']=1
INPUT=object_schema({'action':{'enum':['preview','apply']},'product_id':ID,
    'base_update_time':{'type':'integer','minimum':0},'changes':CHANGES},['action','product_id','changes'])
INPUT['allOf']=[{'if':{'properties':{'action':{'const':'apply'}}},'then':{'required':['base_update_time']}}]
DESCRIPTION=('预览或修改商品，调用 galaxy-cli youzan product-update preview/apply --input JSON。必填 product_id、changes；apply 另必填最近读取/预览得到的 base_update_time。'
    'changes 可选 goods_no、barcode、title、append_image_ids、image_ids、group_ids、price、origin_price、sku_updates、hide_stock、detail_html、is_display、sale_scope，至少一项。'
    '省略字段不修改，编码/条码空字符串清空；主图追加 append_image_ids 与完整替换排序 image_ids 互斥，素材须先在当前店铺查询/上传，不允许清空全部主图；group_ids 是完整目标商品分组数组。'
    'price 为单规格零售价，origin_price 为划线价，均用元单位十进制字符串，origin_price=null 清空；多规格改价通过 sku_updates。'
    'sku_updates 每项必填 sku_id，可选 price、is_sell、stock_num、code、barcode；stock_num 是非负目标库存，不是增减量，未指定 SKU 保留。'
    'hide_stock 是隐藏库存布尔值；detail_html 仅适用于支持的富文本模式，不能覆盖未知组件或提交脚本；is_display 是立即上下架布尔值；sale_scope 含互斥的 on_shop_ids/off_shop_ids。'
    'preview 不保存，返回 product_id、base_update_time、field/before/after 变更列表及适用限制；apply 保存前检查版本、权限和商品类型，保存后回读，返回完整实际响应。'
    '分销商品限制：供货价、供应商身份、供应商库存、供应商规格/编码及供应商禁用状态不可修改，运费/配送等页面受限项目不能绕过；'
    '分销 SKU 仅可在实时 min/max 区间改零售价，不能启用供应商已禁用 SKU，本店自营库存参数不能用于覆盖供应商库存。'
    '触及限制时工具返回 distribution_read_only/distribution_price_range 等明确错误，应直接告诉用户具体限制，不换字段绕过。'
    '新增删除 SKU、重建规格、定时开售、预售、备货、行业类目和资质尚未开放参数；未开放字段报错。'
    '新扩展字段依照现有保存结构实现，实际能力以当前商品及上游校验为准。结果不确定时先回读，不盲目重试，以免重复修改。')
DISTRIBUTION=('分销商品的供货价、供应商身份、供应商库存、规格和供应商编码由供货商控制，不能通过本工具修改；'
    '运费/配送等上游受限项目不支持；零售价需遵守各 SKU 的实时建议区间，供应商已禁用的 SKU 不能重新启用。')


def normalize(arguments):
    if not isinstance(arguments,dict) or not Draft202012Validator(INPUT).is_valid(arguments):
        invalid('商品修改需要 product_id、changes 及 preview/apply；apply 必须提供 base_update_time。仅接受工具列出的变更字段，供货价、供应商库存、规格重建、物流等不接受修改参数。')
    result=copy.deepcopy(arguments);c=result['changes']
    if 'append_image_ids' in c and 'image_ids' in c:invalid('append_image_ids 与 image_ids 不能同时传。')
    for key in ('goods_no','barcode','title'):
        if key in c and (any(ord(x)<32 or ord(x)==127 for x in c[key]) or key=='title' and not c[key].strip()):invalid('商品名称及编码不能包含控制字符，名称不能为空。')
    identifiers=[]
    for row in c.get('sku_updates',[]):
        if len(row)==1:invalid('每个 SKU 至少指定一个变更字段。')
        identifiers.append(row['sku_id'])
    if len(set(identifiers))!=len(identifiers):invalid('同一 SKU 不能重复提交。')
    if 'sale_scope' in c and set(c['sale_scope']['on_shop_ids'])&set(c['sale_scope']['off_shop_ids']):invalid('同一个分店不能同时上架和下架。')
    return result


def money(value):
    try:
        number=Decimal(str(value))
        if not number.is_finite() or number<0 or number.as_tuple().exponent < -2:raise ValueError()
        return number
    except (InvalidOperation,ValueError,TypeError):invalid('金额必须是非负、最多两位小数的元单位数字。')


def number(value):
    amount=money(value)
    return int(amount) if amount==amount.to_integral() else float(amount)


def distributed(product):
    fx=product.get('extraParam',{}).get('fenXiaoParam',{})
    return bool(fx.get('supplierGoodsId') and fx.get('supplierKdtId') and not fx.get('isSupplierItem')) or product.get('itemMarkParam',{}).get('goodsType')==10


class DetailValidator(HTMLParser):
    TAGS={'p','div','span','br','img','b','strong','em','i','u','ul','ol','li','table','thead','tbody','tr','td','th','h1','h2','h3','h4','h5','h6','hr','a','blockquote','section'}
    def __init__(self,client):super().__init__(convert_charrefs=True);self.client=client
    def handle_starttag(self,tag,attrs):
        if tag not in self.TAGS:invalid('详情只接受常用富文本标签，不允许脚本、iframe、表单等内容。')
        values=dict(attrs)
        for key,value in attrs:
            if key.startswith('on') or key in ('srcdoc','formaction') or key=='style' and re.search(r'url\s*\(|expression|@import|\\',value or '',re.I):invalid('详情包含不允许的脚本、事件或外部样式引用。')
            if key in ('href','src') and value:
                parts=urlsplit(value)
                if parts.scheme not in ('http','https') or parts.username or parts.password:invalid('详情链接只接受 HTTP/HTTPS 地址。')
        if tag=='img':
            src=values.get('src','')
            with self.client.state.transaction() as state:allowed={r['url'] for r in state['assets'].values()}
            if src not in allowed:raise ToolError('material_not_found','详情图片须来自当前店铺素材查询或上传结果。',1)
    handle_startendtag=handle_starttag
    def handle_comment(self,data):invalid('详情不接受 HTML 注释或隐藏指令。')
    def handle_decl(self,decl):invalid('详情不接受文档声明。')


def product_groups(client,ids):
    if not ids:return
    rows=must(client.request('/v4/goods/tag/queryGroupListJSON.json',params={'groupIds':','.join(ids)}))
    if not isinstance(rows,list) or {str(r.get('groupId')) for r in rows}!=set(ids):
        raise ToolError('group_not_found','商品分组不属于当前店铺或已失效。',1)


def linked_shops(client,product):
    fx=product.get('extraParam',{}).get('fenXiaoParam',{})
    params={'allSearchDataSimple':'true','hqItemId':product['id'],'hqKdtId':client.credentials['kdt_id'],
        'fenXiaoParam':json.dumps({'isFenXiao':distributed(product),'isUnAdd':False,
            'originKdtId':fx.get('supplierKdtId',0),'originGoodsId':fx.get('supplierGoodsId',0)},separators=(',',':'))}
    data=must(client.request('/v4/goods/api/listSubShopItemPageByDisplay',params=params))
    if not isinstance(data,dict) or not isinstance(data.get('items'),list):raise ToolError('invalid_upstream','商品分店范围不可用。',1)
    return data


def apply_changes(client,product,changes):
    target=copy.deepcopy(product);fx=distributed(product)
    if product.get('itemAssistDetail',{}).get('isEditLock') or product.get('itemAssistDetail',{}).get('isLock'):
        raise ToolError('product_locked','当前商品已被有赞锁定，不能编辑。',1)
    for field,upstream in [('goods_no','goodsNo'),('barcode','barcode'),('title','title')]:
        if field in changes:target[upstream]=changes[field]
    if 'title' in changes:
        result=client.request('/v4/goods/edit/api/validFiled',params={'field':'goods_title','text':changes['title'],
            'shopName':client.credentials.get('shop_name',''),'scene':'ITEM_UPDATE','alias':product.get('alias',''),
            'goods_shipment':product.get('shipment',0),'goods_platform':product.get('extraParam',{}).get('fenXiaoParam',{}).get('goodsPlatform',0),
            'goods_type_fenxiao':product.get('itemMarkParam',{}).get('goodsType',0)})
        must(result)
    if changes.keys()&{'image_ids','append_image_ids'}:
        if 'image_ids' in changes:target['picture']=[client.state.asset(i) for i in changes['image_ids']]
        else:
            have={str(p['id']) for p in target.get('picture',[])}
            target.setdefault('picture',[]).extend(client.state.asset(i) for i in changes['append_image_ids'] if i not in have)
        if len(target['picture'])>15:invalid('当前编辑页面支持最多 15 张主图，请减少图片。')
    if 'group_ids' in changes:
        product_groups(client,changes['group_ids']);target['tag']=[int(i) for i in changes['group_ids']]
    stock=target.setdefault('itemStockParam',{});rows=stock.get('stock',[])
    if not isinstance(rows,list):raise ToolError('unsupported_product','当前商品规格结构不支持此编辑方式。',1)
    index={str(row['id']):row for row in rows}
    limits=product.get('extraParam',{}).get('fenXiaoParam',{}).get('fxItemRetailPriceLimitationMap',{})
    supplier_rows=product.get('extraParam',{}).get('fenXiaoParam',{}).get('fxSku',{}).get('stock',[])
    supplier_index={str(row.get('id')):row for row in supplier_rows}
    if 'price' in changes:
        if len(rows)>1:invalid('多规格商品请通过 sku_updates 明确指定每个 SKU 的 price。')
        target.setdefault('itemPriceParam',{})['price']=number(changes['price'])
        if rows:changes={**changes,'sku_updates':[*changes.get('sku_updates',[]),{'sku_id':str(rows[0]['id']),'price':changes['price']}]}
        elif fx:
            limit=limits.get('total',{})
            if not limit or not money(limit['min'])<=money(changes['price'])<=money(limit['max']):
                raise ToolError('distribution_price_range','分销商品零售价必须位于供货商实时价格区间内；无法取得区间时不执行改价。',1)
    if 'origin_price' in changes:target.setdefault('itemPriceParam',{})['origin']='' if changes['origin_price'] is None else format(money(changes['origin_price']),'.2f')
    for patch in changes.get('sku_updates',[]):
        row=index.get(patch['sku_id'])
        if row is None:raise ToolError('sku_not_found','SKU 不属于当前商品，请先读取商品详情。',1)
        if fx and patch.keys()&{'stock_num','code','barcode'}:raise ToolError('distribution_read_only',DISTRIBUTION,1)
        if 'is_sell' in patch:
            supplier=supplier_index.get(patch['sku_id'])
            if fx and patch['is_sell'] and (row.get('disableStatus') or supplier and supplier.get('isSell')==0):
                raise ToolError('distribution_read_only','供货商已禁用该分销 SKU，分销商不能重新启用。',1)
            row['isSell']=int(patch['is_sell'])
        if 'price' in patch:
            if fx:
                limit=limits.get(patch['sku_id'])
                if not isinstance(limit,dict) or 'min' not in limit or 'max' not in limit:
                    raise ToolError('distribution_price_range','未取得该分销 SKU 实时价格区间，不能执行改价。',1)
                if not money(limit['min'])<=money(patch['price'])<=money(limit['max']):
                    raise ToolError('distribution_price_range',f"该分销 SKU 零售价必须在 {limit['min']}～{limit['max']} 元之间。",1)
            row['price']=format(money(patch['price']),'.2f')
        if 'stock_num' in patch:
            if target.get('extraParam',{}).get('retailParam',{}).get('retailItemId'):
                raise ToolError('stock_managed_elsewhere','该商品库存由零售库存系统管理，不能通过商品编辑覆盖。',1)
            row['stockNum']=str(patch['stock_num']);row['spotStockNum']=str(patch['stock_num']);row['isSkuStockNumEdited']=True
        for key in ('code','barcode'):
            if key in patch:row[key]=patch[key]
    if any('price' in p for p in changes.get('sku_updates',[])) and rows:
        selling=[money(r['price']) for r in rows if r.get('isSell',1) and not r.get('disableStatus')]
        if selling:target.setdefault('itemPriceParam',{})['price']=number(min(selling))
    if any('stock_num' in p for p in changes.get('sku_updates',[])):
        total=sum(int(r.get('stockNum',0)) for r in rows if r.get('isSell',1) and not r.get('disableStatus'))
        stock.update({'totalStock':total,'spotStockNum':total,'isStockNumEdited':True})
    if 'hide_stock' in changes:stock['hideStock']=int(changes['hide_stock'])
    if 'detail_html' in changes:
        components=product.get('components')
        if product.get('componentsExtraId') or components not in (None,[], '[]',''):
            raise ToolError('unsupported_detail_mode','该商品使用组件式详情，不能用 detail_html 覆盖，请在有赞页面编辑或使用受支持的富文本商品。',1)
        validator=DetailValidator(client);validator.feed(changes['detail_html']);validator.close();target['content']=changes['detail_html']
    if 'is_display' in changes:target['isDisplay']=int(changes['is_display'])
    if 'sale_scope' in changes:
        branches=must(client.request(PRODUCT+'sub-shop-list'))
        allowed={str(row['kdtId']) for row in branches} if isinstance(branches,list) else set()
        if not set(changes['sale_scope']['on_shop_ids']+changes['sale_scope']['off_shop_ids'])<=allowed:
            raise ToolError('shop_scope_denied','销售终端不属于当前账号可管理的分店。',1)
    return target


def projection(product,changes):
    result={}
    for field,upstream in [('goods_no','goodsNo'),('barcode','barcode'),('title','title'),('detail_html','content')]:
        if field in changes:result[field]=product.get(upstream,'')
    if changes.keys()&{'image_ids','append_image_ids'}:result['image_ids']=[str(x['id']) for x in product.get('picture',[])]
    if 'group_ids' in changes:result['group_ids']=sorted(str(i) for i in product.get('tag',[]))
    if 'price' in changes:result['price']=format(money(product.get('itemPriceParam',{}).get('price',0)),'.2f')
    if 'origin_price' in changes:
        value=product.get('itemPriceParam',{}).get('origin');result['origin_price']=None if value in (None,'') else format(money(value),'.2f')
    if 'hide_stock' in changes:result['hide_stock']=bool(product.get('itemStockParam',{}).get('hideStock',0))
    if 'is_display' in changes:result['is_display']=bool(product.get('isDisplay'))
    if 'sku_updates' in changes:
        rows={str(r['id']):r for r in product.get('itemStockParam',{}).get('stock',[])}
        result['sku_updates']=[]
        for patch in changes['sku_updates']:
            row=rows.get(patch['sku_id'],{})
            item={'sku_id':patch['sku_id']}
            for key in patch.keys()-{'sku_id'}:
                raw=row.get({'stock_num':'stockNum','is_sell':'isSell'}.get(key,key))
                item[key]=format(money(raw),'.2f') if key=='price' else int(raw or 0) if key=='stock_num' else bool(raw) if key=='is_sell' else raw
            result['sku_updates'].append(item)
    return result


def serialize(product,target,changes,scope):
    # Preserve values from the fresh product. Write flags only for intentionally edited inventory.
    stock=copy.deepcopy(target.get('itemStockParam',{}))
    changed_stock={p['sku_id'] for p in changes.get('sku_updates',[]) if 'stock_num' in p}
    stock['isStockNumEdited']=bool(changed_stock)
    for row in stock.get('stock',[]):row['isSkuStockNumEdited']=str(row['id']) in changed_stock
    selling=copy.deepcopy(target.get('itemSellParam',{}))
    selling.setdefault('shopMethod',selling.get('buyWay',1));selling.setdefault('messages',[])
    selling.setdefault('startSaleModel',{'status':0});selling.setdefault('startSoldTime','')
    price=copy.deepcopy(target.get('itemPriceParam',{}));price.setdefault('costPrice',0)
    extra=copy.deepcopy(target.get('extraParam',{}))
    for key in ('snapKey','snapshotToken'):extra.pop(key,None)
    fx=extra.get('fenXiaoParam',{})
    if fx:extra['fenXiaoParam']={k:fx[k] for k in ('goodsPlatform','supplierKdtId','supplierGoodsId') if k in fx}
    source=product.get('components',[])
    if not isinstance(source,str):source=json.dumps(source,ensure_ascii=False)
    on=[int(r['kdtId']) for r in scope.get('items',[]) if r.get('isDisplay')==1]
    off=[int(r['kdtId']) for r in scope.get('items',[]) if r.get('isDisplay')==0]
    if 'sale_scope' in changes:
        for identifier in changes['sale_scope']['on_shop_ids']:
            i=int(identifier)
            if i not in on:on.append(i)
            if i in off:off.remove(i)
        for identifier in changes['sale_scope']['off_shop_ids']:
            i=int(identifier)
            if i not in off:off.append(i)
            if i in on:on.remove(i)
    payload={key:copy.deepcopy(target.get(key,default)) for key,default in {
        'id':None,'alias':'','goodsNo':'','barcode':'','title':'','picture':[],'componentsExtraId':0,'tag':[],
        'pictureHeight':320,'shipment':0,'videoId':0,'isDisplay':0,'itemMarkParam':{},'itemDeliveryParam':{},
        'warranty':0,'itemDisplayParam':{},'skuPictureDisplayConfig':False,
        'needCustomsInfo':False,'needCustomsCheck':False,'needCustomsPicture':False}.items()}
    payload.update({'detailEdit':'detail_html' in changes,'content':target.get('content',''),'components':source,
        'explainVideo':product.get('explainVideo',{'videoId':product.get('videoId',0),'videoFrom':1}),
        'displayOnKdtIds':on,'displayOffKdtIds':off,'noSaleKdtIds':product.get('noSaleKdtIds',[]),
        'itemScheduleDisplayList':product.get('itemScheduleDisplayList',[]),'itemPriceParam':price,
        'itemSellParam':selling,'itemStockParam':stock,'extraParam':extra,'propList':product.get('propList',product.get('propModels',[])),
        'skuPropList':product.get('skuPropList',[]),'haveSkuExtend':bool(stock.get('stock')),
        'source':1,'productionTime':product.get('productionTime',-1),'modifyScheduleDisplayKdtIds':[]})
    return payload


def run(client,context,args):
    args=normalize(args)
    with write_lock(context,'product:'+args['product_id']):
        initial=client.product(args['product_id'])
        if not success(initial):return initial
        product=initial['data'];changes=args['changes']
        if args['action']=='apply' and product.get('updateTime')!=args['base_update_time']:
            raise ToolError('version_conflict','商品已发生变化，请重新读取或预览，使用最新 updateTime 后再保存。',1)
        target=apply_changes(client,product,changes)
        before,after=projection(product,changes),projection(target,changes)
        delta=[{'field':key,'before':before.get(key),'after':value} for key,value in after.items() if before.get(key)!=value]
        scope=None
        if 'sale_scope' in changes:
            scope=linked_shops(client,product)
            old={str(r['kdtId']):r.get('isDisplay') for r in scope['items']}
            for value,status in [('on_shop_ids',1),('off_shop_ids',0)]:
                for identifier in changes['sale_scope'][value]:
                    if old.get(identifier)!=status:delta.append({'field':'sale_scope.'+identifier,'before':old.get(identifier),'after':status})
        if args['action']=='preview':
            result={'product_id':args['product_id'],'base_update_time':product['updateTime'],'changes':delta}
            if distributed(product):result['warnings']=[DISTRIBUTION]
            return result
        if not delta:return initial
        scope=scope if scope is not None else linked_shops(client,product)
        # Recheck right before committing; the process lock also covers other Galaxy writers.
        current=must(client.product(args['product_id']))
        if current.get('updateTime')!=product.get('updateTime'):raise ToolError('version_conflict','商品在准备期间发生变化，请重新预览。',1)
        payload=serialize(product,target,changes,scope)
        try:result=client.request(PRODUCT+'item/update/_textarea_',method='POST',body=payload)
        except ToolError:
            raise ToolError('save_may_have_succeeded','保存请求结果未确认，可能已经生效；请先读取当前商品核对，不要直接重试。',1) from None
        if not success(result):return result
        try:
            verified=must(client.product(args['product_id']))
            if projection(verified,changes)!=after:raise ValueError()
            if 'sale_scope' in changes:
                actual={str(r['kdtId']):r.get('isDisplay') for r in linked_shops(client,verified)['items']}
                if any(actual.get(i)!=flag for key,flag in [('on_shop_ids',1),('off_shop_ids',0)] for i in changes['sale_scope'][key]):raise ValueError()
        except (ToolError,ValueError):
            raise ToolError('save_may_have_succeeded','有赞已受理保存，但回读未能确认全部变更；请读取商品核对，不要盲目重试。',1) from None
        return result


def register(registry):
    @registry.tool(name='youzan.product-update',category='youzan',description=DESCRIPTION,input_schema=INPUT,output_schema=OUTPUT)
    def invoke(arguments):
        args=normalize(arguments);client,context=client_context('product-update',args);return run(client,context,args)
