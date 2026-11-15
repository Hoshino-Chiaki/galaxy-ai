import asyncio
import json
import subprocess
import time

import pytest

from xingxu.auth import ROLES
from xingxu.tool_context import DispatchDenied
from xingxu.tools import help_arguments
from test_tools import authorized, call


def test_gateway_canonicalizes_only_the_fixed_clock_command():
    for args in (['time', 'now'], ['time.now'], ['time', 'now', '--format', 'json']):
        assert help_arguments(json.dumps({'args': args})) == [
            'time', 'now', '--input', '{"timezone": "Asia/Shanghai"}', '--format', 'json']
    assert help_arguments(json.dumps({'args': ['time', 'help']})) == [
        'help', '--format', 'json', '--input', '{"category": "time"}']


@pytest.mark.parametrize('args', [['time', 'set'], ['time', 'now', '--shell', 'date'],
    ['time', 'now', '--input', '{"timestamp_ms":0}'], ['time', 'now', '--input', '{"timezone":"/etc/passwd"}'],
    ['time', 'now', '--input', '{"timezone":"UTC","timezone":"Asia/Shanghai"}'],
    ['time', 'now', '--input', '{}', '--input', '{}']])
def test_clock_arguments_cannot_expand_command_or_change_source(args):
    with pytest.raises(DispatchDenied):help_arguments(json.dumps({'args': args}))


def test_authenticated_clock_uses_no_business_credentials_and_records_audit(authorized, monkeypatch):
    _, context, dispatcher = authorized
    dispatcher.category_provider = lambda *_: pytest.fail('Time must not read Youzan credentials')
    dispatcher.feishu_provider = object()
    spawn = asyncio.create_subprocess_exec
    captured = []
    async def run(*args, **kwargs):
        captured.append(kwargs)
        return await spawn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', run)
    before = time.time_ns() // 1_000_000
    event = asyncio.run(dispatcher.execute(context, call(args=['time', 'now'])))
    after = time.time_ns() // 1_000_000
    assert event['status'] == 'success' and event['command'] == 'galaxy-cli time now'
    assert before <= event['result']['timestamp_ms'] <= after
    assert captured[0]['stdin'] == asyncio.subprocess.DEVNULL
    with dispatcher.audit.transaction(write=False) as state:
        assert state['records'][event['id']]['command'] == 'galaxy-cli time now'
        assert state['records'][event['id']]['user_id'] == context.user_id


def test_time_query_rejects_missing_login_and_revoked_permission(authorized, monkeypatch):
    _, context, dispatcher = authorized
    dispatcher._run = lambda *_: pytest.fail('Denied calls must not spawn CLI')
    assert asyncio.run(dispatcher.execute(None, call(args=['time', 'now'])))['status'] == 'denied'
    monkeypatch.setitem(ROLES, 'employee', [p for p in ROLES['employee'] if p != 'workspace:read'])
    event = asyncio.run(dispatcher.execute(context, call(args=['time', 'now'])))
    assert event['result']['error']['code'] == 'permission_denied'


def test_logout_while_reading_discards_the_clock_result(authorized):
    auth, context, dispatcher = authorized
    original = dispatcher._run
    async def run(*args, **kwargs):
        result = await original(*args, **kwargs)
        auth.logout(context.session_token)
        return result
    dispatcher._run = run
    event = asyncio.run(dispatcher.execute(context, call(args=['time', 'now'])))
    assert event['status'] == 'denied'
    assert event['result']['error']['code'] == 'session_expired'
    assert 'timestamp_ms' not in event['result']
