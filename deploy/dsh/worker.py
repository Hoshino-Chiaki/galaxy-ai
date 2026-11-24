"""Fixed jail launcher. The trusted supervisor never executes model shell on the host."""
import asyncio
import base64
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import time

RUNTIME = Path('/opt/galaxy-ai/dsh-runtime')
ROOT = Path(os.environ.get('GALAXY_DSH_STORAGE', '/opt/galaxy-ai/hosted-files/.dsh'))
JOBS = Path('/run/galaxy-dsh/jobs')
SOCKET = '/run/galaxy-dsh/runner.sock'
BROKERS = Path('/opt/galaxy-ai/hosted-files/.dsh-brokers')
ACCOUNTS = Path(os.environ.get('GALAXY_ACCOUNTS_ROOT', '/opt/galaxy-ai/backend/private/accounts'))
OWNER = re.compile(r'[0-9a-f]{64}')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
LIMIT = 64 * 1024 * 1024
active = {}


def scope_root(owner):
    if os.environ.get('GALAXY_ACCOUNT_STORAGE') != 'files-v1':
        return ROOT / owner
    registry = json.loads(regular(ACCOUNTS, '.owners.json', 1024 * 1024))
    record = next((row for row in registry.values() if row.get('runtime_owner') == owner), None)
    if record is None:
        raise ValueError('Account scope unavailable')
    name = record['directory']
    if not isinstance(name, str) or not name or name.startswith('.') or '/' in name or '\\' in name:
        raise ValueError('Invalid account scope')
    root = ACCOUNTS / name / 'tasks' / 'dsh'
    for path in (ACCOUNTS, ACCOUNTS / name, ACCOUNTS / name / 'tasks', root):
        if path.is_symlink():
            raise ValueError('Linked account scope')
    return root


def identifiers(body):
    owner, conversation = body['owner'], body['conversation']
    if not isinstance(owner, str) or not OWNER.fullmatch(owner) or not isinstance(conversation, str) or not UUID.fullmatch(conversation):
        raise ValueError('Invalid scope')
    return owner, conversation


def regular(root, relative, maximum=20 * 1024 * 1024):
    parts = relative.split('/')
    if len(parts) > 20 or any(not p or p in ('.', '..') or '\\' in p or '\x00' in p for p in parts):
        raise ValueError('Invalid relative path')
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            info = os.fstat(leaf)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 <= info.st_size <= maximum:
                raise ValueError('Invalid file')
            data = bytearray()
            while chunk := os.read(leaf, 256 * 1024):
                data.extend(chunk)
                if len(data) > maximum:
                    raise ValueError('File too large')
            if os.fstat(leaf).st_mtime_ns != info.st_mtime_ns:
                raise ValueError('File changed')
            return bytes(data)
        finally:
            os.close(leaf)
    finally:
        os.close(descriptor)


def copy_regular(source, target, maximum=LIMIT):
    """No links/special files cross the jail boundary; all writes have fixed roots."""
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    if source.is_symlink():
        raise ValueError('Linked checkpoint root')
    if not source.exists():
        return
    total, count = 0, 0
    for directory, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = [d for d in dirs if not Path(directory, d).is_symlink()]
        relative = Path(directory).relative_to(source)
        if len(relative.parts) > 15:
            dirs.clear()
            continue
        for name in files:
            path = (relative / name).as_posix()
            info = Path(directory, name).lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                continue
            # A normal session file must either be saved or fail this turn.
            # Silently dropping an oversized/unreadable log loses memory.
            data = regular(source, path, maximum=min(maximum, 32 * 1024 * 1024))
            total += len(data)
            count += 1
            if total > maximum or count > 2048:
                raise ValueError('Checkpoint quota exceeded')
            destination = target / relative / name
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(0o600)


def tree_size(root):
    return sum(p.stat(follow_symlinks=False).st_size for p in root.rglob('*') if p.is_file() and not p.is_symlink())


def account_library(owner):
    if os.environ.get('GALAXY_ACCOUNT_STORAGE') != 'files-v1':
        return None
    account = scope_root(owner).parent.parent
    library = account / 'workspace' / 'library'
    for path in (account / 'workspace', library):
        if path.is_symlink() or not path.is_dir():
            raise ValueError('Invalid account file library')
    return library


