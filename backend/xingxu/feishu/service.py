"""One enterprise app per workspace, isolated encrypted authorizations per employee."""
import asyncio
import copy
import hashlib
import json
import time
import uuid
from pathlib import Path
from cryptography.fernet import InvalidToken
from ..auth import AuthFailure, COMPANY_ATTENDANCE_READ
from ..store import JsonRepository
from .oauth import FeishuOAuth, READ_SCOPES
from .application import FeishuApplication, APPLICATION_SCOPES
from galaxy_cli.feishu_capabilities import all_scopes, DOMAINS, scopes_satisfied, APPLICATION_ONLY


def owner_key(user):
    return hashlib.sha256(json.dumps([user['workspace_id'], user['id']]).encode()).hexdigest()


class FeishuService:
    def __init__(self, directory, cipher, *, transport=None):
        self.repo = JsonRepository(directory, 'feishu', ('apps', 'connections'))
        self.cipher = cipher
        self.oauth = FeishuOAuth(transport)
        self.application = FeishuApplication(transport)
        self.locks = {}
        self.bot = None

    def _encrypt(self, value):return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def _decrypt(self, value):
        try:return json.loads(self.cipher.decrypt(value.encode()))
        except (InvalidToken, ValueError, TypeError):
            raise AuthFailure('feishu_credential_error', '飞书凭证无法读取，请重新配置或授权。', 503) from None

    def _snapshot(self, user):
        with self.repo.transaction(write=False) as state:
            return copy.deepcopy(state['apps'].get(user['workspace_id'])), copy.deepcopy(state['connections'].get(owner_key(user), {}))

    def _tokens(self, record):
        tokens={key:self._decrypt(value) for key,value in record.get('grants',{}).items()}
        if record.get('token'):tokens['documents']=self._decrypt(record['token'])
        return tokens

    def _app(self, user):
        app, _ = self._snapshot(user)
        if not app:raise AuthFailure('feishu_app_required', '管理员尚未配置飞书企业应用。', 409)
        return {**app, 'app_secret': self._decrypt(app['secret'])}

    @staticmethod
    def attendance_access(user, management):
        # 公司考勤读取接入统一权限层。后续管理界面配置的调用者权限和数据
        # 范围也必须作用于此，不能因为服务端持有管理员 Cookie 或应用 token
        # 就向所有员工放行；平台角色标记不能覆盖 Galaxy 的权限决定。
        company = (COMPANY_ATTENDANCE_READ in user.get('permissions', [])
                   and (management or {}).get('identity_verified') is True)
        return 'application' if company else 'self'

    def summary(self, user):
        app, record = self._snapshot(user)
        valid = bool(app and record.get('revision') == app['revision'])
        tokens=self._tokens(record) if valid else {}
        token=max(tokens.values(),key=lambda t:t.get('expires_at',0),default={})
        scopes=sorted({s for t in tokens.values() for s in t.get('scope','').split()})
        pending = record.get('pending') if valid else None
        if pending and pending['expires_at'] <= time.time():pending = None
        result = {'configured': bool(app), 'app_id': app.get('app_id', '') if app else '',
            'connected': bool(token.get('access_token')), 'identity': record.get('identity') if token else None,
            'expires_at': token.get('expires_at'), 'refresh_expires_at': token.get('refresh_expires_at'),
            'scopes': scopes, 'required_scopes': list(READ_SCOPES),
            'authorization_mode':'all',
            'management': record.get('management') if token else None,
            'attendance_access': self.attendance_access(user, record.get('management')) if token else None,
            'granted_scope_count':len(scopes),
            'permission_import':{'scopes':{'user':all_scopes(), 'tenant':list(APPLICATION_SCOPES)}},
            'business_domains':len(DOMAINS),'business_operations':sum(len(v['commands']) for v in DOMAINS.values()),
            'pending': {key: pending[key] for key in ('id', 'url', 'user_code', 'expires_at', 'interval')} if pending else None,
            'cli_ready': (Path(__file__).resolve().parents[2]/'feishu/bin/lark-cli').is_file()}
        if 'feishu:app:configure' in user['permissions']:result['has_app_secret'] = bool(app and app.get('secret'))
        if self.bot:result['bot'] = self.bot.summary(user)
        return result

    def save_app(self, user, body):
        if 'feishu:app:configure' not in user['permissions']:raise AuthFailure('forbidden', '仅管理员可以配置飞书企业应用。', 403)
        with self.repo.transaction() as state:
            old = state['apps'].get(user['workspace_id'])
            if not body.app_secret and (not old or old['app_id'] != body.app_id):
                raise AuthFailure('feishu_secret_required', '请输入 App Secret。', 422)
            changed = not old or old['app_id'] != body.app_id or bool(body.app_secret)
            state['apps'][user['workspace_id']] = {'app_id': body.app_id,
                'secret': self._encrypt(body.app_secret) if body.app_secret else old['secret'],
                'revision': str(uuid.uuid4()) if changed else old['revision'], 'updated_at': int(time.time())}
        return self.summary(user)

    def _commit(self, user, revision, update, *, pending_id=None, grant_id=None):
        with self.repo.transaction() as state:
            app = state['apps'].get(user['workspace_id'])
            record = state['connections'].get(owner_key(user), {})
            if (not app or app['revision'] != revision
                or pending_id and (record.get('pending') or {}).get('id') != pending_id
                or grant_id is not None and (record.get('grant_id') != grant_id or record.get('revision') != revision)):
                raise AuthFailure('feishu_connection_changed', '飞书配置或授权任务已变化，请重新操作。', 409)
            if record.get('revision') != revision:record = {'revision': revision}
            record.update(update)
            state['connections'][owner_key(user)] = record

    async def start(self, user, validate=lambda: None, *, group=None):
        async with self.locks.setdefault(owner_key(user), asyncio.Lock()):
            validate();app = self._app(user)
            # Old clients may still send a group; it no longer narrows authorization.
            scopes=all_scopes(write='feishu:write' in user['permissions'])
            result = await self.oauth.start(app,scopes=scopes)
            validate()
            pending = {'id': str(uuid.uuid4()), 'code': self._encrypt(result['device_code']), 'url': result['url'],
                'user_code': result['user_code'], 'expires_at': int(time.time()) + result['expires_in'],
                'interval': result['interval'], 'next_poll_at': 0,'mode':'all'}
            self._commit(user, app['revision'], {'pending': pending})
            return self.summary(user)

    @staticmethod
    def _token(value, previous=None):
        previous = previous or {}
        if not isinstance(value.get('access_token'), str) or not value['access_token'] or value.get('token_type', 'Bearer').lower() != 'bearer':
            raise AuthFailure('feishu_token_error', '飞书未返回可用的授权凭证，请重新授权。', 502)
        try:
            seconds = int(value['expires_in'])
            if seconds <= 0:raise ValueError()
            refresh = value.get('refresh_token') or previous.get('refresh_token', '')
            refresh_expires = int(time.time()) + int(value['refresh_token_expires_in']) if value.get('refresh_token_expires_in') else previous.get('refresh_expires_at')
        except (KeyError, TypeError, ValueError):
            raise AuthFailure('feishu_token_error', '飞书授权有效期格式异常。', 502) from None
        return {'access_token': value['access_token'], 'refresh_token': refresh, 'expires_at': int(time.time()) + seconds,
            'refresh_expires_at': refresh_expires, 'scope': value.get('scope', previous.get('scope', ''))}

    async def poll(self, user, pending_id, validate=lambda: None):
        async with self.locks.setdefault(owner_key(user), asyncio.Lock()):
            validate();app = self._app(user);_, record = self._snapshot(user)
            pending = record.get('pending')
            if record.get('revision') != app['revision'] or not pending or pending['id'] != str(pending_id):
                raise AuthFailure('not_found', '此授权任务不存在或已结束。', 404)
            if pending['expires_at'] <= time.time():
                self._commit(user, app['revision'], {'pending': None})
                raise AuthFailure('feishu_authorization_expired', '授权链接已过期，请重新发起授权。', 409)
            if pending['next_poll_at'] > time.time():return self.summary(user)
            status, value = await self.oauth.poll(app, self._decrypt(pending['code']))
            validate()
            if value.get('error') in ('authorization_pending', 'slow_down'):
                if value['error'] == 'slow_down':pending['interval'] += 5
                pending['next_poll_at'] = time.time() + pending['interval']
                self._commit(user, app['revision'], {'pending': pending}, pending_id=pending['id'])
                return self.summary(user)
            if status != 200 or value.get('error'):
                self._commit(user, app['revision'], {'pending': None}, pending_id=pending['id'])
                raise AuthFailure('feishu_authorization_rejected', '飞书授权未完成或已被拒绝，请重新授权。', 409)
            token = self._token(value)
            identity = await self.oauth.identity(token['access_token'])
            previous=record.get('identity')
            if self._tokens(record) and previous and any(previous.get(key)!=identity.get(key) for key in ('open_id','tenant_key')):
                self._commit(user,app['revision'],{'pending':None},pending_id=pending['id'])
                raise AuthFailure('feishu_identity_mismatch','这次授权与已绑定的飞书账号或团队不一致；如需换绑，请先断开连接。',409)
            validate()
            management = await self.application.inspect(app, identity)
            validate()
            update={'identity':identity,'management':management,'pending':None,'grant_id':str(uuid.uuid4())}
            if pending.get('mode')=='all':
                grants=dict(record.get('grants',{}))
                if record.get('token'):
                    previous_token=self._decrypt(record['token'])
                    key='legacy-'+hashlib.sha256(previous_token.get('scope','').encode()).hexdigest()[:16]
                    grants[key]=record['token']
                update.update({'token':self._encrypt(token),'grants':grants})
            else:
                # Complete an already-pending 0.7.0 authorization without data loss.
                group=pending.get('group','documents')
                if group=='documents':update['token']=self._encrypt(token)
                else:update['grants']={**record.get('grants',{}),group:self._encrypt(token)}
            self._commit(user, app['revision'], update, pending_id=pending['id'])
            return self.summary(user)

    async def disconnect(self, user):
        async with self.locks.setdefault(owner_key(user), asyncio.Lock()):
            with self.repo.transaction() as state:state['connections'].pop(owner_key(user), None)
        if self.bot:await self.bot.disconnect(user)
        return self.summary(user)

    async def credentials(self, user, validate=lambda: None, *, domain=None, action=None):
        if (domain,action) in APPLICATION_ONLY:
            return await self.application_credentials(user, validate, domain=domain, action=action)
        async with self.locks.setdefault(owner_key(user), asyncio.Lock()):
            validate();app = self._app(user);_, record = self._snapshot(user)
            if record.get('revision') != app['revision'] or not (record.get('token') or record.get('grants')):
                raise AuthFailure('feishu_connection_required', '请先在控制台飞书页面授权当前账号。', 409)
            entry=DOMAINS[domain]['commands'][action] if domain else None
            candidates=[(key,t) for key,t in self._tokens(record).items()
                        if entry is None or scopes_satisfied(entry,t.get('scope','').split())]
            if not candidates:
                raise AuthFailure('feishu_scope_required','当前个人授权尚未包含此操作权限。请点击飞书「补全授权」，服务器会自动申请全部已接入用户权限，无需选择范围；仍缺失时需管理员在飞书后台开通应用权限并发布。这不代表账号不是管理员。所需权限：'+', '.join(entry['scopes']),409)
            extra=set(entry.get('conditional_scopes',[])) if entry else set()
            group,token=max(candidates,key=lambda row:(row[1].get('expires_at',0)>time.time()+60,len(extra&set(row[1].get('scope','').split())),row[1].get('expires_at',0)))
            if token['expires_at'] <= time.time() + 60:
                if not token.get('refresh_token') or token.get('refresh_expires_at') and token['refresh_expires_at'] <= time.time():
                    raise AuthFailure('feishu_authorization_expired', '飞书授权已过期，请重新授权。', 409)
                status, value = await self.oauth.refresh(app, token['refresh_token'])
                validate()
                if status != 200 or value.get('error'):
                    raise AuthFailure('feishu_refresh_failed', '飞书授权刷新失败，请在控制台重新授权。', 409)
                token = self._token(value, token)
                if entry and not scopes_satisfied(entry,token.get('scope','').split()):
                    raise AuthFailure('feishu_scope_required','刷新后的飞书授权不含此操作权限，请点击飞书「补全授权」，无需选择范围。',409)
                update={'token':self._encrypt(token)} if group=='documents' else {'grants':{**record.get('grants',{}),group:self._encrypt(token)}}
                self._commit(user, app['revision'], update)
            validate()
            return {'app_id': app['app_id'], 'access_token': token['access_token'], 'scopes': token['scope'],
                'open_id': record.get('identity', {}).get('open_id', ''), 'revision': app['revision'], 'grant_id': record.get('grant_id')}

    async def application_credentials(self, user, validate=lambda: None, *, domain, action):
        # A live, employee-owned OAuth identity is still required. An application
        # token is never a general fallback for missing personal permissions.
        personal = await self.credentials(user, validate)
        async with self.locks.setdefault(owner_key(user), asyncio.Lock()):
            validate()
            if not self.unchanged(user, personal):
                raise AuthFailure('feishu_connection_changed', '飞书授权已变化，请重试。', 409)
            app = self._app(user)
            if app['revision'] != personal['revision']:
                raise AuthFailure('feishu_connection_changed', '飞书应用配置已变化，请重新授权。', 409)
            identity = await self.oauth.identity(personal['access_token'])
            _, record = self._snapshot(user)
            if any(identity.get(k) != record.get('identity', {}).get(k) for k in ('open_id', 'tenant_key')):
                raise AuthFailure('feishu_identity_mismatch', '飞书身份与绑定账号不一致，请重新授权。', 409)
            management = await self.application.inspect(app, identity)
            if management['status'] == 'inactive' or management['reason'] == 'identity_mismatch':
                raise AuthFailure('feishu_identity_unavailable', '当前飞书员工身份不可用，请重新授权。', 403)
            token = await self.application.token(app)
            entry = DOMAINS[domain]['commands'][action] if domain is not None else None
            if entry and token['scope_known'] and not scopes_satisfied(entry, token['scope'].split()):
                token = await self.application.token(app, force=True)
            if entry and token['scope_known'] and not scopes_satisfied(entry, token['scope'].split()):
                raise AuthFailure('feishu_application_scope_required', '企业应用缺少考勤读取权限，请管理员开通 attendance:task:readonly 并发布。', 409)
            current = validate()
            if isinstance(current, dict):
                user = current
            self._commit(user, app['revision'], {'management': management}, grant_id=personal['grant_id'])
            return {'app_id': app['app_id'], 'access_token': token['access_token'],
                'scopes': token['scope'], 'identity': 'application', 'open_id': identity['open_id'],
                'employee_id': management['employee_id'], 'employee_no': management['employee_no'],
                'is_tenant_manager': management['is_tenant_manager'] is True,
                'attendance_access': self.attendance_access(user, management),
                'revision': app['revision'], 'grant_id': personal['grant_id']}

    async def directory_credentials(self, user, body, validate=lambda: None):
        from urllib.parse import quote
        from galaxy_cli.tools.feishu.authorized_users import normalize
        body = normalize(body)
        current = validate()
        if isinstance(current, dict):user = current
        if not {'feishu:read',COMPANY_ATTENDANCE_READ} <= set(user['permissions']):
            raise AuthFailure('permission_denied', '当前账号没有公司授权名单读取权限。', 403)
        credentials = await self.application_credentials(user, validate, domain=None, action=None)
        if credentials['attendance_access'] != 'application':
            raise AuthFailure('feishu_identity_unavailable', '尚未核验账号属于当前企业应用，不能读取公司授权名单。', 403)
        params = {'user_id_type':'user_id', 'department_id_type':'open_department_id'}
        action = body['action']
        if action == 'scope':path = '/contact/v3/scopes'
        elif action == 'user':path = '/contact/v3/users/' + quote(body['user_id'],safe='')
        elif action == 'users':
            path = '/contact/v3/users/find_by_department'
            params['department_id'] = body['department_id']
        else:
            path = '/contact/v3/departments/' + quote(body['department_id'],safe='') + '/children'
            params['fetch_child'] = 'true' if body.get('fetch_child',False) else 'false'
        if action != 'user':
            params['page_size'] = body.get('page_size',50)
            if 'page_token' in body:params['page_token'] = body['page_token']
        status, result = await self.application._request('GET', 'https://open.feishu.cn/open-apis'+path,
            headers={'Authorization':'Bearer '+credentials['access_token']},params=params)
        current = validate()
        if isinstance(current, dict):user = current
        if not {'feishu:read',COMPANY_ATTENDANCE_READ} <= set(user['permissions']):
            raise AuthFailure('permission_denied', '公司授权名单读取权限已变化，本次结果未返回。', 403)
        if not self.unchanged(user,credentials):
            raise AuthFailure('feishu_connection_changed', '飞书授权已变化，本次结果未返回。', 409)
        def redact(value):
            if isinstance(value,dict):return {k:redact(v) for k,v in value.items()}
            if isinstance(value,list):return [redact(v) for v in value]
            if isinstance(value,str):return value.replace(credentials['access_token'],'[已隐藏凭证]')
            return value
        # The CLI receives only this authorized business result, never the
        # application token used by these fixed server-side GET requests.
        return {'directory_access':'application','directory_result':redact(result),'directory_http_status':status,
                'revision':credentials['revision'],'grant_id':credentials['grant_id']}

    def unchanged(self, user, credentials):
        app, record = self._snapshot(user)
        return bool(app and app['revision'] == credentials['revision'] and record.get('revision') == app['revision']
            and record.get('grant_id') == credentials['grant_id'] and (record.get('token') or record.get('grants')))
