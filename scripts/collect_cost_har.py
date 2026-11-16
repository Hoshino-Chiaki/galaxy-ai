"""Local-only HAR collection prototype. Never sends captured requests.

Raw entries are encrypted with Fernet. Its key is protected with Windows DPAPI
for the current Windows user. Public indexes contain structure, not body values.
"""
import base64
import ctypes
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import ijson
from cryptography.fernet import Fernet

SOURCE = Path('C:/Users/Administrator/Desktop/成本价')
OUTPUT = Path(__file__).resolve().parents[1] / 'artifacts' / 'har-cost-20261116'
SECRET_NAME = re.compile(r'cookie|authorization|token|csrf|secret|password|signature|session|^sign$', re.I)
COST_NAME = re.compile(r'cost|purchase.?price|成本', re.I)
ENV_HEADERS = {'user-agent', 'sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform',
    'sec-ch-ua-full-version-list', 'sec-ch-ua-platform-version', 'accept-language',
    'accept-encoding', 'origin', 'referer', 'x-sec-ch-ua-not-a-border'}


class Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(value, decrypt=False):
    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    function.restype = ctypes.c_bool
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(result.data)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def parse_embedded(value):
    if isinstance(value, str) and value.lstrip()[:1] in ('{', '['):
        try:
            return json.loads(value)
        except (ValueError, RecursionError):
            pass
    return value


def request_body(request):
    payload = request.get('postData', {})
    text = payload.get('text')
    mime = payload.get('mimeType', '').lower()
    if isinstance(text, str) and 'application/x-www-form-urlencoded' in mime:
        # Raw text and the original ordered duplicate fields remain in the vault.
        return {key: value for key, value in parse_qsl(text, keep_blank_values=True)}
    return parse_embedded(text) if text is not None else payload.get('params')


def response_body(response):
    content = response.get('content', {})
    text = content.get('text')
    if isinstance(text, str) and content.get('encoding') == 'base64':
        try:
            text = base64.b64decode(text).decode('utf-8')
        except (ValueError, UnicodeError):
            return None
    return parse_embedded(text)


def structure(value, path='', depth=0):
    """Extract field paths and types without exporting any field values."""
    if depth > 12:
        return []
    parsed = parse_embedded(value)
    if parsed is not value:
        return [{'path': path or '$', 'type': 'JSON-encoded string'}] + structure(parsed, path + '(json)', depth + 1)
    if isinstance(value, dict):
        output = []
        for name, child in value.items():
            child_path = path + '.' + str(name) if path else str(name)
            output.append({'path': child_path, 'type': type(child).__name__,
                           'sensitive_field_name': bool(SECRET_NAME.search(str(name)))})
            output.extend(structure(child, child_path, depth + 1))
        return output
    if isinstance(value, list):
        output = []
        for child in value[:3]:
            output.extend(structure(child, path + '[]', depth + 1))
        # This is a bounded structural sample; raw arrays are kept in full.
        return list({(item['path'], item['type']): item for item in output}.values())
    return []


def safe_path(path):
    path = re.sub(r'(?<=/)[0-9]{5,}(?=/|$)', '{id}', path)
    return re.sub(r'(?<=/)[a-fA-F0-9-]{24,}(?=/|$)', '{opaque}', path)


def safe_origin(value, include_path=False):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        return parsed.scheme + '://' + host + (safe_path(parsed.path) if include_path else '')
    except ValueError:
        return '[unparsed]'


