import asyncio
import copy
import json
import time
from http.cookies import SimpleCookie
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from xingxu.app import create_app
from xingxu.auth import AuthFailure
from xingxu.tools import ExecutionContext
from xingxu.youzan import YouzanInput, owner_key


@pytest.fixture
def setup(tmp_path):
    calls = {'crm': [], 'fail': set(), 'browser': 0}
    def upstream(request):
        if request.url.path == '/v4/shop/getShopList.json':
            return httpx.Response(200, json={'code': 0, 'data': {'pageNum': 1, 'pageSize': 48, 'total': 2,
                'data': [{'kdtId': 111, 'shopName': '测试总部'}, {'kdtId': 222, 'shopName': '测试分店'}]}})
        if request.url.path == '/v4/shop/enter-shop':
            identifier = request.url.params['kdtId']
            if identifier in calls['fail']:
                return httpx.Response(403)
            return httpx.Response(302, headers={'Location': 'https://crm.youzan.com/crm/member/customer/manage',
                'Set-Cookie': f'access_token=fixture-access-{identifier}-private; Domain=.youzan.com; Path=/'})
        assert request.url.path == '/crm/member/customer/manage'
        cookies = SimpleCookie(request.headers['cookie'])
        identifier = cookies['kdt_id'].value
        assert cookies['access_token'].value == f'fixture-access-{identifier}-private'
        calls['crm'].append(identifier)
        return httpx.Response(200, text='<script>window._global={"csrf_token":' + json.dumps(f'fixture-csrf-{identifier}-private') + '}</script>')
    async def no_browser(*args):
        calls['browser'] += 1
        raise AssertionError('CRM preparation must never log in or open a browser')
    app = create_app(tmp_path, youzan_transport=httpx.MockTransport(upstream), youzan_login_runner=no_browser)
    auth = app.state.auth
    auth.import_accounts([('admin', 'fixture-admin-password'), ('employee', 'fixture-employee-password')])
    admin = auth.login('admin', 'fixture-admin-password', False, '127.0.0.1')
    employee = auth.login('employee', 'fixture-employee-password', False, '127.0.0.1')
    service = app.state.youzan
    service.save(admin['user'], YouzanInput(account='fixture-merchant', password='fixture-merchant-password', kdt_id='111'))
    with service.repo.transaction() as state:
        record = state['connections'][owner_key(admin['user'])]
        record.update(sid={'secret': service._encrypt('fixture-sid-multi-private'), 'expires_at': int(time.time()) + 86400},
            shops=[{'id': '111', 'name': '测试总部'}, {'id': '222', 'name': '测试分店'}])
    context = ExecutionContext(admin['user']['id'], admin['user']['workspace_id'], tuple(admin['user']['permissions']), 'fixture-conversation', admin['access_token'])
    return app, admin, employee, context, calls, tmp_path


def call(shops='all', args=None):
    return {'id': 'multi-call', 'type': 'function', 'function': {'name': 'galaxy_cli', 'arguments': json.dumps({
        'args': args or ['youzan', 'customer-search', '--input', '{"keyword":"测试"}'], 'shops': shops})}}


def test_collects_distinct_encrypted_sessions_and_preserves_selected_shop(setup):
    app, admin, _, _, calls, directory = setup
    service = app.state.youzan
    first = asyncio.run(service.resolve_shop_credentials(admin['user'], 'all'))
    assert [row['credentials']['kdt_id'] for row in first] == ['111', '222']
    assert first[0]['credentials']['csrf'] != first[1]['credentials']['csrf']
    assert first[0]['credentials']['cookies']['access_token'] != first[1]['credentials']['cookies']['access_token']
    assert first[0]['credentials']['sid'] == first[1]['credentials']['sid']
    assert service.summary(admin['user'])['kdt_id'] == '111'
    assert all(row['ready'] for row in service.summary(admin['user'])['shop_sessions'])
    asyncio.run(service.resolve_shop_credentials(admin['user'], 'all'))
    assert calls['crm'] == ['111', '222']
    raw = (directory / 'youzan.json').read_text()
    assert 'fixture-csrf-' not in raw and 'fixture-access-' not in raw and 'fixture-sid-multi-private' not in raw
    assert calls['browser'] == 0
    service.select_shop(admin['user'], '222')
    assert service.tool_credentials(admin['user'])['csrf'] == first[1]['credentials']['csrf']
    assert service.tool_credentials(admin['user'], '111')['csrf'] == first[0]['credentials']['csrf']


