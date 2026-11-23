#!/usr/bin/env python3
"""管家婆网页登录：账号、密码 -> 已验证的 Session/Cookie。

复用已验证的网页登录流程；所有凭证只在服务器内存中处理。
"""

import json
import math
import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


DEFAULT_DOMAIN = "http://cdmzzaetsc.szbs001.gjpfz.com"
LOGIN_SERVICE = "/CarpaServer/CarpaServer.LoginService.ajax/"
MAIN_SERVICE = "/CarpaServer/CarpaServer.MainService.ajax/"


class LoginError(RuntimeError):
    pass


@dataclass
class LoginSession:
    session: httpx.Client
    domain: str
    username: str
    database: str
    database_name: str

    @property
    def sid(self):
        return self.session.cookies.get("ASP.NET_SessionId")

    @property
    def cookie(self):
        # 按目标域名生成实际请求 Cookie 头。
        return self.session.build_request("GET", self.domain + "/Default.gspx").headers.get("Cookie", "")


def list_databases(*, domain=DEFAULT_DOMAIN, timeout=15, transport=None):
    """Read the pre-login account-book choices without passwords or existing cookies."""
    with httpx.Client(trust_env=False, follow_redirects=True, transport=transport, timeout=timeout) as client:
        client.get(domain.rstrip('/') + '/Default.gspx').raise_for_status()
        response = client.post(domain.rstrip('/') + LOGIN_SERVICE + '_GetServerContextData',
            headers={'Content-Type': 'application/json; charset=utf-8', 'X-ClientType': 'SLJson',
                'X-JSONFormat': 'true', 'Referer': domain.rstrip('/') + '/ClientBin/CarpaClient.xap?v=475612175'},
            content=json.dumps({'formId': 'b6b04e7', 'clientInfo': {
                '__ConfigGUID': 'c21eae49_0', '__Params': {'name': None, 'password': None},
                '__FormGuid': str(uuid.uuid4()), '__Tag': None}}, separators=(',', ':')).encode())
        response.raise_for_status()
        try:
            context = json.loads(response.content.decode('utf-8-sig'))
            choices = context['dbList']
            if not isinstance(choices, list) or not choices:
                raise ValueError()
            result, seen = [], set()
            for item in choices:
                name, identifier = item['name'], item['database']
                if (not isinstance(name, str) or not name or not isinstance(identifier, str)
                        or not identifier or len(identifier) > 160 or identifier in seen):
                    raise ValueError()
                seen.add(identifier)
                result.append({'database': identifier, 'name': name})
            index = int(context.get('selectedIndex', 0))
            default = result[index]['database'] if 0 <= index < len(result) else None
        except (ValueError, TypeError, KeyError, IndexError, UnicodeError):
            raise LoginError('无法读取管家婆账套列表，请刷新后重试。') from None
        return {'databases': result, 'default_database': default}



