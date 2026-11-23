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


def test_interactive_slider_uses_encoder_and_keeps_sms_input_interactive(tmp_path, monkeypatch):
    import base64
    script = tmp_path / 'session_research/youzan-login.cjs'
    script.parent.mkdir(parents=True);script.touch()
    package = tmp_path / 'session_research/runtime/node_modules/playwright-core/package.json'
    package.parent.mkdir(parents=True);package.write_text('{}')
    encoder = tmp_path / 'session_research/encode_youzan_human_request.py';encoder.touch()
    python = tmp_path / 'investigation/youzan-slider/.venv/bin/python'
    python.parent.mkdir(parents=True);python.touch()
    payload = {'background': base64.b64encode(b'fixture-background').decode(),
        'piece': base64.b64encode(b'fixture-piece').decode(), 'raw_cy': 90,
        'display_width': 280, 'start_x': 30, 'start_y': 200, 'random_string': 'fixture-private-random'}
    points = [{'x': 0, 'y': 0, 'ms': 0}, {'x': 150, 'y': 1, 'ms': 1000}]
    calls, ui, progress = [], [], []
    monkeypatch.setattr(login_module.VirtualDisplay, 'prefix', lambda _: ('fixture-xvfb',))
    monkeypatch.setattr(login_module.VirtualDisplay, 'environment', lambda _: {'PATH': '/usr/bin:/bin'})
    async def process(argv, body, **options):
        calls.append((argv, body))
        if str(encoder) in argv:
            assert body == payload
            await options['on_event']({'pointer_points': points, 'summary': {'release_delay_ms': 25},
                'userBehaviorData': 'fixture-private-encoded'})
            return 0
        callback = options['interactive']
        action = await callback({'event': 'verification_input', 'kind': 'slider',
            'image': 'fixture-image', 'encoder_input': payload})
        assert action == {'kind': 'slider', 'points': points, 'release_delay_ms': 25}
        assert await callback({'kind': 'send'}) == {'kind': 'send'}
        assert await callback({'kind': 'code'}) == {'kind': 'code', 'code': '012345'}
        await options['on_event']({'event': 'managed_credentials', 'sid': 'fixture-private-sid', 'expires_at': 12345})
        await options['on_event']({'result': 'login_success_sid_saved', 'authenticated': True})
        return 0
    async def interactive(event):
        ui.append(event['kind']);return {'kind': event['kind'], **({'code': '012345'} if event['kind']=='code' else {})}
    async def receive(code):progress.append(code)
    monkeypatch.setattr(login_module, 'run_json_process', process)
    result = asyncio.run(YouzanLoginAdapter(tmp_path)('fixture', 'fixture-private-password',
        tmp_path/'private/profile', receive, interactive=interactive))
    assert result['sid'] == 'fixture-private-sid'
    assert ui == ['send', 'code'] and progress == ['slider_payload_ready']
    assert len(calls) == 2 and '--managed-interactive' in calls[0][0]
    assert str(python) == calls[1][0][0] and str(encoder) == calls[1][0][1]
    assert 'fixture-private' not in repr([argv for argv, _ in calls])


@pytest.mark.parametrize('invalid', ['missing', 'extra', 'base64', 'number', 'encoder_failed', 'backwards_points'])
def test_slider_encoder_rejects_incomplete_input_or_invalid_output(tmp_path, monkeypatch, invalid):
    import base64
    encoder = tmp_path/'session_research/encode_youzan_human_request.py'
    encoder.parent.mkdir(parents=True);encoder.touch()
    python = tmp_path/'investigation/youzan-slider/.venv/bin/python'
    python.parent.mkdir(parents=True);python.touch()
    payload = {'background': base64.b64encode(b'image').decode(), 'piece': base64.b64encode(b'image').decode(),
        'raw_cy': 50, 'display_width': 280, 'start_x': 30, 'start_y': 200, 'random_string': 'fixture-private-random'}
    if invalid=='missing':payload.pop('piece')
    elif invalid=='extra':payload['script']='/outside/private'
    elif invalid=='base64':payload['background']='!bad!'
    elif invalid=='number':payload['display_width']=float('nan')
    invoked=[]
    async def process(argv, body, **options):
        invoked.append(argv)
        if invalid=='encoder_failed':
            await options['on_event']({'error_type': 'fixture-private-failure'});return 1
        await options['on_event']({'pointer_points': [{'x':0,'y':0,'ms':10},{'x':10,'y':0,'ms':5}],
            'summary':{'release_delay_ms':10}});return 0
    monkeypatch.setattr(login_module,'run_json_process',process)
    with pytest.raises(AuthFailure) as error:
        asyncio.run(YouzanLoginAdapter(tmp_path)._slider_action({'kind':'slider','encoder_input':payload}))
    assert error.value.code=='browser_or_adapter_error' and 'fixture-private' not in error.value.message
    assert bool(invoked)==(invalid in ['encoder_failed','backwards_points'])


def test_slider_event_round_trip_over_real_browser_and_encoder_pipes(tmp_path, monkeypatch):
    import base64
    import json
    import os
    import sys
    script = tmp_path/'session_research/youzan-login.cjs';script.parent.mkdir(parents=True)
    payload={'background':base64.b64encode(b'fixture-bg').decode(),'piece':base64.b64encode(b'fixture-piece').decode(),
        'raw_cy':50,'display_width':280,'start_x':30,'start_y':200,'random_string':'fixture-random'}
    script.write_text('''const fs=require('fs');let line='';for(;;){const b=Buffer.alloc(1);if(!fs.readSync(0,b,0,1,null)||b[0]===10)break;line+=b.toString()}
const request=JSON.parse(line);const lines=require('readline').createInterface({input:process.stdin,crlfDelay:Infinity})[Symbol.asyncIterator]();
(async()=>{console.log(JSON.stringify(EVENT));const a=JSON.parse((await lines.next()).value);
if(a.kind!=='slider'||a.points.at(-1).x!==140||a.release_delay_ms!==20)process.exit(7);
console.log(JSON.stringify({event:'managed_credentials',sid:'fixture-private-sid',expires_at:12345}));
console.log(JSON.stringify({result:'login_success_sid_saved',authenticated:true}));process.exit(0)})();'''.replace('EVENT',json.dumps({'event':'verification_input','kind':'slider','encoder_input':payload})))
    package=tmp_path/'session_research/runtime/node_modules/playwright-core/package.json';package.parent.mkdir(parents=True);package.write_text('{}')
    encoder=tmp_path/'session_research/encode_youzan_human_request.py'
    encoder.write_text('import json,sys\np=json.load(sys.stdin)\nassert p["display_width"]==280\nprint(json.dumps({"pointer_points":[{"x":0,"y":0,"ms":0},{"x":140,"y":0,"ms":1000}],"summary":{"release_delay_ms":20}}))\n')
    python=tmp_path/'investigation/youzan-slider/.venv/bin/python';python.parent.mkdir(parents=True);python.symlink_to(sys.executable)
    monkeypatch.setattr(login_module.VirtualDisplay,'prefix',lambda _:())
    monkeypatch.setattr(login_module.VirtualDisplay,'environment',lambda _:os.environ.copy())
    async def interactive(_):raise AssertionError('Slider must not wait for client input')
    progress=[]
    async def receive(code):progress.append(code)
    result=asyncio.run(YouzanLoginAdapter(tmp_path)('fixture','fixture-private-password',tmp_path/'profile',receive,interactive=interactive))
    assert result=={'sid':'fixture-private-sid','expires_at':12345}
    assert progress==['slider_payload_ready']
