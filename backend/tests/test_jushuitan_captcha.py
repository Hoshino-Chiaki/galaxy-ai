import base64
import json
import time
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from test_ai import setup
from test_jushuitan import SESSION
from xingxu.auth import AuthFailure
from xingxu.jushuitan.captcha import Captchas, image_url
from xingxu.jushuitan.service import CAPTCHA, KEY, LOGIN, owner

GIF = base64.b64encode(b'GIF89a' + b'fixture-image').decode()


def upstream(request):
    assert request.url.host == 'account.scm121.com'
    assert request.headers['source'] == 'ACCOUNT'
    if request.url.path == httpx.URL(CAPTCHA).path:
        assert request.method == 'GET' and request.url.params['loginName'] == 'own-account'
        return httpx.Response(200, headers={'Set-Cookie': 'challenge_cookie=fixture; Secure; Path=/'},
            json={'success': True, 'code': '0000', 'data': {'img': GIF, 'preToken': 'private-challenge-token'}})
    if request.url.path == httpx.URL(KEY).path:
        return httpx.Response(200, json={'success': True, 'code': '0000', 'data': '0123456789abcdef'})
    assert request.url.path == httpx.URL(LOGIN).path
    body = json.loads(request.content)
    assert body['fromBu'] == 'jst-gyl-pc'
    if 'verifyCode' not in body:
        return httpx.Response(200, json={'success': False, 'code': '20001', 'message': '图形验证码不能为空！'})
    assert body['verifyCode'] == 'fixture123'
    assert body['preToken'] == 'private-challenge-token'
    assert 'challenge_cookie=fixture' in request.headers.get('cookie', '')
    return httpx.Response(200, json={'success': True, 'code': '0000', 'data': SESSION})


def test_login_returns_image_then_submits_user_entered_answer_and_saves_session(setup):
    app, client, _, employee, _ = setup
    service = app.state.jushuitan
    requests = []
    def capture(request):requests.append(request);return upstream(request)
    service.transport = httpx.MockTransport(capture)
    first = client.post('/api/v1/jushuitan/login', headers=employee, json={'account': 'own-account', 'password': 'fixture-password'})
    assert first.status_code == 200 and first.json()['captcha_required']
    captcha = first.json()['captcha']
    assert captcha['image'].startswith('data:image/gif;base64,')
    assert set(captcha) == {'id', 'image', 'expires_at'}
    assert 'private-challenge-token' not in first.text and 'fixture-password' not in first.text
    assert client.get('/api/v1/jushuitan', headers=employee).json()['connected'] is False
    second = client.post('/api/v1/jushuitan/login', headers=employee, json={'account': 'own-account', 'password': 'fixture-password',
        'challenge_id': captcha['id'], 'verify_code': 'fixture123'})
    assert second.status_code == 200 and second.json()['connected']
    assert sum(r.url.path == httpx.URL(LOGIN).path for r in requests) == 2
    assert not service.captchas.pending
    replay = client.post('/api/v1/jushuitan/login', headers=employee, json={'account': 'own-account',
        'challenge_id': captcha['id'], 'verify_code': 'fixture123'})
    assert replay.status_code == 409 and replay.json()['error']['code'] == 'jushuitan_captcha_expired'


def test_refresh_invalidates_previous_and_other_employee_cannot_consume(setup):
    app, client, admin, employee, _ = setup
    app.state.jushuitan.transport = httpx.MockTransport(upstream)
    first = client.post('/api/v1/jushuitan/captcha', headers=employee, json={'account': 'own-account'}).json()
    second = client.post('/api/v1/jushuitan/captcha', headers=employee, json={'account': 'own-account'}).json()
    for headers, identifier, account in [(employee, first['id'], 'own-account'), (admin, second['id'], 'own-account'), (employee, second['id'], 'different-account')]:
        result = client.post('/api/v1/jushuitan/login', headers=headers, json={'account': account,
            'challenge_id': identifier, 'verify_code': 'fixture123'})
        assert result.status_code == 409
    result = client.post('/api/v1/jushuitan/login', headers=employee, json={'account': 'own-account', 'password': '',
        'challenge_id': second['id'], 'verify_code': 'fixture123'})
    assert result.status_code == 200 and result.json()['connected']


def test_expired_challenge_and_image_content_are_validated():
    store = Captchas()
    value = store.issue('owner-a', 'account', {'img': GIF, 'preToken': 'secret'}, {})
    store.pending['owner-a']['expires_at'] = time.time() - 1
    with pytest.raises(AuthFailure):store.consume('owner-a', 'account', value['id'])
    for encoded in ['<svg/>', base64.b64encode(b'<svg onload="alert(1)"/>').decode(), 'invalid-base64', 'A' * (2*1024*1024+1)]:
        with pytest.raises(ValueError):image_url(encoded)
    assert image_url('data:image/gif;base64,' + GIF).startswith('data:image/gif;base64,')


def test_challenge_error_preserves_original_connection_and_can_refresh(setup):
    app, client, _, employee, _ = setup
    service = app.state.jushuitan
    service.login_runner = AsyncMock(return_value=SESSION)
    assert client.post('/api/v1/jushuitan/login', headers=employee, json={'account': 'own-account', 'password': 'saved'}).status_code == 200
    service.login_runner = service.login
    service.transport = httpx.MockTransport(upstream)
    before = service.record(app.state.auth.identity(employee['Authorization'][7:])['user'])
    def wrong(request):
        if request.url.path == httpx.URL(LOGIN).path:
            return httpx.Response(200, json={'success': False, 'code': '20001', 'message': '图形验证码错误！'})
        return upstream(request)
    service.transport = httpx.MockTransport(wrong)
    captcha = client.post('/api/v1/jushuitan/captcha', headers=employee, json={'account': 'own-account'}).json()
    result = client.post('/api/v1/jushuitan/login', headers=employee, json={'account': 'own-account',
        'challenge_id': captcha['id'], 'verify_code': 'wronganswer'})
    assert result.status_code == 200 and result.json()['captcha_required']
    assert result.json()['captcha']['id'] != captcha['id']
    assert service.record(app.state.auth.identity(employee['Authorization'][7:])['user']) == before


def test_captcha_fetch_requires_login_and_rejects_foreign_fields_and_partial_answers(setup):
    _, client, _, employee, _ = setup
    assert client.post('/api/v1/jushuitan/captcha', json={'account': 'own-account'}).status_code == 401
    assert client.post('/api/v1/jushuitan/captcha', headers=employee, json={'account': 'own-account', 'user_id': 'other'}).status_code == 422
    for fields in [{'challenge_id': str(uuid.uuid4())}, {'verify_code': 'a'}, {'challenge_id': str(uuid.uuid4()), 'verify_code': '\n'}]:
        result = client.post('/api/v1/jushuitan/login', headers=employee, json={'account': 'own-account', **fields})
        assert result.status_code == 422


def test_new_challenge_does_not_reuse_any_previous_cookies(setup):
    app, client, _, employee, _ = setup
    requests = []
    def capture(request):requests.append(request);return upstream(request)
    app.state.jushuitan.transport = httpx.MockTransport(capture)
    for _ in range(2):
        assert client.post('/api/v1/jushuitan/captcha', headers=employee, json={'account': 'own-account'}).status_code == 200
    assert all('cookie' not in request.headers for request in requests)