def test_names_resolve_only_to_unique_authorized_shops(setup):
    app, admin, _, _, calls, _ = setup
    result = asyncio.run(app.state.youzan.resolve_shop_credentials(admin['user'], ['分店']))
    assert [row['kdt_id'] for row in result] == ['222']
    assert calls['crm'] == ['222']
    for selection in (['999'], ['测试']):
        with pytest.raises(AuthFailure):
            asyncio.run(app.state.youzan.resolve_shop_credentials(admin['user'], selection))
    assert calls['crm'] == ['222']


def test_sid_changes_invalidate_all_cached_shop_credentials(setup):
    app, admin, _, _, calls, _ = setup
    service = app.state.youzan
    asyncio.run(service.resolve_shop_credentials(admin['user'], 'all'))
    with service.repo.transaction() as state:
        state['connections'][owner_key(admin['user'])]['sid']['secret'] = service._encrypt('fixture-new-sid-private')
    assert not any(row['ready'] for row in service.summary(admin['user'])['shop_sessions'])
    with pytest.raises(AuthFailure):
        service.tool_credentials(admin['user'])
    asyncio.run(service.resolve_shop_credentials(admin['user'], 'all'))
    assert calls['crm'] == ['111', '222', '111', '222']


def test_failure_is_per_shop_and_retry_does_not_refetch_ready_shops(setup):
    app, admin, _, _, calls, _ = setup
    calls['fail'] = {'222'}
    rows = asyncio.run(app.state.youzan.resolve_shop_credentials(admin['user'], 'all'))
    assert 'credentials' in rows[0] and rows[1]['error']['code'] == 'shop_access_required'
    calls['fail'].clear()
    rows = asyncio.run(app.state.youzan.resolve_shop_credentials(admin['user'], 'all'))
    assert all('credentials' in row for row in rows)
    assert calls['crm'] == ['111', '222']


def test_scope_dispatch_preserves_full_results_redacts_secrets_and_reports_partial(setup):
    app, _, _, context, calls, directory = setup
    dispatcher = app.state.ai.dispatcher
    calls['fail'] = {'222'}
    async def run(arguments, user, *, credentials, **kwargs):
        return {'code': 0, 'data': {'shop_id': credentials['kdt_id'], 'unknown': {'nested': None}, 'sid': credentials['sid']}}
    dispatcher._run = AsyncMock(side_effect=run)
    result = asyncio.run(dispatcher.execute(context, call()))
    assert result['status'] == 'success' and result['result']['partial'] is True
    assert result['result']['shops'][0]['data']['data']['unknown'] == {'nested': None}
    assert result['result']['shops'][1]['error']['code'] == 'shop_access_required'
    assert result['result']['shops'][0]['data']['data']['sid'] == '[已隐藏凭证]'
    assert dispatcher._run.await_count == 1
    assert 'fixture-sid-multi-private' not in json.dumps(result) + (directory / 'tool_audit.json').read_text()


@pytest.mark.parametrize('scope', ['all', ['111', '222']])
def test_write_broadcast_is_denied_before_refresh_or_process_start(setup, scope):
    app, _, _, context, calls, _ = setup
    dispatcher = app.state.ai.dispatcher
    dispatcher._run = AsyncMock()
    result = asyncio.run(dispatcher.execute(context, call(scope, ['youzan', 'material-category', 'create', '--input', '{"name":"测试"}'])))
    assert result['status'] == 'denied'
    assert result['result']['error']['code'] == 'shop_scope_denied'
    dispatcher._run.assert_not_awaited()
    assert calls['crm'] == []


