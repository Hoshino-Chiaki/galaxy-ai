import hashlib
import secrets
import time
import uuid
from threading import BoundedSemaphore
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError

from .store import JsonRepository

HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(24))
PASSWORD_WORKERS = BoundedSemaphore(4)
# 后续权限管理须统一覆盖角色、操作和数据范围，同时作用于管理员 Cookie
# 接通的网页接口以及 CLI / OpenAPI。下面只是当前默认授权；平台管理员凭证
# 属于服务器的服务凭证，不代表每位登录员工都继承该凭证的权限。
COMPANY_ATTENDANCE_READ = 'feishu:attendance:company:read'
ROLES = {
    "admin": ["workspace:read", "ai:chat", "tools:galaxy_cli", "galaxy:help", "users:manage", "ai:configure", "youzan:configure", "youzan:customer:read", "youzan:report:read", "youzan:order:read", "excel:export", "file:publish", "file:read"],
    "employee": ["workspace:read", "ai:chat", "tools:galaxy_cli", "galaxy:help", "youzan:configure", "youzan:customer:read", "youzan:report:read", "youzan:order:read", "excel:export", "file:publish", "file:read"],
}
CATALOG_READ=['youzan:material:read','youzan:product:read']
CATALOG_WRITE=['youzan:material:category:create','youzan:material:category:update','youzan:material:upload',
    'youzan:product:update','youzan:product:price:update','youzan:product:stock:update','youzan:product:display:update']
ROLES['admin'].extend(CATALOG_READ+CATALOG_WRITE)
ROLES['employee'].extend(CATALOG_READ)
for role in ROLES.values():role.extend(['feishu:chat', 'feishu:configure', 'feishu:read', 'feishu:write', 'feishu:create', 'feishu:delete', 'feishu:update', 'task:upload', 'task:har:read', 'guanjia:configure'])
ROLES['admin'].append('feishu:app:configure')
ROLES['admin'].append(COMPANY_ATTENDANCE_READ)
ROLES['admin'].append('youzan:renew:configure')
ROLES['admin'].append('permissions:manage')
ROLES['admin'].append('ai:usage:read')
ROLES['admin'].append('ai:prompt:configure')
for role in ROLES.values():role.extend(['clawbot:configure','clawbot:send', 'jushuitan:configure', 'jushuitan:order:read'])
for role in ROLES.values():role.extend(['schedule:create','schedule:read','schedule:update','schedule:delete'])



class AuthFailure(Exception):
    def __init__(self, code, message, status=401, retry_after=None):
        self.code, self.message, self.status, self.retry_after = code, message, status, retry_after


@dataclass(frozen=True)
class AuthPolicy:
    session_seconds: int = 12 * 3600
    remember_seconds: int = 30 * 86400
    attempt_window: int = 15 * 60
    account_attempts: int = 10
    ip_attempts: int = 50


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def public_user(user, permissions=None):
    return {"id": user["id"], "username": user["username"], "role": user["role"],
            "workspace_id": user["workspace_id"], "permissions": ROLES.get(user["role"], []) if permissions is None else permissions}


