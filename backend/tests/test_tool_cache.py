import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from test_excel import setup, context
from xingxu.tool_cache import cache_result, cacheable
from xingxu.tool_context import DispatchDenied
from xingxu.tools import help_arguments
from galaxy_cli.registry import ToolError


def canonical(category, name, body, action=None):
    return help_arguments(json.dumps({'args': [category, name, *([action] if action else []), '--input', json.dumps(body)]}))


def read(files, user, receipt):
    return json.loads((files.cache(user) / receipt['path']).read_bytes())


@pytest.mark.parametrize('command,args,data', [
    ('gjp.inventory', ['gjp', 'inventory', '--input', '{}'], {'data': {'rows': [{'barcode': '123', 'cost': 106}]}, 'total': 48325}),
    ('feishu.attendance', ['feishu', 'attendance', '--input', '{"action":"user_tasks.query"}'], {'ok': True, 'data': {'items': [{'name': 'Alice'}], 'has_more': True, 'page_token': 'next'}}),
    ('youzan.product-search', ['youzan', 'product-search', 'query', '--input', '{}'], {'code': 200, 'data': {'items': [{'id': '123'}]}}),
    ('account.me', ['account', 'me', '--input', '{}'], {'channels': {'youzan': {'status': 'connected'}}}),
    ('task.har', ['task', 'har', '--input', '{"file_id":"fixture"}'], {'requests': [{'headers': {'cookie': 'user-uploaded-private'}}]}),
    ('schedule.list', ['schedule', 'list', '--input', '{}'], {'tasks': [{'id': 'real-task'}]}),
    ('future.query', ['future', 'query', '--input', '{}'], {'data': {'any': [1, False, None]}}),
])
def test_all_business_categories_save_original_json_and_return_only_receipt(setup, monkeypatch, command, args, data):
    app, admin, employee, _ = setup;monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    dispatcher = app.state.ai.dispatcher
    # The dispatcher has already authorized/validated the tool before this boundary.
    dispatcher._check_command = lambda ctx, cmd, canonical: admin['user']
    result = asyncio.run(cache_result(dispatcher, context(admin), command, args, None, data, False))
    assert result['cached'] and result['format'] == 'json' and result['coverage'] == 'tool_response'
    assert result['sandbox_path'] == '/account-cache/' + result['path']
    assert read(dispatcher.files, admin['user'], result) == data
    assert 'data' not in result and len(json.dumps(result)) < 1700
    assert not (dispatcher.files.cache(employee['user']) / result['path']).exists()
    manifest = json.loads((dispatcher.files.cache(admin['user']) / result['metadata_path'].removeprefix('/account-cache/')).read_bytes())
    assert manifest['user_id'] == admin['user']['id'] and manifest['query']['command'] == command
    assert 'arguments_sha256' in manifest['query'] and manifest['complete']


@pytest.mark.parametrize('command,args', [
    ('help', ['help']), ('time.now', ['time', 'now', '--input', '{}']),
    ('interaction.ask', ['interaction', 'ask']), ('task.request-upload', ['task', 'request-upload']),
    ('excel.create', ['excel', 'create']), ('file.publish', ['file', 'publish']),
    ('feishu.drive', ['feishu', 'drive', '--input', '{"action":"help"}']),
])
def test_control_results_stay_inline(command, args):
    assert not cacheable(command, args)


def test_dispatcher_account_query_now_returns_file_and_audit_contains_no_raw_data(setup, monkeypatch):
    app, admin, _, _ = setup;monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    dispatcher = app.state.ai.dispatcher
    dispatcher.account_provider = SimpleNamespace(summary=lambda u: {'channels': {'test': {'status': 'connected'}}, 'private_data': 'not-in-chat'})
    call = {'id': 'fixture', 'type': 'function', 'function': {'name': 'galaxy_cli', 'arguments': json.dumps({'args': ['account', 'me', '--input', '{}']})}}
    event = asyncio.run(dispatcher.execute(context(admin), call))
    assert event['status'] == 'success' and event['result']['cached']
    assert read(dispatcher.files, admin['user'], event['result'])['private_data'] == 'not-in-chat'
    assert 'not-in-chat' not in json.dumps(event)
    with dispatcher.audit.transaction(write=False) as state:
        assert 'not-in-chat' not in json.dumps(state)


def test_authorization_revoked_during_cache_write_discards_new_files(setup, monkeypatch):
    app, admin, _, _ = setup;monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    dispatcher = app.state.ai.dispatcher;checks = 0
    def authorize(*args):
        nonlocal checks
        checks += 1
        if checks > 1:raise DispatchDenied('permission_denied', 'revoked')
        return admin['user']
    dispatcher._check_command = authorize
    with pytest.raises(DispatchDenied):
        asyncio.run(cache_result(dispatcher, context(admin), 'gjp.inventory', ['gjp', 'inventory', '--input', '{}'], None, {'data': 'private'}, False))
    assert not list(dispatcher.files.cache(admin['user']).rglob('*.json'))


def test_partial_and_failed_response_remains_explicit(setup, monkeypatch):
    app, admin, _, _ = setup;monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    dispatcher = app.state.ai.dispatcher;dispatcher._check_command = lambda *args: admin['user']
    data = {'partial': True, 'shops': [{'error': {'code': 'expired', 'message': 'bad'}}]}
    receipt = asyncio.run(cache_result(dispatcher, context(admin), 'youzan.product-get', ['youzan','product-get','--input','{}'], 'all', data, True))
    assert not receipt['success'] and receipt['partial'] and 'error' in receipt
    assert not receipt['business_success'] and read(dispatcher.files, admin['user'], receipt) == data


