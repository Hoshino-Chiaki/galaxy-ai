"""Task-local SSH helper. Reads the user-designated password without echoing it."""
import hashlib
import json
import shlex
import sys
import uuid
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'artifacts' / 'chat-progress-remote-state.json'
if STATE.exists():
    state = json.loads(STATE.read_text(encoding='utf-8'))
else:
    state = {'validation': '/var/tmp/galaxy-ai-chat-progress-20261116-' + uuid.uuid4().hex[:10]}
    STATE.write_text(json.dumps(state), encoding='utf-8')
validation = state['validation']
if not validation.startswith('/var/tmp/galaxy-ai-chat-progress-20261116-') or '/' in validation.rsplit('/', 1)[1]:
    raise SystemExit('Invalid task validation directory')
PASSWORD_FILE = Path('C:/Users/Administrator/Desktop/新建文本文档 (2).txt')
raw = PASSWORD_FILE.read_bytes()
for encoding in ('utf-8-sig', 'utf-16', 'gb18030'):
    try:
        password = raw.decode(encoding).strip()
        break
    except UnicodeError:
        pass
else:
    raise SystemExit('Cannot decode the designated password file')
if not password or '\n' in password or '\r' in password:
    raise SystemExit('Expected a single password in the designated file')

client = paramiko.SSHClient()
client.load_host_keys(str(Path.home() / '.ssh' / 'known_hosts'))
client.set_missing_host_key_policy(paramiko.RejectPolicy())
client.connect('124.222.51.137', username='root', password=password,
    look_for_keys=False, allow_agent=False, timeout=12, auth_timeout=15, banner_timeout=15)


def run(command, *, timeout=600):
    _, stdout, _ = client.exec_command(command, timeout=timeout)
    stdout.channel.set_combine_stderr(True)
    output = stdout.read().decode('utf-8', errors='replace')
    status = stdout.channel.recv_exit_status()
    print(output.replace(password, '[hidden]'), end='', flush=True)
    if status:
        raise SystemExit(f'Remote command failed with exit status {status}')
    return output


def python(code):
    return run('/opt/galaxy-ai/backend/.venv/bin/python -c ' + shlex.quote(code))


def upload(relative, destination=None):
    target = destination or validation + '/' + relative
    with client.open_sftp() as sftp:
        sftp.put(str(ROOT / relative), target)
        sftp.chmod(target, 0o644)


def rollback(backup):
    print('Restoring saved runtime and Python environment.', flush=True)
    run('systemctl stop galaxy-ai.service')
    python(f'''
from pathlib import Path
import json, os, shutil
project = Path('/opt/galaxy-ai')
backup = Path({backup!r})
manifest = json.loads((backup / 'manifest.json').read_text())
venv = project / 'backend/.venv'
assert not venv.is_symlink() and venv.resolve() == Path('/opt/galaxy-ai/backend/.venv')
os.replace(venv, backup / 'failed-venv')
shutil.copytree(backup / 'venv', venv, symlinks=True)
for relative, item in manifest['files'].items():
    target = project / relative
    if item['existed']:
        shutil.copy2(backup / 'original' / relative, target)
    elif target.exists():
        target.unlink()
print('Saved runtime restored.')
''')
    run('systemctl start galaxy-ai.service')
    health_check()


def health_check():
    python('''
import json, time, urllib.request
for attempt in range(30):
    try:
        with urllib.request.urlopen('http://127.0.0.1:18443/health', timeout=2) as response:
            value = json.load(response)
        assert value.get('status') == 'ok'
        print(json.dumps(value))
        break
    except Exception:
        time.sleep(0.5)
else:
    raise SystemExit('Service health check failed')
''')
    run('systemctl is-active galaxy-ai.service')


TESTS = ['test_agent_runtime.py', 'test_agent_service.py', 'test_ai.py', 'test_tools.py',
         'test_chat_cancel.py', 'test_thinking.py', 'test_tool_prompts.py', 'test_context_integration.py', 'test_chat_progress.py']
RUNTIME_FILES = ['backend/xingxu/ai.py', 'backend/xingxu/chat_runs.py',
                 'backend/xingxu/app.py', 'backend/requirements.txt']


