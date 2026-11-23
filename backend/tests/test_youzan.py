import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from xingxu.app import create_app
from xingxu.auth import AuthFailure
from xingxu.youzan import YouzanService, extract_csrf

PASSWORD = "fixture-youzan-secret-password"
SID = "fixture-sid-secret"
CSRF = "fixture-crm-secret"
BODY = {"account": "fixture-merchant", "password": PASSWORD, "kdt_id": "123456", "prepare_login": False}
URL = "/api/v1/admin/youzan"


@pytest.fixture
def setup(tmp_path):
    calls = {"browser": [], "http": [], "blocked": False, "failure": None, "redirect": False, "shops": [], "expired": False}

    async def login(account, password, profile, progress):
        calls["browser"].append((account, password, profile))
        await progress("browser_ready")
        while calls["blocked"]:
            await asyncio.sleep(.01)
        calls["expired"] = False
        if calls["failure"]:
            raise AuthFailure(calls["failure"], "private body " + PASSWORD, 502)
        return {"sid": SID + account, "expires_at": int(time.time()) + 86400}

    def upstream(request):
        if request.url.path == "/v4/shop/getShopList.json":
            calls["shops"].append(request)
            if calls["expired"]:
                return httpx.Response(401)
            return httpx.Response(200, json={"code": 0, "data": {"pageNum": 1, "pageSize": 48, "total": 2,
                "data": [{"kdtId": 123456, "shopName": "Fixture shop"}, {"kdtId": 789, "shopName": "Other fixture shop"}]}})
        if request.url.path == "/v4/shop/enter-shop":
            return httpx.Response(302, headers={"Location": "https://crm.youzan.com/crm/member/customer/manage", "Set-Cookie": "access_token=fixture-business-access-token; Domain=.youzan.com; Path=/"})
        calls["http"].append(request)
        assert str(request.url) == "https://crm.youzan.com/crm/member/customer/manage"
        assert request.method == "GET"
        assert "sid=" in request.headers["cookie"]
        assert "kdt_id=" in request.headers["cookie"]
        assert "csrf-token" not in request.headers
        assert "authorization" not in request.headers
        if calls["redirect"]:
            return httpx.Response(302, headers={"Location": "https://untrusted.example/collect"})
        return httpx.Response(200, text='<script>window._global={"csrf_token":' + json.dumps(CSRF) + '}</script>')

    app = create_app(tmp_path, youzan_transport=httpx.MockTransport(upstream), youzan_login_runner=login)
    auth = app.state.auth
    auth.import_accounts([("admin", "fixture-admin-password"), ("employee", "fixture-employee-password")])
    admin = auth.login("admin", "fixture-admin-password", False, "127.0.0.1")
    employee = auth.login("employee", "fixture-employee-password", False, "127.0.0.1")
    headers = {"Authorization": "Bearer " + admin["access_token"]}
    employee_headers = {"Authorization": "Bearer " + employee["access_token"]}
    with TestClient(app) as client:
        yield app, client, headers, employee_headers, calls, tmp_path


def wait(client, headers):
    for _ in range(200):
        response = client.get(URL, headers=headers)
        assert response.status_code == 200
        result = response.json()
        if result["job"] and result["job"].get("status") != "running":
            return result
        time.sleep(.01)
    raise AssertionError("Job did not finish")


def assert_private(response):
    text = response if isinstance(response, str) else json.dumps(response)
    for secret in (PASSWORD, SID, CSRF, "encrypted", "profile_revision"):
        assert secret not in text


