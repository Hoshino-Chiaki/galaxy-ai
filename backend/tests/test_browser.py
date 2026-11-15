"""Shared browser primitives must preserve isolation, bounds and child cleanup."""
import asyncio
import json
import os
import sys

import httpx
import pytest

from xingxu.browser import ResponseLimitExceeded, SessionLocks, open_session, read_page, run_json_process
from xingxu.browser.process import OutputLimitExceeded


def test_cookie_sessions_are_isolated_and_redirects_are_left_to_integration():
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(302, headers={'Location': 'https://outside.invalid/collect'})
    async def run():
        jar = httpx.Cookies();jar.set('session', 'fixture-private-cookie', domain='example.invalid', path='/')
        async with open_session(cookies=jar, transport=httpx.MockTransport(upstream)) as first:
            assert (await read_page(first, 'https://example.invalid/page'))[0] == 302
        async with open_session(transport=httpx.MockTransport(upstream)) as second:
            await read_page(second, 'https://example.invalid/page')
    asyncio.run(run())
    assert len(calls) == 2 and calls[0].headers['cookie'] == 'session=fixture-private-cookie'
    assert 'cookie' not in calls[1].headers


def test_page_response_size_is_bounded():
    async def run():
        async with open_session(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'x' * 17))) as client:
            with pytest.raises(ResponseLimitExceeded):
                await read_page(client, 'https://example.invalid', limit=16)
    asyncio.run(run())


def test_shared_session_lock_serializes_operations_without_retaining_secret():
    locks = SessionLocks('fixture-provider')
    secret = 'fixture-private-session'
    async def run():
        first = locks.get(secret, fallback='user-a')
        assert first is locks.get(secret, fallback='user-b')
        assert first is not locks.get('different-session', fallback='user-a')
        events = []
        async def operation(name):
            async with first:
                events.append(name + ':start');await asyncio.sleep(.01);events.append(name + ':end')
        await asyncio.gather(operation('a'), operation('b'))
        assert events == ['a:start', 'a:end', 'b:start', 'b:end']
    asyncio.run(run())
    assert secret not in repr(locks.__dict__)


def test_browser_adapter_pipe_keeps_payload_out_of_arguments_and_environment(tmp_path, monkeypatch):
    observed = [];events = [];spawn = asyncio.create_subprocess_exec
    async def capture(*argv, **kwargs):
        observed.append((argv, kwargs));return await spawn(*argv, **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', capture)
    secret = 'fixture-private-password'
    script = 'import json,sys; x=json.load(sys.stdin); print("not json"); print(json.dumps({"event":"complete","received":bool(x["password"])}))'
    async def receive(event):events.append(event)
    code = asyncio.run(run_json_process((sys.executable, '-c', script), {'password': secret},
        cwd=tmp_path, environment={'PATH': '/usr/bin:/bin'}, on_event=receive))
    assert code == 0 and events == [{'event': 'complete', 'received': True}]
    assert secret not in repr(observed)
    assert observed[0][1]['stdin'] == asyncio.subprocess.PIPE


@pytest.mark.parametrize('reason', ['timeout', 'output', 'cancel'])
def test_browser_child_is_stopped_on_timeout_output_limit_or_cancellation(tmp_path, monkeypatch, reason):
    processes = [];spawn = asyncio.create_subprocess_exec
    async def capture(*args, **kwargs):
        process = await spawn(*args, **kwargs);processes.append(process);return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', capture)
    async def receive(event):pass
    async def run():
        script = 'import time;time.sleep(30)' if reason != 'output' else 'import time;print("x"*300,flush=True);time.sleep(30)'
        task = asyncio.create_task(run_json_process((sys.executable, '-c', script), {}, cwd=tmp_path,
            environment={'PATH': '/usr/bin:/bin'}, on_event=receive, timeout=.05 if reason == 'timeout' else 10, output_limit=100))
        if reason == 'cancel':
            while not processes:await asyncio.sleep(.005)
            task.cancel()
        with pytest.raises({'timeout': TimeoutError, 'output': OutputLimitExceeded, 'cancel': asyncio.CancelledError}[reason]):await task
    asyncio.run(run())
    assert len(processes) == 1 and processes[0].returncode is not None
