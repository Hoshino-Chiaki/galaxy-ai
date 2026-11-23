import asyncio
import copy
import time
from fastapi.testclient import TestClient
from test_youzan_renewal import configured
from xingxu.auth import AuthFailure
from xingxu.youzan.service import owner_key


def wait_for_connection(client, headers):
    for _ in range(200):
        result = client.get('/api/v1/admin/youzan', headers=headers).json()
        if result.get('job', {}).get('status') != 'running':
            return result
        time.sleep(.01)
    raise AssertionError('Connection did not finish')


def test_saving_credentials_starts_login_and_all_shop_preparation_by_default(configured):
    app,service,admin,_,calls,_=configured
    with TestClient(app) as client:
        headers={'Authorization':'Bearer '+admin['access_token']}
        for extra in ({}, {'prepare_login': True}):
            response=client.put('/api/v1/admin/youzan',headers=headers,
                json={'account':'fixture','password':'fixture-password', **extra})
            assert response.status_code==200
            assert response.json()['job']['kind'] == 'connect'
            result = wait_for_connection(client, headers)
            assert result['job']['code'] == 'all_ready'
            assert result['sid']['present'] and result['csrf']['present']
            assert len(result['shops']) == 1 and result['shop_sessions'][0]['ready']
            assert 'fixture-private' not in response.text
        assert calls['login']==1 and calls['crm']==2


def test_reconnect_reuses_existing_sid_and_refreshes_every_shop(configured):
    _,service,admin,_,calls,_=configured
    async def scenario():
        for _ in range(2):
            await service.start(admin['user'],'connect',admin['access_token'])
            await service.tasks[owner_key(admin['user'])]
    asyncio.run(scenario())
    assert calls['login']==1 and calls['crm']==2
    result=service.summary(admin['user'])
    assert result['job']['code']=='all_ready' and result['shop_sessions'][0]['ready']


def test_failed_reconnect_preserves_previous_session_and_does_not_retry_login(configured):
    _,service,admin,_,calls,_=configured
    key=owner_key(admin['user'])
    async def first():
        await service.start(admin['user'],'connect',admin['access_token']);await service.tasks[key]
    asyncio.run(first())
    with service.repo.transaction() as state:
        state["connections"][key]["sid"]["expires_at"]=int(time.time())-1
        original=copy.deepcopy(state['connections'][key]['sid'])
    calls['failure']='human_verification_required'
    asyncio.run(first())
    result=service.summary(admin['user'])
    assert result['job']['status']=='failed' and result['job']['code']=='human_verification_required'
    assert calls['login']==2
    with service.repo.transaction(write=False) as state:assert state['connections'][key]['sid']==original


def test_prepare_login_flag_requires_boolean_and_explicit_false_stays_passive(configured):
    app,service,admin,_,calls,_=configured
    with TestClient(app) as client:
        headers={'Authorization':'Bearer '+admin['access_token']}
        assert client.put('/api/v1/admin/youzan',headers=headers,json={'account':'fixture','prepare_login':'true'}).status_code==422
        response=client.put('/api/v1/admin/youzan',headers=headers,json={'account':'fixture','prepare_login':False})
        assert response.status_code==200 and not response.json()['job'] and calls['login']==0


def test_saving_unchanged_password_preserves_profile_and_current_session(configured):
    _,service,admin,_,calls,_=configured
    from xingxu.youzan.schemas import YouzanInput
    key=owner_key(admin['user'])
    async def connect():
        await service.start(admin['user'],'connect',admin['access_token']);await service.tasks[key]
    asyncio.run(connect())
    with service.repo.transaction(write=False) as st:before=copy.deepcopy(st['connections'][key])
    service.save(admin['user'],YouzanInput(account='fixture',password='fixture-password'))
    with service.repo.transaction(write=False) as st:after=st['connections'][key]
    assert after['profile_revision']==before['profile_revision']
    assert after['sid']==before['sid'] and after['shop_sessions']==before['shop_sessions']
    calls['failure']='sms_verification_required'
    asyncio.run(connect())
    assert calls['login']==1 and service.summary(admin['user'])['job']['status']=='success'


def test_rejected_sid_uses_original_login_once(configured):
    _,service,admin,_,calls,_=configured
    key=owner_key(admin['user'])
    original=service._list_shops
    async def expired_until_login(record):
        if not calls['login']:raise AuthFailure('login_required','fixture expired',409)
        return await original(record)
    service._list_shops=expired_until_login
    async def scenario():
        await service.start(admin['user'],'connect',admin['access_token']);await service.tasks[key]
    asyncio.run(scenario())
    assert calls['login']==1 and calls['crm']==1


def test_legacy_sid_button_prepares_all_shops_without_a_selected_shop(configured):
    _, service, admin, _, calls, _ = configured
    from xingxu.youzan.schemas import YouzanInput
    service.save(admin['user'], YouzanInput(account='fixture', kdt_id=''))
    async def scenario():
        await service.start(admin['user'], 'sid', admin['access_token'])
        await service.tasks[owner_key(admin['user'])]
    asyncio.run(scenario())
    result = service.summary(admin['user'])
    assert calls['login'] == 1 and calls['crm'] == 1
    assert result['job']['code'] == 'all_ready' and result['kdt_id'] == ''
    assert result['csrf']['present'] and result['csrf']['updated_at']
    assert result['shop_sessions'][0]['ready']


def test_removed_default_shop_does_not_block_remaining_authorized_shops(configured):
    _, service, admin, _, calls, _ = configured
    from xingxu.youzan.schemas import YouzanInput
    service.save(admin['user'], YouzanInput(account='fixture', kdt_id='999'))
    async def scenario():
        await service.start(admin['user'], 'connect', admin['access_token'])
        await service.tasks[owner_key(admin['user'])]
    asyncio.run(scenario())
    result = service.summary(admin['user'])
    assert result['job']['code'] == 'all_ready' and result['kdt_id'] == ''
    assert [shop['id'] for shop in result['shops']] == ['123']
    assert result['shop_sessions'][0]['ready'] and calls['crm'] == 1
