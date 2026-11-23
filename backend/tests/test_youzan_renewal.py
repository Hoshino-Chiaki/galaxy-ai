import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from xingxu.app import create_app
from xingxu.auth import AuthFailure
from xingxu.youzan.schemas import RenewalInput, YouzanInput
from xingxu.youzan.service import owner_key


@pytest.fixture
def configured(tmp_path):
    calls = {'login': 0, 'shops': 0, 'crm': 0, 'failure': None}
    async def login(*_):
        calls['login'] += 1
        if calls['failure']:
            raise AuthFailure(calls['failure'], 'secret upstream data must not leak', 502)
        return {'sid': 'fixture-private-sid', 'expires_at': time.time() + 86400}
    def http(request):
        if request.url.path.endswith('getShopList.json'):
            calls['shops'] += 1
            return httpx.Response(200, json={'code': 0, 'data': {'total': 1, 'data': [{'kdtId': 123, 'shopName': 'Fixture'}]}})
        if request.url.path.endswith('enter-shop'):
            return httpx.Response(302, headers={'Location': 'https://crm.youzan.com/crm/member/customer/manage'})
        calls['crm'] += 1
        return httpx.Response(200, text='window._global={"csrf_token":"fixture-private-csrf"}')
    app = create_app(tmp_path, youzan_transport=httpx.MockTransport(http), youzan_login_runner=login)
    app.state.auth.import_accounts([('admin', 'fixture-admin-password'), ('employee', 'fixture-employee-password')])
    admin = app.state.auth.login('admin', 'fixture-admin-password', False, 'fixture')
    employee = app.state.auth.login('employee', 'fixture-employee-password', False, 'fixture')
    service = app.state.youzan
    service.save(admin['user'], YouzanInput(account='fixture', password='fixture-password', kdt_id='123'))
    return app, service, admin, employee, calls, tmp_path


def due(service, admin, kind='sid'):
    with service.repo.transaction() as state:
        entry = state['connections'][owner_key(admin['user'])]['renewal'][kind]
        entry['next_at'] = time.time() - 1


def test_default_intervals_and_admin_only_endpoint(configured, monkeypatch):
    app, service, admin, employee, _, _ = configured
    monkeypatch.setattr(service.renewal, 'start', lambda: None)
    with TestClient(app) as client:
        endpoint = '/api/v1/admin/youzan/renewal'
        assert client.put(endpoint, json={}).status_code == 401
        assert client.put(endpoint, json={}, headers={'Authorization': 'Bearer ' + employee['access_token']}).status_code == 403
        response = client.put(endpoint, json={}, headers={'Authorization': 'Bearer ' + admin['access_token']})
        assert response.status_code == 200
        for kind in ('sid', 'csrf'):
            assert response.json()['renewal'][kind]['interval_days'] == 2
            assert response.json()['renewal'][kind]['enabled'] is True
        assert 'auth_version' not in response.text and admin['access_token'] not in response.text
        for invalid in ({'sid': {'interval_days': 0}}, {'csrf': {'enabled': 'true'}}, {'user_id': employee['user']['id']}):
            assert client.put(endpoint, json=invalid, headers={'Authorization': 'Bearer ' + admin['access_token']}).status_code == 422


def test_background_refresh_survives_logout_and_covers_both_without_duplicate(configured):
    app, service, admin, _, calls, directory = configured
    service.renewal.configure(admin['user'], RenewalInput())
    due(service, admin, 'sid'); due(service, admin, 'csrf')
    app.state.auth.logout(admin['access_token'])
    asyncio.run(service.renewal.tick())
    assert calls['login'] == 1 and calls['crm'] == 1
    summary = service.summary(admin['user'])
    assert summary['sid']['present'] and summary['csrf']['present']
    for kind in ('sid', 'csrf'):
        assert summary['renewal'][kind]['last_success_at']
        if kind == 'sid':
            assert summary['renewal'][kind]['next_at'] == summary['sid']['expires_at']
        else:
            assert summary['renewal'][kind]['next_at'] > time.time() + 1.9 * 86400
    assert summary['job']['trigger'] == 'automatic'
    assert admin['access_token'] not in (directory / 'youzan.json').read_text()
    assert 'fixture-private' not in json.dumps(summary)
    asyncio.run(service.renewal.tick())
    assert calls['login'] == 1
    assert not service.summary(admin['user'])['renewal']['csrf']['paused']