try:
    if sys.argv[1] == 'audit':
        with client.open_sftp() as sftp:
            for remote in ('/AGENTS.md', '/opt/AGENTS.md', '/opt/galaxy-ai/AGENTS.md',
                           '/opt/galaxy-ai/backend/AGENTS.md', '/opt/galaxy-ai/backend/xingxu/AGENTS.md'):
                try:
                    with sftp.open(remote) as stream:
                        content = stream.read().decode('utf-8')
                except FileNotFoundError:
                    continue
                print('REMOTE INSTRUCTIONS:', remote, '\n' + content.replace(password, '[hidden]'), flush=True)
            for relative in RUNTIME_FILES + ['backend/requirements-dev.txt']:
                try:
                    with sftp.open('/opt/galaxy-ai/' + relative) as stream:
                        content = stream.read()
                except FileNotFoundError:
                    print('Absent optional baseline:', relative, flush=True)
                    continue
                target = ROOT / 'artifacts' / 'chat-progress-server-baseline' / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                print(json.dumps({'baseline': relative, 'sha256': hashlib.sha256(content).hexdigest()}), flush=True)
        run("/opt/galaxy-ai/backend/.venv/bin/python -c \"import sys,importlib.metadata as m; print(sys.version); print({n:m.version(n) for n in ['fastapi','httpx','cryptography','pydantic']})\"")
        run('systemctl is-active galaxy-ai.service')
        run('curl -fsS http://127.0.0.1:18443/health')
    elif sys.argv[1] == 'stage':
        print('Preparing isolated server validation:', validation, flush=True)
        python(f'''
from pathlib import Path
import shutil
root = Path({validation!r})
root.mkdir(mode=0o700)
(root / 'backend').mkdir()
(root / 'backend/tests').mkdir()
(root / 'galaxy-cli').mkdir()
def ignore(directory, names):
    path = Path(directory)
    return [name for name in names if name == '__pycache__' or (path / name).is_file() and not name.endswith(('.py', '.json'))]
shutil.copytree('/opt/galaxy-ai/backend/xingxu', root / 'backend/xingxu', ignore=ignore)
shutil.copytree('/opt/galaxy-ai/galaxy-cli/src', root / 'galaxy-cli/src', ignore=ignore)
shutil.copy2('/opt/galaxy-ai/galaxy-cli/pyproject.toml', root / 'galaxy-cli/pyproject.toml')
print('Copied server source only; no private, browser or hosted-file directories.')
''')
        for relative in RUNTIME_FILES + ['backend/requirements-dev.txt', 'backend/pytest.ini'] + ['backend/tests/' + name for name in TESTS]:
            upload(relative)
        run('/opt/galaxy-ai/backend/.venv/bin/python -m venv ' + validation + '/.venv')
        run(validation + '/.venv/bin/python -m pip install -q -r ' + validation + '/backend/requirements-dev.txt -e ' + validation + '/galaxy-cli')
        python(f'''
from pathlib import Path
target = Path({validation!r})
(target / 'galaxy-cli/.venv').symlink_to(target / '.venv', target_is_directory=True)
print('Validation dependencies ready.')
''')
    elif sys.argv[1] == 'sync':
        for relative in RUNTIME_FILES + ['backend/requirements-dev.txt', 'backend/pytest.ini'] + ['backend/tests/' + name for name in TESTS]:
            upload(relative)
        print('Validation source synchronized.', flush=True)
    elif sys.argv[1] == 'clean-env':
        run('/opt/galaxy-ai/backend/.venv/bin/python -m venv --clear ' + validation + '/.venv')
        run(validation + '/.venv/bin/python -m pip install -q -r ' + validation + '/backend/requirements-dev.txt -e ' + validation + '/galaxy-cli')
        run(validation + '/.venv/bin/python -m pip check')
    elif sys.argv[1] == 'test':
        print('Running isolated Linux regression suite:', validation, flush=True)
        command = ('cd ' + validation + ' && env -u XINGXU_DATA_DIR -u GALAXY_FILES_DIR '
            'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=' + validation + '/backend:' + validation + '/galaxy-cli/src '
            + validation + '/.venv/bin/python -m pytest -c backend/pytest.ini '
            + ' '.join('backend/tests/' + name for name in TESTS) + ' -q --tb=short')
        output = run(command)
        with client.open_sftp() as sftp:
            for relative in RUNTIME_FILES:
                actual = hashlib.sha256(sftp.open(validation + '/' + relative).read()).hexdigest()
                expected = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                if actual != expected:
                    raise SystemExit('Validation source hash mismatch: ' + relative)
        state['validated_sha256'] = {relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() for relative in RUNTIME_FILES}
        state['test_result'] = output
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    elif sys.argv[1] == 'deploy':
        expected = {relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() for relative in RUNTIME_FILES}
        if state.get('validated_sha256') != expected:
            raise SystemExit('Current source has not passed server validation')
        baseline = {relative: hashlib.sha256((ROOT / 'artifacts/chat-progress-server-baseline' / relative).read_bytes()).hexdigest()
                    for relative in RUNTIME_FILES}
        backup = '/var/backups/galaxy-ai/chat-progress-20261116-' + validation.rsplit('-', 1)[1]
        state['backup'] = backup
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Backing up existing runtime:', backup, flush=True)
        python(f'''
from pathlib import Path
import hashlib, json, shutil, subprocess, sys
project = Path('/opt/galaxy-ai')
backup = Path({backup!r})
for relative, expected in {baseline!r}.items():
    assert hashlib.sha256((project / relative).read_bytes()).hexdigest() == expected, 'Production changed: ' + relative
venv = project / 'backend/.venv'
assert venv.is_dir() and not venv.is_symlink()
required = sum(path.stat().st_size for path in venv.rglob('*') if path.is_file())
assert shutil.disk_usage('/var/backups').free > required * 3 + 200000000, 'Insufficient backup space'
backup.mkdir(parents=True, mode=0o700)
(backup / 'incoming/backend/xingxu').mkdir(parents=True)
manifest = {{'files': {{}}}}
for relative in {RUNTIME_FILES!r}:
    source = project / relative
    existed = source.exists()
    manifest['files'][relative] = {{'existed': existed}}
    if existed:
        assert source.is_file() and not source.is_symlink()
        target = backup / 'original' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest['files'][relative]['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
shutil.copytree(venv, backup / 'venv', symlinks=True)
check = subprocess.run([sys.executable, '-m', 'pip', 'check'], capture_output=True, text=True)
manifest['dependency_check_before'] = {{'status': check.returncode, 'output': check.stdout + check.stderr}}
(backup / 'manifest.json').write_text(json.dumps(manifest, indent=2))
print('Backup complete; private data directories were not copied or replaced.')
''')
        for relative in RUNTIME_FILES:
            upload(relative, backup + '/incoming/' + relative)
        try:
            run('umask 022 && /opt/galaxy-ai/backend/.venv/bin/python -m pip install -q -r ' + backup + '/incoming/backend/requirements.txt')
            python(f'''
from pathlib import Path
import hashlib, json, os, subprocess, sys, tempfile
project, backup = Path('/opt/galaxy-ai'), Path({backup!r})
manifest = json.loads((backup / 'manifest.json').read_text())
check = subprocess.run([sys.executable, '-m', 'pip', 'check'], capture_output=True, text=True)
if check.returncode:
    before = set(manifest['dependency_check_before']['output'].splitlines())
    new_errors = set((check.stdout + check.stderr).splitlines()) - before
    assert not new_errors, 'New dependency conflicts: ' + repr(new_errors)
for relative, expected in {baseline!r}.items():
    assert hashlib.sha256((project / relative).read_bytes()).hexdigest() == expected, 'Production changed before activation'
for relative in {RUNTIME_FILES!r}:
    assert hashlib.sha256((backup / 'incoming' / relative).read_bytes()).hexdigest() == {expected!r}[relative]
# Install the importing module last; the running service is not hot-reloaded.
ordered = [path for path in {RUNTIME_FILES!r} if path != 'backend/xingxu/ai.py'] + ['backend/xingxu/ai.py']
for relative in ordered:
    source, target = backup / 'incoming' / relative, project / relative
    old = target.stat() if target.exists() else None
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.langgraph-', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(source.read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o644)
    if old:
        os.chown(temporary, old.st_uid, old.st_gid)
    os.replace(temporary, target)
print('Activated validated source files:', len(ordered))
''')
            run('cd /opt/galaxy-ai/backend && runuser -u galaxy-ai -- env PYTHONDONTWRITEBYTECODE=1 /opt/galaxy-ai/backend/.venv/bin/python -c ' + shlex.quote(
                "from xingxu.ai import AiService; from importlib.metadata import version; print('LangGraph import:', version('langgraph'))"))
            run('systemctl restart galaxy-ai.service')
            health_check()
        except BaseException:
            rollback(backup)
            raise
        state['deployed_sha256'] = expected
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Deployment complete with rollback backup:', backup, flush=True)
    elif sys.argv[1] == 'cleanup':
        python(f'''
from pathlib import Path
import shutil
target = Path({validation!r})
assert target.parent == Path('/var/tmp') and target.name.startswith('galaxy-ai-chat-progress-20261116-')
assert not target.is_symlink() and target.resolve() == target
shutil.rmtree(target)
print('Removed the task-created isolated validation copy; rollback backup retained.')
''')
        state['validation_removed'] = True
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    elif sys.argv[1] == 'context-smoke':
        script = '''
import asyncio, json, time
from pathlib import Path
from xingxu.ai import AiService
from xingxu.agent_runtime import ChatGraph, ContextPolicy
from xingxu.conversation_context import ConversationContext
async def main():
    service = AiService(Path('/opt/galaxy-ai/backend/private'))
    provider, _ = service.snapshot()
    policy = ContextPolicy(input_tokens=8192, recent_turns=1)
    service.context_policy, service.agent = policy, ChatGraph(policy)
    history = []
    for index in range(16):
        history.extend([
            {'id': 'synthetic-user-' + str(index), 'role': 'user', 'created_at': 1794787200,
             'content': '合成验证记录，时区使用北京时间。记录序号 ' + str(index) + ' ' + 'x' * 700},
            {'id': 'synthetic-assistant-' + str(index), 'role': 'assistant', 'created_at': 1794787201,
             'content': '合成记录已确认，没有执行业务操作。' + 'y' * 700},
        ])
    memory = ConversationContext(history, None, policy, deepseek=True)
    start = time.monotonic()
    content, usage, _, events, _ = await service.complete(provider,
        [{'role': 'system', 'content': '这是合成数据的上下文功能验证。不执行业务操作。'},
         {'role': 'user', 'content': '请只回复：校验通过'}], memory=memory, thinking=False)
    summary = memory.snapshot()
    ok = bool(summary and summary['covered_messages'] > 0 and '校验通过' in content and not events)
    print(json.dumps({'live_context_test': {'ok': ok, 'model': provider['model'],
        'covered_messages': summary['covered_messages'] if summary else 0,
        'latency_ms': round((time.monotonic() - start) * 1000), 'usage': usage,
        'reply': content[:60], 'saved_chat': False}}, ensure_ascii=False))
    if not ok:
        raise SystemExit('Live context validation did not pass')
asyncio.run(main())
'''
        output = run('cd /opt/galaxy-ai/backend && runuser -u galaxy-ai -- env PYTHONDONTWRITEBYTECODE=1 /opt/galaxy-ai/backend/.venv/bin/python -c ' + shlex.quote(script))
        state['live_context_test'] = json.loads(output)
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        health_check()
    elif sys.argv[1] == 'smoke':
        script = '''
import asyncio, json
from pathlib import Path
from xingxu.ai import AiService
from xingxu.auth import AuthFailure
async def main():
    service = AiService(Path('/opt/galaxy-ai/backend/private'))
    try:
        provider, _ = service.snapshot()
        result = await service.test(provider['id'])
        print(json.dumps({'live_model_test': result}, ensure_ascii=False))
    except AuthFailure as error:
        print(json.dumps({'live_model_test': {'ok': False, 'code': error.code, 'message': error.message}}, ensure_ascii=False))
asyncio.run(main())
'''
        output = run('cd /opt/galaxy-ai/backend && runuser -u galaxy-ai -- env PYTHONDONTWRITEBYTECODE=1 /opt/galaxy-ai/backend/.venv/bin/python -c ' + shlex.quote(script))
        state['live_model_test'] = json.loads(output)
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        health_check()
    else:
        raise SystemExit('Unknown operation')
finally:
    client.close()