def test_explicit_single_shop_write_uses_only_that_shops_credentials(setup):
    app, _, _, context, calls, _ = setup
    dispatcher = app.state.ai.dispatcher
    dispatcher._run = AsyncMock(return_value={'code': 0, 'data': 'fixture-created-id'})
    result = asyncio.run(dispatcher.execute(context, call(['222'], ['youzan', 'material-category', 'create', '--input', '{"name":"测试"}'])))
    assert result['status'] == 'success'
    assert [row['kdt_id'] for row in result['result']['shops']] == ['222']
    assert dispatcher._run.await_args.kwargs['credentials']['csrf'] == 'fixture-csrf-222-private'
    assert calls['crm'] == ['222']


def test_connection_changes_during_crm_refresh_do_not_commit_stale_sessions(setup):
    app, admin, _, _, _, _ = setup
    service = app.state.youzan
    original = service._crm_csrf
    async def change(record):
        value = await original(record)
        with service.repo.transaction() as state:
            state['connections'][owner_key(admin['user'])]['revision'] = 'new-connection-revision'
        return value
    service._crm_csrf = change
    with pytest.raises(AuthFailure, match='连接已变更'):
        asyncio.run(service.resolve_shop_credentials(admin['user'], 'all'))
    assert not any(row['ready'] for row in service.summary(admin['user'])['shop_sessions'])


@pytest.mark.parametrize('scope', [None, True, [], ['111', '111'], [111], {'kdt_id': '111'}])
def test_invalid_scope_is_rejected_without_upstream_or_cli_access(setup, scope):
    app, _, _, context, calls, _ = setup
    app.state.ai.dispatcher._run = AsyncMock()
    result = asyncio.run(app.state.ai.dispatcher.execute(context, call(scope)))
    assert result['status'] == 'denied'
    app.state.ai.dispatcher._run.assert_not_awaited()
    assert calls['crm'] == []


def test_logout_and_other_user_never_use_cached_shop_sessions(setup):
    app, admin, employee, context, _, _ = setup
    asyncio.run(app.state.youzan.resolve_shop_credentials(admin['user'], 'all'))
    with pytest.raises(AuthFailure):
        asyncio.run(app.state.youzan.resolve_shop_credentials(employee['user'], 'all'))
    app.state.auth.logout(admin['access_token'])
    app.state.ai.dispatcher._run = AsyncMock()
    result = asyncio.run(app.state.ai.dispatcher.execute(context, call()))
    assert result['status'] == 'denied'
    app.state.ai.dispatcher._run.assert_not_awaited()


def test_authorization_changes_mid_batch_discard_prior_results(setup):
    app, admin, _, context, _, _ = setup
    dispatcher = app.state.ai.dispatcher
    async def revoke(*args, **kwargs):
        app.state.auth.logout(admin['access_token'])
        return {'code': 0, 'data': {'must_not_escape': True}}
    dispatcher._run = AsyncMock(side_effect=revoke)
    result = asyncio.run(dispatcher.execute(context, call()))
    assert result['status'] == 'denied'
    assert 'must_not_escape' not in json.dumps(result)
    assert dispatcher._run.await_count == 1


def test_all_refresh_job_only_reads_existing_sid(setup):
    app, admin, _, _, calls, _ = setup
    headers = {'Authorization': 'Bearer ' + admin['access_token']}
    with TestClient(app) as client:
        assert client.post('/api/v1/admin/youzan/refresh', headers=headers, json={'kind': 'all'}).status_code == 202
        for _ in range(100):
            summary = client.get('/api/v1/admin/youzan', headers=headers).json()
            if summary['job']['status'] != 'running':
                break
            time.sleep(.01)
        assert summary['job']['code'] == 'all_ready'
        assert sum(row['ready'] for row in summary['shop_sessions']) == 2
        assert calls['browser'] == 0