def test_manual_refresh_encryption_and_csrf_only(setup):
    app, client, admin, _, calls, directory = setup
    result = client.put(URL, headers=admin, json=BODY)
    assert result.status_code == 200
    assert_private(result.text)
    assert result.json()["auto_refresh"] is False
    assert calls["browser"] == [] and calls["http"] == []
    assert PASSWORD not in (directory / "youzan.json").read_text()
    assert client.post(URL + "/refresh", headers=admin, json={"kind": "sid"}).status_code == 202
    summary = wait(client, admin)
    assert summary["job"]["status"] == "success"
    assert summary["sid"]["present"] and summary["csrf"]["present"]
    assert len(calls["browser"]) == 1 and len(calls["http"]) == 2
    assert calls["browser"][0][:2] == (BODY["account"], PASSWORD)
    assert SID + BODY["account"] in calls["http"][0].headers["cookie"]
    assert "kdt_id=123456" in calls["http"][0].headers["cookie"]
    assert_private(summary)
    disk = (directory / "youzan.json").read_text()
    for secret in (PASSWORD, SID, CSRF):
        assert secret not in disk
    record = next(iter(json.loads(disk)["connections"].values()))
    assert app.state.ai.cipher.decrypt(record["password"].encode()).decode() == PASSWORD
    assert app.state.ai.cipher.decrypt(record["sid"]["secret"].encode()).decode() == SID + BODY["account"]
    assert app.state.ai.cipher.decrypt(record["csrf"]["secret"].encode()).decode() == CSRF
    assert (directory / "youzan.json").stat().st_mode & 0o777 == 0o600
    assert client.post(URL + "/refresh", headers=admin, json={"kind": "csrf"}).status_code == 202
    assert wait(client, admin)["job"]["code"] == "all_ready"
    assert len(calls["browser"]) == 1 and len(calls["http"]) == 4
    restored = YouzanService(directory, app.state.ai.cipher)
    user = app.state.auth.identity(admin["Authorization"][7:])["user"]
    assert restored.summary(user)["csrf"]["present"]
    assert len(calls["browser"]) == 1  # restart/read never triggers work


def test_employee_connections_are_allowed_but_identities_cannot_be_overridden(setup):
    _, client, admin, employee, calls, _ = setup
    assert client.get(URL, headers=employee).json()['configured'] is False
    assert client.put(URL, headers=employee, json=BODY).status_code == 200
    assert client.get(URL, headers=admin).json()['configured'] is False
    for method, path, body in [("GET", URL, None), ("PUT", URL, BODY),
                               ("POST", URL + "/refresh", {"kind": "sid"}), ("POST", URL + "/cancel", None)]:
        assert client.request(method, path, json=body).status_code == 401
    assert client.put(URL, headers=employee, json={**BODY, "user_id": "someone-else"}).status_code == 422
    assert client.put(URL, headers=admin, json={**BODY, "profile": "/tmp/arbitrary"}).status_code == 422
    assert client.post(URL + "/refresh", headers=admin, json={"kind": "command", "command": "bash"}).status_code == 422
    assert calls["browser"] == [] and calls["http"] == []


def test_distinct_owners_get_distinct_credentials_and_profiles(setup):
    app, client, admin, other, calls, _ = setup
    # A second administrator must still have a separate personal connection.
    with app.state.auth.repository.transaction() as state:
        for user in state["users"].values():
            if user["username"] == "employee":
                user["role"] = "admin"
    client.put(URL, headers=admin, json=BODY)
    assert client.get(URL, headers=other).json()["configured"] is False
    client.put(URL, headers=other, json={**BODY, "account": "other-merchant", "kdt_id": "789"})
    for headers in (admin, other):
        client.post(URL + "/refresh", headers=headers, json={"kind": "sid"})
        assert wait(client, headers)["job"]["status"] == "success"
    assert calls["browser"][0][2] != calls["browser"][1][2]
    assert "sid=" + SID + BODY["account"] in calls["http"][0].headers["cookie"]
    assert "sid=" + SID + "other-merchant" in calls["http"][-1].headers["cookie"]
    assert BODY["account"] not in client.get(URL, headers=other).text
    assert "other-merchant" not in client.get(URL, headers=admin).text


def test_keep_password_shop_change_and_account_change_clear_binding(setup):
    _, client, admin, _, calls, _ = setup
    client.put(URL, headers=admin, json=BODY)
    client.post(URL + "/refresh", headers=admin, json={"kind": "sid"})
    assert wait(client, admin)["csrf"]["present"]
    saved = {key:value for key,value in BODY.items() if key != 'password'}
    unchanged = client.put(URL, headers=admin, json=saved).json()
    assert unchanged["sid"]["present"] and unchanged["csrf"]["present"]
    shop_changed = client.put(URL, headers=admin, json={**saved, "kdt_id": "789"}).json()
    assert shop_changed["sid"]["present"] and shop_changed["csrf"]["present"]
    blank = client.put(URL, headers=admin, json={**BODY, "account": "new", "password": ""})
    assert blank.status_code == 200 and not blank.json()['has_password']
    changed = client.put(URL, headers=admin, json={**BODY, "account": "new"}).json()
    assert not changed["sid"]["present"] and not changed["csrf"]["present"]
    client.post(URL + "/refresh", headers=admin, json={"kind": "sid"})
    assert wait(client, admin)["job"]["status"] == "success"
    assert calls["browser"][0][2] != calls["browser"][1][2]