@pytest.mark.parametrize('change', ['disabled', 'role', 'auth_version', 'inactive'])
def test_background_grants_recheck_current_identity_and_policy(configured, change):
    app, service, admin, _, calls, _ = configured
    service.renewal.configure(admin['user'], RenewalInput())
    due(service, admin)
    if change == 'disabled':
        service.renewal.configure(admin['user'], RenewalInput(sid={'enabled': False}, csrf={'enabled': False}))
    else:
        with app.state.auth.repository.transaction() as state:
            raw = state['users'][admin['user']['id']]
            if change == 'role': raw['role'] = 'employee'
            elif change == 'inactive': raw['active'] = False
            else: raw['auth_version'] += 1
    asyncio.run(service.renewal.tick())
    assert calls['login'] == calls['shops'] == calls['crm'] == 0


def test_manual_challenge_pauses_and_preserves_old_credentials(configured):
    _, service, admin, _, calls, _ = configured
    calls['failure'] = 'human_verification_required'
    key = owner_key(admin['user'])
    original = {'secret': service._encrypt('fixture-old-private-sid'), 'expires_at': time.time() - 10}
    with service.repo.transaction() as state:
        state['connections'][key]['sid'] = original
    service.renewal.configure(admin['user'], RenewalInput())
    due(service, admin)
    asyncio.run(service.renewal.tick())
    result = service.summary(admin['user'])
    assert result['renewal']['sid']['paused'] is True
    assert result['renewal']['sid']['error']['code'] == 'human_verification_required'
    assert result['renewal']['sid']['next_at'] is None
    with service.repo.transaction(write=False) as state:
        assert state['connections'][key]['sid'] == original
    assert 'secret upstream' not in json.dumps(result)
    asyncio.run(service.renewal.tick())
    assert calls['login'] == 1
    assert service.summary(admin['user'])['renewal']['csrf']['paused']


def test_policy_change_during_login_does_not_commit_sid(configured):
    _, service, admin, _, _, _ = configured
    service.renewal.configure(admin['user'], RenewalInput()); due(service, admin)
    async def change(*_):
        service.renewal.configure(admin['user'], RenewalInput(sid={'enabled': False}, csrf={'enabled': False}))
        return {'sid': 'fixture-private-new-sid', 'expires_at': time.time() + 86400}
    service.login_runner = change
    asyncio.run(service.renewal.tick())
    assert not service.summary(admin['user'])['sid']['present']


def test_failed_runtime_uses_backoff_instead_of_repeated_logins(configured):
    _, service, admin, _, calls, _ = configured
    calls['failure'] = 'browser_missing'
    service.renewal.configure(admin['user'], RenewalInput()); due(service, admin)
    asyncio.run(service.renewal.tick())
    entry = service.summary(admin['user'])['renewal']['sid']
    assert not entry['paused'] and entry['next_at'] >= time.time() + 1790
    assert entry['error']['code'] == 'browser_missing'
    asyncio.run(service.renewal.tick())
    assert calls['login'] == 1


def test_successful_manual_refresh_resumes_paused_schedule(configured):
    _, service, admin, _, calls, _ = configured
    service.renewal.configure(admin['user'], RenewalInput()); due(service, admin)
    calls['failure'] = 'human_verification_required'
    asyncio.run(service.renewal.tick())
    assert service.summary(admin['user'])['renewal']['sid']['paused']
    calls['failure'] = None
    async def manual():
        await service.start(admin['user'], 'sid', admin['access_token'])
        await service.tasks[owner_key(admin['user'])]
    asyncio.run(manual())
    entry = service.summary(admin['user'])['renewal']['sid']
    assert not entry['paused'] and entry['error'] is None and entry['next_at'] > time.time()


