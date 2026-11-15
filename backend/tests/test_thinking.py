import json
import datetime as dt
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from xingxu.app import create_app
from xingxu.ai import ProviderInput


@pytest.fixture
def setup(tmp_path):
    requests = []
    def upstream(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '已完成'}, 'finish_reason': 'stop'}]})
    app = create_app(tmp_path, ai_transport=httpx.MockTransport(upstream))
    app.state.auth.import_accounts([('admin', 'test-admin-password'), ('employee', 'test-employee-password')])
    admin = app.state.auth.login('admin', 'test-admin-password', False, 'localhost')
    employee = app.state.auth.login('employee', 'test-employee-password', False, 'localhost')
    app.state.ai.save_provider(ProviderInput(name='DeepSeek', model='deepseek-flash', base_url='https://fixture.invalid/v1', api_key='fixture-private-key'))
    return app, admin, employee, requests


def headers(session):
    return {'Authorization': 'Bearer ' + session['access_token']}


@pytest.mark.parametrize('thinking,effort,expected', [(True, 'low', 'low'), (True, 'high', 'high'), (True, 'max', 'max'), (False, 'max', 'none')])
def test_thinking_parameters_reach_upstream_and_do_not_modify_provider(setup, thinking, effort, expected):
    app, _, employee, requests = setup
    before = app.state.ai.config.file.read_bytes()
    with TestClient(app) as client:
        result = client.post('/api/v1/chat/send', headers=headers(employee), json={'message': '参数验证', 'thinking': thinking, 'reasoning_effort': effort})
        assert result.status_code == 200
        assert requests[-1]['thinking'] == {'type': 'enabled' if thinking else 'disabled'}
        assert requests[-1]['reasoning_effort'] == expected
        reply = result.json()['conversation']['messages'][-1]
        assert reply['thinking'] == thinking and reply['reasoning_effort'] == effort
    assert app.state.ai.config.file.read_bytes() == before


def test_invalid_thinking_values_are_rejected_before_upstream(setup):
    app, _, employee, requests = setup
    with TestClient(app) as client:
        for body in [{'thinking': 'true'}, {'reasoning_effort': 'unlimited'}, {'thinking': 1}]:
            response = client.post('/api/v1/chat/send', headers=headers(employee), json={'message': 'invalid', **body})
            assert response.status_code == 422
    assert not requests


def test_settings_are_per_turn_and_do_not_cross_accounts(setup):
    app, admin, employee, requests = setup
    with TestClient(app) as client:
        assert client.post('/api/v1/chat/send', headers=headers(admin), json={'message': 'a', 'thinking': True, 'reasoning_effort': 'max'}).status_code == 200
        assert client.post('/api/v1/chat/send', headers=headers(employee), json={'message': 'b', 'thinking': False, 'reasoning_effort': 'low'}).status_code == 200
        assert client.post('/api/v1/chat/send', headers=headers(admin), json={'message': 'c'}).status_code == 200
    assert requests[0]['reasoning_effort'] == 'max'
    assert requests[1]['reasoning_effort'] == 'none'
    assert 'thinking' not in requests[2] and 'reasoning_effort' not in requests[2]


def test_blank_output_token_setting_uses_supplier_default(setup):
    app, admin, employee, requests = setup
    provider = app.state.ai.snapshot()[0]
    with TestClient(app) as client:
        saved = client.put('/api/v1/admin/ai/providers/' + provider['id'], headers=headers(admin), json={
            'name': 'DeepSeek', 'model': 'deepseek-flash', 'base_url': 'https://fixture.invalid/v1', 'max_tokens': None})
        assert saved.status_code == 200 and saved.json()['max_tokens'] is None
        assert client.post('/api/v1/chat/send', headers=headers(employee), json={'message': '默认额度'}).status_code == 200
    assert 'max_tokens' not in requests[-1]


def test_truncated_tool_arguments_never_execute_or_become_success(setup):
    app, _, employee, _ = setup
    app.state.ai.transport = httpx.MockTransport(lambda _: httpx.Response(200, json={'choices': [{'finish_reason': 'length',
        'message': {'content': None, 'reasoning_content': 'fixture', 'tool_calls': [{'id': 'partial', 'type': 'function', 'function': {'name': 'galaxy_cli', 'arguments': '{'}}]}}]}))
    with TestClient(app) as client:
        result = client.post('/api/v1/chat/send', headers=headers(employee), json={'message': '截断验证'})
        assert result.status_code == 502 and result.json()['error']['code'] == 'output_truncated'
        assert 'fixture-private-key' not in result.text
        assert client.get('/api/v1/chat/conversations', headers=headers(employee)).json()['conversations'] == []
    assert not app.state.ai.dispatcher.audit.file.exists()


def test_global_prompt_date_variables_are_rendered_but_other_content_unchanged(setup):
    app, _, employee, requests = setup
    prompt = '由管理员定义策略。今天 {{today}}，昨天 {{yesterday}}，北京时间 {{current_time}}。'
    app.state.ai.set_prompt(prompt)
    today = dt.datetime.now(ZoneInfo('Asia/Shanghai')).date()
    with TestClient(app) as client:
        assert client.post('/api/v1/chat/send', headers=headers(employee), json={'message': '日期验证'}).status_code == 200
    sent = requests[-1]['messages'][0]['content']
    assert sent.startswith('由管理员定义策略。') and '{{' not in sent
    assert today.isoformat() in sent and (today - dt.timedelta(days=1)).isoformat() in sent
    assert app.state.ai.settings()['global_prompt'] == prompt


def test_single_model_batch_can_exceed_four_calls(setup):
    app, _, employee, _ = setup
    requests = []
    def upstream(request):
        payload = json.loads(request.content); requests.append(payload)
        message = {'content': 'done'} if len(requests) > 1 else {'content': None, 'tool_calls': [
            {'id': 'help-' + str(i), 'type': 'function', 'function': {'name': 'galaxy_cli', 'arguments': '{"args":["help"]}'}} for i in range(7)]}
        return httpx.Response(200, json={'choices': [{'message': message}]})
    app.state.ai.transport = httpx.MockTransport(upstream)
    with TestClient(app) as client:
        response = client.post('/api/v1/chat/send', headers=headers(employee), json={'message': '多调用验证'})
        assert response.status_code == 200
        events = response.json()['conversation']['messages'][-1]['tool_events']
        assert len(events) == 7 and all(e['status'] == 'success' for e in events)
