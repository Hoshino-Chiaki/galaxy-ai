"""Real DSH jail with an isolated socket and synthetic model/accounts; no business calls."""
import asyncio
import contextlib
import importlib.util
import json
import os
import subprocess
import shlex
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from xingxu.account_storage import Accounts, scoped
from xingxu.auth import AuthService, AuthFailure
from xingxu.ai import AiService
from xingxu.store import JsonRepository
import xingxu.dsh_runtime as dsh
from xingxu.tool_context import ExecutionContext


async def exercise(directory):
    private = directory / 'private'
    auth = AuthService(JsonRepository(private))
    auth.import_accounts([('chiaki-fixture', 'fixture-password'), ('yuzhuo-fixture', 'fixture-password')])
    first = auth.login('chiaki-fixture','fixture-password',False,'fixture')
    second = auth.login('yuzhuo-fixture','fixture-password',False,'fixture')
    Accounts(private).ensure(first['user'])
    Accounts(private).ensure(second['user'])
    ai = AiService(private, authorize=auth.identity)
    ai.dispatcher.executable = Path('/opt/galaxy-ai/galaxy-cli/.venv/bin/galaxy-cli')
    cid = str(uuid.uuid4())
    context = ExecutionContext(first['user']['id'],first['user']['workspace_id'],tuple(first['user']['permissions']),cid,first['access_token'])
    provider = {'model':'fixture-model','max_tokens':4096}
    async def command(text, *, checkpoint=None, ctx=context):
        seen = []
        async def model(_provider, wire, **options):
            seen.append(wire)
            if len(seen) == 1:
                assert 'bash' in [tool['function']['name'] for tool in options.get('tools',[])]
                message = {'role':'assistant','content':None,'tool_calls':[{'id':'fixture-'+uuid.uuid4().hex,
                    'type':'function','function':{'name':'bash','arguments':json.dumps({'command':text})}}]}
            else:
                message = {'role':'assistant','content':'fixture complete'}
            return message, {'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}, ''
        async def prepare(wire):return wire
        with patch.object(ai,'_model_message',model):
            result = await dsh.DshTurn(ai,ctx,provider,run=None,progress={},thinking=False,reasoning_effort='low').execute(
                [{'role':'user','content':'Run the fixture command.'}],prepare=prepare,checkpoint=checkpoint)
        return result,seen
    result,seen = await command("python -c \"from pathlib import Path;Path('proof.txt').write_text('account-file-fixture')\"; galaxy-cli time now --input '{}' --format json; galaxy-cli file publish --input '{\"path\":\"proof.txt\"}' --format json")
    assert result[0] == 'fixture complete'
    assert len(result[3]) == 2 and all(e['status']=='success' for e in result[3]), result[3]
    checkpoint = result[-1]
    assert checkpoint and (ai.dispatcher.files.dsh_root(first['user'])/cid/checkpoint['run_id']/'complete').is_file()
    resumed,history = await command("python -c \"print(open('proof.txt').read())\"",checkpoint=checkpoint)
    assert 'account-file-fixture' in json.dumps(history[-1])
    private_key = '/opt/galaxy-ai/backend/private/ai-secret.key'
    checked,history = await command('python -c '+shlex.quote('import os;print("host-key-visible",os.path.exists('+repr(private_key)+'))'),checkpoint=resumed[-1])
    assert 'host-key-visible False' in json.dumps(history[-1])
    foreign = ExecutionContext(second['user']['id'],second['user']['workspace_id'],tuple(second['user']['permissions']),cid,second['access_token'])
    try:
        await command('true',checkpoint=checkpoint,ctx=foreign)
    except AuthFailure:
        pass
    else:
        raise AssertionError('Foreign checkpoint accepted')
    print(json.dumps({'isolated_worker':True,'native_python':True,'cli_tool':True,'file_publication':True,
        'account_checkpoint_location':True,'resumed_context':True,'host_key_hidden':True,'foreign_checkpoint_denied':True}),flush=True)


def main():
    os.environ.pop('GALAXY_DATABASE_CONFIG',None)
    os.environ['GALAXY_ACCOUNT_STORAGE']='files-v1'
    root=Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='galaxy-storage-dsh-') as temporary:
        directory=Path(temporary)
        os.environ['GALAXY_FILES_DIR']=str(directory/'shared-files')
        os.environ['GALAXY_DSH_BROKER_DIR']=str(directory/'brokers')
        os.environ['GALAXY_ACCOUNTS_ROOT']=str(directory/'private/accounts')
        (directory/'brokers').mkdir()
        socket=directory/'worker.sock'
        dsh.SOCKET=str(socket)
        launcher='''import asyncio,importlib.util,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('account_test_worker',sys.argv[1]);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.SOCKET=sys.argv[2];module.JOBS=Path(sys.argv[3]);module.BROKERS=Path(sys.argv[4]);asyncio.run(module.main())
'''
        process=subprocess.Popen(['/usr/bin/python3','-c',launcher,str(root/'dsh-runtime/worker.py'),
            str(socket),str(directory/'jobs'),str(directory/'brokers')],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            import time
            for _ in range(100):
                if socket.exists():break
                if process.poll() is not None:raise RuntimeError('Isolated worker failed to start')
                time.sleep(.05)
            assert socket.exists()
            asyncio.run(exercise(directory))
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill();process.wait()
            if process.stderr:process.stderr.close()


if __name__ == '__main__':main()
