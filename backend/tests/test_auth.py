import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from xingxu.app import create_app
from xingxu.auth import AuthFailure, AuthPolicy, AuthService, HASHER, digest
from xingxu.store import JsonRepository
from xingxu.bootstrap import read_accounts


@pytest.fixture
def service(tmp_path):
    value = AuthService(JsonRepository(tmp_path), AuthPolicy(account_attempts=100, ip_attempts=100))
    value.seed("test.admin", "sample-test-password")
    return value


def test_login_expiry_and_revocation(service):
    now = [1000]
    service.clock = lambda: now[0]
    normal = service.login("test.admin", "sample-test-password", False, "127.0.0.1")
    remembered = service.login("test.admin", "sample-test-password", True, "127.0.0.1")
    assert normal["expires_at"] == now[0] + 12 * 3600
    assert remembered["expires_at"] == now[0] + 30 * 86400
    assert service.identity(normal["access_token"])["user"]["role"] == "admin"
    raw = service.repository.file.read_text()
    assert normal["access_token"] not in raw
    assert remembered["access_token"] not in raw
    assert "sample-test-password" not in raw
    now[0] = normal["expires_at"]
    with pytest.raises(AuthFailure):
        service.identity(normal["access_token"])
    service.logout(remembered["access_token"])
    with pytest.raises(AuthFailure):
        service.identity(remembered["access_token"])


def test_password_failure_and_durable_throttle(tmp_path):
    policy = AuthPolicy(account_attempts=2)
    value = AuthService(JsonRepository(tmp_path), policy)
    value.seed("test.admin", "sample-test-password")
    for _ in range(2):
        with pytest.raises(AuthFailure) as error:
            value.login("test.admin", "incorrect", False, "127.0.0.1")
        assert error.value.status == 401
    restarted = AuthService(JsonRepository(tmp_path), policy)
    with pytest.raises(AuthFailure) as error:
        restarted.login("test.admin", "sample-test-password", False, "127.0.0.1")
    assert error.value.status == 429


def test_http_identity_permission_and_input_errors(tmp_path):
    application = create_app(tmp_path)
    service = application.state.auth
    service.seed("test.admin", "sample-test-password")
    with TestClient(application) as client:
        assert client.get("/api/v1/workspace").status_code == 401
        secret = "invalid-secret-not-to-echo"
        invalid = client.post("/api/v1/auth/login", json={"username": "test.admin", "password": secret, "extra": secret})
        assert invalid.status_code == 422
        assert secret not in invalid.text
        assert invalid.headers["cache-control"] == "no-store"
        response = client.post("/api/v1/auth/login", json={"username": "test.admin", "password": "sample-test-password", "remember": True})
        assert response.status_code == 200
        headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        assert "access_token" not in me.text and "password_hash" not in me.text
        assert client.get("/api/v1/admin/status", headers=headers).status_code == 200
        with service.repository.transaction() as state:
            next(iter(state["users"].values()))["role"] = "employee"
        assert client.get("/api/v1/admin/status", headers=headers).status_code == 403
        assert client.get("/api/v1/workspace", headers=headers).json()["user"]["role"] == "employee"
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_user_disable_and_password_change_revoke_sessions(service):
    session = service.login("test.admin", "sample-test-password", True, "127.0.0.1")
    with service.repository.transaction() as state:
        next(iter(state["users"].values()))["auth_version"] += 1
    with pytest.raises(AuthFailure):
        service.identity(session["access_token"])
    session = service.login("test.admin", "sample-test-password", True, "127.0.0.1")
    with service.repository.transaction() as state:
        next(iter(state["users"].values()))["active"] = False
    with pytest.raises(AuthFailure):
        service.identity(session["access_token"])


def test_concurrent_sessions_survive_restart(service):
    with ThreadPoolExecutor(max_workers=8) as workers:
        sessions = list(workers.map(lambda _: service.login("test.admin", "sample-test-password", False, "127.0.0.1"), range(16)))
    with service.repository.transaction(write=False) as state:
        assert len(state["sessions"]) == 16
        assert len(state["attempts"]["account:" + digest("test.admin")]) == 16
        assert len({item["access_token"] for item in sessions}) == 16
    restarted = AuthService(JsonRepository(service.repository.directory))
    for item in sessions:
        assert restarted.identity(item["access_token"])["user"]["username"] == "test.admin"


def test_corrupt_storage_fails_closed_and_bootstrap_is_idempotent(service):
    assert service.seed("test.admin", "a-different-password") is False
    with service.repository.transaction(write=False) as state:
        assert HASHER.verify(next(iter(state["users"].values()))["password_hash"], "sample-test-password")
    service.repository.file.write_text("{broken")
    with pytest.raises(json.JSONDecodeError):
        service.login("test.admin", "sample-test-password", False, "127.0.0.1")
    assert service.repository.file.read_text() == "{broken"


def test_employee_import_and_separate_identity_permissions(tmp_path):
    application = create_app(tmp_path)
    service = application.state.auth
    accounts = [("test.admin", "admin-example-password"), ("test.employee", "employee-example-password")]
    assert service.import_accounts(accounts) == {"created": 2, "existing": 0}
    admin = service.login(*accounts[0], True, "127.0.0.1")
    employee = service.login(*accounts[1], True, "127.0.0.1")
    assert admin["user"]["id"] != employee["user"]["id"]
    assert employee["user"]["role"] == "employee"
    assert "ai:configure" not in employee["user"]["permissions"]
    with TestClient(application) as client:
        a = {"Authorization": "Bearer " + admin["access_token"]}
        b = {"Authorization": "Bearer " + employee["access_token"]}
        assert client.get("/api/v1/admin/status", headers=a).status_code == 200
        assert client.get("/api/v1/admin/status", headers=b).status_code == 403
        assert client.get("/api/v1/auth/me", headers=b).json()["user"]["id"] == employee["user"]["id"]
        client.post("/api/v1/auth/logout", headers=b)
        assert client.get("/api/v1/auth/me", headers=b).status_code == 401
        assert client.get("/api/v1/auth/me", headers=a).status_code == 200
    before = service.repository.file.read_text()
    assert service.import_accounts([(name, "a-different-password") for name, _ in accounts]) == {"created": 0, "existing": 2}
    assert service.repository.file.read_text() == before


def test_invalid_import_is_atomic_and_file_preserves_password_spaces(tmp_path):
    service = AuthService(JsonRepository(tmp_path))
    with pytest.raises(ValueError):
        service.import_accounts([("same", "first"), ("same", "second")])
    assert not service.repository.file.exists()
    service.seed("original.admin", "sample-test-password")
    before = service.repository.file.read_text()
    with pytest.raises(ValueError):
        service.import_accounts([("original.admin", "preserved"), ("new.employee", "password"), ("bad", "")])
    assert service.repository.file.read_text() == before
    file = tmp_path / "input.txt"
    file.write_text("\ufefforiginal.admin\npassword with spaces\nnew.employee\nsecond password\n", encoding="utf-8")
    assert read_accounts(file) == [("original.admin", "password with spaces"), ("new.employee", "second password")]
    file.write_text("account\npassword\nincomplete\n")
    with pytest.raises(ValueError):
        read_accounts(file)
