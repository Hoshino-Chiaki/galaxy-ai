import importlib.util
import json
from pathlib import Path

import pytest

from xingxu.files import FileService


def worker_module():
    path = Path(__file__).resolve().parents[2] / 'deploy/dsh/worker.py'
    spec = importlib.util.spec_from_file_location('cache_worker_test', path)
    worker = importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    return worker


def test_server_cache_and_worker_mount_resolve_same_account(tmp_path, monkeypatch):
    monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE', 'files-v1')
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    files = FileService(tmp_path)
    alice = {'id': 'a', 'username': 'alice', 'workspace_id': 'w', 'permissions': []}
    bob = {**alice, 'id': 'b', 'username': 'bob'}
    a, b = files.catalog_context(alice, '123'), files.catalog_context(bob, '123')
    assert a['cache_directory'] == str(tmp_path / 'accounts/alice/cache')
    assert a['cache_directory'] != b['cache_directory']
    assert a['cache_identity'] == {'user_id': 'a', 'workspace_id': 'w'}
    registry = json.loads((tmp_path / 'accounts/.owners.json').read_bytes())
    owner = next(r['runtime_owner'] for r in registry.values() if r['user_id'] == 'a')
    worker = worker_module();monkeypatch.setattr(worker, 'ACCOUNTS', tmp_path / 'accounts')
    cache = worker.account_cache(owner)
    assert str(cache) == a['cache_directory']
    args = worker.jail_arguments(tmp_path, tmp_path / 'broker', 'token', cache=cache)
    index = args.index('/account-cache')
    assert args[index-2:index+1] == ['--ro-bind', a['cache_directory'], '/account-cache']
    assert b['cache_directory'] not in args and str(tmp_path / 'accounts') not in args
    with pytest.raises(ValueError):worker.account_cache('c'*64)
    cache.rmdir();cache.symlink_to(b['cache_directory'], target_is_directory=True)
    with pytest.raises((ValueError, OSError)):worker.account_cache(owner)


def test_legacy_worker_never_mounts_account_root(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_ACCOUNT_STORAGE', raising=False)
    worker = worker_module()
    with pytest.raises(ValueError):worker.account_cache('a'*64)
