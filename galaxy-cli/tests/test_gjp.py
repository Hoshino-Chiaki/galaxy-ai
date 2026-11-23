import copy
import io
import json
import unittest
from unittest.mock import patch

from galaxy_cli.gjp.client import Client, PAGER, INVENTORY, SELECTOR, ATTRIBUTES, TEMPLATES
from galaxy_cli.gjp.schemas import normalize
from galaxy_cli.registry import ToolError, load_registry


class ReportFixture(Client):
    def __init__(self):
        self.calls = []

    def post(self, path, body):
        self.calls.append((path, copy.deepcopy(body)))
        if path == INVENTORY:
            return {'GoodsStockListDataSource': {'PagerId': '$new-session$GoodsStockListDataSource'},
                    'stocktype': [{'value': '0', 'text': '全部'}]}
        if path == SELECTOR:
            product = body['clientInfo']['__Params']['typeflag'] == 'P'
            fields = ['ptypeid', 'pfullname', 'psonnum', 'parid'] if product else ['bctypeid', 'bcfullname', 'bcsonnum', 'parid']
            return {'typeds': {'PagerId': '$new-session$typeds', 'QueryParams': {'typeflag': 'P' if product else 'C'},
                              'FirstPageData': {'fields': fields, 'rows': [['00001', '分类', 2, '00000']]}, 'ItemCount': 1}}
        if path == ATTRIBUTES:
            return [{'id': 1, 'title': '品牌15'}]
        if path == PAGER and 'typeflag' in body['queryParams']:
            product = body['queryParams']['typeflag'] == 'P'
            fields = ['ptypeid', 'pfullname', 'psonnum', 'parid'] if product else ['bctypeid', 'bcfullname', 'bcsonnum', 'parid']
            return {'itemList': {'fields': fields, 'rows': [['00001', '分类', 2, '00000']]}, 'itemCount': 1}
        return {'itemList': {'fields': ['ptypeid_barcode', 'avgtotal'], 'rows': [['001234', None]]}, 'itemCount': 101}


class GjpTests(unittest.TestCase):
    def test_defaults_and_filters_are_used_with_fresh_pager(self):
        client = ReportFixture()
        result = client.inventory({'organization_id': '0000100002', 'barcode': '001234', 'page': 2})['data']
        init, page = client.calls
        self.assertEqual(init[0], INVENTORY)
        self.assertNotEqual(init[1]['formId'], TEMPLATES['inventory_init']['formId'])
        self.assertEqual(page[1]['pagerId'], '$new-session$GoodsStockListDataSource')
        query = page[1]['queryParams']
        self.assertEqual(query['ctypeid'], '0000100002')
        self.assertEqual(query['ptypebar'], '001234')
        self.assertEqual(query['pricetypeid'], '')
        self.assertFalse(query['Showzero'])
        self.assertEqual(page[1]['first'], 100)
        self.assertIsNone(result['rows'][0]['avgtotal'])
        self.assertEqual(result['total'], 101)
        self.assertFalse(result['has_more'])
        self.assertEqual(TEMPLATES['inventory_query']['ctypeid'], '')

    def test_zero_stock_is_included_only_when_explicitly_requested(self):
        client = ReportFixture()
        result = client.inventory({'include_zero': True})['data']
        self.assertTrue(client.calls[-1][1]['queryParams']['Showzero'])
        self.assertTrue(result['filters']['include_zero'])

    def test_root_children_and_product_choices(self):
        client = ReportFixture()
        root = client.selection({})['data']
        self.assertEqual(root['options'][0]['id'], '00001')
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[-1][1]['queryParams']['parid'], '00000')
        client.selection({'parent_id': '00001'})
        self.assertEqual(client.calls[-1][1]['queryParams']['parid'], '00001')
        self.assertTrue(client.calls[-1][1]['queryParams']['bc_limit'])
        product = client.product_info({'action': 'products'})['data']
        self.assertEqual(product['options'][0]['name'], '分类')
        self.assertEqual(client.product_info({})['data']['attributes'][0]['title'], '品牌15')

    def test_untrusted_credentials_filters_and_large_pages_are_rejected(self):
        for body in ({'cookie': 'secret'}, {'sid': 'secret'}, {'page_size': 201}, {'include_zero': 1},
                     {'organization_id': "' OR 1=1"}, {'queryParams': {}}, {'stock_type': '9'}):
            with self.subTest(body=body), self.assertRaises(ToolError):
                normalize('inventory', body)

    def test_session_expiry_and_server_exception_are_not_success_or_raw_dump(self):
        client = Client({'domain': 'http://cdmzzaetsc.szbs001.gjpfz.com', 'sid': 'private-sid', 'cookie': 'test-cookie'})
        for payload, code in [({'Message': '会话超时，请刷新该页或重新登录'}, 'guanjia_session_expired'),
                              ({'Message': '没有指定连接字符串', 'ExceptionType': 'InvalidOperationException'}, 'guanjia_session_expired'),
                              ({'Message': 'secret-connection-string', 'ExceptionType': 'ServerError'}, 'gjp_api_error')]:
            response = io.BytesIO(json.dumps(payload).encode())
            response.headers = {}
            with patch.object(client.opener, 'open', return_value=response), self.assertRaises(ToolError) as error:
                client.post(ATTRIBUTES, {})
            self.assertEqual(error.exception.code, code)
            self.assertNotIn('secret-connection-string', str(error.exception))

    def test_three_tools_are_registered(self):
        registry = load_registry()
        self.assertEqual({t['name'] for t in registry.describe(category='gjp')},
                         {'gjp.inventory', 'gjp.organizations', 'gjp.product-info'})


if __name__ == '__main__':
    unittest.main()