def account_cache(owner):
    if os.environ.get('GALAXY_ACCOUNT_STORAGE') != 'files-v1':
        raise ValueError('Account cache requires account storage')
    cache = scope_root(owner).parent.parent / 'cache'
    # Verify every component with O_NOFOLLOW before exposing this account's cache.
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in cache.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)
    return cache


def jail_arguments(stage, broker, token, library=None, cache=None):
    args = ['/usr/bin/bwrap', '--unshare-all', '--unshare-user', '--die-with-parent', '--new-session',
            '--disable-userns', '--cap-drop', 'ALL', '--uid', '0', '--gid', '0',
            '--ro-bind', '/usr', '/usr', '--tmpfs', '/usr/local',
            '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/sbin', '/sbin',
            '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--proc', '/proc', '--dev', '/dev', '--dir', '/etc',
            '--ro-bind', '/etc/ssl/certs', '/etc/ssl/certs',
            '--ro-bind', str(RUNTIME), str(RUNTIME), '--symlink', str(RUNTIME), '/runtime',
            '--bind', str(stage / 'workspace'), '/workspace', '--bind', str(stage / 'home'), '/state',
            '--size', str(16 * 1024 * 1024), '--tmpfs', '/tmp', '--dir', '/run',
            '--ro-bind', str(broker), '/run/broker.sock', '--chdir', '/workspace', '--clearenv']
    if library is not None:
        args += ['--ro-bind', str(library), '/account-files']
    if cache is not None:
        args += ['--ro-bind', str(cache), '/account-cache']
    environment = {'PATH': '/runtime/bin:/runtime/venv/bin:/usr/bin:/bin', 'HOME': '/state',
        'LANG': 'C.UTF-8', 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'GALAXY_RUN_TOKEN': token, 'NODE_OPTIONS': '--max-old-space-size=256'}
    if os.environ.get('GALAXY_DSH_DEBUG') == '1':
        environment['GALAXY_DSH_DEBUG'] = '1'
    for key, value in environment.items():
        args += ['--setenv', key, value]
    return args + ['/runtime/venv/bin/python', '-I', '/runtime/inner.py']


async def send(writer, value):
    writer.write(json.dumps(value, ensure_ascii=False).encode() + b'\n')
    await writer.drain()


