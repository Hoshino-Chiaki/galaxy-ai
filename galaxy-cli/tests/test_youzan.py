import contextlib
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

from galaxy_cli.cli import main
from galaxy_cli.registry import ToolError, load_registry
from galaxy_cli.contracts.youzan import normalize, request_body
from galaxy_cli.youzan_http import YouzanClient

SID, CSRF, TOKEN = 'fixture-private-sid-0001', 'fixture-private-csrf-0002', 'fixture-business-token-0003'
CREDS = {'sid': SID, 'csrf': CSRF, 'kdt_id': '123456', 'shop_name': '测试店铺', 'cookies': {'access_token': TOKEN}}


class Adapter:
    def __init__(self):
        self.calls = []
    def open(self, request, **kwargs):
        self.calls.append(request)
        if urlsplit(request.full_url).hostname == 'gateway.youzan.com':
            self.assert_token = parse_qs(urlsplit(request.full_url).query)['access_token'] == [TOKEN]
            body = {'code': 200, 'success': True, 'data': {'paginator': {'total_count': 2}, 'items': [
                {'store_kdt_id': 101, 'store_name': '直营门店-东区'}, {'store_kdt_id': 102, 'store_name': '直营门店-西区'}]}}
        else:
            body = {'code': 0, 'data': {'total': 1, 'list': [{'accountId': 'fixture-customer', 'accountType': 1,
                'name': '测试客户 ' + SID, 'nickName': '昵称', 'showMobileDisplay': '138****1234',
                'authInfo': {'isMobileAuth': True}, 'tradeCount': 2, 'tags': ['测试'], 'lastTradeTime': 1793548800000}]}}
        return io.BytesIO(json.dumps(body).encode())


