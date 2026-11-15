import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from test_excel import setup, context
from xingxu.tools import help_arguments
from xingxu.youzan.tool_runtime import readonly_youzan


def call(action='create', body=None, shops=None):
    value = {'args': ['youzan', 'product-group', action, '--input', json.dumps(body or {'name':'Fixture'})]}
    if shops is not None: value['shops'] = shops
    return {'id':'group-test','type':'function','function':{'name':'galaxy_cli','arguments':json.dumps(value)}}


def test_command_alias_and_readonly_classification():
    a = ['youzan','product-group','create','--input','{"name":"Example"}']
    b = ['youzan.product-group','create','--input','{"name":"Example"}']
    assert help_arguments(json.dumps({'args':a})) == help_arguments(json.dumps({'args':b}))
    for action in ('list','get','stores'):
        assert readonly_youzan(['youzan','product-group',action])
    assert not readonly_youzan(['youzan','product-group','create'])


def test_employee_cannot_create_before_credentials_or_cli_are_accessed(setup):
    app, _, employee, _ = setup
    dispatcher = app.state.ai.dispatcher; dispatcher._run = AsyncMock()
    dispatcher.category_provider = lambda _: pytest.fail('Credentials must not be read')
    result = asyncio.run(dispatcher.execute(context(employee), call()))
    assert result['result']['error']['code'] == 'permission_denied'
    dispatcher._run.assert_not_awaited()


@pytest.mark.parametrize('shops', ['all', ['one','two']])
def test_multiple_shop_creation_is_denied_before_any_shop_request(setup, shops):
    app, admin, _, _ = setup
    dispatcher = app.state.ai.dispatcher; dispatcher._run = AsyncMock(); dispatcher.shop_resolver = AsyncMock()
    result = asyncio.run(dispatcher.execute(context(admin), call(shops=shops)))
    assert result['result']['error']['code'] == 'shop_scope_denied'
    dispatcher._run.assert_not_awaited(); dispatcher.shop_resolver.assert_not_awaited()
