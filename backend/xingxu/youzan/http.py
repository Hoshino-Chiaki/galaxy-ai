"""Youzan page policies, shop discovery and CRM credential collection."""
import asyncio
import json
import re
import time
from urllib.parse import urlencode, urljoin, urlsplit
import httpx

from ..auth import AuthFailure
from ..browser import ResponseLimitExceeded, open_session, read_page
from .constants import MESSAGES, CRM_URL, SHOP_LIST_URL, SHOP_PAGE_URL
from .csrf import extract_csrf


async def read_youzan_page(client, url, *, headers=None, params=None):
    try:
        return await read_page(client, url, headers=headers, params=params)
    except ResponseLimitExceeded:
        raise AuthFailure('upstream_error', MESSAGES['upstream_error'], 502) from None


class YouzanHttpClient:
    def __init__(self, decrypt, *, transport=None, read=None):
        self._decrypt = decrypt
        self.transport = transport
        self._read = read or read_youzan_page

    def _cookies(self, record, *, shop=False):
        saved = record.get("sid", {})
        if not saved.get("secret") or saved.get("expires_at") and saved["expires_at"] <= time.time():
            raise AuthFailure("login_required", MESSAGES["login_required"], 409)
        cookies = httpx.Cookies()
        cookies.set("sid", self._decrypt(saved["secret"]), domain=".youzan.com", path="/")
        if shop:
            cookies.set("kdt_id", record["kdt_id"], domain=".youzan.com", path="/")
        return cookies

    async def _list_shops(self, record):
        cookies = self._cookies(record)
        result, seen = [], set()
        try:
            async with open_session(transport=self.transport, cookies=cookies) as client:
                for page in range(1, 51):
                    status, headers, text = await self._read(client, SHOP_LIST_URL,
                        headers={"Accept": "application/json", "Referer": SHOP_PAGE_URL},
                        params={"pageNum": page, "pageSize": 48, "shopName": "", "filterBoundedWecomShop": "true"})
                    if status == 401 or status in {301, 302, 303, 307, 308} and urlsplit(urljoin(SHOP_LIST_URL, headers.get("location", ""))).hostname == "account.youzan.com":
                        raise AuthFailure("login_required", MESSAGES["login_required"], 502)
                    if status != 200:
                        raise AuthFailure("upstream_error", MESSAGES["upstream_error"], 502)
                    try:
                        data = json.loads(text)
                    except ValueError:
                        raise AuthFailure("upstream_error", MESSAGES["upstream_error"], 502) from None
                    if not isinstance(data, dict) or data.get("code") != 0:
                        if isinstance(data, dict) and re.search(r"登录|登陆|login", str(data.get("msg", "")), re.I):
                            raise AuthFailure("login_required", MESSAGES["login_required"], 502)
                        raise AuthFailure("upstream_error", MESSAGES["upstream_error"], 502)
                    payload = data.get("data")
                    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                        raise AuthFailure("upstream_error", MESSAGES["upstream_error"], 502)
                    rows = payload["data"]
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        identifier = str(row.get("kdtId", ""))
                        name = row.get("shopName")
                        if not re.fullmatch(r"[0-9]{1,24}", identifier) or not isinstance(name, str) or not name.strip() or identifier in seen:
                            continue
                        seen.add(identifier)
                        result.append({"id": identifier, "name": name.strip()[:160]})
                    total = payload.get("total")
                    if isinstance(total, int) and len(result) >= total or not rows:
                        return result
                raise AuthFailure("upstream_error", MESSAGES["upstream_error"], 502)
        except httpx.HTTPError:
            raise AuthFailure("upstream_error", MESSAGES["upstream_error"], 502) from None

    async def _crm_csrf(self, record):
        cookies = self._cookies(record, shop=True)
        entry = "https://www.youzan.com/v4/shop/enter-shop?" + urlencode({"kdtId": record["kdt_id"], "redirect": CRM_URL})
        allowed = {("crm.youzan.com", "/crm/member/customer/manage"), ("crm.youzan.com", "/v4/scrm/customer/manage"),
                   ("store.youzan.com", "/v4/scrm/customer/manage"), ("www.youzan.com", "/v4/shop/enter-shop"),
                   ("www.youzan.com", "/v4/scrm/customer/manage"), ("crm.youzan.com", "/v4/scrm/overview"),
                   ("www.youzan.com", "/v4/scrm/overview")}
        entered = bool(record.get("_collect_category_credentials"))
        url = entry if entered else CRM_URL
        try:
            async with asyncio.timeout(90):
                async with open_session(transport=self.transport, cookies=cookies) as client:
                    for _ in range(8):
                        parts = urlsplit(url)
                        if parts.hostname == "account.youzan.com" and parts.scheme == "https":
                            raise AuthFailure("login_required", MESSAGES["login_required"], 502)
                        if parts.hostname == "www.youzan.com" and parts.path == "/v4/shop/shop-list" and parts.scheme == "https":
                            if entered:
                                raise AuthFailure("shop_access_required", MESSAGES["shop_access_required"], 502)
                            entered = True
                            url = entry
                            parts = urlsplit(url)
                        if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443) or (parts.hostname, parts.path) not in allowed:
                            raise AuthFailure("unexpected_redirect", MESSAGES["unexpected_redirect"], 502)
                        status, headers, text = await self._read(client, url, headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0", "Referer": SHOP_PAGE_URL})
                        if status == 401:
                            raise AuthFailure("login_required", MESSAGES["login_required"], 502)
                        if status == 403:
                            raise AuthFailure("shop_access_required", MESSAGES["shop_access_required"], 502)
                        if status in {301, 302, 303, 307, 308}:
                            if not headers.get("location"):
                                break
                            next_url = urljoin(url, headers["location"])
                            # Entry is always built from our saved selection, never from a redirect query.
                            if urlsplit(next_url).path == "/v4/shop/enter-shop":
                                if entered:
                                    raise AuthFailure("shop_access_required", MESSAGES["shop_access_required"], 502)
                                entered = True
                                next_url = entry
                            url = next_url
                            continue
                        if status != 200:
                            raise AuthFailure("upstream_error", MESSAGES["upstream_error"], 502)
                        if parts.hostname not in {"crm.youzan.com", "store.youzan.com", "www.youzan.com"}:
                            raise AuthFailure("shop_access_required", MESSAGES["shop_access_required"], 502)
                        if "该账号没有相关操作权限" in text:
                            raise AuthFailure("shop_access_required", MESSAGES["shop_access_required"], 502)
                        csrf = extract_csrf(text)
                        cookie_names = {"access_token", "team_auth_key", "bifrost_csrf_token", "carmen_csrf_token"}
                        record["_category_session"] = {"origin": "https://" + parts.hostname,
                            "cookies": {cookie.name: cookie.value for cookie in client.cookies.jar
                                        if cookie.name in cookie_names and cookie.domain.lstrip('.') in {"youzan.com", "store.youzan.com", "crm.youzan.com"}}}
                        return csrf
                    raise AuthFailure("unexpected_redirect", MESSAGES["unexpected_redirect"], 502)
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise AuthFailure("upstream_error", MESSAGES["upstream_error"], 502) from None
