"""User-owned Youzan connections, refresh jobs and tool credential access."""
import asyncio
import copy
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from cryptography.fernet import InvalidToken

from ..auth import AuthFailure
from ..store import JsonRepository
from ..browser import SessionLocks
from .constants import MESSAGES, ACTIVE, ROOT
from .http import YouzanHttpClient, read_youzan_page
from .login import YouzanLoginAdapter
from .sessions import ShopSessions
from .renewal import RefreshGrant, public_schedule


def owner_key(user):
    return hashlib.sha256(json.dumps([user["workspace_id"], user["id"]]).encode()).hexdigest()


class YouzanService:
    def __init__(self, directory, cipher, *, transport=None, login_runner=None, authorize=None):
        self.directory = Path(directory)
        self.repo = JsonRepository(directory, "youzan", ("connections",))
        self.cipher = cipher
        self.transport = transport
        self.login_runner = login_runner or self._browser_login
        self.authorize = authorize
        self.renewal = None
        self.tasks = {}
        self.browser_slots = asyncio.Semaphore(2)
        self.shop_sessions = ShopSessions(self)
        self.operation_locks = SessionLocks('youzan')
        with self.repo.transaction() as state:
            for record in state["connections"].values():
                if record.get("job", {}).get("status") in ACTIVE:
                    record["job"].update(status="failed", code="interrupted", message=MESSAGES["interrupted"], finished_at=int(time.time()))

    def _decrypt(self, value):
        try:
            return self.cipher.decrypt(value.encode()).decode()
        except (InvalidToken, ValueError, KeyError):
            raise AuthFailure("credential_unavailable", "有赞凭证无法读取，请重新保存账号密码。", 503) from None

    def _encrypt(self, value):
        return self.cipher.encrypt(value.encode()).decode()

    @staticmethod
    def _public(record):
        if not record:
            return {"configured": False, "account": "", "kdt_id": "", "has_password": False,
                    "sid": {"present": False}, "csrf": {"present": False}, "shops": [], "shops_updated_at": None, "job": None, "auto_refresh": False,
                    "multi_shop": False, "shop_sessions": []}
        now = int(time.time())
        sid = record.get("sid", {})
        expired = bool(sid.get("expires_at") and sid["expires_at"] <= now)
        selected_session = record.get('shop_sessions', {}).get(record.get('kdt_id'), {})
        csrf_present = ShopSessions.ready(record, selected_session) if record.get('multi_shop') else bool(record.get('csrf', {}).get('secret'))
        return {"configured": True, "account": record["account"], "kdt_id": record["kdt_id"],
                "has_password": bool(record.get("password")), "auto_refresh": any(x['enabled'] for x in public_schedule(record).values()),
                "renewal": public_schedule(record),
                "updated_at": record["updated_at"], "shops": copy.deepcopy(record.get("shops", [])),
                "shops_updated_at": record.get("shops_updated_at"),
                "multi_shop": record.get('multi_shop', False), "shop_sessions": ShopSessions.public(record),
                "sid": {"present": bool(sid.get("secret")), "updated_at": sid.get("updated_at"),
                        "expires_at": sid.get("expires_at"), "expired": expired},
                "csrf": {"present": csrf_present,
                         "updated_at": record.get("csrf", {}).get("updated_at"), "expired": expired},
                "job": copy.deepcopy(record.get("job"))}

    def summary(self, user):
        with self.repo.transaction(write=False) as state:
            key = owner_key(user)
            record = state["connections"].get(key)
            result = self._public(record)
            result['renewal'] = public_schedule(record)
            result['auto_refresh'] = any(x['enabled'] for x in result['renewal'].values())
            result['can_configure_renewal'] = 'youzan:renew:configure' in user['permissions']
        lock = self.shop_sessions.locks.get(key)
        return {**result, 'preparing_shops': bool(lock and lock.locked())}

    def save(self, user, body):
        key = owner_key(user)
        with self.repo.transaction() as state:
            old = state["connections"].get(key)
            if old and old.get("job", {}).get("status") in ACTIVE:
                raise AuthFailure("busy", "刷新进行中，请等待完成或取消后修改配置。", 409)
            changed = not old or old["account"] != body.account or bool(body.password)
            if not body.password and (not old or old["account"] != body.account):
                raise AuthFailure("missing_password", "请输入此有赞账号的密码。", 422)
            shop_id = body.kdt_id if body.kdt_id is not None else (old["kdt_id"] if old and not changed else "")
            shop_changed = not old or old["kdt_id"] != shop_id
            record = {"id": old["id"] if old else str(uuid.uuid4()),
                      "revision": str(uuid.uuid4()), "account": body.account, "kdt_id": shop_id,
                      "password": self._encrypt(body.password) if body.password else old["password"],
                      "profile_revision": str(uuid.uuid4()) if changed else old["profile_revision"],
                      "updated_at": int(time.time()), "job": {}}
            if old and old.get('renewal'):
                record['renewal'] = copy.deepcopy(old['renewal'])
            if old and not changed:
                record["sid"] = old.get("sid", {})
                record["shops"] = old.get("shops", [])
                record["shops_updated_at"] = old.get("shops_updated_at")
                record['shop_sessions'] = old.get('shop_sessions', {})
                record['multi_shop'] = old.get('multi_shop', False)
                if not shop_changed:
                    record["csrf"] = old.get("csrf", {})
                    record["category_credentials"] = old.get("category_credentials", {})
                elif ShopSessions.ready(record, record['shop_sessions'].get(shop_id, {})):
                    entry = record['shop_sessions'][shop_id]
                    record.update(csrf=entry['csrf'], category_credentials=entry['category_credentials'])
            state["connections"][key] = record
            return self._public(record)

    def _authorize(self, token):
        if isinstance(token, RefreshGrant):
            if self.renewal is None:
                raise AuthFailure('forbidden', MESSAGES['forbidden'], 403)
            self.renewal.authorize(token)
            return
        if self.authorize:
            identity = self.authorize(token)
            if "youzan:configure" not in identity["user"]["permissions"]:
                raise AuthFailure("forbidden", MESSAGES["forbidden"], 403)

    async def start(self, user, kind, token=None):
        self._authorize(token)
        key = owner_key(user)
        if isinstance(token, RefreshGrant) and token.owner != key:
            raise AuthFailure('forbidden', MESSAGES['forbidden'], 403)
        lock = self.shop_sessions.locks.get(key)
        if lock and lock.locked():
            raise AuthFailure('busy', '店铺会话正在准备，请稍后刷新。', 409)
        with self.repo.transaction() as state:
            record = state["connections"].get(key)
            if not record:
                raise AuthFailure("credentials_missing", MESSAGES["credentials_missing"], 422)
            if self._operation_lock(record, key).locked():
                raise AuthFailure('busy', '有赞工具正在使用此登录会话，请完成后刷新。', 409)
            if record.get("job", {}).get("status") in ACTIVE or key in self.tasks:
                raise AuthFailure("busy", "此连接正在刷新，请等待完成。", 409)
            if kind in ('csrf', 'all') and not record.get("sid", {}).get("secret"):
                raise AuthFailure("missing_sid", "请先刷新 SID。", 422)
            if kind == "csrf" and not record["kdt_id"]:
                raise AuthFailure("missing_kdt_id", "请先从店铺列表选择店铺。", 422)
            identifier = str(uuid.uuid4())
            record["job"] = {"id": identifier, "kind": kind, "status": "running", "code": "starting",
                             "trigger": "automatic" if isinstance(token, RefreshGrant) else "manual",
                             "message": MESSAGES["starting"] if kind == "sid" else MESSAGES["crm"],
                             "started_at": int(time.time()), "finished_at": None}
            snapshot = copy.deepcopy(record)
            if kind == 'all':
                record['multi_shop'] = snapshot['multi_shop'] = True
            result = self._public(record)
        task = asyncio.create_task(self._run(key, snapshot, kind, token))
        self.tasks[key] = task
        task.add_done_callback(lambda done: self.tasks.pop(key, None) if self.tasks.get(key) is done else None)
        return result

    def _update(self, key, revision, *, code, status="running", values=None):
        with self.repo.transaction() as state:
            record = state["connections"].get(key)
            if not record or record["revision"] != revision:
                raise AuthFailure("stale", "配置已变更，请重新刷新。", 409)
            record.update(values or {})
            record["job"].update(code=code, status=status, message=MESSAGES.get(code, MESSAGES["failed"]))
            if status != "running":
                record["job"]["finished_at"] = int(time.time())
            if status == 'success':
                completed = []
                if record['job'].get('kind') == 'sid':
                    completed.append('sid')
                if code in ('success', 'csrf_success', 'all_ready'):
                    completed.append('csrf')
                for kind in completed:
                    schedule = record.get('renewal', {}).get(kind)
                    if schedule:
                        now = int(time.time())
                        schedule.update(last_success_at=now, last_attempt_at=now, paused=False, error=None,
                            next_at=now + schedule['interval_days'] * 86400 if schedule['enabled'] else None)

    def select_shop(self, user, shop_id):
        with self.repo.transaction() as state:
            record = state["connections"].get(owner_key(user))
            if not record:
                raise AuthFailure("credentials_missing", MESSAGES["credentials_missing"], 422)
            if record.get("job", {}).get("status") in ACTIVE:
                raise AuthFailure("busy", "请等待当前任务完成后选择店铺。", 409)
            if not any(shop["id"] == shop_id for shop in record.get("shops", [])):
                raise AuthFailure("unknown_shop", "请选择当前账号获取到的店铺。", 422)
            if record["kdt_id"] != shop_id:
                entry = record.get('shop_sessions', {}).get(shop_id, {})
                ready = ShopSessions.ready(record, entry)
                record.update(kdt_id=shop_id, csrf=entry['csrf'] if ready else {}, category_credentials=entry['category_credentials'] if ready else {},
                    revision=str(uuid.uuid4()), updated_at=int(time.time()), job={})
            return self._public(record)

    async def shops(self, user, token=None):
        """Automatic page load reads names with the existing SID; it never launches login."""
        self._authorize(token)
        key = owner_key(user)
        with self.repo.transaction(write=False) as state:
            record = copy.deepcopy(state["connections"].get(key))
        if not record or not record.get("sid", {}).get("secret") or record.get("job", {}).get("status") in ACTIVE:
            return self._public(record)
        shops = await self._list_shops(record)
        self._authorize(token)
        with self.repo.transaction() as state:
            current = state["connections"].get(key)
            if not current or current["revision"] != record["revision"] or current.get("job", {}).get("status") in ACTIVE:
                raise AuthFailure("busy", "连接状态已变化，请稍后刷新店铺列表。", 409)
            current.update(shops=shops, shops_updated_at=int(time.time()))
            return self._public(current)

    async def _login(self, key, record, token):
        profile = self.directory / "youzan-profiles" / record["id"] / record["profile_revision"]
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        profile.parent.chmod(0o700)
        async def progress(code):
            if code in {"browser_ready", "form_ready", "slider_payload_ready"}:
                self._update(key, record["revision"], code=code)
        async with self.browser_slots:
            self._authorize(token)
            result = await self.login_runner(record["account"], self._decrypt(record["password"]), profile, progress)
        sid = result.get("sid")
        if not isinstance(sid, str) or not sid or len(sid) > 4096 or re.search(r"[\s;\r\n]", sid):
            raise AuthFailure("login_unconfirmed", MESSAGES["login_unconfirmed"], 502)
        expires = result.get("expires_at")
        expires = int(expires) if isinstance(expires, (int, float)) and expires > 0 else None
        if expires and expires <= time.time():
            raise AuthFailure("login_required", MESSAGES["login_required"], 502)
        self._authorize(token)
        saved = {"secret": self._encrypt(sid), "updated_at": int(time.time()), "expires_at": expires}
        self._update(key, record["revision"], code="shops", values={"sid": saved, "csrf": {}, "category_credentials": {}, "shop_sessions": {}, "shops": []})
        record["sid"] = saved

    def _operation_lock(self, record, key):
        # Youzan binds a current shop to SID, so its whole operation must be serialized.
        secret = record.get('sid', {}).get('secret')
        return self.operation_locks.get(self._decrypt(secret) if secret else None, fallback=key)

    def operation_guard(self, user):
        key = owner_key(user)
        with self.repo.transaction(write=False) as state:
            record = state['connections'].get(key, {})
        return self._operation_lock(record, key)

    async def _run(self, key, record, kind, token):
        async with self._operation_lock(record, key):
            await self._run_bound(key, record, kind, token)

    async def _run_bound(self, key, record, kind, token):
        revision = record["revision"]
        try:
            self._authorize(token)
            if kind in {"sid", "shops"}:
                self._update(key, revision, code="shops")
                try:
                    shops = await self._list_shops(record)
                except AuthFailure as error:
                    if error.code != "login_required":
                        raise
                    # Only a confirmed missing/expired login reaches the fixed background program.
                    await self._login(key, record, token)
                    shops = await self._list_shops(record)
                self._authorize(token)
                self._update(key, revision, code="shops", values={"shops": shops, "shops_updated_at": int(time.time())})
                if kind == "shops" or not record["kdt_id"]:
                    self._update(key, revision, code="shops_ready" if kind == "shops" else "sid_ready", status="success")
                    return
                if not any(shop["id"] == record["kdt_id"] for shop in shops):
                    raise AuthFailure("shop_access_required", MESSAGES["shop_access_required"], 403)
            if kind == 'all' or record.get('multi_shop'):
                self._update(key, revision, code='all_csrf')
                results = await self.shop_sessions.collect(key, record, 'all', force=True, validate=lambda: self._authorize(token))
                self._update(key, revision, code='all_partial' if any('error' in result for result in results) else 'all_ready', status='success')
                return
            self._update(key, revision, code="crm")
            record["_collect_category_credentials"] = True
            csrf = await self._crm_csrf(record)
            self._authorize(token)
            self._update(key, revision, code="success" if kind == "sid" else "csrf_success", status="success",
                         values={"csrf": {"secret": self._encrypt(csrf), "updated_at": int(time.time())},
                                 "category_credentials": {"secret": self._encrypt(json.dumps(record.get("_category_session", {}))),
                                                          "updated_at": int(time.time())}})
        except asyncio.CancelledError:
            self._update(key, revision, code="cancelled", status="failed")
            raise
        except AuthFailure as error:
            code = error.code if error.code in MESSAGES else "forbidden" if error.status in {401, 403} else "failed"
            self._update(key, revision, code=code, status="failed")
        except Exception:
            self._update(key, revision, code="failed", status="failed")

    def _cookies(self, record, *, shop=False):
        return self._http_client()._cookies(record, shop=shop)

    _read = staticmethod(read_youzan_page)

    async def _list_shops(self, record):
        return await self._http_client()._list_shops(record)

    async def _crm_csrf(self, record):
        return await self._http_client()._crm_csrf(record)

    def _http_client(self):
        return YouzanHttpClient(self._decrypt, transport=self.transport, read=self._read)

    async def resolve_shop_credentials(self, user, selection, validate=lambda: None, *, prepare=True, force=False):
        key = owner_key(user)
        with self.repo.transaction(write=False) as state:
            record = copy.deepcopy(state['connections'].get(key))
        if not record or not record.get('sid', {}).get('secret'):
            raise AuthFailure('connection_required', '当前用户尚未绑定有赞会话。', 409)
        if record.get('job', {}).get('status') in ACTIVE:
            raise AuthFailure('connection_busy', '当前用户的有赞会话正在刷新，请稍后重试。', 409)
        return await self.shop_sessions.collect(key, record, selection, prepare=prepare, force=force, validate=validate)

    def tool_credentials(self, user, shop_id=None):
        """Private category provider; never exposed by an HTTP response or to the model."""
        if not any(p in user["permissions"] for p in ("youzan:customer:read","youzan:report:read","youzan:material:read","youzan:product:read")):
            raise AuthFailure("forbidden", "当前账号没有有赞工具读取权限。", 403)
        with self.repo.transaction(write=False) as state:
            record = copy.deepcopy(state["connections"].get(owner_key(user)))
        if not record or not record.get("sid", {}).get("secret"):
            raise AuthFailure("connection_required", "当前用户尚未连接有赞店铺，请在有赞控制台选择店铺并手动刷新凭证。", 409)
        if record.get("job", {}).get("status") in ACTIVE:
            raise AuthFailure("connection_busy", "当前用户的有赞凭证正在刷新，请完成后重试。", 409)
        if record["sid"].get("expires_at") and record["sid"]["expires_at"] <= time.time():
            raise AuthFailure("credential_expired", "SID 已到浏览器期限，请在有赞控制台手动刷新。", 409)
        if shop_id is not None:
            if shop_id not in {shop['id'] for shop in record.get('shops', [])}:
                raise AuthFailure('shop_denied', '该店铺不在当前账号的授权列表中。', 403)
            return self.shop_sessions.credentials(record, shop_id)
        if record.get('multi_shop'):
            if record.get('kdt_id') not in {shop['id'] for shop in record.get('shops', [])}:
                raise AuthFailure('shop_denied', '当前默认店铺不在授权店铺列表中，请重新选择。', 403)
            return self.shop_sessions.credentials(record, record['kdt_id'])
        if not record.get('csrf', {}).get('secret') or not record.get('kdt_id'):
            raise AuthFailure('connection_required', '请先选择店铺并刷新有赞会话。', 409)
        secret = record.get("category_credentials", {}).get("secret")
        category = json.loads(self._decrypt(secret)) if secret else {}
        return {"sid": self._decrypt(record["sid"]["secret"]), "csrf": self._decrypt(record["csrf"]["secret"]),
                "kdt_id": record["kdt_id"], "shop_name": next((s["name"] for s in record.get("shops", []) if s["id"] == record["kdt_id"]), ""),
                "cookies": category.get("cookies", {}), "origin": category.get("origin", "https://store.youzan.com")}

    async def _browser_login(self, account, password, profile, progress):
        return await YouzanLoginAdapter(ROOT)(account, password, profile, progress)

    async def cancel(self, user):
        task = self.tasks.get(owner_key(user))
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # Done callbacks run on the loop; remove now so a retry can start immediately.
            self.tasks.pop(owner_key(user), None)
            with self.repo.transaction() as state:
                record = state["connections"].get(owner_key(user))
                if record and record.get("job", {}).get("status") in ACTIVE:
                    record["job"].update(status="failed", code="cancelled", message=MESSAGES["cancelled"], finished_at=int(time.time()))
        return self.summary(user)

    async def close(self):
        if self.renewal is not None:
            await self.renewal.close()
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with self.repo.transaction() as state:
            for record in state["connections"].values():
                if record.get("job", {}).get("status") in ACTIVE:
                    record["job"].update(status="failed", code="interrupted", message=MESSAGES["interrupted"], finished_at=int(time.time()))