class YouzanTests(unittest.TestCase):
    def test_customer_search_routes_and_credentials_follow_shop_origin(self):
        for origin, path in [('https://www.youzan.com', '/v4/scrm/api/customer/manage/customerList'),
                             ('https://crm.youzan.com', '/crm/member/customer/api/manage/customerList')]:
            adapter = Adapter()
            with self.subTest(origin=origin), patch('urllib.request.build_opener', return_value=adapter):
                client = YouzanClient({**CREDS, 'origin': origin}, customer_search_only=True)
                client.search({'keyword': '指定客户', 'page': 2})
                request = adapter.calls[0]
                self.assertEqual(request.full_url, origin + path)
                self.assertEqual(request.headers['Origin'], origin)
                self.assertEqual(request.headers['Csrf-token'], CSRF)
                body = json.loads(request.data)
                self.assertEqual(body['query']['keyword'], '指定客户')
                self.assertEqual(body['page'], 2)
                if origin == 'https://crm.youzan.com':
                    self.assertIs(body['needOpenId'], True)
                with self.assertRaises(ToolError):
                    client._json(origin + '/unverified-api')
            with self.assertRaises(ToolError):
                YouzanClient({**CREDS, 'origin': origin})
        with self.assertRaises(ToolError):
            YouzanClient({**CREDS, 'origin': 'https://outside.example'}, customer_search_only=True)

    def test_help_category_and_nested_command_discovery(self):
        catalog = load_registry().invoke('help', {})
        self.assertIn('youzan', {t['category'] for t in catalog['data']})
        self.assertIn('youzan.customer-search', {t['tool'] for t in load_registry().invoke('help', {'category': 'youzan'})['data']})
        for arguments in [['help', 'youzan'], ['help', '--category', 'youzan'], ['youzan', 'help'], ['youzan', 'customer-search', '--help']]:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(arguments)
            self.assertEqual(code, 0)
            self.assertNotIn('input_schema', out.getvalue())
            self.assertIn('data', json.loads(out.getvalue()))

    def test_date_ranges_and_filter_mapping(self):
        body = request_body({'last_trade_start': '2026-11-01', 'last_trade_end': '2026-11-08',
            'customer_created_start': '2026-09-01', 'member_created_end': '2026-11-08 12:30:00',
            'mobile_authorized': False, 'wecom_friend': True, 'page': 2, 'page_size': 5}, ['101'])
        self.assertEqual(body['query']['lastTradeTimeEnd'], '2026-11-08 23:59:59')
        self.assertEqual(body['query']['createdAtStart'], '2026-09-01 00:00:00')
        self.assertEqual(body['query']['memberCreatedAtEnd'], '2026-11-08 12:30:00')
        self.assertIs(body['query']['isAuthMobile'], False)
        self.assertEqual(body['query']['wecomStaffRelation'], 2)
        self.assertEqual(body['query']['relatedKdtIds'], [101])
        self.assertEqual(body['page'], 2)

    def test_invalid_ranges_types_credentials_and_url_overrides(self):
        for body in [{'sid': SID}, {'token': TOKEN}, {'kdt_id': 'other'}, {'user_id': 'other'}, {'url': 'https://outside.example'},
                     {'mobile_authorized': 'true'}, {'page_size': 100000}, {'branch_names': [' ']},
                     {'last_trade_start': '2026-02-30'}, {'last_trade_start': '2026-11-08', 'last_trade_end': '2026-11-01'}]:
            with self.subTest(keys=list(body)), self.assertRaises(ToolError):
                normalize(body)

    def test_category_pipe_real_handler_projects_fields_and_removes_secrets(self):
        adapter, out = Adapter(), io.StringIO()
        with patch('urllib.request.build_opener', return_value=adapter), patch('sys.stdin', io.StringIO(json.dumps({'categories': {'youzan': CREDS}}))), contextlib.redirect_stdout(out):
            code = main(['youzan', 'customer-search', '--input', '{"keyword":"测试客户"}'])
        self.assertEqual(code, 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result['data']['total'], 1)
        self.assertEqual(result['data']['list'][0]['showMobileDisplay'], '138****1234')
        self.assertEqual(result['data']['list'][0]['name'], '测试客户 [已隐藏凭证]')
        self.assertEqual(adapter.calls[0].headers['Csrf-token'], CSRF)
        self.assertEqual(json.loads(adapter.calls[0].data)['query']['keyword'], '测试客户')
        for value in [SID, CSRF, TOKEN]:
            self.assertNotIn(value, out.getvalue())

    def test_branch_names_resolve_using_data_permission_list(self):
        adapter = Adapter()
        with patch('urllib.request.build_opener', return_value=adapter):
            result = YouzanClient(CREDS).search({'branch_names': ['东区'], 'wecom_friend': False})
        self.assertEqual(set(result), {'code', 'data'})
        self.assertIn('accountId', result['data']['list'][0])
        self.assertIn('authInfo', result['data']['list'][0])
        self.assertTrue(adapter.assert_token)
        self.assertEqual(json.loads(adapter.calls[-1].data)['query']['relatedKdtIds'], [101])
        self.assertEqual(json.loads(adapter.calls[-1].data)['query']['wecomStaffRelation'], 0)

    def test_ambiguous_or_unpermitted_branch_never_searches_customer(self):
        for filters in [{'branch_names': ['直营门店']}, {'branch_names': ['不存在']}, {'branch_ids': ['999']}]:
            adapter = Adapter()
            with patch('urllib.request.build_opener', return_value=adapter), self.assertRaises(ToolError):
                YouzanClient(CREDS).search(filters)
            self.assertEqual(len(adapter.calls), 1)
            self.assertEqual(urlsplit(adapter.calls[0].full_url).hostname, 'gateway.youzan.com')

    def test_no_category_credentials_is_not_a_shared_account_fallback(self):
        out = io.StringIO()
        with patch('sys.stdin', io.StringIO('{}')), contextlib.redirect_stdout(out):
            self.assertNotEqual(main(['youzan', 'customer-search']), 0)
        self.assertEqual(json.loads(out.getvalue())['error']['code'], 'connection_required')

    def test_redirect_or_upstream_body_does_not_expose_tokens(self):
        import urllib.error
        class Rejected:
            def open(self, request, **kwargs):
                raise urllib.error.HTTPError(request.full_url, 302, SID, {'Location': 'https://outside.example?token=' + TOKEN}, None)
        with patch('urllib.request.build_opener', return_value=Rejected()), self.assertRaises(ToolError) as error:
            YouzanClient(CREDS).search({})
        self.assertEqual(error.exception.code, 'credential_expired')
        for value in [SID, CSRF, TOKEN]:
            self.assertNotIn(value, error.exception.message)


class FullResponseTests(unittest.TestCase):
    def test_all_business_fields_values_and_nested_data_are_preserved(self):
        response = {'code': 0, 'msg': 'success', 'data': {'total': 1, 'list': [{
            'accountId': 'c1', 'name': '', 'nickName': '昵称', 'tradeCount': 0, 'memberCreatedAt': None,
            'authInfo': {'isMobileAuth': False}, 'relatedKdtNames': [], 'futureField': {'nested': [None, False, 0, '']}}],
            'extraPageInfo': {'cursor': None}}, 'trace_id': 'fixture-trace'}
        with patch.object(YouzanClient, '_json', return_value=response):
            result = YouzanClient(CREDS).search({})
        self.assertEqual(result, response)
        self.assertNotIn('version', result)
        self.assertNotIn('category', result)
        self.assertNotIn('next_page', result)
        with self.assertRaises(ToolError):
            normalize({'fields': ['id']})

    def test_native_business_failure_is_also_preserved(self):
        response = {'code': 10100, 'msg': '页面已过期', 'data': None, 'extra': {'retry': False}}
        with patch.object(YouzanClient, '_json', return_value=response):
            self.assertEqual(YouzanClient(CREDS).search({}), response)


if __name__ == '__main__':
    unittest.main()
