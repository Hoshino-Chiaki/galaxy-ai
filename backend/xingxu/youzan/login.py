"""Fixed Youzan login adapter; authentication behavior remains integration-owned."""
from ..auth import AuthFailure
from ..browser import OutputLimitExceeded, VirtualDisplay, run_json_process
from .constants import MESSAGES


class YouzanLoginAdapter:
    def __init__(self, root):
        self.root = root

    async def __call__(self, account, password, profile, progress):
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
            code = await run_json_process(
                (*prefix, '/usr/bin/node', str(script), '--managed-stdin'),
                {'account': account, 'password': password, 'profile': str(profile)},
                cwd=self.root, environment={**display.environment(),
                    'HOME': str(profile), 'XDG_CONFIG_HOME': str(profile / 'config'),
                    'XDG_CACHE_HOME': str(profile / 'cache')}, on_event=receive)
        except TimeoutError:
            raise AuthFailure('timeout', MESSAGES['timeout'], 504) from None
        except OutputLimitExceeded:
            raise AuthFailure('browser_or_adapter_error', MESSAGES['browser_or_adapter_error'], 502) from None
        if code == 0 and secret and final.get('authenticated') is True:
            return secret
        result = final.get('result', 'browser_or_adapter_error')
        raise AuthFailure(result if result in MESSAGES else 'browser_or_adapter_error',
                          MESSAGES.get(result, MESSAGES['browser_or_adapter_error']), 502)