def test_busy_cancel_and_no_automatic_retry(setup):
    _, client, admin, _, calls, _ = setup
    client.put(URL, headers=admin, json=BODY)
    calls["blocked"] = True
    assert client.post(URL + "/refresh", headers=admin, json={"kind": "sid"}).status_code == 202
    assert client.post(URL + "/refresh", headers=admin, json={"kind": "sid"}).status_code == 409
    assert client.put(URL, headers=admin, json=BODY).status_code == 409
    cancelled = client.post(URL + "/cancel", headers=admin).json()
    assert cancelled["job"]["code"] == "cancelled"
    assert cancelled["job"]["status"] == "failed"
    calls["blocked"] = False
    count = len(calls["browser"])
    time.sleep(.05)
    assert len(calls["browser"]) == count
    assert not client.get(URL, headers=admin).json()["sid"]["present"]
    client.post(URL + "/refresh", headers=admin, json={"kind": "sid"})
    assert wait(client, admin)["job"]["status"] == "success"


def test_failed_login_preserves_previous_sid_and_sanitizes_error(setup):
    _, client, admin, _, calls, _ = setup
    client.put(URL, headers=admin, json=BODY)
    client.post(URL + "/refresh", headers=admin, json={"kind": "sid"})
    initial = wait(client, admin)
    calls["failure"] = "sms_verification_required"
    calls["expired"] = True
    client.post(URL + "/refresh", headers=admin, json={"kind": "sid"})
    result = wait(client, admin)
    assert result["job"]["code"] == "sms_verification_required"
    assert result["job"]["status"] == "failed"
    assert result["sid"] == initial["sid"] and result["csrf"] == initial["csrf"]
    assert_private(result)
    assert len(calls["http"]) == 2


def test_crm_redirect_never_sends_cookies_to_redirect_host(setup):
    _, client, admin, _, calls, _ = setup
    client.put(URL, headers=admin, json=BODY)
    calls["redirect"] = True
    client.post(URL + "/refresh", headers=admin, json={"kind": "sid"})
    result = wait(client, admin)
    assert result["sid"]["present"] and not result["csrf"]["present"]
    assert result["job"]["code"] == "all_partial"
    assert all(shop['error']['code'] == 'unexpected_redirect' for shop in result['shop_sessions'])
    assert len(calls["http"]) == 2
    assert_private(result)


def test_revoked_session_cannot_commit_login_result(setup):
    app, client, admin, _, calls, _ = setup
    client.put(URL, headers=admin, json=BODY)
    user = app.state.auth.identity(admin["Authorization"][7:])["user"]
    calls["blocked"] = True
    client.post(URL + "/refresh", headers=admin, json={"kind": "sid"})
    for _ in range(100):
        if calls["browser"]:
            break
        time.sleep(.01)
    app.state.auth.logout(admin["Authorization"][7:])
    calls["blocked"] = False
    for _ in range(100):
        if app.state.youzan.summary(user)["job"]["status"] == "failed":
            break
        time.sleep(.01)
    result = app.state.youzan.summary(user)
    assert result["job"]["code"] == "forbidden"
    assert not result["sid"]["present"] and not result["csrf"]["present"]
    assert calls["http"] == []


def test_csrf_json_escaped_value_and_missing_value():
    assert extract_csrf('window._global={"csrf_token":"a\\u002db\\/c"}') == 'a-b/c'
    for document in ('<html>login</html>', '{"csrf_token":""}', '{"csrf_token":"a\\nb"}'):
        with pytest.raises(AuthFailure):
            extract_csrf(document)


