"""Offline validation against the user's local capture; never dumps credentials."""
import hashlib
import json
import socket
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl

from galaxy_cli.registry import load_registry

source = Path('C:/Users/Administrator/Desktop/成本价')
registry = load_registry('galaxy_cli.tools.task')
timings = {}


def invoke(label, **arguments):
    started = time.perf_counter()
    result = registry.invoke('task.har', {'path': str(source), **arguments})
    timings[label] = round(time.perf_counter() - started, 3)
    return result


with patch.object(socket, 'create_connection', side_effect=AssertionError('offline validation')):
    summary = invoke('summary')
    listing = invoke('filter', action='requests', host='gateway.youzan.com', method='POST', search='update.product')
    assert summary['source']['entry_count'] == 638
    assert summary['data']['valid_entries'] == 638
    assert len(listing['data']['items']) == 1
    index = listing['data']['items'][0]['entry_index']
    detail = invoke('detail_sensitive', action='entry', entry_index=index, include_sensitive=True, body_limit=64000)
    safe = invoke('detail_default', action='entry', entry_index=index, body_limit=64000)

    raw = json.loads(source.read_text(encoding='utf-8-sig'))['log']['entries'][index]
    request, response = detail['data']['request'], detail['data']['response']
    assert request['headers'] == raw['request']['headers']
    assert response['headers'] == raw['response']['headers']
    assert request['cookies'] == raw['request']['cookies']
    assert request['query_string'] == raw['request']['queryString']
    assert request['body']['raw_text'] == raw['request']['postData']['text']
    assert response['body']['raw_text'] == raw['response']['content']['text']
    fields = request['body']['parsed']
    assert len(fields) == 64
    decoded = {item['name']: item['decoded_value'] for item in fields}
    cost1 = decoded['item_price_param']['costPrice']
    cost2 = decoded['item_online_channel_param']['itemPriceParam']['costPrice']
    assert cost1 == cost2 == '11.00'
    assert response['body']['parsed']['success'] is True

    # Check actual login values independently from the tool's masking implementation.
    private_values = {item['value'] for side in ('request', 'response')
                      for item in raw[side].get('cookies', []) if len(item.get('value', '')) >= 6}
    for side in ('request', 'response'):
        for header in raw[side].get('headers', []):
            if header['name'].lower() in ('authorization', 'cookie', 'set-cookie', 'csrf-token'):
                private_values.add(header['value'])
    for name, value in parse_qsl(raw['request']['postData']['text'], keep_blank_values=True):
        if any(part in name.lower() for part in ('token', 'csrf', 'password', 'secret')) and len(value) >= 6:
            private_values.add(value)
    safe_text = json.dumps(safe, ensure_ascii=False)
    assert all(value not in safe_text for value in private_values)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == summary['source']['sha256']

report = {
    'source': summary['source'], 'entry_index': index, 'form_field_count': len(fields),
    'captured_cost_values': [cost1, cost2], 'captured_response_success': True,
    'headers_cookies_query_and_raw_bodies_match': True,
    'default_output_credential_checks': len(private_values), 'default_output_credential_check_passed': True,
    'source_unchanged': True, 'network_requests_sent': 0, 'timings_seconds': timings}
output = Path(__file__).resolve().parents[1] / 'artifacts' / 'task-har-local-validation.json'
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
