from galaxy_cli.feishu_capabilities import DOMAINS
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from galaxy_cli.registry import ToolError, load_registry
from galaxy_cli.context import FRAME
from galaxy_cli.youzan_catalog import State, permissions, image_bytes, public_image_url
from galaxy_cli.tools.youzan import material_category, material_list, material_upload, product_get, product_options, product_update


def product(distribution=False):
    p={'id':100,'kdtId':900,'alias':'fixture','title':'Fixture product','goodsNo':'P-001','barcode':'00001','updateTime':1000,
       'picture':[{'id':200,'url':'https://img01.yzcdn.cn/upload_files/fixture.jpg','width':800,'height':800}],
       'tag':[20],'content':'<p>Original detail</p>','componentsExtraId':0,'isDisplay':0,'shipment':0,
       'itemAssistDetail':{'isEditLock':0,'isLock':0},'itemMarkParam':{'goodsType':10 if distribution else 0},
       'itemPriceParam':{'price':129,'origin':'199.00'},'itemStockParam':{'totalStock':5,'spotStockNum':5,'hideStock':0,
           'stock':[{'id':300,'price':'129.00','stockNum':'5','spotStockNum':'5','isSell':1,'code':'001','barcode':'009','isSkuStockNumEdited':True,'disableStatus':0}],
           'isStockNumEdited':True,'skuName1':{'id':1,'text':'Color'},'skuName1Value':[{'id':2,'text':'Red'}]},
       'itemSellParam':{'joinLevelDiscount':0,'buyWay':1},'itemDeliveryParam':{'postage':0,'deliveryTemplateId':0},
       'extraParam':{'retailParam':{},'fenXiaoParam':{}},'itemDisplayParam':{'type':0}}
    if distribution:p['extraParam']['fenXiaoParam']={'supplierKdtId':800,'supplierGoodsId':801,'fxAuth':1,
        'fxItemRetailPriceLimitationMap':{'300':{'min':'129.00','max':'568.00'},'total':{'min':'129.00','max':'568.00'}},
        'fxSku':{'stock':[{'id':300,'isSell':1}]}}
    return p