def test_sid_without_shop_and_csrf_prerequisites(setup):
    _, client, admin, _, calls, _ = setup
    assert client.post(URL + "/refresh", headers=admin, json={"kind": "sid"}).status_code == 422
    client.put(URL, headers=admin, json={**BODY, "kdt_id": ""})
    assert client.post(URL + "/refresh", headers=admin, json={"kind": "csrf"}).status_code == 422
    client.post(URL + "/refresh", headers=admin, json={"kind": "sid"})
    result = wait(client, admin)
    assert result["job"]["code"] == "all_ready" and result["sid"]["present"]
    assert result["csrf"]["present"] and len(calls["http"]) == 2
    assert client.post(URL + "/refresh", headers=admin, json={"kind": "csrf"}).status_code == 422


def test_restart_marks_running_job_interrupted_without_retry(setup):
    app, client, admin, _, calls, directory = setup
    client.put(URL, headers=admin, json=BODY)
    with app.state.youzan.repo.transaction() as state:
        record = next(iter(state["connections"].values()))
        record["job"] = {"id": "fixture", "status": "running"}
    restored = YouzanService(directory, app.state.ai.cipher)
    user = app.state.auth.identity(admin["Authorization"][7:])["user"]
    assert restored.summary(user)["job"]["code"] == "interrupted"
    assert calls["browser"] == [] and calls["http"] == []


def test_missing_encryption_key_does_not_silently_replace_youzan_key(setup):
    from xingxu.ai import AiService
    _, client, admin, _, _, directory = setup
    client.put(URL, headers=admin, json=BODY)
    (directory / "ai-secret.key").unlink()
    with pytest.raises(RuntimeError):
        AiService(directory)
    assert not (directory / "ai-secret.key").exists()


@pytest.mark.parametrize("outcome", ['session_reused', 'profile_account_mismatch', 'pinned_source_changed'])
def test_fixed_login_managed_pipe_uses_only_scoped_profile(tmp_path, outcome):
    """Run the actual fixed entry with a fake browser, without sending login requests."""
    import os
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    entry = tmp_path / 'backend/youzan/login.cjs'
    entry.parent.mkdir(parents=True)
    entry.write_text((root / 'backend/youzan/login.cjs').read_text())
    for name in ['session_research/youzan-login.cjs', 'session_research/youzan-login-runtime.json',
                 'investigation/slider-captcha-lab/human_track.py', 'investigation/youzan-slider/utils.py']:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((root / name).read_bytes())
    if outcome == 'pinned_source_changed':
        (tmp_path / 'investigation/youzan-slider/utils.py').write_text('# changed source')
    shim = tmp_path / "browser-fixture.cjs"
    shim.write_text('''
const Module = require('module'), fs = require('fs'), path = require('path')
const original = Module._load, read = fs.readFileSync
fs.readFileSync = function(file, ...args) {
  if (typeof file === 'string' && path.basename(file) === 'account_password.txt') throw Error('global credentials forbidden')
  return read.call(this, file, ...args)
}
Module._load = function(name, parent, main) {
  if (name === './runtime/node_modules/playwright-core/package.json') return { version: '1.63.0' }
  if (name === './runtime/node_modules/playwright-core') return { chromium: {
    launchPersistentContext: async (profile, options) => {
      if (options.executablePath !== '/usr/sbin/chromium' || options.headless !== false || options.locale !== undefined)
        throw Error('original browser settings changed')
      if (!profile.startsWith(process.env.FIXTURE_ROOT + path.sep)) throw Error('profile not isolated')
      const page = { url: () => 'https://www.youzan.com/v4/dashboard', goto: async () => {},
        evaluate: async () => ({ user_agent: 'fixture', platform: 'fixture', language: 'zh-CN' }) }
      return { request: { get: async () => ({ status: () => 200, json: async () => ({ code: 0, data: { data: [] } }) }) }, pages: () => [page], on: () => {}, close: async () => {}, cookies: async () => [
        { name: 'sid', value: 'fixture-managed-pipe-sid', domain: '.youzan.com', expires: Date.now()/1000 + 3600 },
        { name: 'mobile', value: process.env.FIXTURE_MISMATCH === 'true' ? 'other-account' : 'fixture-account', domain: '.youzan.com' }
      ] }
    }
  } }
  return original.apply(this, arguments)
}
''')
    profile = tmp_path / 'backend/private/youzan-profiles/fixture/profile'
    process = subprocess.run(["/usr/bin/node", "--require", str(shim), str(entry), "--managed-stdin"],
                             input=json.dumps({"account": "fixture-account", "password": PASSWORD, "profile": str(profile)}),
                             capture_output=True, text=True, timeout=10,
                             env={**os.environ, "FIXTURE_ROOT": str(tmp_path), "FIXTURE_MISMATCH": str(outcome == 'profile_account_mismatch').lower()})
    rows = [json.loads(row) for row in process.stdout.splitlines()]
    secrets = [row for row in rows if row.get("event") == "managed_credentials"]
    assert PASSWORD not in process.stdout and PASSWORD not in process.stderr
    if outcome == 'pinned_source_changed':
        assert process.returncode == 23 and not secrets
        assert rows[-1]['result'] == 'browser_or_adapter_error'
        assert rows[-1]['failed_stage'] == 'verify_pinned_runtime'
    elif outcome == 'profile_account_mismatch':
        assert process.returncode == 22 and not secrets
        assert rows[-1]["result"] == "profile_account_mismatch"
    else:
        assert process.returncode == 0
        assert secrets[0]["sid"] == "fixture-managed-pipe-sid"
        assert rows[-1]["result"] == "session_reused"
    assert "report" not in rows[-1]
    assert list((tmp_path / "private").rglob("*.json")) == []
    assert not (tmp_path / 'session_research/youzan-login-result.json').exists()
    assert not (tmp_path / 'session_research/login-runs').exists()


