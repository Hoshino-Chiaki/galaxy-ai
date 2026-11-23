import contextlib
import copy
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from galaxy_cli.cli import main
from galaxy_cli.registry import ToolError, load_registry
from galaxy_cli.tools.youzan import business_report as report

CREDENTIALS = {'sid': 'fixture-report-sid', 'csrf': 'fixture-report-csrf', 'kdt_id': '123456',
    'cookies': {'access_token': 'fixture-report-token', 'carmen_csrf_token': 'fixture-carmen-csrf', 'bifrost_csrf_token': 'fixture-bifrost-csrf'}}
RESULT = {'trace_id': 'trace-fixture', 'code': 200, 'success': True, 'message': 'successful', 'statusCode': 'fixture',
    'data': {'status': 1, 'queryKey': 'fixture-report-query', 'kdtId': 123456, 'adminId': 123,
        'data': {'code': 200, 'success': True, 'message': 'successful', 'data': {
            'paginator': {'pageSize': 20, 'page': 1, 'totalCount': 1},
            'items': [{'isTotal': True, 'turnoverAmtStr': '1,234.50', 'unknown': {'empty': None, 'zero': 0}},
                      {'isTotal': False, 'salesKdtId': 111, 'turnoverAmtStr': '1,234.50', 'timeIntervalStr': '09:00 ~ 09:59'}]}}}}
ARGUMENTS = {'action': 'query', 'start_date': '2026-10-01', 'end_date': '2026-10-31', 'dimensions': [0, 4, 5]}


class Client:
    credentials = CREDENTIALS
    def __init__(self):
        self.calls = []
        self.pending = False
        self.department_pages = False

    def _json(self, url):
        self.calls.append(url)
        path, query = urlsplit(url).path, parse_qs(urlsplit(url).query)
        if path == urlsplit(report.DEPARTMENTS).path:
            page = int(query['page_no'][0])
            return {'response': {'paginator': {'pageSize': 1, 'page': page, 'totalCount': 2 if self.department_pages else 1},
                'items': [{'deptId': 123 + page, 'deptName': '测试部门', 'extra': None}]}}
        if path == urlsplit(report.SALES_UNITS).path:
            return {'code': 200, 'success': True, 'data': {'paginator': {'total_count': 1, 'page_size': 100, 'page': 1},
                'items': [{'kdt_id': 111, 'name': '测试单元', 'unknown': False}]}}
        if path == urlsplit(report.CHANNELS).path:
            return {'code': 200, 'success': True, 'data': [{'code': 'fin_offline_shop', 'desc': '门店渠道', 'biz': '7'}]}
        if path == urlsplit(report.REPORT).path and self.pending:
            return {'code': 200, 'success': True, 'data': {'queryKey': 'fixture-report-query', 'status': 0, 'data': None}}
        return copy.deepcopy(RESULT)

    def redact(self, result):
        return result


