#!/usr/bin/env python3
"""Reusable merchant login and read-only SKU inventory client; Python standard library only."""
import argparse
import copy
import http.cookiejar
import json
import os
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LOGIN = 'https://api.erp321.com/erp/webapi/UserApi/WebLogin/Passport'
IDENTITY = 'https://api.erp321.com/erp/webapi/UserApi/Passport/GetUserInfo'
INVENTORY = 'https://apiweb.erp321.com/webapi/ItemApi/ItemSku/GetPageListV2'
DEFAULT_PROFILE = Path(__file__).resolve().parent / 'private' / 'default'
HEADER_NAMES = {'accept', 'content-type', 'origin', 'referer', 'user-agent', 'gwfp', 'webbox-request-id', 'webbox-route-path'}
DOMAINS = {'erp321.com', 'www.erp321.com', 'api.erp321.com', 'apiweb.erp321.com', 'src.erp321.com'}


class Failure(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        raise Failure('invalid_file', '配置、会话或 HAR 文件不存在或不是有效 JSON。') from None


def private_write(path, value):
    """Atomic replacement; never truncate an existing session on a partial write."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise Failure('invalid_file', '目标文件不能是符号链接。')
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write('\n'); output.flush(); os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def request_body(entry):
    try:
        return json.loads(entry['request']['postData']['text'])
    except (KeyError, TypeError, ValueError):
        raise Failure('invalid_capture', '目标请求没有可用的 JSON 请求体。') from None


def captured_entries(path, endpoint):
    try:
        return [e for e in read_json(path)['log']['entries']
                if e['request']['method'] == 'POST'
                and urllib.parse.urlsplit(e['request']['url'])._replace(query='', fragment='').geturl() == endpoint]
    except (KeyError, TypeError):
        raise Failure('invalid_capture', 'HAR 请求结构无效。') from None


def captured_headers(entry):
    headers = {item['name'].lower(): item['value'] for item in entry['request'].get('headers', [])
               if item['name'].lower() in HEADER_NAMES}
    for name in ('origin', 'referer'):
        if headers.get(name):
            parts = urllib.parse.urlsplit(headers[name])
            if parts.scheme != 'https' or parts.hostname not in DOMAINS:
                raise Failure('invalid_capture', '请求来源不属于固定聚水潭站点。')
    return headers


def prepare(profile, login_har, inventory_har):
    config_file = profile / 'config.json'
    if config_file.exists():
        raise Failure('profile_exists', '配置已存在，未覆盖。新账号请指定另一个 --profile 目录。')
    login_rows = captured_entries(login_har, LOGIN)
    inventory_rows = captured_entries(inventory_har, INVENTORY)
    if not login_rows or not inventory_rows:
        raise Failure('missing_capture', '需要包含 Passport 登录及 GetPageListV2 库存明细的 HAR。')
    login = login_rows[0]; payload = request_body(login).get('data', {})
    required = ('account', 'password', 'j_d_3', 'v_d_144')
    if any(not isinstance(payload.get(key), str) or not payload[key] for key in required):
        raise Failure('missing_credentials', '登录抓包缺少账号、密码或设备标识。')
    inventory = next((e for e in inventory_rows if request_body(e).get('page', {}).get('pageAction') == 1), None)
    if inventory is None:
        raise Failure('missing_capture', '缺少 pageAction=1 的库存明细请求，不能用计数请求代替。')
    body = request_body(inventory)
    identities = captured_entries(login_har, IDENTITY)
    config = {'schema_version': 1, 'account': payload['account'], 'password': payload['password'],
              'device': {key: payload[key] for key in ('j_d_3', 'v_d_144')},
              'bootstrap_cookies': login['request'].get('cookies', []),
              'login_headers': captured_headers(login),
              'identity_headers': captured_headers(identities[0]) if identities else captured_headers(login),
              'inventory_headers': captured_headers(inventory),
              'inventory_from': dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(inventory['request']['url']).query)).get('__from'),
              'inventory_data': body['data']}
    if set(config['inventory_data']) != {'sku_type', 'queryFlds', 'orderBy', 'stock_type'}:
        raise Failure('invalid_capture', '库存筛选与已验证版本不同，请先核对请求。')
    private_write(config_file, config)
    return {'prepared': True, 'profile': str(profile), 'config': str(config_file),
            'note': '含账号密码和浏览器初始状态；不需要在后续命令中重复读取HAR。'}


def cookie_jar(cookies):
    jar = http.cookiejar.CookieJar()
    for value in cookies:
        name, text = value.get('name'), value.get('value', '')
        domain = value.get('domain') or '.erp321.com'
        if not isinstance(name, str) or not isinstance(text, str) or domain.lstrip('.') not in DOMAINS:
            raise Failure('invalid_cookie', '聚水潭 Cookie 格式或域名无效。')
        if any(char in name + text for char in '\r\n\x00'):
            raise Failure('invalid_cookie', '聚水潭 Cookie 含无效字符。')
        expires = value.get('expires')
        # HAR uses ISO dates; those belong to the old capture, not a new login.
        if type(expires) is not int:
            expires = None
        jar.set_cookie(http.cookiejar.Cookie(0, name, text, None, False, domain, True, domain.startswith('.'),
            value.get('path') or '/', True, bool(value.get('secure', True)), expires, expires is None,
            None, None, value.get('rest', {})))
    return jar


def serialize_cookies(jar):
    return [{'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path,
             'secure': c.secure, 'expires': c.expires, 'rest': dict(c._rest)} for c in jar]


class Client:
    def __init__(self, profile=DEFAULT_PROFILE, *, opener=None):
        self.profile = Path(profile)
        self.config = read_json(self.profile / 'config.json')
        self.jar = http.cookiejar.CookieJar()
        self.injected_opener = opener
        self.session = None
        self.secrets = set()
        self.collect_secrets(self.config)
        self.use_cookies(self.config.get('bootstrap_cookies', []))

    def collect_secrets(self, value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('password', 'account', 'value', 'j_d_3', 'v_d_144', 'csrf', 'gwfp') and isinstance(item, str) and item:
                    self.secrets.add(item)
                self.collect_secrets(item)
        elif isinstance(value, list):
            for item in value: self.collect_secrets(item)

    def use_cookies(self, cookies):
        self.collect_secrets(cookies)
        self.jar = cookie_jar(cookies)
        self.opener = self.injected_opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()),
            urllib.request.HTTPCookieProcessor(self.jar))

    def redact(self, value):
        if isinstance(value, dict):return {k: self.redact(v) for k, v in value.items()}
        if isinstance(value, list):return [self.redact(v) for v in value]
        if isinstance(value, str):
            for secret in sorted(self.secrets, key=len, reverse=True):
                if len(secret) >= 8:value = value.replace(secret, '[已隐藏凭证]')
        return value

    def post(self, endpoint, body, headers):
        parts = urllib.parse.urlsplit(endpoint)
        if parts.scheme != 'https' or parts._replace(query='', fragment='').geturl() not in (LOGIN, IDENTITY, INVENTORY) or parts.fragment:
            raise Failure('endpoint_denied', '仅允许固定登录、身份核验和库存查询接口。')
        headers = {k: v for k, v in headers.items() if k.lower() in HEADER_NAMES}
        headers['content-type'] = 'application/json; charset=utf-8'
        request = urllib.request.Request(endpoint, data=json.dumps(body, ensure_ascii=False).encode(), method='POST', headers=headers)
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:raise Failure('response_too_large', '返回过大，请降低 page-size。')
            value = json.loads(raw)
            if not isinstance(value, dict):raise ValueError()
            return value
        except urllib.error.HTTPError as error:
            raise Failure('http_error', f'聚水潭返回 HTTP {error.code}，未自动重试或跳转。') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise Failure('network_error', '聚水潭请求未完成，请检查网络；未自动重复登录。') from None
        except (ValueError, UnicodeError):
            raise Failure('invalid_response', '聚水潭没有返回有效 JSON 对象。') from None

    def checked(self, value):
        if value.get('code') != 0:
            message = self.redact(str(value.get('msg') or '请求未通过，请核对会话和渠道权限。'))
            raise Failure('upstream_rejected', message[:300])
        return value

    def verify(self, uid, coid):
        result = self.checked(self.post(IDENTITY, {'data': {}, 'uid': int(uid), 'coid': int(coid)}, self.config['identity_headers']))
        data = result.get('data')
        if not isinstance(data, dict) or str(data.get('uid')) != str(uid) or str(data.get('coId')) != str(coid):
            raise Failure('identity_mismatch', '新会话的用户或公司身份不匹配，未保存。')
        return {key: data.get(key) for key in ('uid', 'coId', 'userName', 'companyName', 'roleIds', 'isAdmin')}

    def login(self):
        config = self.config
        device = dict(config['device'])
        # Reuse the latest verified browser state for this account, not an old HAR session.
        if (self.profile / 'session.json').exists():
            previous = read_json(self.profile / 'session.json')
            if previous.get('account') == config['account']:
                self.use_cookies(previous['cookies'])
                device.update(previous.get('device', {}))
        payload = {'data': {'account': config['account'], 'password': config['password'],
                           'j_d_3': device['j_d_3'], 'v_d_144': device['v_d_144'], 'isApp': False}}
        result = self.checked(self.post(LOGIN, payload, config['login_headers']))
        data = result.get('data') if isinstance(result.get('data'), dict) else {}
        returned = result.get('cookie')
        if isinstance(returned, dict):
            for text in returned.values():
                if isinstance(text, str) and text:self.secrets.add(text)
        if data.get('hasRisk') or data.get('idaasHasRisk'):
            raise Failure('verification_required', '聚水潭要求额外安全验证，本次未保存或覆盖原会话。')
        if not isinstance(returned, dict) or not all(str(returned.get(k, '')).isdecimal() for k in ('u_id', 'u_co_id')):
            raise Failure('invalid_login', '登录响应没有签发用户和公司身份，未保存会话。')
        cookies = {c.name: c.value for c in self.jar}
        if any(not cookies.get(k) or cookies[k] != returned.get(k) for k in ('u_json', 'u_cid', 'u_sso_token')):
            raise Failure('invalid_login', '未收到完整且一致的新会话 Cookie，未覆盖原会话。')
        uid, coid = str(returned['u_id']), str(returned['u_co_id'])
        identity = self.verify(uid, coid)
        for key in ('j_d_3', 'v_d_144'):
            if cookies.get(key):device[key] = cookies[key]
        self.session = {'schema_version': 1, 'uid': uid, 'coid': coid, 'account': config['account'],
                        'cookies': serialize_cookies(self.jar), 'device': device, 'expires_at': returned.get('u_exp'), 'identity': identity}
        private_write(self.profile / 'session.json', self.session)
        return {'logged_in': True, 'verified': True, 'identity': identity, 'expires_at': returned.get('u_exp'),
                'warning': self.redact(str(result.get('msg') or data.get('msg') or '')), 'session': str(self.profile / 'session.json')}

    def load_session(self):
        state = read_json(self.profile / 'session.json')
        if state.get('account') != self.config.get('account'):
            raise Failure('account_changed', '配置账号与保存会话不同，请重新登录，不能混用。')
        if not all(str(state.get(key, '')).isdecimal() for key in ('uid', 'coid')):
            raise Failure('invalid_session', '保存的会话身份无效，请重新登录。')
        self.session = state
        self.use_cookies(state['cookies'])
        return state

    def persist_cookies(self):
        cookies = serialize_cookies(self.jar)
        if self.session is not None and cookies != self.session['cookies']:
            self.session['cookies'] = cookies
            private_write(self.profile / 'session.json', self.session)

    def identity(self):
        state = self.load_session()
        result = self.verify(state['uid'], state['coid'])
        self.persist_cookies()
        return result

    def inventory(self, *, action='list', page=1, page_size=50):
        if action not in ('list', 'count') or type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 50:
            raise Failure('invalid_input', 'action=list/count，page至少1，page-size范围1至50。')
        state = self.load_session(); coid = state['coid']
        params = {'owner_co_id': coid, 'authorize_co_id': coid}
        if self.config.get('inventory_from'):params['__from'] = self.config['inventory_from']
        body = {'ip': '', 'uid': str(state['uid']), 'coid': coid,
                'page': {'currentPage': page, 'pageSize': page_size, 'pageAction': 1 if action == 'list' else 2},
                'data': copy.deepcopy(self.config['inventory_data'])}
        result = self.checked(self.post(INVENTORY + '?' + urllib.parse.urlencode(params), body, self.config['inventory_headers']))
        rows = result.get('data')
        if not isinstance(rows, list) or not isinstance(result.get('page'), dict):
            raise Failure('invalid_response', '库存返回结构异常，不能作为零库存。')
        if any(not isinstance(row, dict) or str(row.get('co_id')) != coid for row in rows):
            raise Failure('company_mismatch', '库存数据不属于当前会话公司，未输出。')
        self.persist_cookies()
        return self.redact(result)


def main():
    parser = argparse.ArgumentParser(description='聚水潭：保存登录会话及只读库存查询，无第三方依赖。')
    parser.add_argument('--profile', type=Path, default=DEFAULT_PROFILE, help='不同账号使用不同私有目录')
    commands = parser.add_subparsers(dest='command', required=True)
    preparation = commands.add_parser('prepare', help='从已验证HAR生成私有配置，仅首次运行')
    preparation.add_argument('--login-har', type=Path, required=True)
    preparation.add_argument('--inventory-har', type=Path, required=True)
    commands.add_parser('login', help='账号密码登录，核验后保存会话')
    commands.add_parser('identity', help='验证保存会话并读取当前身份')
    inventory_parser = commands.add_parser('inventory', help='只读库存明细或总数')
    inventory_parser.add_argument('--action', choices=['list', 'count'], default='list')
    inventory_parser.add_argument('--page', type=int, default=1)
    inventory_parser.add_argument('--page-size', type=int, default=50)
    inventory_parser.add_argument('--output', type=Path, help='将原始业务JSON存为私有文件，stdout只输出文件位置')
    args = parser.parse_args()
    try:
        profile = args.profile.expanduser().resolve()
        if args.command == 'prepare':result = prepare(profile, args.login_har, args.inventory_har)
        else:
            client = Client(profile)
            if args.command == 'login':result = client.login()
            elif args.command == 'identity':result = client.identity()
            else:
                result = client.inventory(action=args.action, page=args.page, page_size=args.page_size)
                if args.output:
                    private_write(args.output, result)
                    result = {'path': str(args.output.resolve()), 'rows': len(result['data']), 'page': result['page']}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except Failure as error:
        print(json.dumps({'error': {'code': error.code, 'message': error.message}}, ensure_ascii=False))
        return 1
    except (OSError, KeyError, TypeError, ValueError):
        print(json.dumps({'error': {'code': 'invalid_configuration', 'message': '配置、会话或文件结构无效，请检查私有目录。'}}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
