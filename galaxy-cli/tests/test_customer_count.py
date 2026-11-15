import contextlib
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from galaxy_cli.cli import main
from galaxy_cli.registry import ToolError, load_registry
from galaxy_cli.tools.youzan.customer_count import count, normalize


@pytest.mark.parametrize('total', [0, 1, 104027, 311071])
def test_only_total_is_returned_without_customer_records(total):
    client = SimpleNamespace(search=Mock(return_value={'code': 0, 'data': {'total': total,
        'list': [{'name': 'fixture-private-name', 'mobile': 'fixture-private-mobile'}]}}))
    assert count(client, {}) == {'total': total}
    client.search.assert_called_once_with({'page': 1, 'page_size': 1})


@pytest.mark.parametrize('result', [{'code': 403, 'data': {'total': 0}}, {'code': 0, 'data': {}},
    {'code': 0, 'data': {'total': None}}, {'code': 0, 'data': {'total': '5'}},
    {'code': 0, 'data': {'total': True}}, {'code': 0, 'data': {'total': -1}},
    {'code': 0, 'data': {'total': 2.0}}, {'data': {'total': 0}}, {'code': 0, 'data': []}])
def test_failures_or_missing_totals_are_never_invented_as_zero(result):
    with pytest.raises(ToolError):count(SimpleNamespace(search=Mock(return_value=result)), {})


@pytest.mark.parametrize('arguments', [{'keyword': 'x'}, {'page': 2}, {'page_size': 50}, {'kdt_id': '1'},
                                      {'shops': 'all'}, {'sid': 'fixture-secret'}, [], None])
def test_no_filters_credentials_or_scope_can_be_injected_into_business_input(arguments):
    with pytest.raises(ToolError):normalize(arguments)


def test_help_registers_count_and_cli_returns_minimal_output():
    assert next(t for t in load_registry().invoke('help', {})['data'] if t['tool'] == 'youzan.customer-count')['category'] == 'youzan'
    credentials = {'sid': 'fixture-private-sid', 'csrf': 'fixture-private-csrf', 'kdt_id': '123', 'cookies': {}}
    for command in [['youzan', 'customer-count'], ['youzan.customer-count', '--input', '{}']]:
        output = io.StringIO()
        with patch('galaxy_cli.tools.youzan.customer_count.category_credentials', return_value=credentials), \
             patch('galaxy_cli.youzan_http.YouzanClient.search', return_value={'code': 0, 'data': {'total': 12, 'list': [{'name': 'fixture-private-name'}]}}), \
             contextlib.redirect_stdout(output):
            assert main(command) == 0
        assert json.loads(output.getvalue()) == {'total': 12}
        assert 'fixture-private' not in output.getvalue()
