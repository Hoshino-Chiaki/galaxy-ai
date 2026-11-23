import time

from fastapi.testclient import TestClient

from test_youzan_renewal import configured


def wait_for(client, headers, predicate):
    for _ in range(300):
        state = client.get('/api/v1/admin/youzan', headers=headers).json()
        if predicate(state):
            return state
        time.sleep(.01)
    raise AssertionError('Verification did not reach the expected state')


def test_interactive_sms_retries_and_continues_same_connection(configured):
    app, service, admin, employee, calls, _ = configured
    headers = {'Authorization': 'Bearer ' + admin['access_token']}
    other = {'Authorization': 'Bearer ' + employee['access_token']}
    url = '/api/v1/admin/youzan'
    received = []

    async def browser(*args, interactive):
        received.append(await interactive({'kind': 'send'}))
        received.append(await interactive({'kind': 'code'}))
        received.append(await interactive({'kind': 'code', 'rejected': True}))
        return {'sid': 'fixture-private-sid', 'expires_at': time.time() + 3600}

    service.login_runner = browser
    service.supports_interactive_verification = True
    with TestClient(app) as client:
        response = client.put(url, headers=headers, json={
            'account': 'fixture', 'interactive_verification': True,
        })
        job_id = response.json()['job']['id']
        send = wait_for(client, headers, lambda state: state.get('verification'))['verification']
        assert send['kind'] == 'send'
        send_input = {'id': send['id'], 'kind': 'send'}
        assert client.post(url + '/verification', headers=other, json=send_input).status_code == 409
        assert client.post(url + '/verification', headers=headers, json=send_input).status_code == 202
        assert client.post(url + '/verification', headers=headers, json=send_input).status_code == 409
        first = wait_for(client, headers, lambda state: state.get('verification', {}).get('kind') == 'code')['verification']
        action = {'id': first['id'], 'kind': 'code', 'code': '012345'}
        assert client.post(url + '/verification', headers=headers, json=action | {'code': '12'}).status_code == 422
        assert client.post(url + '/verification', headers=headers, json=action).status_code == 202
        retry = wait_for(client, headers, lambda state: state.get('verification', {}).get('rejected'))['verification']
        assert retry['id'] != first['id']
        assert client.post(url + '/verification', headers=headers, json=action | {'id': retry['id']}).status_code == 202
        done = wait_for(client, headers, lambda state: state['job']['status'] != 'running')
        assert done['job']['id'] == job_id and done['job']['code'] == 'all_ready'
        assert done['sid']['present'] and done['shop_sessions'][0]['ready']
        assert 'verification' not in done and '012345' not in str(done)
    assert len(received) == 3 and calls['crm'] == 1


def test_cancel_discards_pending_verification(configured):
    app, service, admin, _, _, _ = configured
    headers = {'Authorization': 'Bearer ' + admin['access_token']}
    url = '/api/v1/admin/youzan'

    async def browser(*args, interactive):
        await interactive({'kind': 'code'})
        raise AssertionError('Cancelled browser must not resume')

    service.login_runner = browser
    service.supports_interactive_verification = True
    with TestClient(app) as client:
        assert client.put(url, headers=headers, json={
            'account': 'fixture', 'interactive_verification': True,
        }).status_code == 200
        prompt = wait_for(client, headers, lambda state: state.get('verification'))['verification']
        result = client.post(url + '/cancel', headers=headers).json()
        assert result['job']['code'] == 'cancelled' and 'verification' not in result
        assert client.post(url + '/verification', headers=headers, json={
            'id': prompt['id'], 'kind': 'code', 'code': '012345',
        }).status_code == 409
