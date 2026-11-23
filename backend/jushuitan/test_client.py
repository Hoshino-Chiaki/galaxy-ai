import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from client import Client, Failure, LOGIN, IDENTITY, INVENTORY, private_write, serialize_cookies, cookie_jar


def cookie(name, value):return {'name': name, 'value': value, 'domain': '.erp321.com', 'path': '/', 'secure': True}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {'account':'fixture-account','password':'fixture-password','device':{'j_d_3':'fixture-device','v_d_144':'fixture-client'},
            'bootstrap_cookies':[cookie('u_json','old-session')], 'login_headers':{},'identity_headers':{},'inventory_headers':{},
            'inventory_data':{'sku_type':1,'queryFlds':['sku_id','qty','orderable'],'orderBy':'','stock_type':6},'inventory_from':'stockInventoryManagement'}
        private_write(self.root/'config.json',self.config);self.client=Client(self.root)
        self.fresh=[cookie('u_json','new-session'),cookie('u_cid','new-client-token'),cookie('u_sso_token','new-sso-token')]
        self.login_result={'code':0,'data':{'hasRisk':False,'idaasHasRisk':False,'msg':'Please reset your password'},
            'cookie':{'u_id':'100','u_co_id':'200','u_json':'new-session','u_cid':'new-client-token','u_sso_token':'new-sso-token','u_exp':2000000000}}
        self.user={'code':0,'data':{'uid':100,'coId':200,'userName':'Test','companyName':'Company','roleIds':[],'isAdmin':False}}

    def stock_session(self):
        state={'uid':'100','coid':'200','account':self.config['account'],'cookies':self.fresh,'device':self.config['device']}
        private_write(self.root/'session.json',state);return state

    def test_login_verifies_identity_before_saving_no_secrets_in_result(self):
        def post(endpoint,body,headers):
            if endpoint==LOGIN:
                self.assertEqual(body['data']['password'],'fixture-password');self.client.use_cookies(self.fresh)
                return copy.deepcopy(self.login_result)
            self.assertEqual(endpoint,IDENTITY);self.assertEqual(body,{'data':{},'uid':100,'coid':200})
            self.assertFalse((self.root/'session.json').exists())
            return copy.deepcopy(self.user)
        self.client.post=Mock(side_effect=post)
        result=self.client.login();self.assertTrue(result['verified']);self.assertIn('reset',result['warning'])
        self.assertEqual((self.root/'session.json').stat().st_mode & 0o777,0o600)
        text=json.dumps(result);self.assertNotIn('new-sso-token',text);self.assertNotIn('fixture-password',text)
        self.assertEqual(self.client.post.call_count,2)

    def test_failed_or_risky_login_does_not_overwrite_old_session(self):
        self.stock_session();before=(self.root/'session.json').read_bytes()
        for result in [{'code':403,'msg':'denied'}, {**self.login_result,'data':{'hasRisk':True}}, {'code':0,'data':{}}]:
            self.client.post=Mock(return_value=copy.deepcopy(result))
            with self.assertRaises(Failure):self.client.login()
            self.assertEqual((self.root/'session.json').read_bytes(),before)
            self.assertEqual(self.client.post.call_count,1)

    def test_identity_mismatch_does_not_save_unverified_session(self):
        def post(endpoint,*_):
            if endpoint==LOGIN:self.client.use_cookies(self.fresh);return copy.deepcopy(self.login_result)
            return {'code':0,'data':{'uid':100,'coId':999}}
        self.client.post=Mock(side_effect=post)
        with self.assertRaises(Failure):self.client.login()
        self.assertFalse((self.root/'session.json').exists())

    def test_inventory_uses_saved_login_scope_and_preserves_full_response(self):
        self.stock_session();result={'code':0,'page':{'count':-1},'data':[{'co_id':200,'owner_co_id':0,'sku_id':'sku-1','qty':5.0,'orderable':3.0,'unknown':None}]}
        self.client.post=Mock(return_value=copy.deepcopy(result))
        actual=self.client.inventory(page=2,page_size=20)
        self.assertEqual(actual,result)
        url,body,_=self.client.post.call_args.args
        from urllib.parse import urlsplit,parse_qs
        self.assertEqual(urlsplit(url).hostname,'apiweb.erp321.com')
        self.assertEqual(parse_qs(urlsplit(url).query)['owner_co_id'],['200'])
        self.assertEqual(body['uid'],'100');self.assertEqual(body['coid'],'200')
        self.assertEqual(body['page'],{'currentPage':2,'pageSize':20,'pageAction':1})
        self.assertEqual(body['data'],self.config['inventory_data'])
        self.assertEqual({c.name:c.value for c in self.client.jar}['u_sso_token'],'new-sso-token')
        self.client.inventory(action='count');self.assertEqual(self.client.post.call_args.args[1]['page']['pageAction'],2)

    def test_inventory_scope_failure_and_invalid_pages_are_rejected(self):
        self.stock_session();self.client.post=Mock(return_value={'code':0,'page':{},'data':[{'co_id':999}]})
        with self.assertRaises(Failure):self.client.inventory()
        self.client.post.reset_mock()
        for options in [{'page':0},{'page_size':0},{'page_size':51},{'action':'write'}]:
            with self.assertRaises(Failure):self.client.inventory(**options)
        self.client.post.assert_not_called()

    def test_new_login_uses_latest_saved_device_state(self):
        previous=self.stock_session();previous['device']['v_d_144']='updated-client'
        private_write(self.root/'session.json',previous)
        def post(endpoint,body,_):
            if endpoint==LOGIN:
                self.assertEqual(body['data']['v_d_144'],'updated-client');self.client.use_cookies(self.fresh);return copy.deepcopy(self.login_result)
            return copy.deepcopy(self.user)
        self.client.post=post;self.client.login()

    def test_no_arbitrary_host_and_no_shared_account_session(self):
        with self.assertRaises(Failure):self.client.post('https://example.com/collect',{}, {})
        state=self.stock_session();state['account']='another-account';private_write(self.root/'session.json',state)
        with self.assertRaises(Failure):self.client.inventory()

    def test_cookie_round_trip_and_response_cookie_updates(self):
        self.stock_session();jar=cookie_jar(self.fresh)
        self.assertEqual(len(cookie_jar(serialize_cookies(jar))),3)
        def post(*_):
            self.client.use_cookies([*self.fresh,cookie('u_env','new-env')]);return copy.deepcopy(self.user)
        self.client.post=post;self.client.identity()
        state=json.loads((self.root/'session.json').read_text())
        self.assertIn('u_env',[c['name'] for c in state['cookies']])


if __name__=='__main__':unittest.main()