def test_shop_names_and_selection_are_scoped_and_reuse_sid(setup):
    _, client, admin, employee, calls, _ = setup
    client.put(URL, headers=admin, json={"account": BODY["account"], "password": PASSWORD, "prepare_login": False})
    client.post(URL + "/refresh", headers=admin, json={"kind": "shops"})
    result = wait(client, admin)
    assert result["job"]["code"] == "shops_ready"
    assert result["shops"] == [{"id": "123456", "name": "Fixture shop"}, {"id": "789", "name": "Other fixture shop"}]
    assert result["kdt_id"] == "" and not result["csrf"]["present"]
    assert len(calls["browser"]) == 1 and calls["http"] == []
    for _ in range(2):
        assert client.get(URL + "/shops", headers=admin).status_code == 200
        client.post(URL + "/refresh", headers=admin, json={"kind": "shops"})
        assert wait(client, admin)["job"]["status"] == "success"
    assert len(calls["browser"]) == 1  # All subsequent checks use HTTP; no new window or login.
    assert client.put(URL + "/shop", headers=admin, json={"kdt_id": "unlisted"}).status_code == 422
    assert client.put(URL + "/shop", headers=admin, json={"kdt_id": "999999"}).status_code == 422
    selected = client.put(URL + "/shop", headers=admin, json={"kdt_id": "123456"})
    assert selected.status_code == 200 and selected.json()["kdt_id"] == "123456"
    assert not selected.json()["csrf"]["present"]  # Selection itself never refreshes credentials.
    client.post(URL + "/refresh", headers=admin, json={"kind": "sid"})
    result = wait(client, admin)
    assert result["job"]["status"] == "success" and result["csrf"]["present"]
    assert len(calls["browser"]) == 1
    changed = client.put(URL + "/shop", headers=admin, json={"kdt_id": "789"}).json()
    assert changed["sid"] == result["sid"] and changed["csrf"]["present"]
    assert client.get(URL + '/shops', headers=employee).json()['configured'] is False
    assert client.put(URL + '/shop', headers=employee, json={'kdt_id':'123456'}).status_code == 422
    assert_private(result)


def test_initial_shop_read_never_automatically_logs_in(setup):
    _, client, admin, _, calls, _ = setup
    client.put(URL, headers=admin, json={"account": BODY["account"], "password": PASSWORD, "prepare_login": False})
    assert client.get(URL + "/shops", headers=admin).json()["shops"] == []
    assert calls["browser"] == [] and calls["shops"] == []


def test_same_account_save_keeps_selected_shop_without_raw_id_input(setup):
    _, client, admin, _, _, _ = setup
    client.put(URL, headers=admin, json=BODY)
    client.post(URL + "/refresh", headers=admin, json={"kind": "shops"})
    wait(client, admin)
    result = client.put(URL, headers=admin, json={"account": BODY["account"], "prepare_login": False}).json()
    assert result["kdt_id"] == BODY["kdt_id"]
    assert result["shops"] and result["sid"]["present"]
    changed = client.put(URL, headers=admin, json={"account": "another-account", "password": PASSWORD, "prepare_login": False}).json()
    assert changed["kdt_id"] == "" and changed["shops"] == [] and not changed["sid"]["present"]