def test_valid_sid_check_does_not_postpone_renewal_beyond_expiry(configured):
    _, service, admin, _, calls, _ = configured
    key = owner_key(admin['user'])
    expiry = int(time.time()) + 3600
    with service.repo.transaction() as state:
        state['connections'][key]['sid'] = {
            'secret': service._encrypt('fixture-existing-sid'), 'updated_at': int(time.time()) - 2 * 86400,
            'expires_at': expiry}
    service.renewal.configure(admin['user'], RenewalInput())
    due(service, admin)
    asyncio.run(service.renewal.tick())
    summary = service.summary(admin['user'])
    assert calls['login'] == 0  # Checking a live session does not mint a new SID.
    assert summary['sid']['expires_at'] == expiry
    assert summary['renewal']['sid']['next_at'] == expiry


def test_expired_sid_recovers_even_when_old_schedule_is_in_the_future(configured):
    _, service, admin, _, calls, _ = configured
    key = owner_key(admin['user'])
    service.renewal.configure(admin['user'], RenewalInput())
    with service.repo.transaction() as state:
        record = state['connections'][key]
        record['sid'] = {'secret': service._encrypt('fixture-expired-sid'), 'expires_at': int(time.time()) - 10}
        record['renewal']['sid']['next_at'] = int(time.time()) + 86400
        record['multi_shop'] = True
        record['kdt_id'] = ''  # The UI no longer requires a single selected shop.
    asyncio.run(service.renewal.tick())
    assert calls['login'] == 1
    summary = service.summary(admin['user'])
    assert not summary['sid']['expired'] and summary['shop_sessions'][0]['ready']
    assert summary['renewal']['sid']['next_at'] == summary['sid']['expires_at']


def test_crm_login_failure_wakes_enabled_sid_task(configured):
    _, service, admin, _, calls, _ = configured
    key = owner_key(admin['user'])
    service.renewal.configure(admin['user'], RenewalInput())
    with service.repo.transaction() as state:
        record = state['connections'][key]
        record['sid'] = {'secret': service._encrypt('fixture-revoked-sid'), 'expires_at': int(time.time()) + 86400}
    original = service._list_shops
    async def revoked_until_login(record):
        if not calls['login']:
            raise AuthFailure('login_required', 'SID 已失效', 409)
        return await original(record)
    service._list_shops = revoked_until_login
    due(service, admin, 'csrf')
    asyncio.run(service.renewal.tick())
    assert calls['login'] == 0
    assert service.summary(admin['user'])['renewal']['sid']['next_at'] <= time.time()
    asyncio.run(service.renewal.tick())
    assert calls['login'] == 1
    assert service.summary(admin['user'])['job']['status'] == 'success'


def test_expired_sid_failure_keeps_retry_backoff(configured):
    _, service, admin, _, calls, _ = configured
    key = owner_key(admin['user'])
    service.renewal.configure(admin['user'], RenewalInput())
    with service.repo.transaction() as state:
        state['connections'][key]['sid'] = {
            'secret': service._encrypt('fixture-expired-sid'), 'expires_at': int(time.time()) - 10}
    calls['failure'] = 'browser_missing'
    asyncio.run(service.renewal.tick())
    entry = service.summary(admin['user'])['renewal']['sid']
    assert entry['next_at'] > time.time() + 1790
    asyncio.run(service.renewal.tick())
    assert calls['login'] == 1


def test_crm_failure_cannot_enable_disabled_sid_refresh(configured):
    _, service, admin, _, calls, _ = configured
    service.renewal.configure(admin['user'], RenewalInput(sid={'enabled': False}))
    due(service, admin, 'csrf')
    asyncio.run(service.renewal.tick())
    entry = service.summary(admin['user'])['renewal']['sid']
    assert not entry['enabled'] and entry['next_at'] is None
    assert calls['login'] == 0
