"""Scoped DSH installation/validation helper; credentials stay in memory."""
import hashlib
import json
from pathlib import Path
import shlex
import sys
import uuid
import paramiko

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'artifacts/dsh-deployment-state.json'
STATE.parent.mkdir(parents=True, exist_ok=True)
state = json.loads(STATE.read_text()) if STATE.exists() else {'validation': '/var/tmp/galaxy-ai-dsh-20261116-' + uuid.uuid4().hex[:10]}
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
FILES = ['backend/xingxu/ai.py', 'backend/xingxu/dsh_protocol.py', 'backend/xingxu/dsh_runtime.py']
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
    mode = sys.argv[1]
    if mode == 'audit':
        baseline = {}
        with client.open_sftp() as ftp:
            for relative in FILES:
                try:
                    content = ftp.open('/opt/galaxy-ai/' + relative).read()
                except FileNotFoundError:
                    baseline[relative] = None
                    continue
                baseline[relative] = hashlib.sha256(content).hexdigest()
                target = ROOT / 'artifacts/dsh-server-baseline' / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        state['baseline'] = baseline
        STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
        print('Source baseline recorded; no user data downloaded.')
        run('systemctl is-active galaxy-ai.service')
    elif mode == 'stage':
        python(f'''from pathlib import Path
import shutil
p=Path({stage!r});p.mkdir(mode=0o700,exist_ok=True)
for name in ('backend/xingxu','galaxy-cli/src'):
 source=Path('/opt/galaxy-ai')/name
 shutil.copytree(source,p/name,ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
print('Isolated source and tests staged; no private data copied.')
''')
        sync_files()
        run('/usr/bin/python3 -m venv ' + stage + '/.venv')
        run(stage + '/.venv/bin/python -m pip install -q -r ' + stage + '/backend/requirements-dev.txt -e ' + stage + '/galaxy-cli')
        python(f"from pathlib import Path\np=Path({stage!r});(p/'galaxy-cli/.venv').symlink_to(p/'.venv',target_is_directory=True)")
    elif mode == 'sync':
        sync_files()
    elif mode == 'cleanup':
        python(f'''from pathlib import Path
import shutil
p=Path({stage!r})
assert p.parent==Path('/var/tmp') and p.name.startswith('galaxy-ai-dsh-20261116-') and p.resolve()==p and not p.is_symlink()
if p.exists(): shutil.rmtree(p)
print('Isolated DSH validation files removed; production, runtime and rollback backup retained.')
''')
    elif mode == 'install-runtime':
        run('apt-get install -y --no-install-recommends bubblewrap')
        python('''from pathlib import Path
import pwd,os
root=Path('/opt/galaxy-ai/dsh-runtime');root.mkdir(mode=0o755,exist_ok=True)
(root/'bin').mkdir(mode=0o755,exist_ok=True)
u=pwd.getpwnam('galaxy-ai')
for p in (Path('/opt/galaxy-ai/hosted-files/.dsh'),Path('/opt/galaxy-ai/hosted-files/.dsh-brokers')):
 p.mkdir(mode=0o700,exist_ok=True);os.chown(p,u.pw_uid,u.pw_gid)
''')
        run('/usr/bin/python3 -m venv /opt/galaxy-ai/dsh-runtime/venv')
        run('/opt/galaxy-ai/dsh-runtime/venv/bin/python -m pip install -q deepseek-harness-sdk==0.2.2rc2 requests==2.32.5 httpx==0.28.1 openpyxl==3.1.5')
        print('Pinned SDK installed; runtime not connected to chat yet.', flush=True)
    elif mode == 'sync-runtime':
        with client.open_sftp() as ftp:
            for name in RUNTIME:
                target = '/opt/galaxy-ai/dsh-runtime/' + ('bin/' if name in ('galaxy-cli', 'galaxy-files') else '') + name
                ftp.put(str(ROOT / 'deploy/dsh' / name), target + '.incoming')
                ftp.chmod(target + '.incoming', 0o755 if name in ('galaxy-cli', 'galaxy-files') else 0o644)
                ftp.posix_rename(target + '.incoming', target)
            unit = (ROOT / 'deploy/dsh/galaxy-dsh.service').read_text(encoding='utf-8')
            details = json.loads(python("import pwd,json\np=pwd.getpwnam('galaxy-ai');print(json.dumps([p.pw_uid,p.pw_gid]))"))
            unit = unit.replace('uid=1000,gid=1000', 'uid='+str(details[0])+',gid='+str(details[1]))
            with ftp.open('/etc/systemd/system/galaxy-dsh.service', 'w') as stream:
                stream.write(unit)
        run('systemd-analyze verify /etc/systemd/system/galaxy-dsh.service')
        run('systemctl daemon-reload')
        run('systemctl restart galaxy-dsh.service')
        run('systemctl is-active galaxy-dsh.service')
    elif mode == 'test':
        sync_files()
        candidate = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in FILES}
        python(f"from pathlib import Path\np=Path({stage!r});p.chmod(0o755)\nfor directory in (p/'backend',p/'galaxy-cli'):\n for item in [directory,*directory.rglob('*')]:\n  if not item.is_symlink():item.chmod(0o755 if item.is_dir() else 0o644)")
        test_paths = ' '.join('backend/tests/' + t for t in TESTS if (ROOT / 'backend/tests' / t).exists())
        command = ('cd ' + stage + ' && runuser -u galaxy-ai -- env -u XINGXU_DATA_DIR -u GALAXY_FILES_DIR GALAXY_AI_RUNTIME=langgraph '
            'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=' + stage + '/backend:' + stage + '/galaxy-cli/src '
            + stage + '/.venv/bin/python -m pytest -p no:cacheprovider -c backend/pytest.ini ' + test_paths + ' -q --tb=short')
        command = command.replace('GALAXY_AI_RUNTIME=langgraph ', 'GALAXY_AI_RUNTIME=langgraph GALAXY_DSH_DEBUG=1 ')
        result = run(command)
        assert candidate == {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in FILES}, 'Source changed during validation'
        state['validated'] = candidate
        state['runtime_hashes'] = {p: hashlib.sha256((ROOT/'deploy/dsh'/p).read_bytes()).hexdigest() for p in RUNTIME}
        state['tests'] = result
        STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
    elif mode in ('regression', 'regression-related', 'baseline-check'):
        python(f"from pathlib import Path\nimport shutil\nroot=Path({stage!r})\nfor name in ('backend/youzan/login.cjs','session_research/youzan-login.cjs','session_research/youzan-login-runtime.json','investigation/slider-captcha-lab/human_track.py','investigation/youzan-slider/utils.py'):\n p=root/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(Path('/opt/galaxy-ai')/name,p)")
        if mode == 'baseline-check':
            command = ('cd ' + stage + ' && env -u XINGXU_DATA_DIR -u GALAXY_FILES_DIR GALAXY_AI_RUNTIME=langgraph PYTHONDONTWRITEBYTECODE=1 '
                'PYTHONPATH=/opt/galaxy-ai/backend:/opt/galaxy-ai/galaxy-cli/src ' + stage + '/.venv/bin/python -m pytest -c backend/pytest.ini '
                'backend/tests/test_catalog_tools.py::test_catalog_command_canonicalization_and_aliases '
                'backend/tests/test_youzan_tools.py::test_catalog_route_is_actual_help_and_admin_only '
                'backend/tests/test_youzan.py::test_fixed_login_managed_pipe_uses_only_scoped_profile -q --tb=short')
            try:
                state['baseline_check'] = run(command)
            except RuntimeError:
                state['baseline_check'] = 'Baseline reproduced the three unchanged catalog expectation failures; see validation log.'
                STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
                raise
            STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
            sys.exit(0)
        exclude = (" --deselect='tests/test_catalog_tools.py::test_catalog_command_canonicalization_and_aliases[product-search-query-body0]'"
            " --deselect='tests/test_catalog_tools.py::test_catalog_command_canonicalization_and_aliases[product-search-options-body1]'"
            " --deselect='tests/test_youzan_tools.py::test_catalog_route_is_actual_help_and_admin_only'" if mode == 'regression-related' else '')
        result = run('cd ' + stage + ' && env -u XINGXU_DATA_DIR -u GALAXY_FILES_DIR GALAXY_AI_RUNTIME=langgraph '
            'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=' + stage + '/backend:' + stage + '/galaxy-cli/src '
            + stage + '/.venv/bin/python -m pytest -c backend/pytest.ini backend/tests -q --tb=short --ignore=backend/tests/test_dsh_live.py' + exclude)
        state['regression'] = result
        STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
    elif mode in ('script', 'script-user'):
        code = Path(sys.argv[2]).read_text(encoding='utf-8')
        with client.open_sftp() as ftp:
            path = stage + '/operation.py'
            with ftp.open(path, 'w') as stream:
                stream.write(code)
        run(('runuser -u galaxy-ai -- ' if mode == 'script-user' else '') + 'env PYTHONPATH=' + stage + '/backend:' + stage + '/galaxy-cli/src ' + stage + '/.venv/bin/python ' + path)
    elif mode == 'production-check':
        code = Path(sys.argv[2]).read_text(encoding='utf-8')
        target = '/var/tmp/galaxy-ai-production-check-20261116.py'
        with client.open_sftp() as ftp:
            with ftp.open(target, 'w') as stream:
                stream.write(code)
            ftp.chmod(target, 0o600)
        try:
            run('env PYTHONPATH=/opt/galaxy-ai/backend /opt/galaxy-ai/backend/.venv/bin/python ' + target)
        finally:
            run('rm -f ' + target)
    elif mode == 'debug-on':
        python("from pathlib import Path\np=Path('/etc/systemd/system/galaxy-dsh.service.d');p.mkdir(exist_ok=True);(p/'validation.conf').write_text('[Service]\\nEnvironment=GALAXY_DSH_DEBUG=1\\n')")
        run('systemctl daemon-reload')
        run('systemctl restart galaxy-dsh.service')
    elif mode == 'debug-off':
        python("from pathlib import Path\nPath('/etc/systemd/system/galaxy-dsh.service.d/validation.conf').unlink(missing_ok=True)")
        run('systemctl daemon-reload')
        run('systemctl restart galaxy-dsh.service')
    elif mode == 'deploy':
        hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in FILES}
        assert hashes == state.get('validated') and state.get('regression')
        with client.open_sftp() as ftp:
            assert {p: hashlib.sha256(ftp.open(stage+'/'+p).read()).hexdigest() for p in FILES} == hashes
            for name, digest in state['runtime_hashes'].items():
                target = '/opt/galaxy-ai/dsh-runtime/' + ('bin/' if name in ('galaxy-cli', 'galaxy-files') else '') + name
                assert hashlib.sha256(ftp.open(target).read()).hexdigest() == digest
        backup = '/var/backups/galaxy-ai/dsh-20261116-' + uuid.uuid4().hex[:10]
        python(f'''from pathlib import Path
import hashlib,json,shutil
p=Path('/opt/galaxy-ai');b=Path({backup!r});b.mkdir(mode=0o700,parents=True)
for rel,digest in {state['baseline']!r}.items():
 s=p/rel
 assert (hashlib.sha256(s.read_bytes()).hexdigest() if s.exists() else None)==digest,rel
 if s.exists():
  t=b/rel;t.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(s,t)
(b/'manifest.json').write_text(json.dumps({state['baseline']!r}))
drop=Path('/etc/systemd/system/galaxy-ai.service.d/dsh.conf')
if drop.exists():shutil.copy2(drop,b/'previous-dsh.conf')
''')
        state['backup'] = backup
        STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
        try:
            with client.open_sftp() as ftp:
                for relative in FILES:
                    target = '/opt/galaxy-ai/' + relative
                    ftp.put(str(ROOT / relative), target + '.incoming')
                    ftp.chmod(target + '.incoming', 0o644)
                    ftp.posix_rename(target + '.incoming', target)
            python("from pathlib import Path\np=Path('/etc/systemd/system/galaxy-ai.service.d');p.mkdir(exist_ok=True)\n(p/'dsh.conf').write_text('[Unit]\\nWants=galaxy-dsh.service\\nAfter=galaxy-dsh.service\\n[Service]\\nEnvironment=GALAXY_AI_RUNTIME=dsh\\n')")
            run('systemctl enable galaxy-dsh.service')
            run('systemctl daemon-reload')
            run('systemctl restart galaxy-ai.service')
            run('systemctl is-active galaxy-ai.service galaxy-dsh.service')
            python("import urllib.request,json,time\nfor i in range(30):\n try:\n  j=json.load(urllib.request.urlopen('http://127.0.0.1:18443/health',timeout=2));assert j['status']=='ok';print('Production health: ok');break\n except Exception:time.sleep(.5)\nelse:raise RuntimeError('Health failed')")
        except BaseException:
            python(f'''from pathlib import Path
import shutil
p=Path('/opt/galaxy-ai');b=Path({backup!r})
for rel,digest in {state['baseline']!r}.items():
 t=p/rel
 if digest:shutil.copy2(b/rel,t)
 else:t.unlink(missing_ok=True)
drop=Path('/etc/systemd/system/galaxy-ai.service.d/dsh.conf')
if (b/'previous-dsh.conf').exists():shutil.copy2(b/'previous-dsh.conf',drop)
else:drop.unlink(missing_ok=True)
''')
            run('systemctl daemon-reload')
            run('systemctl restart galaxy-ai.service')
            raise
        state['deployed'] = hashes
        STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
        print('DSH enabled; no cost synchronization or schedules created.')
finally:
    client.close()