class BusinessReportTests(unittest.TestCase):
    def test_crm_discovery_origin_does_not_block_fixed_report_gateway(self):
        from galaxy_cli.youzan_http import YouzanClient
        for origin in ('https://crm.youzan.com', 'https://www.youzan.com', 'https://store.youzan.com'):
            credentials={**CREDENTIALS,'origin':origin}
            with self.subTest(origin=origin), patch.object(report,'category_credentials',return_value=credentials), \
                    patch.object(YouzanClient,'_json',return_value=copy.deepcopy(RESULT)) as upstream:
                result=load_registry().invoke('youzan.business-report',ARGUMENTS)
                self.assertEqual(result,RESULT)
                self.assertEqual(credentials['origin'],origin)
                upstream.assert_called_once()
                target=urlsplit(upstream.call_args.args[0])
                self.assertEqual(target.hostname,'gateway.youzan.com')
                self.assertEqual(target.path,urlsplit(report.REPORT).path)

    def test_preparation_preserves_pages_and_includes_all_choices(self):
        client = Client(); client.department_pages = True
        result = report.options(client, report.normalize({'action': 'options'}))
        self.assertTrue(report.succeeded('options', result))
        self.assertEqual(len(result['departments']), 2)
        self.assertEqual(len(result['sales_units']), 2)
        self.assertEqual(len(result['time_intervals']), 24)
        self.assertEqual(result['time_intervals'][3], {'value': '03', 'label': '03:00 ~ 03:59'})
        self.assertEqual({d['value'] for d in result['dimensions']}, {0, 4, 5})
        self.assertIsNone(result['departments'][0]['response']['items'][0]['extra'])
        self.assertIs(result['sales_units'][0]['data']['items'][0]['unknown'], False)
        for url in client.calls:
            self.assertIn(urlsplit(url).hostname, ('gateway.youzan.com', 'carmen.youzan.com'))
            self.assertNotIn('sid=', url)

    def test_full_original_query_response_and_filter_conversion(self):
        client = Client()
        arguments = {**ARGUMENTS, 'department_id': '124', 'sales_kdt_ids': ['111'], 'sales_channels': ['fin_offline_shop'],
            'time_intervals': ['03', '09'], 'period': 'month', 'page': 2, 'page_size': 10}
        result = report.query(client, report.normalize(arguments))
        self.assertEqual(result, RESULT)
        query = parse_qs(urlsplit(client.calls[-1]).query)
        self.assertEqual(query['report_type'], ['3'])
        self.assertEqual(query['sales_kdt_ids'], ['[111]'])
        self.assertEqual(query['dimensions'], ['[0,4,5]'])
        self.assertEqual(query['time_intervals'], ['["03","09"]'])
        self.assertEqual(query['page_no'], ['2'])
        self.assertEqual(query['department_id'], ['124'])
        self.assertEqual(query['carmen_csrf_token'], ['fixture-carmen-csrf'])
        self.assertTrue(report.succeeded('query', result))

    def test_omitted_filters_mean_all_and_custom_omits_report_type(self):
        client = Client()
        report.query(client, report.normalize(ARGUMENTS))
        self.assertEqual(len(client.calls), 1)
        query = parse_qs(urlsplit(client.calls[0]).query)
        self.assertNotIn('department_id', query)
        self.assertNotIn('report_type', query)
        self.assertNotIn('business_mode', query)
        self.assertEqual(query['sales_kdt_ids'], ['[]'])
        self.assertEqual(query['sales_channels'], ['[]'])
        self.assertEqual(query['time_intervals'], ['[]'])

    def test_async_poll_uses_only_server_query_key_and_preserves_final_result(self):
        client = Client(); client.pending = True
        with patch.object(report.time, 'sleep'):
            result = report.query(client, report.normalize(ARGUMENTS))
        self.assertEqual(result, RESULT)
        query = parse_qs(urlsplit(client.calls[1]).query)
        self.assertEqual(urlsplit(client.calls[1]).path, urlsplit(report.POLL).path)
        self.assertEqual(query['query_key'], ['fixture-report-query'])

    def test_pending_and_nested_business_errors_are_not_success(self):
        for status in (0, -1):
            value = copy.deepcopy(RESULT); value['data']['status'] = status
            self.assertFalse(report.succeeded('query', value))
        value = copy.deepcopy(RESULT); value['data']['data']['code'] = 403
        self.assertFalse(report.succeeded('query', value))

    def test_unauthorized_filter_never_calls_report_endpoint(self):
        for field, value in [('department_id', '999'), ('sales_kdt_ids', ['999']), ('sales_channels', ['invented'])]:
            client = Client()
            with self.assertRaises(ToolError): report.query(client, report.normalize({**ARGUMENTS, field: value}))
            self.assertFalse(any(urlsplit(u).path == urlsplit(report.REPORT).path for u in client.calls))

    def test_invalid_inputs_reject_credentials_urls_and_invalid_ranges(self):
        for value in [{**ARGUMENTS, 'dimensions': []}, {**ARGUMENTS, 'dimensions': [True]},
            {**ARGUMENTS, 'time_intervals': ['3']}, {**ARGUMENTS, 'start_date': '2026-02-30'},
            {**ARGUMENTS, 'start_date': '2026-11-01'}, {**ARGUMENTS, 'sales_kdt_ids': [111]},
            {**ARGUMENTS, 'url': 'https://invalid.example'}, {**ARGUMENTS, 'access_token': 'forbidden'},
            {'action': 'options', 'query_key': 'arbitrary'}]:
            with self.assertRaises(ToolError): report.normalize(value)

    def test_both_subcommands_have_one_help_entry_and_independent_module(self):
        registry = load_registry()
        tool = registry.get('youzan.business-report')
        self.assertEqual(tool.handler.__module__, 'galaxy_cli.tools.youzan.business_report')
        self.assertIn('business-report options', tool.description)
        self.assertIn('business-report query', tool.description)
        self.assertNotIn('HAR', tool.description)
        self.assertEqual(sum(t['tool'] == 'youzan.business-report' for t in registry.invoke('help', {})['data']), 1)

    def test_cli_accepts_two_subcommands_and_returns_bare_native_json(self):
        client = Client()
        for suffix, expected in [(['options'], None), (['query', '--input', json.dumps({k: v for k, v in ARGUMENTS.items() if k != 'action'})], RESULT)]:
            output = io.StringIO()
            with patch.object(report, 'YouzanClient', return_value=client), patch.object(report, 'category_credentials', return_value=CREDENTIALS), contextlib.redirect_stdout(output):
                code = main(['youzan', 'business-report', *suffix])
            self.assertEqual(code, 0)
            value = json.loads(output.getvalue())
            self.assertNotIn('version', value)
            if expected: self.assertEqual(value, expected)
            else: self.assertEqual(set(value), report.OPTION_KEYS)
