"""Scoped DSH installation/validation helper; credentials stay in memory."""
import hashlib
import json
from pathlib import Path
import shlex
import sys
import uuid
import paramiko

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'artifacts/truncation-deployment-state.json'
STATE.parent.mkdir(parents=True, exist_ok=True)
state = json.loads(STATE.read_text()) if STATE.exists() else {'validation': json.loads((ROOT/'artifacts/dsh-deployment-state.json').read_text())['validation']}
STATE.write_text(json.dumps(state), encoding='utf-8')
stage = state['validation']
assert stage.startswith('/var/tmp/galaxy-ai-dsh-20261116-') and '/' not in stage.rsplit('/', 1)[1]
raw = Path('C:/Users/Administrator/Desktop/新建文本文档 (2).txt').read_bytes()
for encoding in ('utf-8-sig', 'utf-16', 'gb18030'):
    try:
        password = raw.decode(encoding).strip()
        break
    except UnicodeError:
        pass
assert password and '\n' not in password and '\r' not in password
client = paramiko.SSHClient()
client.load_host_keys(str(Path.home() / '.ssh/known_hosts'))
client.set_missing_host_key_policy(paramiko.RejectPolicy())
client.connect('124.222.51.137', username='root', password=password, look_for_keys=False, allow_agent=False, timeout=12)
FILES = ['backend/xingxu/ai.py']
RUNTIME = ['worker.py', 'inner.py', 'profile.patch.yml', 'galaxy-cli', 'galaxy-files']
TESTS = ['test_dsh_protocol.py', 'test_dsh_runtime.py', 'test_dsh_live.py']


def run(command):
    _, output, _ = client.exec_command(command, timeout=1200)
    output.channel.set_combine_stderr(True)
    value = output.read().decode('utf-8', errors='replace').replace(password, '[hidden]')
    print(value, end='', flush=True)
    status = output.channel.recv_exit_status()
    if status:
        raise RuntimeError('Remote operation failed: ' + str(status))
    return value


def python(code):
    return run('/usr/bin/python3 -c ' + shlex.quote(code))


def sync_files():
    paths = FILES + ['backend/tests/' + t for t in TESTS if (ROOT / 'backend/tests' / t).exists()]
    paths += [p.relative_to(ROOT).as_posix() for p in (ROOT/'backend/tests').glob('*.py')]
    paths += ['backend/pytest.ini', 'backend/requirements.txt', 'backend/requirements-dev.txt', 'galaxy-cli/pyproject.toml']
    with client.open_sftp() as ftp:
        for relative in paths:
            target = stage + '/' + relative
            parts = target.rsplit('/', 1)[0].split('/')
            for n in range(2, len(parts) + 1):
                try:
                    ftp.mkdir('/'.join(parts[:n]))
                except OSError:
                    pass
            ftp.put(str(ROOT / relative), target)
            ftp.chmod(target, 0o644)


try:
    mode=sys.argv[1]
    if mode=='audit':
        with client.open_sftp() as ftp:
            data=ftp.open('/opt/galaxy-ai/backend/xingxu/ai.py').read()
        state['baseline']=hashlib.sha256(data).hexdigest()
        (ROOT/'artifacts/truncation-ai-baseline.py').write_bytes(data)
        STATE.write_text(json.dumps(state,indent=2),encoding='utf-8')
        print('Read-only source baseline recorded.')
    elif mode=='test':
        sync_files()
        digest=hashlib.sha256((ROOT/FILES[0]).read_bytes()).hexdigest()
        tests=['test_thinking.py','test_agent_service.py','test_context_integration.py','test_agent_runtime.py','test_ai.py','test_chat_cancel.py','test_chat_progress.py','test_uploads.py']
        result=run('cd '+stage+' && env -u XINGXU_DATA_DIR -u GALAXY_FILES_DIR GALAXY_AI_RUNTIME=langgraph PYTHONDONTWRITEBYTECODE=1 PYTHONPATH='+stage+'/backend:'+stage+'/galaxy-cli/src '+stage+'/.venv/bin/python -m pytest -c backend/pytest.ini '+' '.join('backend/tests/'+t for t in tests)+' -q --tb=short')
        assert digest==hashlib.sha256((ROOT/FILES[0]).read_bytes()).hexdigest()
        state['validated']=digest;state['tests']=result
        STATE.write_text(json.dumps(state,indent=2),encoding='utf-8')
    elif mode=='script':
        target=stage+'/truncation-operation.py'
        with client.open_sftp() as ftp:
            with ftp.open(target,'w') as out:out.write(Path(sys.argv[2]).read_text(encoding='utf-8'))
        run('env GALAXY_AI_RUNTIME=dsh PYTHONPATH='+stage+'/backend:'+stage+'/galaxy-cli/src '+stage+'/.venv/bin/python '+target)
    elif mode=='deploy':
        digest=hashlib.sha256((ROOT/FILES[0]).read_bytes()).hexdigest()
        assert digest==state.get('validated')
        backup='/var/backups/galaxy-ai/summary-recovery-20261116-'+uuid.uuid4().hex[:10]
        with client.open_sftp() as ftp:
            actual=ftp.open('/opt/galaxy-ai/backend/xingxu/ai.py').read()
            assert hashlib.sha256(actual).hexdigest()==state['baseline']
            assert hashlib.sha256(ftp.open(stage+'/'+FILES[0]).read()).hexdigest()==digest
        python("from pathlib import Path\nimport shutil\nb=Path("+repr(backup)+");b.mkdir(parents=True,mode=0o700);shutil.copy2('/opt/galaxy-ai/backend/xingxu/ai.py',b/'ai.py')")
        with client.open_sftp() as ftp:
            target='/opt/galaxy-ai/backend/xingxu/ai.py'
            ftp.put(str(ROOT/FILES[0]),target+'.incoming');ftp.chmod(target+'.incoming',0o644);ftp.posix_rename(target+'.incoming',target)
        try:
            run('systemctl restart galaxy-ai.service')
            python("import time,json,urllib.request\nfor _ in range(30):\n try:\n  r=json.load(urllib.request.urlopen('http://127.0.0.1:18443/health',timeout=2));assert r['status']=='ok';print('Health: ok');break\n except Exception:time.sleep(.5)\nelse:raise RuntimeError('health failed')")
            run('systemctl is-active galaxy-ai.service galaxy-dsh.service')
        except BaseException:
            python("import shutil\nshutil.copy2("+repr(backup+ '/ai.py')+",'/opt/galaxy-ai/backend/xingxu/ai.py')")
            run('systemctl restart galaxy-ai.service');raise
        state['backup']=backup;state['deployed']=digest
        STATE.write_text(json.dumps(state,indent=2),encoding='utf-8');print('Summary recovery deployed; business data unchanged.')
finally:
    client.close()
