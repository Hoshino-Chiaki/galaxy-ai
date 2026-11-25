import asyncio
import json
import os
import signal
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from xingxu.auth import AuthFailure
from xingxu.guanjia.cost_source import CostSource, PERMISSION
from xingxu.guanjia.service import owner_key
from xingxu.store import JsonRepository
from xingxu.tools import GalaxyDispatcher, help_visible, command_allowed
from xingxu.tool_context import ExecutionContext, ToolInterrupted


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    cipher = Fernet(Fernet.generate_key())
    repo = JsonRepository(tmp_path, 'guanjia', ('connections',))
    user = {'id': 'alice', 'username': 'alice', 'workspace_id': 'w', 'permissions': [PERMISSION]}
    with repo.transaction() as state:state['connections'][owner_key(user)] = {'database': 'book', 'session': 'expired-or-absent'}
    config = {'workspace_id': 'w', 'database': 'book', 'server': 'fixture.invalid', 'port': 1433, 'user': 'fixture', 'password': 'fixture-secret'}
    key = json.dumps(['w', 'book'], separators=(',', ':'))
    (tmp_path / 'gjp-cost-sources.json').write_text(json.dumps({'sources': {key: cipher.encrypt(json.dumps(config).encode()).decode()}}))
    return CostSource(tmp_path, cipher, SimpleNamespace(repo=repo)), user


def test_permission_book_binding_and_expired_web_session(source):
    service, user = source
    assert service.credentials(user)['database'] == 'book'
    assert help_visible('gjp.inventory-cost', [PERMISSION])
    assert not help_visible('gjp.inventory-cost', ['guanjia:configure'])
    args = ['gjp', 'inventory-cost', '--input', '{"barcodes":["001"]}']
    assert command_allowed('gjp.inventory-cost', args, [PERMISSION])
    assert not command_allowed('gjp.inventory-cost', ['gjp','inventory-cost','--input','{"source_path":"a.csv"}'], [PERMISSION])
    with pytest.raises(AuthFailure):service.credentials({**user, 'permissions': ['guanjia:configure']})
    with service.guanjia.repo.transaction() as state:state['connections'][owner_key(user)]['database'] = 'another-book'
    with pytest.raises(AuthFailure) as error:service.credentials(user)
    assert error.value.code == 'gjp_cost_source_required'


def test_dispatcher_passes_private_context_and_checks_scope_again(source):
    service, user = source
    user = {**user, 'permissions': [PERMISSION, 'tools:galaxy_cli', 'galaxy:help']}
    dispatcher = GalaxyDispatcher(service.directory, authorize=lambda _: {'user': user})
    dispatcher.gjp_cost_provider = service
    context = ExecutionContext(user['id'], user['workspace_id'], tuple(user['permissions']), 'chat', 'session')
    async def execute(canonical, principal, **kwargs):
        assert kwargs['credentials']['password'] == 'fixture-secret'
        assert kwargs['file_context']['cache_identity']['user_id'] == 'alice'
        assert 'fixture-secret' not in json.dumps(canonical)
        return {'data': {'summary': {'complete': True}, 'rows': []}}
    dispatcher._run = AsyncMock(side_effect=execute)
    async def run():
        event = {}
        result = await dispatcher._gjp_cost_run(context, 'gjp.inventory-cost',
            ['gjp','inventory-cost','--input','{"barcodes":["001"]}'], event)
        assert result['data']['summary']['complete'] and len(event['resume_id']) == 32
    asyncio.run(run())


