"""Offline, verified migration. SQL is read-only; existing data stays available for rollback."""
import argparse
import copy
import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
import uuid
from pathlib import Path

from .account_storage import Accounts, AccountRepository, atomic_json, child, owner_key, runtime_owner, MODE

FIELDS = {'chats': ('conversations',), 'youzan': ('connections',),
          'feishu': ('apps', 'connections'), 'guanjia': ('connections',),
          'files': ('files',), 'uploads': ('requests',), 'tool_audit': ('records',)}


def legacy_repository(directory, name, fields):
    if os.environ.get('GALAXY_DATABASE_CONFIG'):
        from .mysql_store import MySQLRepository
        return MySQLRepository(directory, name, fields)
    from .store import JsonRepository
    repository = object.__new__(JsonRepository)
    repository.__init__(directory, name, fields)
    return repository


def snapshot(directory):
    result = {}
    for name, fields in {'auth': ('users', 'sessions', 'attempts'), **FIELDS}.items():
        with legacy_repository(directory, name, fields).transaction(write=False) as state:
            result[name] = copy.deepcopy(state)
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def copy_directory(source, target):
    source, target = Path(source), Path(target)
    if not source.exists():
        return 0
    if source.is_symlink() or target.is_symlink():
        raise RuntimeError('Linked migration directory')
    def ignored(directory, names):
        return [name for name in names if name.startswith('Singleton') or name == '.galaxy-profile.lock'
                or not (Path(directory) / name).is_file() and not (Path(directory) / name).is_dir()
                or (Path(directory) / name).is_symlink()]
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignored)
    count = 0
    for parent, dirs, files in os.walk(target):
        Path(parent).chmod(0o700)
        for name in files:
            path = Path(parent) / name
            if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                raise RuntimeError('Invalid migrated file')
            original = source / path.relative_to(target)
            if not original.is_file() or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(original.read_bytes()).digest():
                raise RuntimeError('Migration file checksum mismatch')
            path.chmod(0o600)
            count += 1
    return count


def migrate(directory, files_root, *, apply=False):
    directory, files_root = Path(directory), Path(files_root)
    if directory == Path('/opt/galaxy-ai/backend/private'):
        os.environ.setdefault('GALAXY_DATABASE_CONFIG', '/etc/galaxy-ai/database.json')
        if apply and subprocess.run(['systemctl', 'is-active', '--quiet', 'galaxy-ai'], check=False).returncode == 0:
            raise RuntimeError('Stop Galaxy AI before applying account migration')
        if apply and subprocess.run(['systemctl', 'is-active', '--quiet', 'galaxy-dsh'], check=False).returncode == 0:
            raise RuntimeError('Stop Galaxy DSH before applying account migration')
    source = snapshot(directory)
    users = source['auth']['users']
    known = {owner_key(user): user for user in users.values()}
    counts = {}
    for name, fields in FIELDS.items():
        field = next(f for f in fields if f != 'apps')
        records = source[name][field]
        counts[name] = len(records)
        for key, value in records.items():
            owner = key if field == 'connections' else owner_key({'id': value.get('user_id'), 'workspace_id': value.get('workspace_id')})
            if owner not in known and name != 'tool_audit':
                raise RuntimeError('Unknown record owner in ' + name)
            if name == 'chats' and key != value.get('id'):
                raise RuntimeError('Conversation ID mismatch')
    if not apply:
        return {'dry_run': True, 'users': len(users), 'records': counts}
    accounts = Accounts(directory)
    if (accounts.root / '.ready.json').exists():
        raise RuntimeError('Account migration already complete; refusing to overwrite')
    identities = {key: (user, accounts.ensure(user)) for key, user in known.items()}
    copied = 0
    for name, fields in FIELDS.items():
        repository = AccountRepository(directory, name, fields)
        field = repository.field
        repository._save(source[name], {'schema_version': 1, field: {}}, identities)
        restored = repository._load(identities)[field]
        if restored != source[name][field]:
            raise RuntimeError('Record verification failed: ' + name)
    for key, (user, base) in identities.items():
        copied += copy_directory(files_root / '.work' / runtime_owner(user), child(base, 'workspace'))
        copied += copy_directory(files_root / '.dsh' / runtime_owner(user), child(base, 'tasks/dsh'))
        copied += copy_directory(directory / 'cli-users' / user['id'], child(base, 'runtime/cli'))
        record = source['youzan']['connections'].get(key)
        if record:
            browser = child(base, 'browser')
            copied += copy_directory(directory / 'youzan-profiles' / record['id'] / record['profile_revision'], browser)
            atomic_json(browser / '.channel-bindings.json', {'youzan': record['account']})
        else:
            child(base, 'browser')
    for identifier, record in source['files']['files'].items():
        key = owner_key({'id': record['user_id'], 'workspace_id': record['workspace_id']})
        target = child(identities[key][1], 'files/published')
        name = record.get('stored_name', identifier + '.xlsx')
        if Path(name).name != name or name.startswith('.') or '\\' in name:
            raise RuntimeError('Invalid published filename')
        original = files_root / name
        if not original.exists() and not record.get('stored_name'):
            original = directory / 'files' / name
        if not original.is_file() or original.is_symlink():
            raise RuntimeError('Published file unavailable')
        shutil.copy2(original, target / name)
        (target / name).chmod(0o600)
        checksum = hashlib.sha256((target / name).read_bytes()).hexdigest()
        if checksum != hashlib.sha256(original.read_bytes()).hexdigest() or record.get('sha256') and checksum != record['sha256']:
            raise RuntimeError('Published file verification failed')
        copied += 1
    # Record only counts/checksums, never the payload or a credential value.
    marker = {'schema_version': 1, 'mode': MODE, 'created_at': int(time.time()),
              'users': len(users), 'records': counts, 'copied_files': copied,
              'source_digests': {name: digest(source[name]) for name in FIELDS}}
    atomic_json(accounts.root / '.ready.json', marker)
    return marker


