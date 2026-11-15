"""Verify restored Youzan login via the production API; print no credentials."""
import json
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
context = ssl.create_default_context(cafile=str(ROOT / 'desktop-client/electron/certs/server.crt'))
client = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
token = None
phase = 'galaxy_login'
def request(path, body=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request('https://124.222.51.137' + path,
        data=json.dumps(body).encode() if body is not None else None, headers=headers)
    for attempt in range(4):
        try:
            with client.open(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.URLError:
            if body is not None or attempt == 3:
                raise
            time.sleep(1)

try:
    lines = (ROOT / '1.txt').read_text(encoding='utf-8-sig').splitlines()
    token = request('/api/v1/auth/login', {'username': lines[0], 'password': lines[1], 'remember': False})['access_token']
    phase = 'refresh_sid'
    before = request('/api/v1/admin/youzan')
    started = request('/api/v1/admin/youzan/refresh', {'kind': 'sid'})
    deadline = time.monotonic() + 240
    previous = None
    observed_codes = []
    while True:
        current = request('/api/v1/admin/youzan')
        job = current.get('job', {})
        if job.get('code') != previous:
            previous = job.get('code')
            observed_codes.append(previous)
            print(json.dumps({'phase': phase, 'status': job.get('status'), 'code': previous}), flush=True)
        if job.get('id') == started['job']['id'] and job.get('status') != 'running':
            break
        if time.monotonic() >= deadline:
            raise TimeoutError()
        time.sleep(1)
    result = {'server': '124.222.51.137', 'job_status': job.get('status'), 'job_code': job.get('code'),
        'sid_present': current['sid']['present'], 'sid_expired': current['sid']['expired'],
        'sid_updated': current['sid'].get('updated_at') != before['sid'].get('updated_at'),
        'shops': len(current.get('shops', [])), 'crm_ready': sum(s['ready'] for s in current.get('shop_sessions', [])),
        'renewal': current.get('renewal'), 'account_and_shop_unchanged':
            current['account'] == before['account'] and current['kdt_id'] == before['kdt_id']}
    result['observed_codes'] = observed_codes
    result['sid_valid_before_check'] = before['sid']['present'] and not before['sid']['expired']
    result['original_script_sha256'] = '63004226569347014c32cdf95642a72caf0e54931bf0322563e4a3fab4fbc5e3'
    result['requires_sms'] = job.get('code') == 'sms_verification_required'
    result['verified'] = job.get('status') == 'success' and result['sid_present'] and not result['sid_expired'] and result['shops'] > 0
    if result['verified']:
        phase = 'reuse_sid'
        shops = request('/api/v1/admin/youzan/shops')
        result['valid_sid_reused'] = shops['sid'] == current['sid'] and len(shops['shops']) == result['shops']
    (ROOT / 'artifacts/youzan-login-restored-live-20261112.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result['verified']:
        raise SystemExit(1)
except (Exception,) as error:
    print(json.dumps({'verified': False, 'phase': phase, 'error_type': type(error).__name__}), flush=True)
    raise SystemExit(1) from None
finally:
    if token:
        try:
            request('/api/v1/auth/logout', {})
        except Exception:
            pass
