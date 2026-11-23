"""Fixed Youzan login adapter; authentication behavior remains integration-owned."""
import asyncio
import fcntl
import os
import base64
import math
from ..account_storage import enabled as account_files, read_json, atomic_json
from ..auth import AuthFailure
from ..browser import OutputLimitExceeded, VirtualDisplay, run_json_process
from .constants import MESSAGES


class YouzanLoginAdapter:
    def __init__(self, root):
        self.root = root

    async def __call__(self, account, password, profile, progress, *, interactive=None):
        # Every channel for this Galaxy account must use this one profile lease.
        if not account_files(self.root / 'backend/private'):
            return await self._run(account, password, profile, progress, interactive=interactive)
        fd = os.open(profile / '.galaxy-profile.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            async with asyncio.timeout(500 if interactive else 180):
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        await asyncio.sleep(.1)
                return await self._run(account, password, profile, progress, managed_profile=True, interactive=interactive)
        except TimeoutError:
            raise AuthFailure('timeout', MESSAGES['timeout'], 504) from None
        finally:
            os.close(fd)

    async def _slider_action(self, event):
        payload = event.get('encoder_input')
        fields = {'background', 'piece', 'raw_cy', 'display_width', 'start_x', 'start_y', 'random_string'}
        def fail():
            return AuthFailure('browser_or_adapter_error', MESSAGES['browser_or_adapter_error'], 502)
        if not isinstance(payload, dict) or set(payload) != fields:
            raise fail()
        for field in ('background', 'piece'):
            value = payload[field]
            if not isinstance(value, str) or not value or len(value) > 270_000:
                raise fail()
            try:
                base64.b64decode(value, validate=True)
            except ValueError:
                raise fail() from None
        for field in ('raw_cy', 'display_width', 'start_x', 'start_y'):
            value = payload[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 2000:
                raise fail()
        if not payload['display_width'] or not isinstance(payload['random_string'], str) or not 1 <= len(payload['random_string']) <= 1024:
            raise fail()
        python = self.root / 'investigation/youzan-slider/.venv/bin/python'
        script = self.root / 'session_research/encode_youzan_human_request.py'
        if not python.is_file() or not script.is_file():
            raise AuthFailure('login_runtime_unavailable', MESSAGES['login_runtime_unavailable'], 503)
        result = {}
        async def receive(value):
            result.update(value)
        code = await run_json_process((str(python), str(script)), payload,
            cwd=self.root, environment=os.environ.copy(), on_event=receive,
            timeout=25, output_limit=128 * 1024, line_limit=128 * 1024)
        points = result.get('pointer_points')
        summary = result.get('summary')
        release = summary.get('release_delay_ms') if isinstance(summary, dict) else None
        if code or result.get('error_type') or not isinstance(points, list) or not 2 <= len(points) <= 512:
            raise fail()
        previous = 0
        for point in points:
            if not isinstance(point, dict) or set(point) != {'x', 'y', 'ms'} or any(type(point[k]) is not int for k in point):
                raise fail()
            if abs(point['x']) > 500 or abs(point['y']) > 400 or not previous <= point['ms'] <= 10000:
                raise fail()
            previous = point['ms']
        if type(release) is not int or not 0 <= release <= 1000:
            raise fail()
        return {'kind': 'slider', 'points': points, 'release_delay_ms': release}

    async def _run(self, account, password, profile, progress, managed_profile=False, interactive=None):
        display = VirtualDisplay(self.root / 'backend/runtime/xvfb/usr')
        try:
            prefix = display.prefix()
        except FileNotFoundError:
            raise AuthFailure('background_unavailable', MESSAGES['background_unavailable'], 503) from None
        script = self.root / 'session_research/youzan-login.cjs'
        if not script.is_file() or not (self.root / 'session_research/runtime/node_modules/playwright-core/package.json').is_file():
            raise AuthFailure('login_runtime_unavailable', MESSAGES['login_runtime_unavailable'], 503)
        secret, final = None, {}

        async def receive(event):
            nonlocal secret, final
            if event.get('event') == 'managed_credentials':
                secret = {'sid': event.get('sid'), 'expires_at': event.get('expires_at')}
            elif event.get('event'):
                await progress(event['event'])
            elif 'result' in event:
                final = event

        try:
            environment = {**display.environment(), 'HOME': str(profile),
                           'XDG_CONFIG_HOME': str(profile / 'config'), 'XDG_CACHE_HOME': str(profile / 'cache')}
            if managed_profile:
                bindings_file = profile / '.channel-bindings.json'
                bindings = read_json(bindings_file, {})
                if bindings.get('youzan') and bindings['youzan'] != account:
                    reset = self.root / 'backend/youzan/reset-channel.cjs'
                    code = await run_json_process((*prefix, '/usr/bin/node', str(reset)),
                        {'profile': str(profile)}, cwd=self.root, environment=environment, on_event=receive)
                    if code:
                        raise AuthFailure('browser_or_adapter_error', MESSAGES['browser_or_adapter_error'], 502)
                if bindings.get('youzan') != account:
                    atomic_json(bindings_file, {**bindings, 'youzan': account})
            async def respond(event):
                if event.get('kind') == 'slider':
                    action = await self._slider_action(event)
                    await progress('slider_payload_ready')
                    return action
                return await interactive(event)
            options = {'interactive': respond, 'timeout': 480, 'output_limit': 2 * 1024**2,
                       'line_limit': 1024 * 1024} if interactive else {}
            code = await run_json_process(
                (*prefix, '/usr/bin/node', str(script), '--managed-interactive' if interactive else '--managed-stdin'),
                {'account': account, 'password': password, 'profile': str(profile)},
                cwd=self.root, environment=environment, on_event=receive, **options)
        except TimeoutError:
            raise AuthFailure('timeout', MESSAGES['timeout'], 504) from None
        except OutputLimitExceeded:
            raise AuthFailure('browser_or_adapter_error', MESSAGES['browser_or_adapter_error'], 502) from None
        if code == 0 and secret and final.get('authenticated') is True:
            return secret
        result = final.get('result', 'browser_or_adapter_error')
        raise AuthFailure(result if result in MESSAGES else 'browser_or_adapter_error',
                          MESSAGES.get(result, MESSAGES['browser_or_adapter_error']), 502)