def rollback(directory, files_root, backup_root):
    """Export post-cutover records/files before disabling file mode; no new writes lost."""
    directory, files_root, backup_root = Path(directory), Path(files_root), Path(backup_root)
    if directory == Path('/opt/galaxy-ai/backend/private'):
        os.environ.setdefault('GALAXY_DATABASE_CONFIG', '/etc/galaxy-ai/database.json')
        for service in ('galaxy-ai', 'galaxy-dsh'):
            if subprocess.run(['systemctl','is-active','--quiet',service],check=False).returncode == 0:
                raise RuntimeError('Stop Galaxy services before rollback')
    if not (directory / 'accounts/.ready.json').is_file():
        raise RuntimeError('No completed file migration to roll back')
    accounts = Accounts(directory)
    identities = accounts.identities()
    states = {name: AccountRepository(directory,name,fields)._load(identities) for name,fields in FIELDS.items()}
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    def replace_tree(source, target):
        if not source.exists():return
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        staging = target.with_name('.restore-' + uuid.uuid4().hex)
        copy_directory(source, staging)
        if target.exists():
            saved = backup_root / ('old-' + uuid.uuid4().hex)
            os.replace(target, saved)
        os.replace(staging, target)
    for key, (user, base) in identities.items():
        replace_tree(base / 'workspace', files_root / '.work' / runtime_owner(user))
        replace_tree(base / 'tasks/dsh', files_root / '.dsh' / runtime_owner(user))
        replace_tree(base / 'runtime/cli', directory / 'cli-users' / user['id'])
        record = states['youzan']['connections'].get(key)
        if record:
            replace_tree(base / 'browser', directory / 'youzan-profiles' / record['id'] / record['profile_revision'])
    for key, record in states['files']['files'].items():
        owner = owner_key({'id':record['user_id'], 'workspace_id':record['workspace_id']})
        name = record.get('stored_name', key + '.xlsx')
        if Path(name).name != name or name.startswith('.') or '\\' in name:
            raise RuntimeError('Invalid rollback filename')
        source = identities[owner][1] / 'files/published' / name
        if not source.is_file() or source.is_symlink():raise RuntimeError('Rollback file unavailable')
        shutil.copy2(source, files_root / name)
    counts = {}
    for name, fields in FIELDS.items():
        field = next(f for f in fields if f != 'apps')
        restored = states[name][field]
        repository = legacy_repository(directory,name,fields)
        with repository.transaction() as state:
            state[field] = restored
        with repository.transaction(write=False) as state:
            if state[field] != restored:raise RuntimeError('Rollback verification failed')
        counts[name] = len(restored)
    return {'rollback_exported':True, 'records':counts}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=Path('/opt/galaxy-ai/backend/private'))
    parser.add_argument('--files-root', type=Path, default=Path('/opt/galaxy-ai/hosted-files'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--rollback-to-mysql', action='store_true')
    parser.add_argument('--rollback-backup', type=Path)
    args = parser.parse_args()
    if args.rollback_to_mysql:
        if not args.apply or not args.rollback_backup:
            parser.error('Rollback requires --apply and --rollback-backup')
        result = rollback(args.directory,args.files_root,args.rollback_backup)
    else:
        result = migrate(args.directory,args.files_root,apply=args.apply)
    print(json.dumps(result, ensure_ascii=False))
