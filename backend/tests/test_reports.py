import asyncio
import json
import uuid
from unittest.mock import AsyncMock

import pytest

from xingxu.app import create_app
from xingxu.auth import ROLES, AuthFailure
from xingxu.tools import ExecutionContext, help_arguments

CREDS = {'sid': 'fixture-report-sid-123', 'csrf': 'fixture-report-csrf-123', 'kdt_id': '123456',
         'cookies': {'access_token': 'fixture-report-access-123'}}
ARGS = ['youzan', 'business-report', 'query', '--input', '{"start_date":"2026-10-01","end_date":"2026-10-31","dimensions":[4]}']


def call(args):
    return {'id': 'report-call', 'type': 'function', 'function': {'name': 'galaxy_cli', 'arguments': json.dumps({'args': args})}}


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path)
    app.state.auth.import_accounts([('admin', 'test-admin-password'), ('employee', 'test-employee-password')])
    sessions = [app.state.auth.login(name, 'test-' + name + '-password', False, 'localhost') for name in ('admin', 'employee')]
    dispatcher = app.state.ai.dispatcher
    dispatcher.shop_resolver = AsyncMock(return_value=[{'kdt_id': CREDS['kdt_id'], 'shop_name': 'Fixture shop', 'credentials': CREDS}])
    dispatcher.category_provider = lambda user: CREDS if user['id'] == sessions[0]['user']['id'] else (_ for _ in ()).throw(
        AuthFailure('connection_required', '未绑定有赞连接。'))
    return app, sessions, tmp_path


def context(session):
    user = session['user']
    return ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), str(uuid.uuid4()), session['access_token'])


def test_options_and_query_are_canonical_subcommands():
    options = help_arguments(json.dumps({'args': ['youzan', 'business-report', 'options']}))
    assert options[:3] == ['youzan', 'business-report', 'options']
    assert json.loads(options[4]) == {'include_closed': True}
    query = help_arguments(json.dumps({'args': ARGS}))
    assert query[:3] == ARGS[:3]
    assert json.loads(query[4])['period'] == 'custom'
    assert json.loads(query[4])['page_size'] == 20
    assert help_arguments(json.dumps({'args': ['youzan.business-report', *ARGS[2:]]})) == query


def test_private_process_scope_full_output_and_distinct_audit_command(setup):
    app, sessions, directory = setup
    executable = directory / 'report-cli-fixture'
    executable.write_text('''#!/usr/bin/python3
import json,sys
credentials=json.load(sys.stdin)['categories']['youzan']
print(json.dumps({'code':200,'success':True,'data':{'status':1,'queryKey':'fixture',
 'data':{'code':200,'success':True,'data':{'items':[{'isTotal':True,'turnoverAmtStr':'0.00','extra':None,'reflected':credentials['sid']}],
 'paginator':{'totalCount':0,'page':1,'pageSize':20}}}}}))
''')
    executable.chmod(0o700)
    dispatcher = app.state.ai.dispatcher
    dispatcher.executable = executable
    event = asyncio.run(dispatcher.execute(context(sessions[0]), call(ARGS)))
    assert event['status'] == 'success'
    assert event['command'] == 'galaxy-cli youzan business-report query'
    row = event['result']['data']['data']['data']['items'][0]
    assert row['extra'] is None and row['turnoverAmtStr'] == '0.00'
    assert row['reflected'] == '[已隐藏凭证]'
    assert CREDS['sid'] not in json.dumps(event)
    audit = (directory / 'tool_audit.json').read_text()
    assert 'start_date' not in audit and 'turnoverAmtStr' not in audit
    assert CREDS['sid'] not in audit


@pytest.mark.parametrize('status,inner_code', [(0, 200), (-1, 200), (1, 403)])
def test_pending_and_nested_failures_do_not_become_success(setup, status, inner_code):
    app, sessions, _ = setup
    dispatcher = app.state.ai.dispatcher
    dispatcher._run = AsyncMock(return_value={'code': 200, 'success': True, 'data': {'status': status,
        'data': {'code': inner_code, 'success': inner_code == 200, 'data': {'items': []}}}})
    event = asyncio.run(dispatcher.execute(context(sessions[0]), call(ARGS)))
    assert event['status'] == 'error'
    assert event['result']['data']['status'] == status


def test_permission_and_connection_are_separate_from_customer_tools(setup, monkeypatch):
    app, sessions, _ = setup
    dispatcher = app.state.ai.dispatcher
    dispatcher._run = AsyncMock()
    employee = asyncio.run(dispatcher.execute(context(sessions[1]), call(ARGS)))
    assert employee['result']['error']['code'] == 'connection_required'
    monkeypatch.setitem(ROLES, 'admin', [p for p in ROLES['admin'] if p != 'youzan:report:read'])
    admin = asyncio.run(dispatcher.execute(context(sessions[0]), call(ARGS)))
    assert admin['result']['error']['code'] == 'permission_denied'
    dispatcher._run.assert_not_awaited()


@pytest.mark.parametrize('args', [
    ['youzan', 'business-report'], ['youzan', 'business-report', 'export'],
    ['youzan', 'business-report', 'options', '--input', '{"sid":"no"}'],
    ['youzan', 'business-report', 'options', '--input', '{"action":"query"}'],
    ['youzan', 'business-report', 'query', '--input', '{"dimensions":[]}'],
    ['youzan', 'business-report', 'query', '--input', '{"start_date":"2026-02-30","end_date":"2026-03-01","dimensions":[4]}']])
def test_invalid_subcommands_and_inputs_never_start_a_process(setup, args):
    app, sessions, _ = setup
    dispatcher = app.state.ai.dispatcher
    dispatcher._run = AsyncMock()
    event = asyncio.run(dispatcher.execute(context(sessions[0]), call(args)))
    assert event['status'] == 'denied'
    dispatcher._run.assert_not_awaited()