def main():
    OUTPUT.mkdir(exist_ok=False)
    vault = OUTPUT / 'private'
    records = vault / 'entries'
    records.mkdir(parents=True)
    key = Fernet.generate_key()
    protected = dpapi(key)
    (vault / 'key.dpapi').write_bytes(protected)
    assert dpapi(protected, decrypt=True) == key
    cipher = Fernet(key)
    with SOURCE.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()

    domains, methods, mime_types, availability = Counter(), Counter(), Counter(), Counter()
    environment = defaultdict(Counter)
    endpoints = defaultdict(list)
    sessions, observations, focus, started = [], [], [], []
    with SOURCE.open('rb') as stream:
        for index, entry in enumerate(ijson.items(stream, 'log.entries.item', use_float=True)):
            reference = f'private/entries/{index:06d}.json.enc'
            encoded = json.dumps(entry, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            (OUTPUT / reference).write_bytes(cipher.encrypt(encoded))
            request, response = entry.get('request', {}), entry.get('response', {})
            address = urlsplit(request.get('url', ''))
            host = address.hostname or ''
            headers = {header.get('name', '').lower(): header.get('value', '') for header in request.get('headers', [])}
            response_headers = {header.get('name', '').lower(): header.get('value', '') for header in response.get('headers', [])}
            body, result = request_body(request), response_body(response)
            body_schema, result_schema = structure(body), structure(result)
            query = parse_qsl(address.query, keep_blank_values=True)
            method = request.get('method', '')
            content = response.get('content', {})
            mime = content.get('mimeType', '').split(';')[0]
            domains[host] += 1
            methods[method] += 1
            mime_types[mime] += 1
            started.append(entry.get('startedDateTime', ''))
            availability['cookie_header'] += bool(headers.get('cookie'))
            availability['cookie_array'] += bool(request.get('cookies'))
            availability['authorization_header'] += bool(headers.get('authorization'))
            availability['response_text'] += 'text' in content
            availability['request_postdata'] += 'postData' in request
            for name in ENV_HEADERS:
                if name in headers:
                    value = headers[name]
                    if name in ('origin', 'referer'):
                        value = safe_origin(value, include_path=name == 'referer')
                    environment[name][value] += 1

            secrets = {name: value for name, value in headers.items() if SECRET_NAME.search(name)}
            query_secrets = [(name, value) for name, value in query if SECRET_NAME.search(name)]
            body_secrets = {name: value for name, value in body.items() if SECRET_NAME.search(name)} if isinstance(body, dict) else {}
            if secrets or request.get('cookies') or response.get('cookies') or response_headers.get('set-cookie') or query_secrets or body_secrets:
                sessions.append({'entry': index, 'time': entry.get('startedDateTime'), 'host': host,
                    'request_headers': secrets, 'request_cookies': request.get('cookies', []),
                    'response_cookies': response.get('cookies', []), 'set_cookie': [h for h in response.get('headers', []) if h.get('name', '').lower() == 'set-cookie'],
                    'query_credentials': query_secrets, 'body_credentials': body_secrets,
                    'raw_entry': reference})

            paths = [field for field in body_schema if COST_NAME.search(field['path'])]
            observed = {'entry': index, 'time': entry.get('startedDateTime'), 'host': host, 'method': method,
                'path': safe_path(address.path), 'http_version': request.get('httpVersion'),
                'query_fields': [name for name, _ in query], 'payload_mime': request.get('postData', {}).get('mimeType'),
                'request_structure': body_schema, 'response_mime': mime,
                'response_structure': result_schema if isinstance(result, (dict, list)) else [],
                'response_structure_array_sample_size': 3, 'response_text_present': 'text' in content,
                'http_status': response.get('status'), 'response_encoding': content.get('encoding'),
                'business_code': result.get('code') if isinstance(result, dict) and isinstance(result.get('code'), (int, float)) else None,
                'business_success': result.get('success') if isinstance(result, dict) and isinstance(result.get('success'), bool) else None,
                'cost_fields': paths, 'raw_entry': reference}
            observations.append(observed)
            if mime == 'application/json' or entry.get('_resourceType') in ('xhr', 'fetch'):
                endpoints[(host, method, safe_path(address.path))].append(index)
            if address.path.endswith('/api/youzan.item.component.update.product/1.0.0'):
                observed['field_count'] = len(body) if isinstance(body, dict) else None
                focus.append(observed)

    (vault / 'sessions.json.enc').write_bytes(cipher.encrypt(json.dumps(sessions, ensure_ascii=False).encode('utf-8')))
    catalog = [{'host': host, 'method': method, 'path': path, 'count': len(indices), 'entries': indices}
               for (host, method, path), indices in sorted(endpoints.items())]
    write_json(OUTPUT / 'environment.json', {name: [{'value': value, 'observed_requests': count} for value, count in values.items()]
               for name, values in environment.items()})
    write_json(OUTPUT / 'interfaces.json', catalog)
    write_json(OUTPUT / 'request-response-structures.json', observations)
    write_json(OUTPUT / 'cost-price-findings.json', focus)
    manifest = {'source': str(SOURCE), 'source_bytes': SOURCE.stat().st_size, 'source_sha256': digest,
        'entries': len(observations), 'api_groups': len(catalog), 'credential_observations': len(sessions),
        'domains': dict(domains), 'methods': dict(methods), 'mime_types': dict(mime_types),
        'availability': dict(availability), 'capture_first_utc': min(filter(None, started)),
        'capture_last_utc': max(filter(None, started)), 'network_requests_sent': 0,
        'private_storage': 'Fernet-encrypted entries; key protected by current Windows user DPAPI',
        'structure_note': 'Body values are not included in indexes. Arrays sample at most three items for structure; encrypted raw entries retain all items.'}
    write_json(OUTPUT / 'manifest.json', manifest)

    # Verify that headers and raw bodies survive the encrypted round trip.
    for item in focus:
        original = json.loads(cipher.decrypt((OUTPUT / item['raw_entry']).read_bytes()))
        assert original['request']['postData']['mimeType'] == item['payload_mime']
        assert request_body(original['request']) is not None
        assert original['response']['status'] == item['http_status']

    rows = '\n'.join(f"| `{field['path']}` | `{field['type']}` |" for item in focus for field in item['cost_fields'])
    report = f'''# 成本价 HAR 本地采集结果

来源：桌面“成本价”，{manifest['source_bytes']:,} 字节。SHA-256：`{digest}`。

共 {len(observations)} 条请求，{len(catalog)} 个 API 候选分组。采集时间：{manifest['capture_first_utc']} 至 {manifest['capture_last_utc']}（UTC）。本地分析未发送任何采集请求，也未修改原文件或线上商品。

## 已提取

- 环境证据：User-Agent、Client Hints、语言、Origin、Referer 等，见 `environment.json`。UA 声明为 Windows NT 10.0 / Chrome 157，不能据此认定完整浏览器版本、操作系统或运行环境已经独立验证。
- Cookie 请求头：{availability['cookie_header']} 条；Authorization 请求头：{availability['authorization_header']} 条；请求负载：{availability['request_postdata']} 条；有响应正文：{availability['response_text']} 条。
- 全部 {len(observations)} 条原始请求、响应条目均加密保存于 `private/entries/`；Cookie、授权和会话字段另保存在 `private/sessions.json.enc`。密钥受本机当前 Windows 用户 DPAPI 保护。
- 接口候选目录、参数与返回结构分别见 `interfaces.json` 和 `request-response-structures.json`。这不是逐接口注册的工具目录。

## 成本价相关发现

商品查询样例：第 22 条（从 0 开始），`GET gateway.youzan.com/api/youzan.item.component.query.product/1.0.0`。

商品保存样例：第 308 条，`POST gateway.youzan.com/api/youzan.item.component.update.product/1.0.0`。请求为 `application/x-www-form-urlencoded`；其中一些表单字段的值又是 JSON 字符串，需要保留这两层编码。

| 成本字段路径 | 值类型 |
| --- | --- |
{rows}

保存响应在 HAR 中记录为 HTTP 200、业务 code 200、success=true。这只能说明当时记录的返回，不代表当前会话仍有效，或已完成实际结果回读。

保存请求带有完整商品表单（{focus[0]['field_count'] if focus else 0} 个字段），并非已证实的独立成本价补丁接口。此前还有两条 SKU 删除检查相关请求，`do_delete` 分别为 false 和 true，其作用需后续核实；本次没有重放。

## 当前边界

- 本文件主要记录有赞，未发现聚水潭或管家婆业务域名。
- 成本单位、两个成本字段对不同销售渠道的影响，以及是否存在更窄的更新接口，尚需后续验证。
- HAR 不构成完整浏览器环境快照；localStorage、IndexedDB、完整浏览器版本及动态签名状态等不能仅凭已提取请求头认定已恢复。
- 原始 HAR 保留为权威原文。公开索引只提供字段结构，具体请求值与完整响应应按需从本地加密记录读取。
'''
    (OUTPUT / '采集报告.md').write_text(report, encoding='utf-8')
    print(json.dumps({'output': str(OUTPUT), 'entries': len(observations), 'api_groups': len(catalog),
        'credential_observations': len(sessions), 'encrypted_roundtrip_verified': True,
        'network_requests_sent': 0, 'report': str(OUTPUT / '采集报告.md')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
