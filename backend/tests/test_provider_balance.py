import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
from xingxu.app import create_app
from xingxu.ai import ProviderInput
from xingxu.auth import AuthFailure
from xingxu.provider_balance import deepseek_balance


def test_official_balance_and_permissions(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    requests = []
    def upstream(request):
        requests.append(request)
        return httpx.Response(200, json={'is_available': True, 'balance_infos': [{'currency': 'CNY',
            'total_balance': '100.123456', 'granted_balance': '0.123456', 'topped_up_balance': '100.00'}]})
    app = create_app(tmp_path, ai_transport=httpx.MockTransport(upstream))
    app.state.auth.import_accounts([('owner', 'fixture-password'), ('employee', 'fixture-password')])
    provider = app.state.ai.save_provider(ProviderInput(name='DeepSeek', base_url='https://api.deepseek.com/v1', api_key='fixture-secret', model='deepseek-chat'))
    identifier = provider['id']
    client = TestClient(app)
    def headers(name):
        token = app.state.auth.login(name, 'fixture-password', False, 'test')['access_token']
        return {'Authorization': 'Bearer ' + token}
    route = f'/api/v1/admin/ai/providers/{identifier}/balance'
    assert client.get(route, headers=headers('employee')).status_code == 403
    assert not requests
    result = client.get(route, headers=headers('owner'))
    assert result.status_code == 200 and result.json()['balance_infos'][0]['total_balance'] == '100.123456'
    assert str(requests[0].url) == 'https://api.deepseek.com/user/balance'
    assert requests[0].headers['authorization'] == 'Bearer fixture-secret'
    assert 'fixture-secret' not in result.text


def test_proxy_provider_does_not_send_key_to_official_endpoint(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    def upstream(_):
        raise AssertionError('No network request expected')
    app = create_app(tmp_path, ai_transport=httpx.MockTransport(upstream))
    provider = app.state.ai.save_provider(ProviderInput(name='DeepSeek relay', base_url='https://relay.example/v1', api_key='fixture-secret', model='deepseek-chat'))
    with pytest.raises(AuthFailure) as error:
        asyncio.run(deepseek_balance(app.state.ai, provider['id']))
    assert error.value.code == 'balance_unsupported'


@pytest.mark.parametrize('payload', [None, {'is_available': True, 'balance_infos': [{'currency': 'CNY', 'total_balance': 'NaN'}]}])
def test_malformed_upstream_is_not_reported_as_zero(tmp_path, monkeypatch, payload):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    app = create_app(tmp_path, ai_transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    provider = app.state.ai.save_provider(ProviderInput(name='DeepSeek', base_url='https://api.deepseek.com', api_key='fixture-secret', model='deepseek-chat'))
    with pytest.raises(AuthFailure):
        asyncio.run(deepseek_balance(app.state.ai, provider['id']))