async def run_job(body, reader, writer):
    owner, conversation = identifiers(body)
    identifier = body['run_id']
    if not isinstance(identifier, str) or not UUID.fullmatch(identifier) or not isinstance(body['token'], str) or len(body['token']) != 64:
        raise ValueError('Invalid run')
    if active:
        await send(writer, {'type': 'error', 'code': 'dsh_busy'})
        return
    stage = JOBS / identifier
    active[identifier] = stage
    process = None
    monitor = None
    collector = errors = None
    target = None
    try:
        storage = scope_root(owner)
        target = storage / conversation / identifier
        stage.mkdir(mode=0o700)
        (stage / 'workspace').mkdir(mode=0o700)
        (stage / 'home').mkdir(mode=0o700)
        previous = body.get('previous')
        if previous:
            if not isinstance(previous, str) or not UUID.fullmatch(previous):
                raise ValueError('Invalid checkpoint')
            source = storage / conversation / previous
            if not (source / 'complete').is_file():
                raise ValueError('Checkpoint missing')
            copy_regular(source / 'workspace', stage / 'workspace', 32 * 1024 * 1024)
            copy_regular(source / 'sessions', stage / 'home/sessions')
        broker = BROKERS / identifier / 'broker.sock'
        if not broker.is_socket():
            raise ValueError('Broker missing')
        await send(writer, {'type': 'ready'})
        library = account_library(owner) if body.get('account_files') is True else None
        cache = account_cache(owner) if body.get('account_cache') is True else None
        process = await asyncio.create_subprocess_exec(*jail_arguments(stage, broker, body['token'], library, cache),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True, limit=1024 * 1024)
        process.stdin.write(json.dumps(body['input'], ensure_ascii=False).encode())
        await process.stdin.drain()
        process.stdin.close()
        monitor = asyncio.create_task(reader.read(1))

        async def drain_error():
            chunks = bytearray()
            while part := await process.stderr.read(4096):
                if len(chunks) < 8000:
                    chunks.extend(part[:8000 - len(chunks)])
            return chunks.decode(errors='replace')

        errors = asyncio.create_task(drain_error())
        result = None

        async def collect():
            nonlocal result
            total = 0
            while line := await process.stdout.readline():
                total += len(line)
                if total > 2 * 1024 * 1024:
                    raise ValueError('Output quota exceeded')
                value = json.loads(line)
                if value.get('type') == 'phase' and value.get('phase') in ('tool', 'summarizing'):
                    await send(writer, value)
                elif value.get('type') == 'result':
                    if not isinstance(value.get('content'), str) or len(value['content'].encode()) > 512 * 1024:
                        raise ValueError('Invalid final response')
                    result = value
                elif value.get('type') == 'error':
                    result = value
            await process.wait()

        collector = asyncio.create_task(collect())
        done, _ = await asyncio.wait([collector, monitor], timeout=900, return_when=asyncio.FIRST_COMPLETED)
        if monitor in done or collector not in done:
            # Kill before waiting for readers: the jail may still be producing
            # output or waiting on a broker request that is being cancelled.
            return
        await collector
        diagnostic = await errors
        if process.returncode or not result:
            await send(writer, {'type': 'error', 'code': 'dsh_process_failed',
                **({'diagnostic': diagnostic} if os.environ.get('GALAXY_DSH_DEBUG') == '1' else {})})
            return
        if result['type'] == 'result':
            target.mkdir(mode=0o700, parents=True)
            copy_regular(stage / 'home/sessions', target / 'sessions')
            copy_regular(stage / 'workspace', target / 'workspace', 32 * 1024 * 1024)
            # Keep the authoritative prior checkpoint until the next turn points
            # at this one. An abandoned result can never replace saved history.
            for old in target.parent.iterdir():
                if old.is_dir() and old.name not in (identifier, previous) and UUID.fullmatch(old.name):
                    shutil.rmtree(old)
            if tree_size(storage) > 128 * 1024 * 1024:
                raise ValueError('Account storage quota exceeded')
            (target / 'complete').write_text('1')
            result['run_id'] = identifier
        await send(writer, result)
    finally:
        try:
            if process:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            tasks = [task for task in (monitor, collector, errors) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if process:
                # wait() alone can hang after cancellation when a PIPE transport
                # has paused reading. Drain both pipes while reaping the jail.
                await process.communicate()
        finally:
            try:
                if stage.exists():
                    shutil.rmtree(stage)
                if target and target.exists() and not (target / 'complete').is_file():
                    shutil.rmtree(target)
            finally:
                active.pop(identifier, None)


async def client(reader, writer):
    try:
        body = json.loads(await asyncio.wait_for(reader.readline(), 20))
        command = body.get('command')
        if command == 'run':
            await run_job(body, reader, writer)
        elif command == 'read':
            stage = active.get(body['run_id'])
            if stage is None:
                raise ValueError('Run missing')
            data = regular(stage / 'workspace', body['path'])
            await send(writer, {'data': base64.b64encode(data).decode()})
        elif command == 'delete':
            owner, conversation = identifiers(body)
            if active:
                raise ValueError('Worker busy')
            target = scope_root(owner) / conversation
            if target.exists():
                shutil.rmtree(target)
            await send(writer, {'ok': True})
        elif command == 'status':
            await send(writer, {'ok': True, 'active': len(active), 'sdk': '0.2.2rc2'})
        else:
            raise ValueError('Invalid command')
    except (ValueError, KeyError, OSError, asyncio.TimeoutError) as error:
        with contextlib.suppress(OSError):
            await send(writer, {'type': 'error', 'code': 'dsh_worker_rejected',
                **({'diagnostic': str(error)[:1000]} if os.environ.get('GALAXY_DSH_DEBUG') == '1' else {})})
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()


async def main():
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    JOBS.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(SOCKET)
    server = await asyncio.start_unix_server(client, SOCKET, limit=8 * 1024 * 1024)
    os.chmod(SOCKET, 0o600)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())
