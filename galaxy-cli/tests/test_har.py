"""Offline HAR contract tests; fixtures contain no real credentials or traffic."""
import base64
import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from galaxy_cli import har_archive
from galaxy_cli.har_local import main
from galaxy_cli.registry import ToolError, load_registry
from galaxy_cli.tool_categories import CATEGORY_LABELS, DEFAULT_CATEGORY_PROMPTS


def entry():
    return {'startedDateTime': '2026-11-16T02:24:32.753Z', 'time': 1,
            'request': {'method': 'POST', 'url': 'https://example.test/update?a=1&a=2&blank=&access_token=fixture-query-token',
                        'httpVersion': 'HTTP/2', 'headers': [
                            {'name': 'User-Agent', 'value': 'FixtureBrowser/1'},
                            {'name': 'Cookie', 'value': 'sid=fixture-session; mode=1'},
                            {'name': 'Authorization', 'value': 'Bearer fixture-auth-value'},
                            {'name': 'X-Duplicate', 'value': 'first'}, {'name': 'X-Duplicate', 'value': 'second'}],
                        'cookies': [{'name': 'sid', 'value': 'fixture-session', 'domain': 'example.test', 'path': '/'}],
                        'queryString': [{'name': 'a', 'value': '1'}, {'name': 'a', 'value': '2'},
                                        {'name': 'blank', 'value': ''}, {'name': 'access_token', 'value': 'fixture-query-token'}],
                        'postData': {'mimeType': 'application/x-www-form-urlencoded',
                                     'text': 'item_price_param=%7B%22costPrice%22%3A%2211.00%22%2C%22enabled%22%3Afalse%7D&tag=a&tag=b&blank=&csrf_token=fixture-csrf&plus=%2B'}},
            'response': {'status': 200, 'statusText': 'OK', 'httpVersion': 'HTTP/2', 'redirectURL': '',
                         'headers': [{'name': 'Set-Cookie', 'value': 'sid=fixture-new-session; Path=/; Domain=example.test; HttpOnly'}],
                         'cookies': [{'name': 'sid', 'value': 'fixture-new-session', 'path': '/'}],
                         'content': {'mimeType': 'application/json',
                                     'text': '{"success":true,"costPrice":"11.00","count":0,"data":null,"token":"fixture-response-token","echo":"fixture-session"}'}},
            'timings': {'send': 0, 'wait': 1, 'receive': 0}}


class HarTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / '抓包无扩展名'
        self.registry = load_registry('galaxy_cli.tools.task')
        self.raw = {'log': {'version': '1.2', 'creator': {'name': 'Fixture', 'version': '1'}, 'entries': [entry()]}}
        self.write()
        self.network = patch('socket.create_connection', side_effect=AssertionError('Offline tool must not connect'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def write(self):
        self.path.write_text(json.dumps(self.raw, ensure_ascii=False), encoding='utf-8-sig')

    def inspect(self, **arguments):
        return self.registry.invoke('task.har', {'path': str(self.path), **arguments})

    def test_discovery_category_prompt_and_real_haralyzer(self):
        definition = self.registry.get('task.har')
        self.assertEqual(definition.category, 'task')
        self.assertEqual(CATEGORY_LABELS['task'], '任务系统')
        self.assertIn('一次列齐资料需求', DEFAULT_CATEGORY_PROMPTS['task'])
        self.assertIn('哪个平台提供什么数据', DEFAULT_CATEGORY_PROMPTS['task'])
        self.assertIn('Haralyzer', definition.description)
        self.assertEqual(har_archive.HarParser.__module__, 'haralyzer.assets')

    def test_summary_accepts_bom_and_no_extension_without_modifying_source(self):
        before = self.path.read_bytes()
        result = self.inspect()
        self.assertEqual(result['source']['sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(result['source']['entry_count'], 1)
        self.assertEqual(result['data']['hosts'], {'example.test': 1})
        self.assertEqual(result['data']['session_evidence']['requests_with_cookies'], 1)
        self.assertEqual(result['data']['observed_environment_headers']['user-agent'], ['FixtureBrowser/1'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_filter_pagination_keeps_original_indices_and_masks_query(self):
        second = copy.deepcopy(entry())
        second['request']['url'] = 'https://other.test/list'
        second['request']['method'] = 'GET'
        self.raw['log']['entries'] += [second, copy.deepcopy(entry())]
        self.write()
        page = self.inspect(action='requests', host='EXAMPLE.TEST', method='post', search='update', limit=1)['data']
        self.assertEqual(page['total'], 2)
        self.assertEqual(page['next_offset'], 1)
        self.assertNotIn('fixture-query-token', page['items'][0]['url'])
        self.assertIn('a=1&a=2&blank=', page['items'][0]['url'])
        page = self.inspect(action='requests', host='example.test', offset=1, limit=1)['data']
        self.assertEqual(page['items'][0]['entry_index'], 2)
        self.assertIsNone(page['next_offset'])

    def test_sensitive_detail_preserves_headers_form_duplicates_and_nested_types(self):
        result = self.inspect(action='entry', entry_index=0, include_sensitive=True)['data']
        request = result['request']
        self.assertEqual(request['headers'], entry()['request']['headers'])
        self.assertEqual(request['query_string'], entry()['request']['queryString'])
        self.assertEqual(request['body']['raw_text'], entry()['request']['postData']['text'])
        fields = request['body']['parsed']
        self.assertEqual([i['value'] for i in fields if i['name'] == 'tag'], ['a', 'b'])
        self.assertEqual(fields[0]['decoded_value'], {'costPrice': '11.00', 'enabled': False})
        self.assertEqual(fields[-1]['value'], '+')
        response = result['response']['body']['parsed']
        self.assertIs(response['success'], True)
        self.assertEqual(response['count'], 0)
        self.assertIsNone(response['data'])

    def test_default_masks_credentials_and_keeps_useful_business_fields(self):
        result = self.inspect(action='entry', entry_index=0)
        output = json.dumps(result)
        for secret in ('fixture-session', 'fixture-new-session', 'fixture-query-token', 'fixture-auth-value',
                       'fixture-csrf', 'fixture-response-token'):
            self.assertNotIn(secret, output)
        body = result['data']['request']['body']
        self.assertNotIn('raw_text', body)
        self.assertTrue(body['raw_requires_sensitive'])
        self.assertEqual(body['parsed'][0]['decoded_value']['costPrice'], '11.00')
        self.assertEqual(result['data']['response']['body']['parsed']['costPrice'], '11.00')

    def test_short_cookie_values_do_not_corrupt_urls(self):
        self.raw['log']['entries'][0]['request']['headers'].append({'name': 'Referer', 'value': 'https://example.test/page1'})
        self.write()
        result = self.inspect(action='entry', entry_index=0)['data']
        self.assertTrue(result['url'].startswith('https://example.test/'))
        self.assertEqual(result['request']['headers'][-1]['value'], 'https://example.test/page1')

    def test_raw_body_pagination_can_be_reassembled_exactly(self):
        chunks, offset = [], 0
        original = entry()['request']['postData']['text']
        while True:
            body = self.inspect(action='body', entry_index=0, part='request', include_sensitive=True,
                                body_offset=offset, body_limit=17)['data']['body']
            chunks.append(body['raw_text'])
            if body['next_offset'] is None:
                break
            offset = body['next_offset']
        self.assertEqual(''.join(chunks), original)
        self.assertEqual(body['raw_sha256'], hashlib.sha256(original.encode()).hexdigest())

    def test_base64_response_and_binary_or_bad_base64_are_explicit(self):
        content = self.raw['log']['entries'][0]['response']['content']
        content.update({'text': base64.b64encode('{"成本":"11.00"}'.encode()).decode(), 'encoding': 'base64'})
        self.write()
        body = self.inspect(action='entry', entry_index=0, include_sensitive=True)['data']['response']['body']
        self.assertEqual(body['parsed'], {'成本': '11.00'})
        self.assertEqual(body['decode_state'], 'decoded')
        for text in ('not-base64!', base64.b64encode(b'\xff\x00').decode()):
            content['text'] = text
            self.write()
            body = self.inspect(action='body', entry_index=0)['data']['body']
            self.assertEqual(body['decode_state'], 'binary_or_decode_failed')

    def test_missing_empty_and_params_only_bodies_are_distinct(self):
        raw = self.raw['log']['entries'][0]
        raw['response']['content'].pop('text')
        raw['request']['postData'] = {'mimeType': 'multipart/form-data', 'params': [{'name': 'upload', 'fileName': 'a.bin'}]}
        self.write()
        data = self.inspect(action='entry', entry_index=0)['data']
        self.assertEqual(data['response']['body']['state'], 'missing')
        self.assertEqual(data['request']['body']['state'], 'params_only')
        raw['response']['content']['text'] = ''
        self.write()
        self.assertEqual(self.inspect(action='body', entry_index=0)['data']['body']['state'], 'empty')

    def test_malformed_records_keep_indices_and_errors_do_not_echo_input(self):
        self.raw['log']['entries'].insert(0, {'request': None})
        self.write()
        self.assertEqual(self.inspect()['data']['invalid_entry_indices'], [0])
        self.assertEqual(self.inspect(action='requests')['data']['items'][0]['entry_index'], 1)
        for index, code in ((0, 'invalid_entry'), (99, 'entry_not_found')):
            with self.assertRaises(ToolError) as error:
                self.inspect(action='entry', entry_index=index)
            self.assertEqual(error.exception.code, code)
        self.path.write_text('fixture-private-invalid-data', encoding='utf-8')
        with self.assertRaises(ToolError) as error:
            self.inspect()
        self.assertNotIn('fixture-private', str(error.exception))

    def test_invalid_action_arguments_are_rejected(self):
        for arguments in ({'action': 'entry'}, {'action': 'body', 'entry_index': 0, 'body_limit': 64001},
                          {'action': 'requests', 'entry_index': 0}, {'action': 'summary', 'offset': 1},
                          {'action': 'requests', 'limit': 0}, {'include_sensitive': 'true'}, {'workspace_id': 'other'}):
            with self.subTest(arguments=arguments), self.assertRaises(ToolError):
                self.inspect(**arguments)

    def test_local_entry_point_runs_registered_tool_and_reports_errors(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['--input', json.dumps({'path': str(self.path)})])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['source']['parser'], 'haralyzer')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['--input', '{bad-json-fixture-private'])
        self.assertNotEqual(code, 0)
        self.assertNotIn('fixture-private', output.getvalue())


if __name__ == '__main__':
    unittest.main()
