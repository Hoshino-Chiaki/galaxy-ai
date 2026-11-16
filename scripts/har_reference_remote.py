"""Stage, test and deploy purpose-gated uploads; credentials never enter output."""
import hashlib
import json
import shlex
import sys
import uuid
from pathlib import Path
import paramiko

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'artifacts/har-reference-remote-state.json'
STATE.parent.mkdir(parents=True, exist_ok=True)
state = json.loads(STATE.read_text()) if STATE.exists() else {'validation': '/var/tmp/galaxy-ai-har-reference-20261116-' + uuid.uuid4().hex[:10]}
validation = state['validation']
assert validation.startswith('/var/tmp/galaxy-ai-har-reference-20261116-') and '/' not in validation.rsplit('/', 1)[1]
STATE.write_text(json.dumps(state), encoding='utf-8')
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
FILES = [
    'backend/xingxu/uploads.py', 'backend/xingxu/task_tools.py', 'backend/xingxu/tools.py',
    'backend/xingxu/ai.py', 'backend/xingxu/auth.py', 'backend/xingxu/app.py', 'backend/xingxu/store.py',
    'backend/xingxu/conversation_context.py', 'backend/xingxu/tool_prompts.py',
    'galaxy-cli/pyproject.toml', 'galaxy-cli/src/galaxy_cli/cli.py',
    'galaxy-cli/src/galaxy_cli/tool_categories.py', 'galaxy-cli/src/galaxy_cli/har_archive.py',
    'galaxy-cli/src/galaxy_cli/har_local.py', 'galaxy-cli/src/galaxy_cli/tools/task/__init__.py',
    'galaxy-cli/src/galaxy_cli/tools/task/har.py', 'galaxy-cli/src/galaxy_cli/tools/task/request_upload.py']
TESTS = ['test_har_reference.py', 'test_uploads.py', 'test_ai.py', 'test_tools.py', 'test_chat_progress.py', 'test_chat_cancel.py',
         'test_thinking.py', 'test_tool_prompts.py', 'test_task_prompts_local.py', 'test_excel.py',
         'test_file_publish.py', 'test_agent_runtime.py', 'test_agent_service.py', 'test_context_integration.py']


def run(command):
    _, output, _ = client.exec_command(command, timeout=900)
    output.channel.set_combine_stderr(True)
    text = output.read().decode('utf-8', errors='replace').replace(password, '[hidden]')
    print(text, end='', flush=True)
    status = output.channel.recv_exit_status()
    if status:
        raise RuntimeError(f'Remote command failed: {status}')
    return text


def python(code):
    return run('/opt/galaxy-ai/backend/.venv/bin/python -c ' + shlex.quote(code))


def sync():
    paths = FILES + ['backend/pytest.ini', 'backend/requirements.txt', 'backend/requirements-dev.txt']
    paths += ['backend/tests/' + name for name in TESTS]
    paths += ['galaxy-cli/tests/test_cli.py', 'galaxy-cli/tests/test_har.py']
    with client.open_sftp() as sftp:
        for relative in paths:
            target = validation + '/' + relative
            parts = target.rsplit('/', 1)[0].split('/')
            for n in range(2, len(parts) + 1):
                try: sftp.mkdir('/'.join(parts[:n]))
                except OSError: pass
            sftp.put(str(ROOT / relative), target)
            sftp.chmod(target, 0o644)


def health():
    python("import json,time,urllib.request\nfor n in range(30):\n try:\n  r=json.load(urllib.request.urlopen('http://127.0.0.1:18443/health',timeout=2)); assert r['status']=='ok'; print(r); break\n except Exception: time.sleep(.5)\nelse: raise RuntimeError('Health check failed')")