class FakeClient:
    def __init__(self,context,distribution=False):
        self.context=context;self.credentials={'kdt_id':'900','shop_name':'Fixture'};self.state=State(context,'900')
        self.value=product(distribution);self.calls=[];self.saves=0;self.failure=False
        self.rows=[{'categoryId':10,'categoryName':'自定义分组','categoryType':5,'parentCategoryId':0},
            {'categoryId':11,'categoryName':'未分组','categoryType':2,'parentCategoryId':0},
            {'categoryId':12,'categoryName':'Demo','categoryType':1,'parentCategoryId':10}]
        self.images=[];self.shops=[{'kdtId':901,'isDisplay':1},{'kdtId':902,'isDisplay':0}]
    def redact(self,result):return result
    def categories(self):return {'code':0,'msg':'ok','data':copy.deepcopy(self.rows)}
    def category(self,identifier,listing=None):
        for row in listing['data'] if listing is not None else self.rows:
            if str(row['categoryId'])==identifier:return row
        raise ToolError('category_not_found','fixture')
    def product(self,identifier):
        if identifier!='100':raise ToolError('product_not_found','fixture')
        self.state.remember(self.value['picture'])
        return {'code':0,'msg':'ok','data':copy.deepcopy(self.value)}
    def request(self,path,**options):
        self.calls.append((path,copy.deepcopy(options)))
        body=options.get('body',{});params=options.get('params',{})
        if path.endswith('multi-category/create.json'):
            self.rows.append({'categoryId':99,'categoryName':body['categoryName'],'categoryType':1,'parentCategoryId':body['parentCategoryId']})
            data=99
        elif path.endswith('multi-category/update.json'):
            self.category(str(body['categoryId']))['categoryName']=body['categoryName'];data=None
        elif path.endswith('multi-category/move.json'):
            for identifier in body['categoryIds']:self.category(str(identifier))['parentCategoryId']=body['targetCategoryId']
            data=[]
        elif path.endswith('/materials/list'):data={'items':copy.deepcopy(self.images),'paginator':{'page':1,'totalCount':len(self.images)}}
        elif path.endswith('/search/support'):data=True
        elif path.endswith('fetchPubImg.json'):
            data={'attachment_id':201,'attachment_full_url':'https://img01.yzcdn.cn/upload_files/new.jpg','width':32,'height':32}
            self.images=[{'attachmentId':201,'attachmentFullUrl':data['attachment_full_url'],'width':32,'height':32}]
        elif path.endswith('policy.json'):data={'enabled':False}
        elif path.endswith('pubImgUploadToken.json'):data={'uptoken':'fixture-upload-secret'}
        elif path.endswith('queryGroupListJSON.json'):data=[{'groupId':int(i)} for i in params['groupIds'].split(',')]
        elif path.endswith('sub-shop-list'):data=[{'kdtId':901},{'kdtId':902}]
        elif path.endswith('listSubShopItemPageByDisplay'):data={'items':copy.deepcopy(self.shops),'totalCount':2}
        elif path.endswith('item/update/_textarea_'):
            self.saves+=1
            if self.failure:return {'code':123,'msg':'fixture rejected'}
            for key,value in body.items():
                if key=='content' and not body.get('detailEdit'):continue
                self.value[key]=copy.deepcopy(value)
            self.value['updateTime']+=1
            self.value['kdtId']=900
            for r in self.shops:
                if r['kdtId'] in body['displayOnKdtIds']:r['isDisplay']=1
                elif r['kdtId'] in body['displayOffKdtIds']:r['isDisplay']=0
            data={'id':100,'alias':'fixture'}
        else:data=[]
        return {'code':0,'msg':'ok','data':data}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name);(root/'work').mkdir();(root/'locks').mkdir()
        self.context={'workspace_directory':str(root/'work'),'catalog_lock_directory':str(root/'locks')}
        self.client=FakeClient(self.context)
    def tearDown(self):self.temp.cleanup()
    def update(self,changes,action='preview',base=1000):
        return product_update.run(self.client,self.context,{'action':action,'product_id':'100','changes':changes,'base_update_time':base})

    def test_category_create_rename_move_and_system_guards(self):
        result=material_category.run(self.client,self.context,{'action':'create','name':'Test'})
        self.assertEqual(result['data'],99)
        material_category.run(self.client,self.context,{'action':'rename','category_id':'99','name':'Renamed'})
        self.assertEqual(self.client.category('99')['categoryName'],'Renamed')
        material_category.run(self.client,self.context,{'action':'move','category_ids':['99'],'parent_id':'12'})
        self.assertEqual(self.client.category('99')['parentCategoryId'],12)
        for args in ({'action':'rename','category_id':'11','name':'bad'}, {'action':'move','category_ids':['12'],'parent_id':'99'}):
            with self.assertRaises(ToolError):material_category.run(self.client,self.context,args)

    def test_material_list_retains_native_fields_and_caches_only_shop_assets(self):
        self.client.images=[{'attachmentId':201,'attachmentFullUrl':'https://img01.yzcdn.cn/upload_files/new.jpg','width':1,'extra':{'retain':True}}]
        result=material_list.run(self.client,{'category_id':'12','keyword':'fixture'})
        self.assertEqual(result['data']['items'][0]['extra'],{'retain':True})
        self.assertEqual(self.client.state.asset('201')['id'],201)
        with self.assertRaises(ToolError):material_list.run(self.client,{'category_id':'999'})

    def test_network_upload_confirms_destination_without_modifying_product(self):
        with patch('galaxy_cli.tools.youzan.material_upload.public_image_url',side_effect=lambda v:v):
            result=material_upload.run(self.client,self.context,{'category_id':'12','url':'https://example.org/a.png'})
        self.assertEqual(result['data']['attachment_id'],201);self.assertEqual(self.client.saves,0)

    def test_local_upload_uses_scoped_image_and_sends_no_merchant_cookies(self):
        file=Path(self.context['workspace_directory'])/'a.png';Image.new('RGB',(4,4)).save(file)
        observed=[];client=self.client
        class Opener:
            def open(self,request,**_):
                observed.append(request)
                client.images=[{'attachmentId':202,'attachmentFullUrl':'https://img01.yzcdn.cn/upload_files/local.png','width':4,'height':4}]
                return io.BytesIO(json.dumps({'code':0,'data':{'attachment_id':202,'attachment_full_url':client.images[0]['attachmentFullUrl'],'width':4,'height':4}}).encode())
        with patch('urllib.request.build_opener',return_value=Opener()):
            result=material_upload.run(self.client,self.context,{'category_id':'12','path':'a.png'})
        self.assertEqual(result['data']['attachment_id'],202)
        request=observed[0];self.assertEqual(request.full_url,'https://upload.qiniup.com')
        self.assertNotIn('Cookie',dict(request.header_items()));self.assertNotIn('csrf-token',dict(request.header_items()))
        self.assertIn(b'name="x:categoryId"',request.data)
        self.assertNotIn('fixture-upload-secret',json.dumps(result))

    def test_local_upload_rejects_foreign_paths_symlinks_hardlinks_and_non_images(self):
        root=Path(self.context['workspace_directory']);outside=Path(self.temp.name)/'secret';outside.write_bytes(b'secret')
        (root/'link').symlink_to(outside);os.link(outside,root/'hard');(root/'text').write_text('html')
        for value in ('../secret','/etc/passwd','.youzan/900.json','link','hard','text'):
            with self.assertRaises(ToolError):material_upload.run(self.client,self.context,{'category_id':'12','path':value})

    def test_public_url_blocks_private_ip_and_credentials(self):
        for value in ('file:///etc/passwd','http://127.0.0.1/a','http://169.254.169.254/a','https://u:p@example.org/a','http://[::1]/a'):
            with self.assertRaises(ToolError):public_image_url(value)

    def test_product_preview_is_read_only_and_apply_preserves_unmodified_fields(self):
        before=copy.deepcopy(self.client.value)
        preview=self.update({'title':'New name','goods_no':'00042'})
        self.assertEqual(preview['base_update_time'],1000);self.assertEqual(self.client.saves,0)
        result=self.update({'title':'New name','goods_no':'00042'},action='apply')
        self.assertEqual(result['code'],0);self.assertEqual(self.client.value['title'],'New name')
        for key in ('picture','content','itemDeliveryParam','tag'):self.assertEqual(self.client.value[key],before[key])
        self.assertFalse(self.client.value['itemStockParam']['isStockNumEdited'])
        self.assertFalse(self.client.value['itemStockParam']['stock'][0]['isSkuStockNumEdited'])
        self.assertEqual(self.client.shops,[{'kdtId':901,'isDisplay':1},{'kdtId':902,'isDisplay':0}])

    def test_all_inferred_product_fields_roundtrip(self):
        self.client.state.remember([{'id':201,'url':'https://img01.yzcdn.cn/upload_files/new.jpg','width':10,'height':10}])
        changes={'barcode':'00050','image_ids':['201','200'],'group_ids':["21"], 'price':'139.00','origin_price':None,
            'sku_updates':[{'sku_id':'300','is_sell':True,'stock_num':8,'code':'002','barcode':'003'}],
            'hide_stock':True,'detail_html':'<p>New detail</p>','is_display':True,
            'sale_scope':{'on_shop_ids':['902'],'off_shop_ids':['901']}}
        self.update(changes,action='apply')
        p=self.client.value
        self.assertEqual(p['itemStockParam']['stock'][0]['price'],'139.00')
        self.assertEqual(p['itemStockParam']['totalStock'],8);self.assertEqual(p['content'],'<p>New detail</p>')
        self.assertEqual(p['itemStockParam']['stock'][0]['code'],'002');self.assertEqual(p['isDisplay'],1)

    def test_distribution_constraints_and_explanations(self):
        self.client.value=product(True)
        for change,code in [({'sku_updates':[{'sku_id':'300','stock_num':1}]},'distribution_read_only'),
            ({'sku_updates':[{'sku_id':'300','price':'128.99'}]},'distribution_price_range')]:
            with self.assertRaises(ToolError) as error:self.update(change)
            self.assertEqual(error.exception.code,code)
        self.client.value['itemStockParam']['stock'][0]['disableStatus']=1
        with self.assertRaises(ToolError):self.update({'sku_updates':[{'sku_id':'300','is_sell':True}]})
        self.client.value['itemStockParam']['stock'][0]['disableStatus']=0
        value=self.update({'sku_updates':[{'sku_id':'300','price':'139.00'}]})
        self.assertIn('warnings',value)
        self.update({'sku_updates':[{'sku_id':'300','price':'139.00'}]},action='apply')
        self.assertEqual(self.client.value['itemStockParam']['stock'][0]['price'],'139.00')

    def test_version_conflicts_unknown_skus_and_active_content_never_save(self):
        for changes in ({'sku_updates':[{'sku_id':'999','price':'1.00'}]}, {'detail_html':'<script>alert(1)</script>'},
            {'detail_html':'<p onclick="bad()">a</p>'}, {'detail_html':'<img src="file:///etc/passwd">'}):
            with self.assertRaises(ToolError):self.update(changes,action='apply')
        with self.assertRaises(ToolError):self.update({'title':'new'},action='apply',base=999)
        self.assertEqual(self.client.saves,0)

    def test_upstream_rejection_and_unverified_writes_are_not_success(self):
        self.client.failure=True
        self.assertEqual(self.update({'title':'new'},action='apply')['code'],123)
        self.client.failure=False;original=self.client.request
        def lose_save(path,**kw):
            result=original(path,**kw)
            if path.endswith('/_textarea_'):self.client.value['title']='different'
            return result
        self.client.request=lose_save
        with self.assertRaises(ToolError) as error:self.update({'title':'new'},action='apply')
        self.assertEqual(error.exception.code,'save_may_have_succeeded')

    def test_options_and_per_field_permissions(self):
        for action,extra in [('groups',{}),('sku-values',{'spec_id':'1'}),('detail-templates',{}),('delivery',{}),('shops',{})]:
            self.assertEqual(product_options.run(self.client,{'action':action,'product_id':'100',**extra})['code'],0)
        args={'action':'apply','changes':{'sku_updates':[{'sku_id':'300','price':'1.00','stock_num':1}],'is_display':False}}
        self.assertTrue({'youzan:product:price:update','youzan:product:stock:update','youzan:product:display:update'}<=permissions('product-update',args))
        args['action']='preview';self.assertEqual(permissions('product-update',args),{'youzan:product:read'})

    def test_registered_descriptions_include_distribution_rules_and_no_global_prompt_dependency(self):
        registry=load_registry()
        for name in ('product-get','product-options','product-update'):
            description=registry.get('youzan.'+name).description
            self.assertIn('分销',description);self.assertIn('供货价',description);self.assertNotIn('HAR',description)
        data=registry.invoke('help',{})['data']
        self.assertEqual(len(data),19+len(DOMAINS)+int(any(t['tool']=='youzan.product-search' for t in data)))
