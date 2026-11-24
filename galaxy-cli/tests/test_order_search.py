import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
from galaxy_cli.registry import ToolError
from galaxy_cli.tools.youzan import order_search as tool

class OrderTests(unittest.TestCase):
    @staticmethod
    def page(number,total,identifiers):
        return {'code':200,'success':True,'data':{'total':total,'page_no':number,'page_size':2,'data':[
            {'main_order_info':{'kdt_id':123,'order_no':identifier},'unknown':{'preserve':[None,False]}}
            for identifier in identifiers]}}

    def test_all_pages_preserves_full_pages_in_account_file_and_keeps_filters(self):
        class Client:credentials={'kdt_id':'123'}
        responses=[self.page(1,5,['a','b']),self.page(2,5,['c','d']),self.page(3,5,['e'])]
        with tempfile.TemporaryDirectory() as directory,patch.object(tool,'gateway',side_effect=responses) as gateway:
            result=tool.run(Client(),{'action':'query','all_pages':True,'page_size':2,'goods_title':'鞋','has_star':False},
                {'cache_directory':directory,'cache_identity':{'user_id':'alice','workspace_id':'w'}})
            self.assertTrue(result['data']['complete']);self.assertEqual(result['data']['count'],5)
            self.assertEqual(result['data']['pages'],3)
            path=Path(directory)/result['data']['path']
            self.assertEqual([json.loads(line) for line in path.read_text().splitlines()],responses)
            self.assertEqual(path.stat().st_size,result['data']['size'])
            self.assertLess(len(json.dumps(result)),1600)
            self.assertNotIn('unknown',json.dumps(result))
            params=[call.args[2] for call in gateway.call_args_list]
            self.assertEqual([p['page_no'] for p in params],[1,2,3])
            for p in params:
                self.assertEqual(p['goods_title'],'鞋');self.assertFalse(p['has_star']);self.assertNotIn('all_pages',p)

    def test_all_pages_zero_and_single_page_remain_valid(self):
        class Client:credentials={'kdt_id':'123'}
        with tempfile.TemporaryDirectory() as directory,patch.object(tool,'gateway',return_value=self.page(1,0,[])) as gateway:
            result=tool.run(Client(),{'action':'query','all_pages':True},{'cache_directory':directory,'cache_identity':{'user_id':'alice','workspace_id':'w'}})
            self.assertTrue(result['data']['complete']);self.assertEqual(result['data']['count'],0)
            self.assertEqual(gateway.call_count,1)
            self.assertEqual(gateway.call_args.args[2]['page_size'],50)

    def test_3135_orders_are_scanned_in_63_pages_with_small_tool_result(self):
        class Client:credentials={'kdt_id':'123'}
        def response(client,name,params):
            start=(params['page_no']-1)*50
            value=self.page(params['page_no'],3135,[str(n) for n in range(start,min(start+50,3135))])
            value['data']['page_size']=50
            return value
        with tempfile.TemporaryDirectory() as directory,patch.object(tool,'gateway',side_effect=response) as gateway:
            result=tool.run(Client(),{'action':'query','all_pages':True},{'cache_directory':directory,'cache_identity':{'user_id':'alice','workspace_id':'w'}})
            self.assertTrue(result['data']['complete']);self.assertEqual(result['data']['count'],3135)
            self.assertEqual(gateway.call_count,63);self.assertLess(len(json.dumps(result)),1600)
            self.assertEqual(len((Path(directory)/result['data']['path']).read_text().splitlines()),63)

    def test_all_pages_failure_keeps_partial_file_without_claiming_complete(self):
        class Client:credentials={'kdt_id':'123'}
        first=self.page(1,4,['a','b'])
        cases=[{'code':401,'success':False},self.page(2,4,[]),self.page(2,4,['a','b']),self.page(2,5,['c','d']),self.page(1,4,['c','d'])]
        bad_shop=self.page(2,4,['c','d']);bad_shop['data']['data'][0]['main_order_info']['kdt_id']=999
        cases.append(bad_shop)
        for second in cases:
            with self.subTest(second=second),tempfile.TemporaryDirectory() as directory,patch.object(tool,'gateway',side_effect=[first,second]):
                result=tool.run(Client(),{'action':'query','all_pages':True,'page_size':2},{'cache_directory':directory,'cache_identity':{'user_id':'alice','workspace_id':'w'}})
                self.assertFalse(result['success']);self.assertFalse(result['data']['complete'])
                self.assertIn('error',result);self.assertEqual(result['data']['count'],2)
                self.assertEqual([json.loads(line) for line in (Path(directory)/result['data']['path']).read_text().splitlines()],[first])

    def test_all_pages_requires_server_directory_and_rejects_linked_directory(self):
        class Client:credentials={'kdt_id':'123'}
        with self.assertRaises(ToolError):tool.run(Client(),{'action':'query','all_pages':True})
        with self.assertRaises(ToolError):tool.normalize({'action':'query','all_pages':True,'page':2})
        with self.assertRaises(ToolError):tool.normalize({'action':'query','all_pages':'true'})
        with tempfile.TemporaryDirectory() as directory,tempfile.TemporaryDirectory() as other:
            (Path(directory)/'youzan').symlink_to(other,target_is_directory=True)
            with patch.object(tool,'gateway') as gateway,self.assertRaises(ToolError):
                tool.run(Client(),{'action':'query','all_pages':True},{'cache_directory':directory,'cache_identity':{'user_id':'alice','workspace_id':'w'}})
            gateway.assert_not_called();self.assertEqual(list(Path(other).iterdir()),[])

    def test_captured_query_mapping_preserves_all_filters(self):
        args=tool.normalize({'action':'query','express_type':2,'order_type':10,'is_reminder_status':True,'has_star':True,
            'buy_way':204,'shop_kdt_ids':['96058710'],'order_sources':['3'],'sort':0,'start_time':'2025-11-20','end_time':'2026-11-20'})
        q=tool.query_params(args)
        self.assertEqual(q,{'page_no':1,'page_size':20,'time_type':'book_time','search_label':'order_nos','support_product':True,
            'express_type':2,'order_type':10,'is_reminder_status':True,'has_star':True,'buy_way':204,'shop_kdt_ids':[96058710],
            'order_sources':['3'],'sort':0,'start_time':1763568000000,'end_time':1795190399999})

    def test_optional_filters_and_false_zero_are_not_lost(self):
        q=tool.query_params(tool.normalize({'action':'query','search_field':'A， B','goods_title':'鞋','brand_id_list':['12'],
            'sale_way':0,'order_state_list':[3,5],'feedback_state':250,'express_type':0,'buy_way':0,'is_reminder_status':False,
            'has_star':False,'marketing_type':3,'table_id':'8','real_pay_min':0,'real_pay_max':129}))
        self.assertEqual(q['search_field'],'A,B');self.assertEqual(q['brand_id_list'],[12]);self.assertEqual(q['table_id'],8)
        self.assertFalse(q['has_star']);self.assertEqual(q['real_pay_min'],0);self.assertEqual(q['real_pay_max'],129)
        default=tool.query_params(tool.normalize({'action':'query'}))
        self.assertNotIn('shop_kdt_ids',default);self.assertNotIn('order_type',default);self.assertNotIn('start_time',default)

    def test_bad_filters_are_rejected_before_request(self):
        for extra in [{'sid':'x'},{'url':'https://example.org'},{'start_time':'2026-11-20'},
            {'start_time':'2026-11-21','end_time':'2026-11-20'},{'start_time':'2026-02-30','end_time':'2026-11-20'},
            {'time_type':'pay_time','sort':0},{'order_state':3,'order_state_list':[5]},{'sale_way':-1},
            {'search_label':'tel'},{'search_field':'1,,2'},{'search_field':','.join(['a']*101)},
            {'search_label':'buyer_phone_last_4','search_field':'123'},{'real_pay_min':2,'real_pay_max':1}]:
            with self.subTest(extra=extra),self.assertRaises(ToolError):tool.normalize({'action':'query',**extra})

    def test_fixed_endpoint_json_encoding_and_native_result_preserved(self):
        captured=[]
        result={'code':200,'success':True,'data':{'total':2,'page_no':1,'page_size':1,'data':[{'main_order_info':{'kdt_id':456,'head_kdt_id':123},'payment_info':{'real_pay':12900},'unknown':{'retain':None}}]}}
        class Client:
            credentials={'kdt_id':'123','cookies':{'access_token':'private-fixture','carmen_csrf_token':'csrf-fixture'}}
            def _json(self,url):captured.append(url);return copy.deepcopy(result)
            def redact(self,value):return value
            def branches(self):return [{'id':'456','name':'分店'}]
        client=Client()
        actual=tool.run(client,{'action':'query','page_size':1,'shop_kdt_ids':['456'],'order_sources':['3'],'has_star':False})
        self.assertEqual(actual,result)
        url=urlsplit(captured[0]);q=parse_qs(url.query)
        self.assertEqual(url.hostname,'gateway.youzan.com');self.assertEqual(url.path,'/api/'+tool.ENDPOINTS['search'])
        self.assertEqual(json.loads(q['shop_kdt_ids'][0]),[456]);self.assertEqual(json.loads(q['order_sources'][0]),['3'])
        self.assertEqual(q['has_star'],['false'])
        with self.assertRaises(ToolError):tool.run(client,{'action':'query','shop_kdt_ids':['999']})
        self.assertEqual(len(captured),1)
        wrong=copy.deepcopy(result);wrong['data']['data'][0]['main_order_info']={'kdt_id':999,'head_kdt_id':998}
        with patch.object(tool,'gateway',return_value=wrong),self.assertRaises(ToolError):tool.run(client,{'action':'query'})
        with patch.object(tool,'gateway',return_value={'code':200,'success':True,'data':None}),self.assertRaises(ToolError):tool.run(client,{'action':'query'})

    def test_dynamic_options_use_correct_fixed_sources(self):
        class Client:
            credentials={'kdt_id':'123'}
        for section,kind in [('order_types','ordertype'),('payment_methods','payway'),('order_sources','ordersource')]:
            with patch.object(tool,'gateway',return_value={'data':[]}) as gateway:
                tool.run(Client(),{'action':'options','section':section});self.assertEqual(gateway.call_args.args[2],{'type':kind})