def test_crm_normal_shop_entry_and_retail_redirects(setup):
    app, client, admin, _, calls, _ = setup
    client.put(URL, headers=admin, json=BODY)
    client.post(URL + "/refresh", headers=admin, json={"kind": "shops"})
    wait(client, admin)
    requests = []
    entered = False
    def upstream(request):
        nonlocal entered
        requests.append(request)
        if request.url.host == "crm.youzan.com" and request.url.path == "/crm/member/customer/manage":
            return httpx.Response(302, headers={"Location": "/v4/scrm/customer/manage" if entered else "https://www.youzan.com/v4/shop/shop-list?redirect=hidden"})
        if request.url.path == "/v4/shop/enter-shop":
            assert request.url.params["kdtId"] == BODY["kdt_id"]
            entered = True
            return httpx.Response(302, headers={"Location": "https://crm.youzan.com/crm/member/customer/manage", "Set-Cookie": "access_token=fixture-shop-token; Domain=.youzan.com; Path=/"})
        if request.url.host == "crm.youzan.com" and request.url.path == "/v4/scrm/customer/manage":
            return httpx.Response(302, headers={"Location": "https://store.youzan.com/v4/scrm/customer/manage"})
        if request.url.host == "store.youzan.com":
            assert "access_token=fixture-shop-token" in request.headers["cookie"]
            return httpx.Response(200, text='window._global={"csrf_token":"' + CSRF + '"}')
        raise AssertionError("Unexpected request")
    app.state.youzan.transport = httpx.MockTransport(upstream)
    client.post(URL + "/refresh", headers=admin, json={"kind": "csrf"})
    result = wait(client, admin)
    assert result["job"]["code"] == "csrf_success" and result["csrf"]["present"]
    assert len(requests) == 4
    assert len(calls["browser"]) == 1
    assert_private(result)


@pytest.mark.parametrize("target,expected", [
    ("https://account.youzan.com/login?ticket=private", "login_required"),
    ("https://www.youzan.com/v4/shop/shop-list", "shop_access_required"),
    ("https://outside.example/v4/scrm/customer/manage", "unexpected_redirect"),
    ("http://crm.youzan.com/crm/member/customer/manage", "unexpected_redirect"),
    ("https://crm.youzan.com:8443/crm/member/customer/manage", "unexpected_redirect"),
])
def test_redirect_classification_and_boundaries(setup, target, expected):
    app, client, admin, _, _, _ = setup
    client.put(URL, headers=admin, json=BODY)
    client.post(URL + "/refresh", headers=admin, json={"kind": "shops"})
    wait(client, admin)
    requests = []
    def upstream(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": target})
    app.state.youzan.transport = httpx.MockTransport(upstream)
    client.post(URL + "/refresh", headers=admin, json={"kind": "csrf"})
    result = wait(client, admin)
    assert result["job"]["code"] == expected
    assert result["sid"]["present"] and not result["csrf"]["present"]
    assert all(request.url.host in {"www.youzan.com", "crm.youzan.com"} for request in requests)
    assert "private" not in json.dumps(result)


def test_shop_list_pagination_uses_only_current_sid(setup):
    app, client, admin, _, calls, _ = setup
    client.put(URL, headers=admin, json=BODY)
    client.post(URL + "/refresh", headers=admin, json={"kind": "shops"})
    wait(client, admin)
    pages = []
    def upstream(request):
        assert request.url.path == "/v4/shop/getShopList.json"
        assert "kdt_id=" not in request.headers["cookie"]
        page = int(request.url.params["pageNum"])
        pages.append(page)
        return httpx.Response(200, json={"code": 0, "data": {"total": 2, "data": [{"kdtId": 1000 + page, "shopName": "店铺 " + str(page)}]}})
    app.state.youzan.transport = httpx.MockTransport(upstream)
    result = client.get(URL + "/shops", headers=admin).json()
    assert pages == [1, 2] and [shop["name"] for shop in result["shops"]] == ["店铺 1", "店铺 2"]
    assert len(calls["browser"]) == 1
