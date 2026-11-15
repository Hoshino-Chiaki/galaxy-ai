import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from test_multi_shop import setup, call
from xingxu.tools import DispatchDenied, ExecutionContext, help_arguments
from xingxu.auth import ROLES

ARGS = ['youzan', 'customer-count', '--input', '{}']


@pytest.mark.parametrize('args', [['youzan', 'customer-count'], ARGS, ['youzan.customer-count']])
def test_count_command_defaults_to_empty_input(args):
    assert help_arguments(json.dumps({'args': args})) == ['youzan', 'customer-count', '--input', '{}', '--format', 'json']


@pytest.mark.parametrize('body', ['{"keyword":"x"}', '{"shops":"all"}', '{"page_size":1}', 'null'])
def test_count_command_rejects_extra_business_parameters(body):
    with pytest.raises(DispatchDenied):help_arguments(json.dumps({'args': ['youzan', 'customer-count', '--input', body]}))


def test_multi_shop_count_has_independent_totals_and_keeps_partial_failure(setup):
    app, _, _, context, calls, _ = setup
    async def query(arguments, user, *, credentials, **kwargs):
        assert arguments[:2] == ['youzan', 'customer-count']
        assert calls['crm'][-1] == credentials['kdt_id']
        return {'total': 0 if credentials['kdt_id'] == '111' else 311071}
    app.state.ai.dispatcher._run = AsyncMock(side_effect=query)
    event = asyncio.run(app.state.ai.dispatcher.execute(context, call('all', ARGS)))
    assert event['status'] == 'success' and event['result']['partial'] is False
    assert [row['data'] for row in event['result']['shops']] == [{'total': 0}, {'total': 311071}]
    calls['fail'].add('222')
    event = asyncio.run(app.state.ai.dispatcher.execute(context, call('all', ARGS)))
    assert event['result']['partial'] is True
    assert event['result']['shops'][0]['data'] == {'total': 0}
    assert 'error' in event['result']['shops'][1] and 'data' not in event['result']['shops'][1]


def test_count_uses_current_user_permission_and_never_another_users_credentials(setup, monkeypatch):
    app, admin, employee, context, _, _ = setup
    dispatcher = app.state.ai.dispatcher;dispatcher._run = AsyncMock()
    user = employee['user']
    other = ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), 'fixture-other', employee['access_token'])
    event = asyncio.run(dispatcher.execute(other, call('all', ARGS)))
    assert event['status'] == 'denied' and event['result']['error']['code'] == 'connection_required'
    monkeypatch.setitem(ROLES, 'admin', [p for p in ROLES['admin'] if p != 'youzan:customer:read'])
    assert asyncio.run(dispatcher.execute(context, call('all', ARGS)))['result']['error']['code'] == 'permission_denied'
    dispatcher._run.assert_not_awaited()


@pytest.mark.parametrize('payload,expected', [({'total': 104027}, 'success'), ({'total': True}, 'error'),
                                          ({'total': 1, 'name': 'unexpected'}, 'error')])
def test_real_cli_pipe_accepts_only_the_count_output_contract(setup, payload, expected):
    app, _, _, context, _, directory = setup
    executable = directory/'fixture-count-cli'
    executable.write_text('#!/usr/bin/python3\nimport json,sys\nframe=json.load(sys.stdin)\nassert frame["categories"]["youzan"]["kdt_id"]=="111"\nprint('+repr(json.dumps(payload))+')\n')
    executable.chmod(0o700);app.state.ai.dispatcher.executable = executable
    event = asyncio.run(app.state.ai.dispatcher.execute(context, call('current', ARGS)))
    assert event['status'] == expected
    if expected == 'success':assert event['result'] == {'total': 104027}
    else:assert event['result']['error']['code'] == 'invalid_cli_output'
