"""Server-owned account directories and durable, file-backed personal repositories."""
import copy
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote


MODE = 'files-v1'
PERSONAL = {'chats', 'youzan', 'feishu', 'guanjia', 'files', 'uploads', 'tool_audit'}
LOCATIONS = {
    'youzan': 'channels/youzan/connection.json',
    'feishu': 'channels/feishu/connection.json',
    'guanjia': 'channels/guanjia/connection.json',
    'files': 'files/metadata.json',
    'uploads': 'uploads/requests.json',
    'tool_audit': 'logs/tool-audit.json',
}


def enabled(directory):
    return os.environ.get('GALAXY_ACCOUNT_STORAGE') == MODE


def owner_key(user):
    return hashlib.sha256(json.dumps([user['workspace_id'], user['id']]).encode()).hexdigest()


def runtime_owner(user):
    return hashlib.sha256(json.dumps([user['workspace_id'], user['id']], separators=(',', ':')).encode()).hexdigest()


def scoped(repository, user, *, write=True, identifier=None):
    if isinstance(repository, AccountRepository):
        return repository.transaction(write=write, user=user, identifier=identifier)
    return repository.transaction(write=write)


def folder(path):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError('Linked account directory')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise RuntimeError('Invalid account directory')
    path.chmod(0o700)
    return path


def child(root, relative):
    path = Path(root)
    for part in Path(relative).parts:
        if part in ('', '.', '..') or '/' in part or '\\' in part or '\x00' in part:
            raise ValueError('Invalid account path')
        path = folder(path / part)
    return path