class AuthService:
    def __init__(self, repository: JsonRepository, policy=AuthPolicy(), clock=time.time):
        self.repository, self.policy, self.clock = repository, policy, clock

    def _public(self, user):
        permissions = self.repository.permissions(user) if hasattr(self.repository, 'permissions') else None
        profile = self.repository.profile(user) if hasattr(self.repository, 'profile') else {}
        return {**public_user(user, permissions), **profile}

    def seed(self, username, password):
        username = username.strip()
        if not username or len(username) > 160 or not password or len(password) > 256:
            raise ValueError("Credentials need a nonempty account and password within size limits")
        encoded = HASHER.hash(password)
        with self.repository.transaction() as state:
            if any(user["username"] == username for user in state["users"].values()):
                return False
            if state["users"]:
                raise ValueError("Bootstrap is restricted to the first account")
            identifier = str(uuid.uuid4())
            state["users"][identifier] = {"id": identifier, "username": username,
                "password_hash": encoded, "role": "admin", "workspace_id": "default",
                "active": True, "auth_version": 1, "created_at": int(self.clock())}
        return True

    def import_accounts(self, accounts):
        """Local provisioning: first account is admin, subsequent accounts are employees.

        Existing identities, password hashes, permissions and sessions are preserved.
        Validate the whole batch before changing the store.
        """
        normalized = [(username.strip(), password) for username, password in accounts]
        if not normalized or any(not name or len(name) > 160 or not password or len(password) > 256
                                 for name, password in normalized):
            raise ValueError("Invalid credential file format")
        if len({name for name, _ in normalized}) != len(normalized):
            raise ValueError("Duplicate accounts in credential file")
        prepared = [(name, HASHER.hash(password)) for name, password in normalized]
        created = 0
        with self.repository.transaction() as state:
            for index, (username, encoded) in enumerate(prepared):
                role = "admin" if index == 0 else "employee"
                existing = next((u for u in state["users"].values() if u["username"] == username), None)
                if existing:
                    if existing["role"] != role:
                        raise ValueError("Account role conflicts with credential file order")
                    continue
                if index == 0 and state["users"]:
                    raise ValueError("The first account must match the existing administrator")
                identifier = str(uuid.uuid4())
                state["users"][identifier] = {"id": identifier, "username": username,
                    "password_hash": encoded, "role": role, "workspace_id": "default",
                    "active": True, "auth_version": 1, "created_at": int(self.clock())}
                created += 1
        return {"created": created, "existing": len(normalized) - created}

    def login(self, username, password, remember, client_ip):
        username = username.strip()
        now = int(self.clock())
        keys = ["account:" + digest(username), "ip:" + digest(client_ip)]
        limited = False
        with self.repository.transaction() as state:
            state["attempts"] = {key: [t for t in values if t > now - self.policy.attempt_window]
                for key, values in state["attempts"].items()
                if any(t > now - self.policy.attempt_window for t in values)}
            for key, maximum in zip(keys, [self.policy.account_attempts, self.policy.ip_attempts]):
                if len(state["attempts"].get(key, [])) >= maximum:
                    limited = True
            if not limited:
                for key in keys:
                    state["attempts"].setdefault(key, []).append(now)
            user = next((dict(u) for u in state["users"].values() if u["username"] == username), None)
        if limited:
            raise AuthFailure("rate_limited", "尝试次数过多，请稍后再试。", 429, self.policy.attempt_window)
        try:
            # Bound memory-heavy Argon2 work when multiple employees log in together.
            with PASSWORD_WORKERS:
                verified = HASHER.verify(user["password_hash"] if user else DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            verified = False
        if not verified or not user or not user.get("active") or user["role"] not in ROLES:
            raise AuthFailure("invalid_credentials", "账号或密码错误。")

        token = secrets.token_urlsafe(32)
        expires = now + (self.policy.remember_seconds if remember else self.policy.session_seconds)
        with self.repository.transaction() as state:
            current = state["users"].get(user["id"])
            if (not current or not current.get("active") or current["password_hash"] != user["password_hash"]
                    or current["auth_version"] != user["auth_version"] or current["role"] not in ROLES):
                raise AuthFailure("invalid_credentials", "账号或密码错误。")
            state["sessions"] = {k: s for k, s in state["sessions"].items() if s["expires_at"] > now}
            state["sessions"][digest(token)] = {"user_id": user["id"], "created_at": now,
                "expires_at": expires, "remember": remember, "auth_version": current["auth_version"]}
        return {"access_token": token, "token_type": "Bearer", "expires_at": expires,
                "remember": remember, "user": self._public(current)}

    def identity(self, token):
        if not token or len(token) > 256:
            raise AuthFailure("unauthenticated", "请先登录。")
        with self.repository.transaction(write=False) as state:
            session = state["sessions"].get(digest(token))
            user = state["users"].get(session["user_id"]) if session else None
            if not session or session["expires_at"] <= self.clock() or not user or not user.get("active"):
                raise AuthFailure("session_expired", "登录已失效，请重新登录。")
            if user["auth_version"] != session["auth_version"] or user["role"] not in ROLES:
                raise AuthFailure("session_expired", "登录已失效，请重新登录。")
        return {"user": self._public(user), "expires_at": session["expires_at"],
                "remember": session["remember"]}

    def logout(self, token):
        with self.repository.transaction() as state:
            state["sessions"].pop(digest(token), None)
