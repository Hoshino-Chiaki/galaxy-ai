"""Protocol-only checks; no browser or historical login script is executed."""
import asyncio
from pathlib import Path

import pytest

from xingxu.auth import AuthFailure
from xingxu.youzan.login import YouzanLoginAdapter
import xingxu.youzan.login as login_module


@pytest.mark.parametrize('outcome', ['success', 'unknown_failure', 'timeout'])
def test_youzan_adapter_keeps_protocol_and_error_mapping_inside_integration(tmp_path, monkeypatch, outcome):
    calls, progress = [], []
    script = tmp_path / 'session_research/youzan-login.cjs'
    script.parent.mkdir(parents=True)
    script.touch()
    package = tmp_path / 'session_research/runtime/node_modules/playwright-core/package.json'
    package.parent.mkdir(parents=True)
    package.write_text('{}')
    monkeypatch.setattr(login_module.VirtualDisplay, 'prefix', lambda _: ('fixture-xvfb',))
    monkeypatch.setattr(login_module.VirtualDisplay, 'environment', lambda _: {'PATH': '/usr/bin:/bin'})
    async def process(argv, payload, **kwargs):
        calls.append((argv, payload, kwargs))
        if outcome == 'timeout':raise TimeoutError()
        await kwargs['on_event']({'event': 'browser_ready'})
        await kwargs['on_event']({'event': 'managed_credentials', 'sid': 'fixture-private-sid', 'expires_at': 12345})
        await kwargs['on_event']({'result': 'session_reused' if outcome == 'success' else 'fixture-private-password',
                                  'authenticated': outcome == 'success'})
        return 0 if outcome == 'success' else 1
    monkeypatch.setattr(login_module, 'run_json_process', process)
    async def receive(code):progress.append(code)
    request = YouzanLoginAdapter(tmp_path)('fixture-account', 'fixture-private-password', tmp_path/'private/profile', receive)
    if outcome == 'success':
        assert asyncio.run(request) == {'sid': 'fixture-private-sid', 'expires_at': 12345}
    else:
        with pytest.raises(AuthFailure) as failure:asyncio.run(request)
        assert failure.value.code == ('timeout' if outcome == 'timeout' else 'browser_or_adapter_error')
        assert 'fixture-private' not in failure.value.message
    argv, payload, options = calls[0]
    assert str(script) in argv
    assert 'fixture-private-password' not in repr(argv) + repr(options['environment'])
    assert payload['profile'] == str(tmp_path/'private/profile')
    assert payload['password'] == 'fixture-private-password'
    assert progress == ([] if outcome == 'timeout' else ['browser_ready'])