def test_ui_refresh_does_not_interrupt_tool_session_preparation(setup):
    app, admin, _, _, _, _ = setup
    service = app.state.youzan
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        original = service._crm_csrf
        async def wait(record):
            started.set()
            await release.wait()
            return await original(record)
        service._crm_csrf = wait
        task = asyncio.create_task(service.resolve_shop_credentials(admin['user'], 'all'))
        await started.wait()
        assert service.summary(admin['user'])['preparing_shops'] is True
        with pytest.raises(AuthFailure, match='正在准备'):
            await service.start(admin['user'], 'all', admin['access_token'])
        release.set()
        await task
        assert service.summary(admin['user'])['preparing_shops'] is False
    asyncio.run(scenario())


def test_other_user_prepares_independent_sessions_for_same_shop_ids(setup):
    app, admin, employee, _, calls, _ = setup
    service = app.state.youzan
    service.save(employee['user'], YouzanInput(account='other-fixture-account', password='fixture-password', kdt_id='111'))
    with service.repo.transaction() as state:
        state['connections'][owner_key(employee['user'])].update(sid={'secret': service._encrypt('other-user-sid-private'), 'expires_at': int(time.time()) + 86400})
    asyncio.run(service.resolve_shop_credentials(admin['user'], 'all'))
    assert not service.summary(employee['user'])['shop_sessions']
    asyncio.run(service.resolve_shop_credentials(employee['user'], 'all'))
    assert service.tool_credentials(employee['user'], '111')['sid'] == 'other-user-sid-private'
    assert service.tool_credentials(admin['user'], '111')['sid'] == 'fixture-sid-multi-private'
    assert calls['crm'] == ['111', '222', '111', '222']


def test_each_query_rebinds_upstream_shop_and_serializes_shared_sid(setup):
    app, admin, _, context, calls, _ = setup
    dispatcher = app.state.ai.dispatcher
    async def upstream_query(arguments, user, *, credentials, **kwargs):
        identifier = credentials['kdt_id']
        assert calls['crm'][-1] == identifier
        await asyncio.sleep(.02)
        assert calls['crm'][-1] == identifier, 'Another operation switched the upstream shop during a query'
        return {'code': 0, 'data': {'list': [{'shop': identifier}], 'total': int(identifier)}}
    dispatcher._run = AsyncMock(side_effect=upstream_query)
    async def scenario():
        return await asyncio.gather(dispatcher.execute(context, call()), dispatcher.execute(context, call('current')))
    batch, current = asyncio.run(scenario())
    assert batch['status'] == current['status'] == 'success'
    assert [row['data']['data']['list'][0]['shop'] for row in batch['result']['shops']] == ['111', '222']
    assert current['result']['data']['list'][0]['shop'] == '111'
    assert calls['crm'] == ['111', '222', '111']


@pytest.mark.parametrize('overview', [False, True])
def test_observed_crm_redirect_variants_fetch_tokens_without_leaving_fixed_domains(setup, overview):
    app, admin, _, _, _, _ = setup
    service = app.state.youzan
    def upstream(request):
        path = request.url.path
        if path == '/v4/shop/enter-shop':
            return httpx.Response(302, headers={'Location': 'https://crm.youzan.com/crm/member/customer/manage'})
        if path == '/crm/member/customer/manage':
            return httpx.Response(302, headers={'Location': '/v4/scrm/overview' if overview else '/v4/scrm/customer/manage'})
        if path == '/v4/scrm/customer/manage' and request.url.host == 'crm.youzan.com':
            return httpx.Response(302, headers={'Location': 'https://www.youzan.com/v4/scrm/customer/manage'})
        return httpx.Response(200, text='<script>window._global={"csrf_token":"fixture-redirect-csrf"}</script>')
    service.transport = httpx.MockTransport(upstream)
    with service.repo.transaction(write=False) as state:
        record = copy.deepcopy(state['connections'][owner_key(admin['user'])])
    record['_collect_category_credentials'] = True
    assert asyncio.run(service._crm_csrf(record)) == 'fixture-redirect-csrf'
    assert record['_category_session']['origin'] == ('https://crm.youzan.com' if overview else 'https://www.youzan.com')