def test_bulk_cache_receipt_has_real_counts_and_small_summary(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    user = {'id':'u','username':'fixture','workspace_id':'w','permissions':[PERMISSION,'tools:galaxy_cli','galaxy:help']}
    dispatcher = GalaxyDispatcher(tmp_path, authorize=lambda _: {'user': user})
    context = ExecutionContext('u','w',tuple(user['permissions']),'chat','session')
    result = {'data': {'resume_id':'a'*32, 'summary': {'complete':True,'total':1200,'processed':1200,'batches':3},
                       'rows':[{'barcode':str(i),'avg_cost':'1.00'} for i in range(1200)]}}
    from xingxu.tool_cache import cache_result
    receipt = asyncio.run(cache_result(dispatcher, context, 'gjp.inventory-cost',
        ['gjp','inventory-cost','--input','{"barcodes":["001"]}'],None,result,False))
    assert receipt['complete'] and receipt['count'] == receipt['total'] == 1200
    assert receipt['coverage'] == 'requested_barcodes' and receipt['pages'] == 3
    assert len(json.dumps(receipt)) < 3000 and 'rows' not in receipt
    root = dispatcher.files.cache(user)
    assert len(json.loads((root / receipt['path']).read_text())['data']['rows']) == 1200
    manifest = json.loads((root / receipt['metadata_path'].removeprefix('/account-cache/')).read_text())
    assert manifest['count'] == 1200 and manifest['coverage'] == 'requested_barcodes'


def test_cancel_reaps_real_process_and_preserves_only_completed_batch(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    from galaxy_cli.gjp.inventory_cost import run
    job = tmp_path / 'jobs';job.mkdir()
    frame = {'job_id': uuid.uuid4().hex, 'job_directory': str(job),
        'cache_identity': {'user_id': 'u', 'workspace_id': 'w'},
        'categories': {'gjp': {'mode':'inventory-cost-database','database':'book',
                             'workspace_id':'w','source_revision':'v1'}}}
    executable = tmp_path / 'fixture-cli'
    module_root = str(Path(sys.modules[run.__module__].__file__).resolve().parents[2])
    executable.write_text('#!' + sys.executable + '\nimport sys\nsys.path.insert(0,' + repr(module_root) + ')\n' + '''import json,os,sys,time
from pathlib import Path
from galaxy_cli.gjp.inventory_cost import run
frame=json.load(sys.stdin)
class Client:
 def __init__(self,_):self.calls=0
 def __enter__(self):return self
 def __exit__(self,*_):pass
 def query(self,codes,scope):
  self.calls+=1
  if self.calls==2:
   Path(frame['job_directory'],'started').write_text(str(os.getpid()))
   time.sleep(60)
  return []
run(frame['input'],frame,Client)
''')
    executable.chmod(0o700)
    dispatcher = GalaxyDispatcher(tmp_path / 'private')
    dispatcher.executable = executable
    user = {'id':'u','workspace_id':'w','username':'fixture','permissions':[PERMISSION]}
    async def cancel():
        task = asyncio.create_task(dispatcher._run(['gjp','inventory-cost','--input',json.dumps({'barcodes':[str(i) for i in range(1001)]})],
            user,credentials=frame['categories']['gjp'],file_context=frame))
        for _ in range(200):
            if (job/'started').exists():break
            if task.done():await task
            await asyncio.sleep(.02)
        assert (job/'started').exists()
        pid=int((job/'started').read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,3)
        with pytest.raises(ProcessLookupError):os.kill(pid,0)
    asyncio.run(cancel())
    state = json.loads((job/(frame['job_id']+'.json')).read_text())
    assert len(state['rows']) == 500 and state['batches'] == 1
    class Resume:
        calls=[]
        def __init__(self,_):pass
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def query(self,codes,scope):self.calls.append(codes);return []
    result=run({'resume_id':frame['job_id']},frame,Resume)['data']
    assert result['summary']['complete'] and Resume.calls[0][0]=='500' and len(Resume.calls)==2


def test_dsh_materializes_only_own_workspace_source_and_preserves_columns(tmp_path, monkeypatch):
    import base64
    from xingxu.dsh_runtime import DshTurn
    permissions=[PERMISSION,'file:read']
    user={'id':'u','workspace_id':'w','permissions':permissions}
    dispatcher=SimpleNamespace(files=SimpleNamespace(workspace=lambda _:tmp_path),execute=AsyncMock(return_value={'result':{'ok':True}}))
    turn=DshTurn(SimpleNamespace(dispatcher=dispatcher),ExecutionContext('u','w',tuple(permissions),'chat','s'),{},
        run=None,progress={},thinking=None,reasoning_effort=None)
    monkeypatch.setattr(turn,'principal',lambda:user)
    worker=AsyncMock(return_value={'data':base64.b64encode(b'code\n001\n').decode()})
    monkeypatch.setattr('xingxu.dsh_runtime.worker_request',worker)
    async def exercise():
        await turn.cli({'args':['gjp','inventory-cost','--input',json.dumps({'source_path':'/workspace/codes.csv','barcode_column':'code'})]})
        call=dispatcher.execute.call_args.args[1]
        args=json.loads(call['function']['arguments'])['args']
        body=json.loads(args[3])
        assert body['barcode_column']=='code' and body['source_path'].startswith('dsh-exports/chat/')
        assert (tmp_path/body['source_path']).read_text()=='code\n001\n'
        worker.reset_mock()
        await turn.cli({'args':['gjp','inventory-cost','--input','{"source_path":"/account-files/codes.csv"}']})
        await turn.cli({'args':['gjp','inventory-cost','--input','{"barcodes":["001"]}']})
        worker.assert_not_awaited()
    asyncio.run(exercise())