def read_bytes(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError('Invalid account file')
        return stream.read()


def read_json(path, default=None):
    try:
        return json.loads(read_bytes(path))
    except FileNotFoundError:
        return copy.deepcopy(default)


def atomic_json(path, value):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError('Linked account file')
    folder(path.parent)
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode() + b'\n'
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(path, write=True):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a+') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError('Invalid storage lock')
        fcntl.flock(stream, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class Accounts:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.root = folder(self.directory / 'accounts')

    def ensure(self, user):
        if not all(isinstance(user.get(k), str) and user[k] for k in ('id', 'workspace_id')):
            raise ValueError('Account identity required')
        key = owner_key(user)
        if not isinstance(user.get('username'), str) or not user['username']:
            registered = read_json(self.root / '.owners.json', {}).get(key)
            raw = registered or self.users().get(user['id'])
            if not raw or raw.get('workspace_id') != user['workspace_id']:
                raise ValueError('Unknown account identity')
            user = {**user, 'username': raw['username']}
        with locked(self.root / '.registry.lock'):
            registry = read_json(self.root / '.owners.json', {})
            old = registry.get(key)
            if old:
                if (old['user_id'], old['workspace_id']) != (user['id'], user['workspace_id']):
                    raise RuntimeError('Account identity mismatch')
                name = old['directory']
            else:
                name = quote(user['username'], safe='-_')
                if name.startswith('.') or len(name.encode()) > 180 or not name:
                    name = 'account-' + key[:20]
                if any(value['directory'] == name for value in registry.values()):
                    name += '-' + key[:12]
            if Path(name).name != name or name.startswith('.') or '\\' in name:
                raise RuntimeError('Invalid registered account directory')
            path = folder(self.root / name)
            identity = {'user_id': user['id'], 'workspace_id': user['workspace_id'],
                        'username': user['username'], 'directory': name, 'runtime_owner': runtime_owner(user)}
            if old != identity:
                atomic_json(path / 'account.json', identity)
                registry[key] = identity
                atomic_json(self.root / '.owners.json', registry)
            return path

    def users(self):
        from .store import JsonRepository
        with JsonRepository(self.directory).transaction(write=False) as state:
            return copy.deepcopy(state['users'])

    def identities(self):
        return {owner_key(user): (user, self.ensure(user)) for user in self.users().values()}

    def path(self, user, relative=''):
        return child(self.ensure(user), relative) if relative else self.ensure(user)

    def browser(self, user):
        return self.path(user, 'browser')


class AccountRepository:
    """Keep existing internal transaction contracts; personal payloads never enter SQL.

    The module lock protects read/modify/write callers across processes. Chat logs
    append deltas with fsync, instead of repeatedly rewriting an entire conversation.
    """
    def __init__(self, directory, name, fields):
        expected = {'chats': ('conversations',), 'youzan': ('connections',),
                    'guanjia': ('connections',), 'feishu': ('apps', 'connections'),
                    'files': ('files',), 'uploads': ('requests',), 'tool_audit': ('records',)}
        if name not in expected or set(fields) != set(expected[name]):
            raise ValueError('Unknown account repository')
        self.directory, self.name, self.fields = Path(directory), name, fields
        self.accounts = Accounts(directory)

    @contextmanager
    def transaction(self, *, write=True, user=None, identifier=None):
        identities = ({owner_key(user): (user, self.accounts.ensure(user))}
                      if user is not None else self.accounts.identities())
        with locked(self.accounts.root / ('.' + self.name + '.lock'), write):
            if self.name == 'feishu':
                if os.environ.get('GALAXY_DATABASE_CONFIG'):
                    from .mysql_store import MySQLRepository
                    shared = MySQLRepository(self.directory, 'feishu', ('apps',))
                else:
                    from .store import JsonRepository
                    shared = JsonRepository(self.directory, 'feishu', ('apps',))
                with shared.transaction(write=write) as common:
                    state = self._load(identities, identifier)
                    state['apps'] = common['apps']
                    before = copy.deepcopy(state)
                    yield state
                    if write:
                        self._save(state, before, identities)
                        common['apps'] = state['apps']
            else:
                state = self._load(identities, identifier)
                before = copy.deepcopy(state) if write else None
                yield state
                if write:
                    self._save(state, before, identities)

    @property
    def field(self):
        return next(key for key in self.fields if key != 'apps')

    def _load(self, identities, identifier=None):
        records = {}
        if self.name == 'tool_audit':
            system = read_json(self.accounts.root / '.system-audit.json', {'records': {}})
            records.update(system['records'])
        for owner, (user, base) in identities.items():
            if self.name == 'chats':
                directory = base / 'chats'
                if directory.is_symlink():
                    raise RuntimeError('Linked conversation directory')
                if identifier is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(identifier)):
                    raise ValueError('Invalid conversation identifier')
                paths = [directory / (str(identifier) + '.jsonl')] if identifier is not None else directory.glob('*.jsonl')
                for path in paths:
                    value = self.read_chat(path)
                    if value is not None:
                        self._check_owner(value, user)
                        if path.stem != value['id'] or value['id'] in records:
                            raise RuntimeError('Invalid conversation identity')
                        records[value['id']] = value
            else:
                relative = Path(LOCATIONS[self.name])
                parent = child(base, relative.parent)
                saved = read_json(parent / relative.name, {'schema_version': 1, self.field: {}})
                if saved.get('schema_version') != 1 or not isinstance(saved.get(self.field), dict):
                    raise RuntimeError('Invalid account repository')
                for key, value in saved[self.field].items():
                    if self.field == 'connections':
                        if key != owner:
                            raise RuntimeError('Channel identity mismatch')
                    else:
                        self._check_owner(value, user)
                    if key in records:
                        raise RuntimeError('Duplicate account record')
                    records[key] = value
        return {'schema_version': 1, self.field: records}

    @staticmethod
    def _check_owner(value, user):
        if (value.get('user_id'), value.get('workspace_id')) != (user['id'], user['workspace_id']):
            raise RuntimeError('Record owner mismatch')

    def _save(self, state, before, identities):
        records = state.get(self.field)
        if state.get('schema_version') != 1 or not isinstance(records, dict):
            raise ValueError('Invalid account state')
        grouped = {owner: {} for owner in identities}
        system_records = {}
        for key, value in records.items():
            if self.name == 'chats' and value.get('id') != key:
                raise ValueError('Conversation identifier mismatch')
            if self.field == 'connections':
                owner = key
            else:
                owner = owner_key({'id': value.get('user_id'), 'workspace_id': value.get('workspace_id')})
            # Audit history can refer to deleted users or unauthenticated calls.
            # Keep these records as system audit; never assign them to another user.
            if self.name == 'tool_audit' and owner not in identities:
                system_records[key] = value
                continue
            if owner not in identities:
                raise RuntimeError('Unknown account owner')
            grouped[owner][key] = value
            previous = before[self.field].get(key)
            if previous and self.field != 'connections':
                if (previous.get('user_id'), previous.get('workspace_id')) != (value.get('user_id'), value.get('workspace_id')):
                    raise RuntimeError('Record ownership cannot change')
        if self.name == 'tool_audit':
            atomic_json(self.accounts.root / '.system-audit.json', {'schema_version': 1, 'records': system_records})
        for owner, values in grouped.items():
            user, base = identities[owner]
            if self.name == 'chats':
                directory = child(base, 'chats')
                for key, value in values.items():
                    previous = before[self.field].get(key)
                    if previous != value:
                        self.append_chat(directory, key, previous, value)
                for key, old in before[self.field].items():
                    if (old.get('user_id'), old.get('workspace_id')) == (user['id'], user['workspace_id']) and key not in records:
                        self.append_chat(directory, key, old, None)
            else:
                relative = Path(LOCATIONS[self.name])
                parent = child(base, relative.parent)
                target = parent / relative.name
                payload = {'schema_version': 1, self.field: values}
                if read_json(target) != payload:
                    atomic_json(target, payload)

    @staticmethod
    def read_chat(path):
        value = None
        try:
            data = read_bytes(path)
        except FileNotFoundError:
            return None
        # An interrupted append is uncommitted; only a complete newline ends an event.
        for line in data.splitlines(keepends=True):
            if not line.endswith(b'\n'):
                break
            event = json.loads(line)
            if event.get('schema_version') != 1:
                raise RuntimeError('Unknown conversation event format')
            if event['event'] == 'delete':
                value = None
            elif event['event'] == 'snapshot':
                value = event['record']
            elif event['event'] == 'update' and value is not None:
                value.update(event.get('set', {}))
                for key in event.get('remove', []):
                    value.pop(key, None)
                if 'append_messages' in event:
                    value.setdefault('messages', []).extend(event['append_messages'])
            else:
                raise RuntimeError('Invalid conversation event sequence')
        return value

    @staticmethod
    def append_chat(directory, key, previous, value):
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key):
            raise ValueError('Invalid conversation identifier')
        event = {'schema_version': 1}
        if value is None:
            event['event'] = 'delete'
        elif previous is None:
            event.update(event='snapshot', record=value)
        else:
            patch = {k: v for k, v in value.items() if previous.get(k) != v or k not in previous}
            event.update(event='update', set=patch, remove=list(previous.keys() - value.keys()))
            old_messages, messages = previous.get('messages', []), value.get('messages', [])
            if 'messages' in patch and messages[:len(old_messages)] == old_messages:
                event['append_messages'] = messages[len(old_messages):]
                patch.pop('messages')
        encoded = json.dumps(event, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode() + b'\n'
        target = Path(directory) / (key + '.jsonl')
        if value is None:
            target.unlink(missing_ok=True)
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return
        fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+b') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeError('Invalid conversation file')
            stream.seek(0, os.SEEK_END)
            committed = stream.tell()
            if committed:
                stream.seek(committed - 1)
                if stream.read(1) != b'\n':
                    cursor = committed
                    committed = 0
                    while cursor:
                        start = max(0, cursor - 65536)
                        stream.seek(start)
                        chunk = stream.read(cursor - start)
                        if b'\n' in chunk:
                            committed = start + chunk.rfind(b'\n') + 1
                            break
                        cursor = start
            stream.seek(committed)
            stream.truncate()
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
