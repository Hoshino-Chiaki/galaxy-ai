import copy
import io
import json
import tempfile
import unittest
from urllib.parse import parse_qs

from galaxy_cli.registry import ToolError, load_registry
from galaxy_cli.tools.youzan import product_group as group
from galaxy_cli.youzan_catalog import permissions, CatalogClient


class Client:
    credentials = {'kdt_id': '900'}
    def __init__(self):
        self.rows, self.configs, self.calls = [], {}, []
        self.rejected, self.mismatch = False, False
    def branches(self): return [{'id': '901', 'name': 'Fixture branch'}]
    def request(self, path, **kwargs):
        self.calls.append((path, copy.deepcopy(kwargs)))
        params = kwargs.get('params', {})
        if path == group.CREATE:
            if self.rejected: return {'code': 123, 'msg': 'rejected', 'data': None}
            config = json.loads(kwargs['body']['data'])[0]
            identifier = str(100 + len(self.rows))
            row = {'groupId': int(identifier), 'title': config['title'], 'upperId': config.get('upper_id', 0)}
            self.rows.append(row); self.configs[identifier] = config
            if self.mismatch: self.configs[identifier]['title'] = 'Different'
            return {'code': 0, 'data': {'id': int(identifier), 'alias': 'fixture', 'native_extra': 'kept'}}
        if path.endswith('queryGroupListJSON.json'):
            return {'code': 0, 'data': [copy.deepcopy(r) for r in self.rows if str(r['groupId']) == params['groupIds']]}
        if path.endswith('querySubGroupList.json'):
            return {'code': 0, 'data': [copy.deepcopy(r) for r in self.rows if str(r.get('upperId')) == params['upperId']]}
        if path.endswith('listMultiLevelGroupPage.json'):
            rows = [r for r in self.rows if not r.get('upperId') and params.get('keyword', '') in r['title']]
            size, page = params['pageSize'], params['p']
            return {'code': 0, 'data': {'items': [{'first': copy.deepcopy(r), 'second': []} for r in rows[(page-1)*size:page*size]], 'paginator': {'totalCount': len(rows)}}}
        if path.endswith('item.json'):
            return {'code': 0, 'data': {'id': int(params['id']), 'components': json.dumps([self.configs[params['id']]])}}
        raise AssertionError(path)


class ProductGroupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.context = {'catalog_lock_directory': self.temp.name}
        self.client = Client()
    def tearDown(self): self.temp.cleanup()
    def create(self, **options): return group.run(self.client, self.context, {'action': 'create', 'name': 'Example', **options})
    def writes(self): return [c for c in self.client.calls if c[0] == group.CREATE]

    def test_minimal_create_defaults_and_full_native_response(self):
        result = self.create()
        self.assertEqual(result['data']['native_extra'], 'kept')
        config = json.loads(self.writes()[0][1]['body']['data'])[0]
        self.assertEqual(config['title'], 'Example'); self.assertNotIn('upper_id', config)
        self.assertEqual(config['size'], '2'); self.assertEqual(config['buy_btn_type'], '6')
        self.assertNotIn('product_id', config); self.assertNotIn('button_text', config)

    def test_child_create_checks_parent_and_refuses_third_level(self):
        parent = str(self.create()['data']['id'])
        child = str(self.create(name='Child', parent_id=parent)['data']['id'])
        self.assertEqual(self.client.configs[child]['upper_id'], int(parent))
        with self.assertRaises(ToolError) as failure: self.create(name='Third', parent_id=child)
        self.assertEqual(failure.exception.code, 'group_level_unsupported')
        self.assertEqual(len(self.writes()), 2)

    def test_all_page_options_map_and_boolean_false_is_preserved(self):
        self.create(subtitle='Alias', show_group_name=True, sort_rule='custom', first_priority='hot', second_priority='index_desc',
            show_search_bar=False, list_style='big', card_style='minimal', show_buy_button=False,
            buy_button_style=4, button_text='Buy', show_product_name=False, show_price=False,
            group_filter=True, color_style='default', content_html='<p>Details</p>', hidden_shop_ids=['901'])
        c = self.client.configs['100']
        expected = {'subtitle':'Alias','show_tag_title':'1','sort_rule':'1','first_priority':'3','second_priority':'0',
            'show_search_bar':'0','size':'0','size_type':'2','buy_btn':'0','buy_btn_type':'4','button_text':'Buy',
            'show_title':'0','price':'0','group_select_switch':'1','price_btn_style':'1','content':'<p>Details</p>'}
        for k,v in expected.items(): self.assertEqual(c[k], v)
        self.assertEqual(c['groupVisibleParam']['hiddenSubKdtIds'], ['901'])

    def test_duplicate_and_foreign_parent_or_store_never_post(self):
        self.create()
        for args, code in [({}, 'group_exists'), ({'name':'New','parent_id':'999'}, 'group_not_found'),
                           ({'name':'New','hidden_shop_ids':['999']}, 'shop_denied')]:
            with self.assertRaises(ToolError) as failure: self.create(**args)
            self.assertEqual(failure.exception.code, code)
        self.assertEqual(len(self.writes()), 1)

    def test_failure_and_uncertain_write_are_not_reported_as_verified_success(self):
        self.client.rejected = True
        self.assertEqual(self.create()['code'], 123)
        self.client.rejected = False; self.client.mismatch = True
        with self.assertRaises(ToolError) as failure: self.create()
        self.assertEqual(failure.exception.code, 'write_unverified')
        self.assertIn('100', failure.exception.message)

    def test_invalid_inputs_and_active_html_are_rejected(self):
        for kwargs in ({'name':' '}, {'name':'a'*51}, {'name':'line\nnew'}, {'card_style':'waterfall','list_style':'big'},
                       {'sort_rule':'custom','first_priority':'created_desc','second_priority':'created_asc'},
                       {'csrf':'injected'}, {'kdt_id':'other'}, {'show_price':'false'}):
            with self.assertRaises(ToolError): self.create(**kwargs)
        with self.assertRaises(ToolError): self.create(content_html='<script>alert(1)</script>')
        self.assertFalse(self.writes())

    def test_single_priority_remains_optional_and_style_infers_only_needed_default(self):
        args = group.normalize({'action':'create','name':'A','first_priority':'created_desc','card_style':'waterfall'})
        self.assertEqual(args['second_priority'], 'index_desc'); self.assertEqual(args['list_style'], 'small')
        self.assertEqual(args['sort_rule'], 'custom')

    def test_reads_need_no_product_and_schema_denies_write_without_permission(self):
        self.assertEqual(group.run(self.client, self.context, {'action':'list'})['code'], 0)
        self.assertEqual(permissions('product-group', {'action':'list'}), {'youzan:product:read'})
        self.assertEqual(permissions('product-group', {'action':'create'}), {'youzan:product:read','youzan:product:update'})
        definition = load_registry().get('youzan.product-group')
        self.assertNotIn('HAR', definition.description)
        self.assertIn('name', definition.description)

    def test_transport_uses_group_referer_and_form_encoded_configuration(self):
        client = CatalogClient({'sid':'fixture-private-sid','csrf':'fixture-private-csrf','kdt_id':'900'},
                               {'workspace_directory':self.temp.name})
        requests = []
        class Opener:
            def open(self, request, **_):
                requests.append(request)
                return io.BytesIO(b'{"code":0,"data":{"id":100}}')
        client.opener = Opener()
        client.request(group.CREATE, method='POST', form=True, body={'data':json.dumps([{'type':'config','title':'A'}])})
        request = requests[0]
        self.assertEqual(request.full_url, 'https://store.youzan.com' + group.CREATE)
        self.assertEqual(request.headers['Referer'], 'https://store.youzan.com/v4/goods/shop-tag/index')
        self.assertEqual(request.headers['Content-type'], 'application/x-www-form-urlencoded')
        self.assertEqual(parse_qs(request.data.decode())['csrf_token'], ['fixture-private-csrf'])


if __name__ == '__main__': unittest.main()