def test_password_not_stored_in_query_metadata_and_write_cache_failure_cannot_cause_resubmit(setup, monkeypatch):
    app, admin, _, _ = setup;monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    dispatcher = app.state.ai.dispatcher;dispatcher._check_command = lambda *args: admin['user']
    args = ['erp', 'employee-create', '--input', '{"action":"create","password":"fixture-secret"}']
    receipt = asyncio.run(cache_result(dispatcher, context(admin), 'erp.employee-create', args, None, {'created': True, 'id': 'new-user'}, False))
    manifest = (dispatcher.files.cache(admin['user']) / receipt['metadata_path'].removeprefix('/account-cache/')).read_text()
    assert 'fixture-secret' not in manifest
    with patch('xingxu.tool_cache.write_result', side_effect=OSError('disk full')):
        receipt = asyncio.run(cache_result(dispatcher, context(admin), 'erp.employee-create', args, None, {'created': True, 'id': 'new-user'}, False))
    assert receipt['operation_completed'] and receipt['id'] == 'new-user' and not receipt['cached']
    assert 'cache_error' in receipt and 'error' not in receipt


def test_feishu_download_is_available_in_sandbox_and_duplicate_source_removed(setup, monkeypatch):
    app, admin, _, _ = setup;monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    dispatcher = app.state.ai.dispatcher;dispatcher._check_command = lambda *args: admin['user']
    source = dispatcher.files.workspace(admin['user']) / 'sample.xlsx';source.write_bytes(b'fixture-data')
    result = {'path': 'sample.xlsx', 'filename': 'report.xlsx', 'size': 12}
    receipt = asyncio.run(cache_result(dispatcher, context(admin), 'feishu.file-download', ['feishu','file-download','--input','{}'], None, result, False))
    assert receipt['format'] == 'binary' and receipt['filename'] == 'report.xlsx'
    assert (dispatcher.files.cache(admin['user']) / receipt['path']).read_bytes() == b'fixture-data'
    assert not source.exists()


def test_large_response_exceeds_old_model_limit_but_is_only_returned_as_path(setup, monkeypatch, tmp_path):
    app, admin, _, _ = setup;monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    dispatcher = app.state.ai.dispatcher
    exe = tmp_path / 'fixture-cli'
    exe.write_text('#!/usr/bin/python3\nimport json\nprint(json.dumps({"code":200,"data":{"description":"x"*400000}}))\n')
    exe.chmod(0o700);dispatcher.executable = exe
    original = asyncio.run(dispatcher._run(['youzan','product-get','--input','{}'], admin['user'],
        credentials={'sid': 'fixture-sid', 'csrf': 'fixture-csrf'}, catalog_context=dispatcher.files.catalog_context(admin['user'], '123')))
    dispatcher._check_command = lambda *args: admin['user']
    receipt = asyncio.run(cache_result(dispatcher, context(admin), 'youzan.product-get', ['youzan','product-get','--input','{}'], None, original, False))
    assert receipt['size'] > 400000 and len(json.dumps(receipt)) < 1700
    assert len(read(dispatcher.files, admin['user'], receipt)['data']['description']) == 400000


def test_cancellation_signals_cache_writer_and_does_not_leave_a_background_waiter(setup, monkeypatch):
    import threading
    import time
    app, admin, _, _ = setup;monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    dispatcher=app.state.ai.dispatcher;dispatcher._check_command=lambda *args:admin['user']
    entered=threading.Event()
    def waiting_writer(*args):
        cancel=args[7];entered.set()
        while not cancel.is_set():time.sleep(.005)
        raise ToolError('cache_cancelled','cancelled',1)
    async def run():
        task=asyncio.create_task(cache_result(dispatcher,context(admin),'gjp.inventory',['gjp','inventory','--input','{}'],None,{'data':[]},False))
        assert await asyncio.to_thread(entered.wait,2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,2)
    with patch('xingxu.tool_cache.write_result',side_effect=waiting_writer):asyncio.run(run())


def test_existing_bulk_order_cache_is_reused_without_another_json_copy(setup,monkeypatch):
    from galaxy_cli.tools.youzan import order_search
    app,admin,_,_=setup;monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE','files-v1')
    dispatcher=app.state.ai.dispatcher;dispatcher._check_command=lambda *args:admin['user']
    class Client:credentials={'kdt_id':'123'}
    data={'code':200,'data':{'total':0,'page_no':1,'data':[]}}
    with patch.object(order_search,'gateway',return_value=data):
        original=order_search.run(Client(),{'action':'query','all_pages':True},dispatcher.files.catalog_context(admin['user'],'123'))
    result=asyncio.run(cache_result(dispatcher,context(admin),'youzan.order-search',['youzan','order-search','query','--input','{"all_pages":true}'],None,original,False))
    assert result['cached'] and result['sandbox_path']==original['data']['sandbox_path']
    root=dispatcher.files.cache(admin['user'])
    assert len(list(root.rglob('*.jsonl')))==1 and len(list(root.rglob('*.json')))==1
