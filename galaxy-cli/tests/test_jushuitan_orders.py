import io
import json

import pytest

from galaxy_cli.jushuitan.orders import Client, payload, normalize, ENDPOINT
from galaxy_cli.registry import ToolError, load_registry


class Opener:
    def __init__(self, results):self.results, self.requests = iter(results), []
    def open(self, request, timeout):
        self.requests.append(request)
        return io.BytesIO(json.dumps(next(self.results)).encode())


def test_verified_filter_names_time_provinces_and_guarantees():
    result = payload({'online_order_nos': ['00123'], 'distribution_online_order_nos': ['456'], 'order_ids': ['789'],
        'product_name': '商品', 'sku_codes': ['a', 'b'], 'sku_match': 'fuzzy', 'style_codes': ['c'],
        'provinces': ['湖南省'], 'exclude_provinces': True, 'after_sale_guarantees': ['分销无忧售后'],
        'start_time': '2026-11-24 00:00:00', 'end_time': '2026-11-24 23:59:59'})
    assert result['soIdList'] == ['00123'] and result['originalSoIds'] == ['456'] and result['oidList'] == ['789']
    assert result['skuIdsVague'] == 'a,b' and result['styleCode'] == ['c']
    assert json.loads(result['receiveAddressJson']) == {'湖南省': ''} and result['isExceptAddress'] == 'Except'
    assert result['orderLabels'] == ['分销无忧售后']
    assert 'uid' not in result and 'coid' not in result and 'orderStatus' not in result


def test_count_and_list_are_distinct_original_responses_and_do_not_trust_has_next():
    count = {'success': True, 'code': '0000', 'data': [], 'total': 1, 'hasNext': False}
    query = {'success': True, 'code': '0000', 'data': [{'soId': '00123', 'paidAmount': 12.5}], 'total': -1, 'hasNext': True}
    opener = Opener([count, query])
    value = Client({'access_token': 'own-token'}, opener=opener).orders({'online_order_nos': ['00123']})
    assert value['count'] == count and value['query'] == query and not value['has_more']
    assert [json.loads(r.data)['searchType'] for r in opener.requests] == [2, 1]
    assert all(r.full_url == ENDPOINT and r.get_header('Authorization') == 'own-token' for r in opener.requests)
    assert all('Cookie' not in r.headers for r in opener.requests)


@pytest.mark.parametrize('body', [{'uid': 1}, {'coId': 1}, {'url': 'https://other'}, {'access_token': 'secret'},
    {'start_time': '2026-11-24'}, {'page_size': 0}, {'page_size': 51}, {'online_order_nos': '001'},
    {'start_time': '2026-11-25 00:00:00', 'end_time': '2026-11-24 00:00:00'}, {'sku_match': 'fuzzy'}])
def test_rejects_identity_overrides_and_invalid_input(body):
    with pytest.raises(ToolError):normalize(body)


def test_upstream_failure_is_not_zero_orders_and_is_not_retried():
    opener = Opener([{'success': False, 'code': '1007', 'message': 'blocked', 'data': None}])
    with pytest.raises(ToolError, match='安全检查'):Client({'access_token': 'own-token'}, opener=opener).orders({})
    assert len(opener.requests) == 1


def test_tool_is_registered_with_complete_input_and_no_capture_jargon():
    tool = load_registry().get('jushuitan.order-search')
    assert tool.category == 'jushuitan' and 'online_order_nos' in tool.input_schema['properties']
    assert 'HAR' not in tool.description and 'Cookie' in tool.description
