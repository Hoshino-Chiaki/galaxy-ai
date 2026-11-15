"""Fixed, read-only Youzan adapters. No arbitrary URL, headers or cookie parameters."""
import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .registry import ToolError

CUSTOMERS = "https://store.youzan.com/v4/scrm/api/customer/manage/customerList"
BRANCHES = "https://gateway.youzan.com/api/youzan.shop.hq.store.search.afterdatapermission/1.0.0"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class YouzanClient:
    def __init__(self, credentials, *, customer_search_only=False):
        self.credentials = credentials
        self.origin = credentials.get("origin", "https://store.youzan.com")
        self.customer_search_only = customer_search_only
        allowed = {"https://store.youzan.com"}
        if customer_search_only:
            allowed.update({"https://www.youzan.com", "https://crm.youzan.com"})
        if self.origin not in allowed:
            raise ToolError("unsupported_shop", "当前工具对应有赞零售 CRM，请选择支持该客户页面的店铺。", 1)
        self.jar = http.cookiejar.CookieJar()
        # Only our category's fixed-domain cookies are accepted. Never a raw Cookie header.
        cookies = {"sid": credentials["sid"], "kdt_id": credentials["kdt_id"]}
        extra = credentials.get("cookies", {})
        for name in ("access_token", "team_auth_key", "bifrost_csrf_token", "carmen_csrf_token"):
            if isinstance(extra, dict) and isinstance(extra.get(name), str):
                cookies[name] = extra[name]
        for name, value in cookies.items():
            if not isinstance(value, str) or not value or any(c in value for c in "\r\n;\x00"):
                raise ToolError("invalid_context", "有赞分类凭证格式无效，请重新刷新。", 1)
            self.jar.set_cookie(http.cookiejar.Cookie(0, name, value, None, False, ".youzan.com", True, True,
                "/", True, True, None, True, None, None, {}))
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPCookieProcessor(self.jar))

    def _json(self, url, body=None):
        if self.origin != "https://store.youzan.com":
            if url == CUSTOMERS and self.customer_search_only:
                path = "/crm/member/customer/api/manage/customerList" if self.origin == "https://crm.youzan.com" else "/v4/scrm/api/customer/manage/customerList"
                url = self.origin + path
                if self.origin == "https://crm.youzan.com":
                    body = {**body, "needOpenId": True}
            else:
                raise ToolError("unsupported_shop", "该店铺版本暂只支持客户搜索；分店筛选及其他工具尚未适配。", 1)
        page = "/crm/member/customer/manage" if self.origin == "https://crm.youzan.com" else "/v4/scrm/customer/manage"
        headers = {"Accept": "application/json", "Origin": self.origin,
                   "Referer": self.origin + page, "User-Agent": "Mozilla/5.0"}
        if "https://" + str(urllib.parse.urlsplit(url).hostname) == self.origin:
            headers["csrf-token"] = self.credentials["csrf"]
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ToolError("upstream_limit", "有赞响应过大，请缩小 page_size。", 1)
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("Invalid response")
            return result
        except urllib.error.HTTPError as error:
            code = "credential_expired" if error.code in (301, 302, 303, 307, 308, 401) else "upstream_error"
            raise ToolError(code, "有赞请求未通过，请在控制台检查当前店铺并手动刷新分类凭证。", 1) from None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            raise ToolError("upstream_error", "有赞请求失败，请检查网络或手动刷新凭证。", 1) from None

    def branches(self):
        access = self.credentials.get("cookies", {}).get("access_token")
        if not access:
            raise ToolError("connection_required", "按分店筛选需要店铺业务 token，请先在控制台手动刷新 CRM 凭证。", 1)
        result, seen = [], set()
        for page in range(1, 21):
            params = {"access_token": access, "source": "WEB_BACK_END", "retail_source": "WEB-RETAIL-AJAX",
                      "idempotent_no": uuid.uuid4().hex, "page_size": 100, "page_no": page, "open_search_after": "false"}
            data = self._json(BRANCHES + "?" + urllib.parse.urlencode(params))
            payload = data.get("data")
            if data.get("code") != 200 or data.get("success") is not True or not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise ToolError("branch_lookup_failed", "无法读取当前账号有数据权限的分店，请手动刷新店铺业务凭证。", 1)
            rows = payload["items"]
            for row in rows:
                if not isinstance(row, dict):
                    continue
                identifier, name = str(row.get("store_kdt_id", "")), row.get("store_name")
                if identifier.isdecimal() and isinstance(name, str) and name and identifier not in seen:
                    seen.add(identifier); result.append({"id": identifier, "name": name[:160]})
            total = payload.get("paginator", {}).get("total_count")
            if not rows or isinstance(total, int) and len(result) >= total:
                return result
        raise ToolError("branch_limit", "分店数量超出单次查询上限。", 1)

    def search(self, arguments):
        from .tools.youzan.customer_search import search
        return search(self, arguments)

    def customer_get(self, arguments):
        from .tools.youzan.customer_get import customer_get
        return customer_get(self, arguments)

    def redact(self, result):
        # Redact strings recursively: replacing raw serialized numbers can corrupt JSON.
        values=[self.credentials['sid'],self.credentials['csrf'],*[v for k,v in self.credentials.get('cookies',{}).items()
            if k not in ('kdt_id','_kdt_id_','wa_kdt_id')]]
        def visit(value):
            if isinstance(value,dict):return {k:visit(v) for k,v in value.items()}
            if isinstance(value,list):return [visit(v) for v in value]
            if isinstance(value,str):
                for secret in values:
                    if isinstance(secret,str) and len(secret)>=8:value=value.replace(secret,'[已隐藏凭证]')
            return value
        return visit(result)
