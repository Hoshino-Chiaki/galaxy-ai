import contextlib
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.error import URLError

from galaxy_cli.cli import main
from galaxy_cli.registry import ToolError, load_registry
from galaxy_cli.tools.youzan.customer_get import normalize, CUSTOMER_INFO, RELATIONS, BEHAVIOR
from galaxy_cli.youzan_http import YouzanClient

CREDENTIALS = {'sid': 'fixture-private-sid', 'csrf': 'fixture-private-csrf', 'kdt_id': '123456', 'cookies': {}}
CUSTOMER = {'code': 0, 'msg': 'success', 'data': {'baseInfo': {'userId': 2222, 'name': '', 'birthday': None},
    'customerInfo': {'extra': [None, False, 0, '']}, 'assetInfo': {'balance': 0}, 'futureField': {'nested': []}}, 'trace': 'preserved'}
RELATION_DATA = {'code': 0, 'msg': '', 'data': []}
BEHAVIOR_DATA = {'code': 0, 'msg': 'ok', 'data': {'recommendSwitch': {'recommendSwitch': False},
    'allBehavior': [{'name': '购买记录', 'num': 1, 'amount': 0, 'time': 1, 'extra': None}]}}


class Adapter:
    def __init__(self):
        self.requests = []
        self.failure = None
        self.customer = CUSTOMER
    def open(self, request, **kwargs):
        self.requests.append(request)
        path = urlsplit(request.full_url).path
        if self.failure == path:
            raise URLError('fixture-private-sid')
        response = self.customer if path == urlsplit(CUSTOMER_INFO).path else RELATION_DATA if path == urlsplit(RELATIONS).path else BEHAVIOR_DATA
        return io.BytesIO(json.dumps(response).encode())


class CustomerGetTests(unittest.TestCase):
    def test_each_registered_business_tool_has_own_category_module(self):
        registry = load_registry()
        self.assertEqual(registry.get('youzan.customer-search').handler.__module__, 'galaxy_cli.tools.youzan.customer_search')
        self.assertEqual(registry.get('youzan.customer-get').handler.__module__, 'galaxy_cli.tools.youzan.customer_get')
        self.assertEqual(set(registry.categories()), {'excel', 'youzan', 'file', 'feishu', 'time'})

    def test_all_three_native_responses_and_fields_are_preserved(self):
        adapter = Adapter()
        with patch('urllib.request.build_opener', return_value=adapter):
            result = YouzanClient(CREDENTIALS).customer_get({'account_id': 'fixture-customer'})
        self.assertEqual(result, {'customer': CUSTOMER, 'relations': RELATION_DATA, 'behavior': BEHAVIOR_DATA})
        self.assertEqual(len(adapter.requests), 3)
        for request in adapter.requests:
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(urlsplit(request.full_url).hostname, 'store.youzan.com')
            self.assertEqual(request.get_header('Csrf-token'), CREDENTIALS['csrf'])
        self.assertEqual(parse_qs(urlsplit(adapter.requests[0].full_url).query), {'accountId': ['fixture-customer'], 'accountType': ['1']})
        self.assertEqual(parse_qs(urlsplit(adapter.requests[1].full_url).query), {'userId': ['2222']})
        self.assertEqual(parse_qs(urlsplit(adapter.requests[2].full_url).query), {'accountId': ['fixture-customer'], 'accountType': ['1']})

    def test_partial_failure_keeps_successful_sections(self):
        adapter = Adapter(); adapter.failure = urlsplit(BEHAVIOR).path
        with patch('urllib.request.build_opener', return_value=adapter):
            result = YouzanClient(CREDENTIALS).customer_get({'account_id': 'fixture-customer'})
        self.assertEqual(result['customer'], CUSTOMER)
        self.assertEqual(result['relations'], RELATION_DATA)
        self.assertEqual(result['behavior']['error']['code'], 'upstream_error')
        self.assertNotIn('fixture-private-sid', json.dumps(result))

    def test_main_business_error_does_not_query_extra_sections(self):
        adapter = Adapter(); adapter.customer = {'code': 10100, 'msg': '未通过验证', 'data': None, 'extra': []}
        with patch('urllib.request.build_opener', return_value=adapter):
            result = YouzanClient(CREDENTIALS).customer_get({'account_id': 'fixture-customer'})
        self.assertEqual(result, {'customer': adapter.customer})
        self.assertEqual(len(adapter.requests), 1)

    def test_missing_relation_user_id_does_not_guess_for_another_account_type(self):
        adapter = Adapter(); adapter.customer = {'code': 0, 'msg': '', 'data': {'baseInfo': {}}}
        with patch('urllib.request.build_opener', return_value=adapter):
            result = YouzanClient(CREDENTIALS).customer_get({'account_id': 'external-id', 'account_type': 2})
        self.assertEqual(result['relations']['error']['code'], 'customer_id_unavailable')
        self.assertEqual(len(adapter.requests), 2)
        self.assertEqual(result['behavior'], BEHAVIOR_DATA)

    def test_invalid_parameters_never_submit_credentials_or_urls(self):
        for arguments in [{}, {'account_id': ''}, {'account_id': 'a\nb'}, {'account_id': 'id', 'sid': 'private'},
                          {'account_id': 'id', 'url': 'https://outside.example'}, {'account_id': 'id', 'account_type': True},
                          {'account_id': 'id', 'account_type': -1}]:
            with self.subTest(arguments=arguments), self.assertRaises(ToolError):
                normalize(arguments)

    def test_cli_returns_all_three_sections_without_metadata_wrapper(self):
        adapter = Adapter(); output = io.StringIO()
        with patch('urllib.request.build_opener', return_value=adapter), patch('sys.stdin', io.StringIO(json.dumps({'categories': {'youzan': CREDENTIALS}}))), contextlib.redirect_stdout(output):
            code = main(['youzan', 'customer-get', '--input', '{"account_id":"fixture-customer"}'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), {'customer': CUSTOMER, 'relations': RELATION_DATA, 'behavior': BEHAVIOR_DATA})


if __name__ == '__main__':
    unittest.main()
