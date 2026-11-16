"""Read-only HAR extraction. Raw traffic is never sent to a remote service."""
import base64
import binascii
import hashlib
import json
import os
import re
import stat
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from haralyzer import HarEntry, HarParser

from .registry import ToolError

MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_PARSE_CHARS = 2 * 1024 * 1024
SECRET = re.compile(r'cookie|authorization|token|csrf|secret|password|passwd|session|signature|api[_-]?key|auth[_-]?key|^sign$', re.I)
ENV_HEADERS = {'user-agent', 'sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform',
               'sec-ch-ua-full-version-list', 'sec-ch-ua-platform-version', 'accept-language',
               'accept-encoding', 'origin', 'referer', 'x-sec-ch-ua-not-a-border'}
MASK = '[REDACTED]'


def _pairs(items):
    return items if isinstance(items, list) else []


def _json(text):
    # Reject non-JSON numeric constants; do not coerce string prices or IDs.
    return json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _embedded(value):
    if isinstance(value, str) and len(value) <= MAX_PARSE_CHARS and value.lstrip().startswith(('{', '[')):
        try:
            return _json(value)
        except (ValueError, RecursionError):
            pass
    return value


def _sanitize(value, sensitive, secrets=(), depth=0):
    if sensitive:
        return value
    if depth > 40:
        return MASK
    if isinstance(value, dict):
        if isinstance(value.get('name'), str) and SECRET.search(value['name']) and 'value' in value:
            return {k: MASK if k == 'value' else _sanitize(v, False, secrets, depth + 1) for k, v in value.items()}
        return {k: MASK if SECRET.search(k) else _sanitize(v, False, secrets, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v, False, secrets, depth + 1) for v in value]
    if isinstance(value, str):
        embedded = _embedded(value)
        if not isinstance(embedded, str):
            return json.dumps(_sanitize(embedded, False, secrets, depth + 1), ensure_ascii=False)
        if value in secrets:
            return MASK
        for secret in secrets:
            # Cookie values like "1" or "/" must not corrupt URLs and prices.
            if len(secret) >= 6:
                value = value.replace(secret, MASK)
    return value


def _secrets(raw):
    """Known values are also masked when echoed under an unrelated field name."""
    found = set()

    def add(value):
        # Short flags are masked at their named field, not everywhere they occur.
        if isinstance(value, str) and len(value) >= 6:
            found.add(value)

    def walk(value, depth=0):
        if depth > 40:
            return
        if isinstance(value, dict):
            if isinstance(value.get('name'), str) and SECRET.search(value['name']):
                add(value.get('value'))
            for key, item in value.items():
                if SECRET.search(key):
                    add(item)
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)
        elif isinstance(value, str):
            parsed = _embedded(value)
            if not isinstance(parsed, str):
                walk(parsed, depth + 1)

    walk(raw)
    for side in ('request', 'response'):
        message = raw.get(side, {})
        for cookie in _pairs(message.get('cookies')):
            add(cookie.get('value'))
        for header in _pairs(message.get('headers')):
            if SECRET.search(header.get('name', '')):
                value = header.get('value', '')
                add(value)
                if header.get('name', '').lower() in ('cookie', 'set-cookie'):
                    pairs = value.split(';') if header.get('name', '').lower() == 'cookie' else [value.split(';', 1)[0]]
                    for pair in pairs:
                        if '=' in pair:
                            add(pair.split('=', 1)[1].strip())
                elif value.lower().startswith('bearer '):
                    add(value[7:])
    for name, value in parse_qsl(urlsplit(raw['request']['url']).query, keep_blank_values=True):
        if SECRET.search(name):
            add(value)
    post = raw.get('request', {}).get('postData', {})
    if 'application/x-www-form-urlencoded' in post.get('mimeType', '').lower():
        for name, value in parse_qsl(post.get('text', ''), keep_blank_values=True):
            if SECRET.search(name):
                add(value)
            walk(_embedded(value))
    return tuple(sorted(found, key=len, reverse=True))