def login(username, password, *, domain=DEFAULT_DOMAIN, database=None, timeout=15):
    """按抓包确认的流程登录；返回值的 session 可直接继续调用接口。

    database 未指定时，选择服务器默认账套。只获取 Cookie 不算成功，
    必须通过网页 hasLogin 和主界面上下文验证。
    """
    if not username:
        raise LoginError("账号不能为空")
    password = '' if password is None else password
    domain = domain.rstrip("/")
    hostname = urlparse(domain).hostname
    if not hostname:
        raise LoginError("服务器地址无效")
    session = httpx.Client(trust_env=False, follow_redirects=True)
    session.headers.update({
        "Accept": "*/*",
        "Referer": domain + "/ClientBin/CarpaClient.xap?v=475612175",
        "Accept-Language": "en-US,en-GB;q=0.8,en;q=0.6,zh-Hans-CN;q=0.4,zh-Hans;q=0.2",
        "Content-Type": "application/json; charset=utf-8",
        "X-ClientType": "SLJson",
        "X-JSONFormat": "true",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64; Trident/7.0; rv:11.0) like Gecko",
        "Pragma": "no-cache",
    })

    def post(path, payload):
        # SLJson 的 userRank 使用 NaN，与原始客户端一致。
        response = session.post(
            domain + path,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                            allow_nan=True).encode("utf-8"),
            timeout=timeout,
        )
        if response.status_code != 200:
            raise LoginError(f"{path.rsplit('/', 1)[-1]}：HTTP {response.status_code}")
        try:
            return json.loads(response.content.decode("utf-8-sig"))
        except (ValueError, UnicodeError) as error:
            raise LoginError("接口返回内容不是有效 JSON") from error

    try:
        response = session.get(domain + "/Default.gspx", timeout=timeout)
        response.raise_for_status()
        if not session.cookies.get("ASP.NET_SessionId"):
            raise LoginError("服务器未分配 ASP.NET_SessionId")
        defaults = {"fzUserName": username, "fzCookie": "1", "fzMode": "0",
                    "ShopGuideStep": "1", "fzCheckStand": ""}
        for name, value in defaults.items():
            session.cookies.set(name, value, domain=hostname, path="/")

        context = post(LOGIN_SERVICE + "_GetServerContextData", {
            "formId": "b6b04e7",
            "clientInfo": {
                "__ConfigGUID": "c21eae49_0",
                "__Params": {"name": None, "password": None},
                "__FormGuid": str(uuid.uuid4()), "__Tag": None,
            },
        })
        if not isinstance(context, dict) or not context.get("dbList"):
            raise LoginError("登录上下文缺少账套列表")
        databases = context["dbList"]
        if database is not None:
            chosen = next((d for d in databases if d["database"] == database), None)
            if chosen is None:
                raise LoginError("指定账套不在服务器返回的列表中")
        else:
            index = int(context.get("selectedIndex", 0))
            if not 0 <= index < len(databases):
                raise LoginError("服务器默认账套索引无效，请使用 --database 指定账套")
            chosen = databases[index]
        if not context.get("softDogInfo") or "isSoftDog" not in context:
            raise LoginError("登录上下文缺少授权信息")
        session.cookies.set("fzZTName", chosen["database"], domain=hostname, path="/")

        post(LOGIN_SERVICE + "CheckRds", {})
        server_ok = post(LOGIN_SERVICE + "CheckServerInfo", {})
        if server_ok is not True:
            raise LoginError("CheckServerInfo 未通过")
        result = post(LOGIN_SERVICE + "UserLogin", {
            "user": {
                "LoginType": 1, "mode": 0, "checkStand": "",
                "name": username, "password": password,
                # 此处需要账套显示名，Cookie 中则使用 database 编号。
                "database": chosen["name"], "lockNum": "err",
                "userRank": math.nan, "HardDiskNo": "errdisk",
                "MacAddress": "errmac", "hasDog": False, "MacAddressAll": "",
            },
            "isSoftDog": context["isSoftDog"],
            "softDogInfo": context["softDogInfo"],
            "cloudDbLst": databases,
        })
        if not isinstance(result, dict) or result.get("code") != 0:
            code = result.get("code") if isinstance(result, dict) else None
            message = result.get("message", "未知错误") if isinstance(result, dict) else "返回格式异常"
            raise LoginError(f"登录失败：code={code}，{message}")

        response = session.get(domain + "/Default.gspx", timeout=timeout)
        response.raise_for_status()
        match = re.search(r"Silverlight\.__initParams\s*=\s*'", response.text)
        if not match:
            raise LoginError("登录返回成功，但网页缺少登录状态")
        try:
            params, _ = json.JSONDecoder().raw_decode(response.text[match.end():])
        except ValueError as error:
            raise LoginError("无法解析网页登录状态") from error
        if params.get("hasLogin") is not True:
            raise LoginError("登录返回成功，但网页 hasLogin 仍为 false")
        main = post(MAIN_SERVICE + "_GetServerContextData", {
            "formId": "cb173d90",
            "clientInfo": {
                "__MainMenu": "MenuBar", "__ConfigGUID": "3ad2dc6a_0",
                "__Params": {"linkOA": "0"},
                "__FormGuid": str(uuid.uuid4()), "__Tag": None,
            },
        })
        if not isinstance(main, dict) or not isinstance(main.get("menus"), dict):
            raise LoginError("登录后无法获取主界面上下文")
        return LoginSession(session, domain, username, chosen["database"], chosen["name"])
    except Exception:
        session.close()
        raise
