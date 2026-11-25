import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_excel import setup, context
from xingxu.auth import ROLES
from xingxu.dsh_runtime import DshTurn
from xingxu.tools import help_arguments, help_visible
from xingxu.tool_cache import cacheable


def call(command, body):
    return {'id': 'batch-test', 'type': 'function', 'function': {'name': 'galaxy_cli',
        'arguments': json.dumps({'args': ['excel', command, '--input', json.dumps(body)]})}}


def test_default_employee_discovery_normalization_and_no_cache():
    assert 'excel:process' in ROLES['employee'] and 'excel:process' in ROLES['admin']
    assert help_visible('excel.transform', ROLES['employee'])
    assert not help_visible('excel.transform', ['excel:export'])
    args = ['excel', 'inspect', '--input', '{"source":{"path":"a.csv"}}']
    assert help_arguments(json.dumps({'args': args}))[:2] == ['excel', 'inspect']
    assert not cacheable('excel.inspect', args)


def test_excel_prompt_is_injected_for_employee_and_editable_in_category(setup):
    from xingxu.tool_prompts import ToolPrompts
    from xingxu.tools import COMMAND_PERMISSIONS
    from galaxy_cli.excel.prompt import EXCEL_SYSTEM_PROMPT
    _, _, employee, directory = setup
    prompts = ToolPrompts(directory)
    messages = prompts.messages(employee['user'], COMMAND_PERMISSIONS)
    content = next(m['content'] for m in messages if '【工具分类：excel】' in m['content'])
    for required in ('header_row','record_path','multiple=error','multiple="join"','status_column','file publish','numeric、blank、invalid'):
        assert required in content
    assert next(c for c in prompts.settings(employee['user'])['categories'] if c['category']=='excel')['prompt'] == EXCEL_SYSTEM_PROMPT


def test_employee_transform_publish_and_scope(setup):
    app, admin, employee, directory = setup
    dispatcher = app.state.ai.dispatcher
    workspace = dispatcher.files.workspace(employee['user'])
    (workspace / 'data.csv').write_text('code,price,qty\n001,0.1,3\n002,2,-4\n')
    body = {'sources': {'a': {'path': 'data.csv'}}, 'steps': [
        {'op': 'calculate', 'table': 'a', 'into': 'a', 'columns': [
            {'name': 'cost', 'expression': 'p*q', 'variables': {'p': 'price', 'q': 'qty'}}]}],
        'exports': [{'table': 'a', 'sheet': '明细', 'totals': ['cost']}]}
    event = asyncio.run(dispatcher.execute(context(employee), call('transform', body)))
    assert event['status'] == 'success', event
    result = event['result']
    assert result['sheets'][0]['rows'] == 2 and result['sheets'][0]['totals']['cost']['sum'] == '-7.70'
    assert str(workspace) not in json.dumps(result)
    foreign = asyncio.run(dispatcher.execute(context(admin), call('inspect', {'source': {'path': result['path']}})))
    assert foreign['status'] == 'error' and foreign['result']['error']['code'] == 'path_denied'
    # Generated receipt is accepted by the existing publication flow, no service refresh.
    publish = {'id': 'p', 'type': 'function', 'function': {'name': 'galaxy_cli', 'arguments': json.dumps({
        'args': ['file', 'publish', '--input', json.dumps({'path': result['path']})]})}}
    output = asyncio.run(dispatcher.execute(context(employee), publish))
    assert output['status'] == 'success' and output['result']['file_id']


def test_dsh_materializes_multiple_sources_without_publishing(tmp_path):
    async def execute():
        workspace = tmp_path / 'workspace'; workspace.mkdir()
        dispatcher = SimpleNamespace(files=SimpleNamespace(workspace=lambda user: workspace), execute=AsyncMock(return_value={'result': {'rows': 1}}))
        turn = DshTurn(SimpleNamespace(dispatcher=dispatcher), context({'user': {'id': 'u', 'workspace_id': 'w', 'permissions': ['excel:process']}, 'access_token': 's'}),
                       {}, run=None, progress={}, thinking=None, reasoning_effort=None)
        turn.principal = lambda: {'permissions': ['excel:process']}
        worker = AsyncMock(return_value={'data': base64.b64encode(b'code\n001\n').decode()})
        body = {'sources': {'a': {'path': '/workspace/a.csv'}, 'b': {'path': '/workspace/b.csv'}, 'c': {'path': '/account-cache/c.csv'}},
                'exports': [{'table': 'a', 'sheet': 'a'}]}
        with patch('xingxu.dsh_runtime.worker_request', worker):
            await turn.cli({'args': ['excel', 'transform', '--input', json.dumps(body)]})
        assert worker.await_count == 2 and dispatcher.execute.await_count == 1
        sent = json.loads(dispatcher.execute.call_args.args[1]['function']['arguments'])
        sources = json.loads(sent['args'][3])['sources']
        assert sources['a']['path'].startswith('dsh-exports/')
        assert sources['b']['path'].startswith('dsh-exports/')
        assert sources['c']['path'] == '/account-cache/c.csv'
        assert (workspace / sources['a']['path']).read_bytes() == b'code\n001\n'
    asyncio.run(execute())