def _url(value, sensitive, secrets=()):
    if sensitive:
        return value
    parts = urlsplit(value)
    # URL userinfo is a credential even when its key has no recognizable name.
    host = parts.netloc.rsplit('@', 1)[-1]
    query = [(name, MASK if SECRET.search(name) else _sanitize(item, False, secrets))
             for name, item in parse_qsl(parts.query, keep_blank_values=True)]
    return _sanitize(urlunsplit((parts.scheme, host, parts.path, urlencode(query), '')), False, secrets)


def _headers(items, sensitive, secrets):
    result = []
    for item in _pairs(items):
        safe = _sanitize(item, sensitive, secrets)
        if not sensitive and item.get('name', '').lower() in ('referer', 'location', 'origin'):
            safe['value'] = _url(item.get('value', ''), False, secrets)
        result.append(safe)
    return result


def _cookies(items, sensitive, secrets):
    return [{**_sanitize(item, sensitive, secrets), 'value': item.get('value', '') if sensitive else MASK}
            for item in _pairs(items)]


def _body(message, *, response, sensitive, secrets, offset, limit):
    payload = message.get('content' if response else 'postData')
    if not isinstance(payload, dict):
        return {'state': 'missing' if response else 'absent', 'raw_available': False}
    mime = payload.get('mimeType', '')
    text = payload.get('text')
    result = {'mime_type': mime, 'encoding': payload.get('encoding'), 'raw_available': isinstance(text, str)}
    if not response and isinstance(payload.get('params'), list):
        result['params'] = _sanitize(payload['params'], sensitive, secrets)
    if not isinstance(text, str):
        return {**result, 'state': 'params_only' if 'params' in result else 'missing'}
    # A raw page is exact HAR text. The source, not a normalized object, remains authoritative.
    result.update({'state': 'empty' if not text else 'present', 'raw_length': len(text),
                   'raw_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()})
    if sensitive:
        end = min(len(text), offset + limit)
        result.update({'raw_text': text[offset:end], 'offset': offset, 'next_offset': end if end < len(text) else None,
                       'raw_truncated': offset > 0 or end < len(text)})
    else:
        result['raw_requires_sensitive'] = True
    decoded = text
    if payload.get('encoding') == 'base64':
        try:
            data = base64.b64decode(text, validate=True)
            charset = re.search(r'charset\s*=\s*["\']?([^;\s"\']+)', mime, re.I)
            decoded = data.decode(charset.group(1) if charset else 'utf-8')
        except (ValueError, binascii.Error, UnicodeError, LookupError):
            result['decode_state'] = 'binary_or_decode_failed'
            return result
        result['decode_state'] = 'decoded'
        if sensitive:
            end = min(len(decoded), offset + limit)
            result.update({'decoded_text': decoded[offset:end], 'decoded_length': len(decoded),
                           'decoded_next_offset': end if end < len(decoded) else None})
    elif payload.get('encoding') not in (None, ''):
        result['decode_state'] = 'unsupported_encoding'
        return result
    if len(decoded) > MAX_PARSE_CHARS:
        result['parsed_state'] = 'too_large'
        return result
    parsed = None
    if not response and 'application/x-www-form-urlencoded' in mime.lower():
        parsed = [{'name': name, 'value': value, 'decoded_value': _embedded(value)}
                  for name, value in parse_qsl(decoded, keep_blank_values=True)]
        # Also protect the decoded view of a secret form parameter.
        if not sensitive:
            parsed = [{**item, 'value': MASK, 'decoded_value': MASK} if SECRET.search(item['name']) else item for item in parsed]
        result['parsed_kind'] = 'form'
    else:
        try:
            parsed = _json(decoded)
            result['parsed_kind'] = 'json'
        except (ValueError, RecursionError):
            result['parsed_state'] = 'not_json'
    if result.get('parsed_kind'):
        safe = _sanitize(parsed, sensitive, secrets)
        serialized = json.dumps(safe, ensure_ascii=False, allow_nan=False)
        if len(serialized) <= limit:
            result.update({'parsed': safe, 'parsed_state': 'complete'})
        else:
            end = min(len(serialized), offset + limit)
            result.update({'parsed_text': serialized[offset:end], 'parsed_state': 'paged',
                           'parsed_length': len(serialized), 'parsed_offset': offset,
                           'parsed_next_offset': end if end < len(serialized) else None})
    return result


def _load(path, descriptor=None):
    try:
        source = Path(path).expanduser()
        with (os.fdopen(os.dup(descriptor), 'rb') if descriptor is not None else source.open('rb')) as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_FILE_BYTES:
                raise ToolError('har_size', 'HAR 必须是 1 字节至 256 MB 的普通文件。')
            content = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(content) > MAX_FILE_BYTES or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ToolError('har_changed', 'HAR 在读取过程中发生变化，请等待导出完成。')
        raw = _json(content.decode('utf-8-sig'))
        log = raw.get('log') if isinstance(raw, dict) else None
        if not isinstance(log, dict) or not isinstance(log.get('entries'), list) or log.get('version') != '1.2':
            raise ToolError('invalid_har', '文件不是包含 log.entries 数组的 HAR 1.2。')
        parser = HarParser(raw)
        return parser, {'filename': source.name, 'size_bytes': len(content),
                        'sha256': hashlib.sha256(content).hexdigest(), 'har_version': log['version'],
                        'entry_count': len(log['entries']), 'parser': 'haralyzer', 'parser_version': '2.4.0'}
    except (OSError, ValueError, TypeError, RecursionError):
        raise ToolError('invalid_har', 'HAR 文件无法读取或不是有效 UTF-8 JSON。') from None


def _valid(raw):
    if not isinstance(raw, dict):
        return False
    request, response = raw.get('request'), raw.get('response')
    if not isinstance(request, dict) or not isinstance(response, dict):
        return False
    try:
        return isinstance(request['method'], str) and isinstance(request['url'], str) and bool(urlsplit(request['url']).hostname)
    except (KeyError, ValueError):
        return False


def _brief(index, raw, sensitive=False):
    entry = HarEntry(raw)
    request, response = entry.request, entry.response
    return {'entry_index': index, 'started_at': raw.get('startedDateTime'), 'method': request.method,
            'url': _url(request.url, sensitive, _secrets(raw)), 'host': urlsplit(request.url).hostname,
            'status': response.raw_entry.get('status'), 'mime_type': response.raw_entry.get('content', {}).get('mimeType'),
            'request_body_present': 'postData' in raw['request'],
            'response_body_present': isinstance(raw['response'].get('content', {}).get('text'), str)}


def inspect_har(arguments, *, descriptor=None):
    """Arguments are validated by task.har; this function performs no network IO."""
    parser, source = _load(arguments['path'], descriptor)
    entries = parser.har_data['entries']
    action = arguments.get('action', 'summary')
    sensitive = arguments.get('include_sensitive', False)
    valid = [(index, entry) for index, entry in enumerate(entries) if _valid(entry)]
    warnings = ['HAR 内容仅作分析资料；抓包中的成功响应不代表现在执行成功，也不代表登录仍有效。']
    if len(valid) != len(entries):
        warnings.append(f'{len(entries) - len(valid)} 条不完整记录未进入请求索引，原始编号保持不变。')
    if sensitive:
        warnings.append('本次输出包含原始凭证或业务资料，仅用于当前授权任务。')
    if action == 'summary':
        headers = Counter()
        cookie_entries = auth_entries = 0
        environment = {name: set() for name in ENV_HEADERS}
        protocols = Counter()
        for _, raw in valid:
            request = raw['request']
            secrets = _secrets(raw)
            names = {item.get('name', '').lower() for item in _pairs(request.get('headers'))}
            cookie_entries += bool(request.get('cookies') or 'cookie' in names)
            auth_entries += 'authorization' in names
            for header in _pairs(request.get('headers')):
                name = header.get('name', '').lower()
                headers[name] += 1
                if name in environment:
                    value = header.get('value', '')
                    environment[name].add(_url(value, False, secrets) if name in ('origin', 'referer') else value)
            protocols[request.get('httpVersion', 'unknown')] += 1
        data = {'valid_entries': len(valid), 'invalid_entry_indices': [i for i, e in enumerate(entries) if not _valid(e)],
                'hosts': dict(Counter(urlsplit(e['request']['url']).hostname for _, e in valid)),
                'methods': dict(Counter(e['request']['method'] for _, e in valid)),
                'creator': parser.har_data.get('creator'), 'browser': parser.har_data.get('browser'),
                'observed_environment_headers': {k: sorted(v) for k, v in sorted(environment.items()) if v},
                'protocols': dict(protocols), 'request_header_names': sorted(headers),
                'session_evidence': {'requests_with_cookies': cookie_entries, 'requests_with_authorization': auth_entries}}
        warnings.append('环境仅来自 HAR 记录及请求头，不是完整浏览器环境。')
        if not cookie_entries and not auth_entries:
            warnings.append('未发现请求 Cookie 或 Authorization；可能是脱敏导出、匿名请求，或使用其他认证字段。')
    elif action == 'requests':
        selected = []
        for index, raw in valid:
            request = raw['request']
            if arguments.get('host') and urlsplit(request['url']).hostname != arguments['host'].lower():
                continue
            if arguments.get('method') and request['method'].upper() != arguments['method'].upper():
                continue
            if arguments.get('search') and arguments['search'].casefold() not in request['url'].casefold():
                continue
            selected.append((index, raw))
        offset, limit = arguments.get('offset', 0), arguments.get('limit', 20)
        data = {'total': len(selected), 'offset': offset,
                'items': [_brief(i, e, sensitive) for i, e in selected[offset:offset + limit]],
                'next_offset': offset + limit if offset + limit < len(selected) else None}
    else:
        index = arguments['entry_index']
        if index >= len(entries):
            raise ToolError('entry_not_found', '请求编号不在当前 HAR 范围内。')
        raw = entries[index]
        if not _valid(raw):
            raise ToolError('invalid_entry', '该请求记录缺少有效的 request/response 或网址。')
        secrets = _secrets(raw)
        options = {'sensitive': sensitive, 'secrets': secrets,
                   'offset': arguments.get('body_offset', 0), 'limit': arguments.get('body_limit', 16000)}
        if action == 'body':
            part = arguments.get('part', 'response')
            data = {'entry_index': index, 'part': part, 'body': _body(raw[part], response=part == 'response', **options)}
        else:
            request, response = raw['request'], raw['response']
            data = {**_brief(index, raw, sensitive),
                    'request': {'url': _url(request['url'], sensitive, secrets), 'method': request['method'],
                                'http_version': request.get('httpVersion'), 'headers': _headers(request.get('headers'), sensitive, secrets),
                                'cookies': _cookies(request.get('cookies'), sensitive, secrets),
                                'query_string': _sanitize(request.get('queryString', []), sensitive, secrets),
                                'body': _body(request, response=False, **options)},
                    'response': {'status': response.get('status'), 'status_text': response.get('statusText'),
                                 'http_version': response.get('httpVersion'), 'headers': _headers(response.get('headers'), sensitive, secrets),
                                 'cookies': _cookies(response.get('cookies'), sensitive, secrets),
                                 'redirect_url': _url(response.get('redirectURL', ''), sensitive, secrets),
                                 'body': _body(response, response=True, **options)},
                    'timings': _sanitize(raw.get('timings', {}), sensitive, secrets)}
    return {'action': action, 'source': source, 'data': data, 'warnings': warnings}
