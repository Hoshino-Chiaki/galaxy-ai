"""Preserve selected upstream diagnostics without returning auth/config stderr."""
import re

from .registry import ToolError


def safe_text(value, secrets=()):
    text = value if isinstance(value, str) else ''
    for secret in secrets:
        if isinstance(secret, str) and secret:
            text = text.replace(secret, '[已隐藏凭证]')
    text = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [已隐藏凭证]', text)
    text = re.sub(r'''(?i)\b(access_token|refresh_token|app_secret|client_secret|authorization)\b["']?\s*[:=]\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}]+)''',
                  r'\1=[已隐藏凭证]', text)
    return ''.join(c for c in text if c in '\n\t' or ord(c) >= 32)[:1000]


def raise_upstream(value, *, http_status=None, secrets=()):
    nested = value.get('error') if isinstance(value, dict) else None
    nested = nested if isinstance(nested, dict) else {}
    error = nested if any(k in nested for k in ('code', 'message', 'type', 'subtype')) else value if isinstance(value, dict) else {}
    raw_code = error.get('code')
    numeric = None
    if type(raw_code) is int and raw_code > 0:
        numeric = raw_code
    elif isinstance(raw_code, str) and re.fullmatch(r'[1-9][0-9]{0,11}', raw_code):
        numeric = int(raw_code)
    message = safe_text(error.get('message') or error.get('msg') or '飞书未返回具体错误说明。', secrets)
    details = {}
    if numeric is not None:
        details['upstream_code'] = numeric
    if type(http_status) is int:
        details['http_status'] = http_status
    request_id = error.get('log_id') or error.get('request_id') or nested.get('log_id')
    if (isinstance(request_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request_id)
            and request_id not in secrets):
        details['request_id'] = request_id
    missing = error.get('missing_scopes', [])
    if isinstance(missing, list):
        missing = [s for s in missing if isinstance(s, str) and re.fullmatch(r'[a-z0-9_:.-]{1,120}', s)]
        if missing:
            details['required_scopes'] = missing
            message += ' 所需权限：' + ', '.join(missing)
    kind = error.get('subtype') or error.get('type') or 'error'
    kind = re.sub(r'[^A-Za-z0-9_-]', '', str(kind))[:80] or 'error'
    code = 'feishu_api_' + str(numeric) if numeric is not None else 'feishu_' + kind
    label = '飞书接口返回错误 ' + str(numeric) if numeric is not None else '飞书操作失败'
    raise ToolError(code, label + '：' + message, 1, details=details)
