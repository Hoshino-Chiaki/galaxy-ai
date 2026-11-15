import copy
import datetime as dt
import unittest
from unittest.mock import patch
from galaxy_cli.registry import ToolError
from galaxy_cli.tools.youzan import product_search as tool
from galaxy_cli.youzan_http import YouzanClient

class ProductSearchTests(unittest.TestCase):
    def test_exact_captured_defaults_and_keyword_id_mapping(self):
        args=tool.normalize({'action':'query','keyword':'P261105980035231'})
        query=tool.query_params(args)
        self.assertEqual(query['scene_code'],'publish_on_sale');self.assertEqual(query['keyword'],'P261105980035231')
        self.assertEqual(query['page'],1);self.assertEqual(query['page_size'],20)
        self.assertEqual(query['channel_param'],{'channel_type':3,'channels':[]})
        self.assertEqual(query['sale_status_param'],-1)
        self.assertNotIn('access_token',query);self.assertNotIn('page_no',query)

    def test_money_and_dates_do_not_lose_zero_or_timezone(self):
        args=tool.normalize({'action':'query','min_price':'0','max_price':'129.01',
            'created_start':'2026-11-01','created_end':'2026-11-02','has_images':False,'has_video':False,'status':'off_sale'})
        q=tool.query_params(args)
        self.assertEqual(q['min_product_price'],0);self.assertEqual(q['max_product_price'],12901)
        self.assertEqual(q['max_create_time']-q['min_create_time'],2*86400000-1)
        self.assertEqual(q['contain_picture'],0);self.assertEqual(q['have_video'],2)
        self.assertEqual(q['sale_status_param'],2);self.assertTrue(q['sub_kdt_unshelve'])

    def test_invalid_filters_credentials_and_reversed_ranges_rejected(self):
        for change in ({'access_token':'secret'},{'url':'https://example.org'},{'product_id':'1'},
            {'min_price':'2.00','max_price':'1.00'},{'created_start':'2026-11-03','created_end':'2026-11-02'},
            {'created_start':'2026-02-30'},{'code':'1'},{'group_ids':['1']}, {'sale_shop_ids':['1'],'unsale_shop_ids':['1']},
            {'properties_values':[{'id':'unverified'}]}):
            with self.subTest(fields=list(change)),self.assertRaises(ToolError):tool.normalize({'action':'query',**change})

    def test_supported_filters_map_to_backend_fields(self):
        args=tool.normalize({'action':'query','code_type':'sku_barcode','code':'00100','product_type':'distribution',
            'category_ids':['10'],'brand_ids':['11'],'group_ids':['12'],'group_channel':1,'supplier_id':'13','leaf_category_id':'14',
            'source_shop_id':'0','inventory_type':1,'delivery_types':['express','selfPick'],'stock_deduct_mode':0,
            'history_types':[1],'property_value_ids':['15'],'sale_shop_ids':['16'],'channel_type':2,'channels':[4],
            'pre_sale_types':[-1],'lifecycle_ids':[1,2],'has_detail':True})
        q=tool.query_params(args)
        self.assertEqual(q['sku_barcode_match'],'00100');self.assertEqual(q['item_types'],[10]);self.assertEqual(q['second_group_ids'],[12])
        self.assertEqual(q['min_product_price'] if 'min_product_price' in q else None,None)
        self.assertEqual(q['default_vendor_id'],13);self.assertEqual(q['organization_id'],0)
        self.assertEqual(q['distribution_search'],{'express':True,'selfPick':True})

    def test_full_native_response_retained_and_other_shop_results_rejected(self):
        class Client:
            credentials={'kdt_id':'900'}
        client=Client()
        result={'code':200,'success':True,'data':{'paginator':{'total_count':1},'items':[{
            'kdt_id':900,'title':'fixture','price':12800,'id_summary':{'online_item_id':100,'spu_id':200,'offline_item_id':300},'extra':{'keep':None}}]}}
        with patch.object(tool,'gateway',return_value=result):
            self.assertEqual(tool.run(client,{'action':'query'}),result)
        wrong=copy.deepcopy(result);wrong['data']['items'][0]['kdt_id']=901
        with patch.object(tool,'gateway',return_value=wrong),self.assertRaises(ToolError):tool.run(client,{'action':'query'})

    def test_gateway_uses_private_token_snake_case_and_fixed_endpoint(self):
        requests=[]
        class Client:
            credentials={'cookies':{'access_token':'fixture-private-token','carmen_csrf_token':'fixture-csrf'}}
            def _json(self,url):requests.append(url);return {'code':200,'data':{}}
            def redact(self,result):return result
        tool.gateway(Client(),'search',{'keyword':'test','is_sub_query':False,'channel_param':{'channel_type':3,'channels':[]}})
        from urllib.parse import urlsplit,parse_qs
        url=urlsplit(requests[0]);q=parse_qs(url.query)
        self.assertEqual(url.hostname,'gateway.youzan.com');self.assertEqual(url.path,'/api/youzan.item.lite.product.search/1.0.0')
        self.assertEqual(q['is_sub_query'],['false']);self.assertEqual(q['access_token'],['fixture-private-token'])

    def test_redaction_preserves_numeric_business_ids_and_string_structure(self):
        client=YouzanClient({'sid':'fixture-sid','csrf':'fixture-csrf','kdt_id':'94598170','cookies':{'_kdt_id_':'94598170','access_token':'fixture-secret-token'}})
        result=client.redact({'kdt_id':94598170,'id':'94598170','text':'value fixture-secret-token'})
        self.assertEqual(result['kdt_id'],94598170);self.assertEqual(result['id'],'94598170')
        self.assertNotIn('fixture-secret-token',result['text'])
