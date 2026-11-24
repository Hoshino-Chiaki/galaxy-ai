import hashlib
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from galaxy_cli.registry import ToolError
from galaxy_cli.request_cache import CachedDataset, TTL
from galaxy_cli.tools.youzan import order_search as tool


def context(root, user='alice'):
    root.mkdir(parents=True, exist_ok=True)
    return {'cache_directory': str(root), 'cache_identity': {'user_id': user, 'workspace_id': 'w'}}


def test_order_receipt_is_directly_readable_and_manifest_records_scope(tmp_path):
    ctx = context(tmp_path / 'alice/cache')
    class Client:
        credentials = {'kdt_id': '123', 'cookies': {'access_token': 'never-store-secret'}}
    response = {'code': 200, 'data': {'total': 1, 'page_no': 1, 'data': [{'main_order_info': {'kdt_id': 123}}]}}
    with patch.object(tool, 'gateway', return_value=response):
        receipt = tool.run(Client(), {'action': 'query', 'all_pages': True, 'time_type': 'pay_time',
            'start_time': '2026-11-16', 'end_time': '2026-11-22'}, ctx)['data']
    root = Path(ctx['cache_directory'])
    assert receipt['sandbox_path'] == '/account-cache/' + receipt['path']
    raw = (root / receipt['path']).read_bytes()
    assert json.loads(raw)['data']['data'] == response['data']['data']
    assert receipt['sha256'] == hashlib.sha256(raw).hexdigest()
    manifest = json.loads((root / receipt['metadata_path'].removeprefix('/account-cache/')).read_bytes())
    assert manifest['complete'] and manifest['user_id'] == 'alice' and manifest['workspace_id'] == 'w'
    assert manifest['query']['shop_id'] == '123'
    assert manifest['query']['filters']['time_type'] == 'pay_time'
    assert manifest['query']['filters']['start_time'] < manifest['query']['filters']['end_time']
    assert 'never-store-secret' not in json.dumps(manifest)


def test_account_roots_are_separate_and_links_cannot_write_into_another_account(tmp_path):
    alice, bob = context(tmp_path / 'alice/cache'), context(tmp_path / 'bob/cache', 'bob')
    with CachedDataset(bob, 'youzan/orders/123', {}) as data:
        data.append(b'{"bob":true}\n');data.save()
    (tmp_path / 'alice/cache/youzan').symlink_to(tmp_path / 'bob/cache/youzan', target_is_directory=True)
    with pytest.raises(ToolError):
        with CachedDataset(alice, 'youzan/orders/123', {}):
            pass
    assert len(list((tmp_path / 'bob/cache').rglob('*.jsonl'))) == 1


def test_expired_snapshots_are_pruned_and_fresh_snapshot_is_preserved(tmp_path):
    ctx = context(tmp_path)
    with CachedDataset(ctx, 'youzan/orders/123', {}) as old:
        old.append(b'old\n');old.save();old_path = tmp_path / old.metadata['path']
    expired = time.time() - TTL - 60
    for p in old_path.parent.iterdir():
        os.utime(p, (expired, expired))
    with CachedDataset(ctx, 'youzan/orders/456', {}) as fresh:
        fresh.append(b'fresh\n');fresh.save()
    assert not old_path.exists() and not old_path.with_suffix('.meta.json').exists()
    with CachedDataset(ctx, 'youzan/orders/789', {}):
        assert (tmp_path / fresh.metadata['path']).read_bytes() == b'fresh\n'


def test_quota_stops_before_append_and_manifest_remains_incomplete(tmp_path):
    ctx = context(tmp_path)
    with CachedDataset(ctx, 'youzan/orders/123', {}, quota=20000) as data:
        data.append(b'x' * 1000);data.save()
        with pytest.raises(ToolError, match='容量限制'):
            data.append(b'x' * 10000)
        assert (tmp_path / data.metadata['path']).stat().st_size == 1000
        manifest = json.loads((tmp_path / data.metadata['metadata_path'].removeprefix('/account-cache/')).read_bytes())
        assert manifest['size'] == 1000 and not manifest['complete']


def test_failed_pagination_manifest_never_claims_complete(tmp_path):
    ctx = context(tmp_path)
    class Client:
        credentials = {'kdt_id': '123'}
    first = {'code': 200, 'data': {'total': 2, 'page_no': 1, 'data': [{'main_order_info': {'kdt_id': 123}}]}}
    with patch.object(tool, 'gateway', side_effect=[first, ToolError('upstream_failed', 'failed', 1)]):
        result = tool.run(Client(), {'action': 'query', 'all_pages': True}, ctx)
    metadata = json.loads((tmp_path / result['data']['metadata_path'].removeprefix('/account-cache/')).read_bytes())
    assert not result['success'] and not metadata['complete']
    assert metadata['count'] == 1 and metadata['pages'] == 1


def test_model_cannot_choose_cache_root_in_order_arguments():
    with pytest.raises(ToolError):
        tool.normalize({'action': 'query', 'all_pages': True, 'cache_directory': '/another-account'})