try:
    mode = sys.argv[1]
    if mode == 'audit':
        with client.open_sftp() as sftp:
            baseline = {}
            for relative in FILES:
                try:
                    content = sftp.open('/opt/galaxy-ai/' + relative).read()
                except FileNotFoundError:
                    baseline[relative] = None
                    continue
                baseline[relative] = hashlib.sha256(content).hexdigest()
                target = ROOT / 'artifacts/har-reference-server-baseline' / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            for name in ('/AGENTS.md', '/opt/AGENTS.md', '/opt/galaxy-ai/AGENTS.md', '/opt/galaxy-ai/backend/AGENTS.md', '/opt/galaxy-ai/galaxy-cli/AGENTS.md'):
                try: print('Instructions:', name, sftp.open(name).read().decode(), flush=True)
                except FileNotFoundError: pass
        state['baseline'] = baseline
        finding = python("from pathlib import Path\nimport json\na=[]\nfor d in ('/etc/nginx/sites-enabled','/etc/nginx/conf.d'):\n for p in Path(d).glob('*'):\n  if p.is_file() and '127.0.0.1:18443' in p.read_text(): a.append(str(p.resolve()))\nprint(json.dumps({'nginx':a}))\n")
        state['nginx'] = json.loads(finding)['nginx'][0]
        with client.open_sftp() as sftp:
            content = sftp.open(state['nginx']).read()
            (ROOT / 'artifacts/har-reference-nginx-baseline.conf').write_bytes(content)
            state['nginx_sha256'] = hashlib.sha256(content).hexdigest()
        STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
        run('systemctl is-active galaxy-ai.service')
        health()
        print('Read-only baseline recorded:', len(FILES), 'files', flush=True)
    elif mode == 'prompt-status':
        python('''from pathlib import Path
import json,sys
sys.path.insert(0,'/opt/galaxy-ai/galaxy-cli/src')
from galaxy_cli.tool_categories import DEFAULT_CATEGORY_PROMPTS
p=Path('/opt/galaxy-ai/backend/private/tool-prompts.json')
state=json.loads(p.read_text()) if p.exists() else {'workspaces':{}}
items=[v['task'] for v in state['workspaces'].values() if 'task' in v]
print(json.dumps({'default_prompt_chars':len(DEFAULT_CATEGORY_PROMPTS['task']), 'task_override_count':len(items),
 'legacy_override_count':sum(x=='为执行各大平台自动化任务准备' for x in items), 'empty_override_count':sum(not x.strip() for x in items)},ensure_ascii=False))
''')
    elif mode == 'stage':
        python(f"from pathlib import Path\nimport shutil\nr=Path({validation!r});r.mkdir(mode=0o700)\n(r/'backend').mkdir();(r/'galaxy-cli').mkdir()\ndef ignore(d,ns): return [n for n in ns if n=='__pycache__' or Path(d,n).is_file() and not n.endswith(('.py','.json'))]\nshutil.copytree('/opt/galaxy-ai/backend/xingxu',r/'backend/xingxu',ignore=ignore)\nshutil.copytree('/opt/galaxy-ai/galaxy-cli/src',r/'galaxy-cli/src',ignore=ignore)\nprint('Isolated source copy created; no live user data copied.')")
        sync()
        run('/opt/galaxy-ai/backend/.venv/bin/python -m venv ' + validation + '/.venv')
        run(validation + '/.venv/bin/python -m pip install -q -r ' + validation + '/backend/requirements-dev.txt -e ' + validation + '/galaxy-cli')
        python(f"from pathlib import Path\np=Path({validation!r});(p/'galaxy-cli/.venv').symlink_to(p/'.venv',target_is_directory=True)")
    elif mode == 'sync':
        sync()
    elif mode == 'test':
        command = ('cd ' + validation + ' && env -u XINGXU_DATA_DIR -u GALAXY_FILES_DIR PYTHONDONTWRITEBYTECODE=1 PYTHONPATH='
                   + validation + '/backend:' + validation + '/galaxy-cli/src ' + validation + '/.venv/bin/python -m pytest -c backend/pytest.ini '
                   + ' '.join('backend/tests/' + name for name in TESTS) + ' galaxy-cli/tests/test_cli.py galaxy-cli/tests/test_har.py -q --tb=short')
        result = run(command)
        state['validated'] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in FILES}
        state['tests'] = result
        STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
    elif mode == 'cleanup':
        assert state.get('deployed') == {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in FILES}
        python(f'''from pathlib import Path
import shutil
p=Path({validation!r})
assert p.parent==Path('/var/tmp') and p.name.startswith('galaxy-ai-har-reference-20261116-') and not p.is_symlink()
assert p.resolve()==p
if p.exists(): shutil.rmtree(p)
print('Isolated validation files removed; rollback backups and live user files retained.')
''')
    elif mode == 'deploy':
        hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in FILES}
        assert hashes == state.get('validated'), 'Current files have not passed validation'
        with client.open_sftp() as sftp:
            staged = {p: hashlib.sha256(sftp.open(validation + '/' + p).read()).hexdigest() for p in FILES}
        assert staged == hashes, 'Validated staged source differs from the deployment candidate'
        backup = '/var/backups/galaxy-ai/har-reference-20261116-' + validation.rsplit('-', 1)[1] + '-' + uuid.uuid4().hex[:6]
        state['backup'] = backup
        STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
        python(f'''from pathlib import Path
import hashlib,json,shutil
p=Path('/opt/galaxy-ai');b=Path({backup!r});b.mkdir(parents=True,mode=0o700)
for rel,digest in {state['baseline']!r}.items():
 s=p/rel
 assert (hashlib.sha256(s.read_bytes()).hexdigest() if s.exists() else None)==digest,rel
 if s.exists():
  t=b/'original'/rel;t.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(s,t)
shutil.copy2({state['nginx']!r},b/'nginx.conf')
assert hashlib.sha256(Path({state['nginx']!r}).read_bytes()).hexdigest()=={state['nginx_sha256']!r}
(b/'manifest.json').write_text(json.dumps({state['baseline']!r}))
print('Rollback backup:',b)
''')
        try:
            with client.open_sftp() as sftp:
                candidate = (ROOT / 'deploy/galaxy-ai.nginx.conf').read_text(encoding='utf-8')
                with sftp.open(backup + '/check-nginx.conf', 'w') as stream:
                    stream.write('events {}\nhttp {\n' + candidate + '\n}\n')
            run('nginx -t -c ' + backup + '/check-nginx.conf')
            run('/opt/galaxy-ai/backend/.venv/bin/python -m pip install -q haralyzer==2.4.0')
            run('/opt/galaxy-ai/galaxy-cli/.venv/bin/python -m pip install -q haralyzer==2.4.0')
            with client.open_sftp() as sftp:
                for relative in FILES:
                    target = '/opt/galaxy-ai/' + relative
                    python(f"from pathlib import Path\np=Path({target!r}).parent;p.mkdir(parents=True,exist_ok=True)")
                    sftp.put(str(ROOT / relative), target + '.incoming')
                    sftp.chmod(target + '.incoming', 0o644)
                sftp.put(str(ROOT / 'deploy/galaxy-ai.nginx.conf'), backup + '/incoming-nginx.conf')
            python(f'''from pathlib import Path
import os,shutil
p=Path('/opt/galaxy-ai')
for rel in {FILES!r}:
 t=p/rel;s=t.with_name(t.name+'.incoming');os.chown(s, t.stat().st_uid if t.exists() else 0, t.stat().st_gid if t.exists() else 0);os.replace(s,t)
shutil.copy2({backup!r}+'/incoming-nginx.conf',{state['nginx']!r})
''')
            run('nginx -t')
            run('systemctl restart galaxy-ai.service')
            health()
            run('systemctl reload nginx')
        except BaseException:
            python(f'''from pathlib import Path
import shutil
p=Path('/opt/galaxy-ai');b=Path({backup!r})
for rel,digest in {state['baseline']!r}.items():
 t=p/rel
 if digest: shutil.copy2(b/'original'/rel,t)
 else: t.unlink(missing_ok=True)
shutil.copy2(b/'nginx.conf',{state['nginx']!r})
''')
            run('systemctl restart galaxy-ai.service')
            run('nginx -t')
            run('systemctl reload nginx')
            raise
        state['deployed'] = hashes
        STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
        print('Validated upload runtime activated.', flush=True)
finally:
    client.close()
