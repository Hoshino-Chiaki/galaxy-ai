import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from xingxu.app import create_app
from xingxu.ai import ProviderInput
from xingxu.auth import AuthService, ROLES
from xingxu.store import JsonRepository
from xingxu.tools import ExecutionContext, GALAXY_PERMISSION, GalaxyDispatcher


def call(name="galaxy_cli", args=None, raw=None):
    return {"id": "call-example", "type": "function", "function": {"name": name,
            "arguments": raw if raw is not None else json.dumps({"args": args or ["help"]})}}


@pytest.fixture
def authorized(tmp_path):
    auth = AuthService(JsonRepository(tmp_path))
    auth.import_accounts([("admin", "test-admin-password"), ("employee", "test-employee-password")])
    result = auth.login("employee", "test-employee-password", False, "127.0.0.1")
    user = result["user"]
    context = ExecutionContext(user["id"], user["workspace_id"], tuple(user["permissions"]), "test-conversation", result["access_token"])
    return auth, context, GalaxyDispatcher(tmp_path, auth.identity)


def test_fixed_cli_exec_environment_and_user_audit(authorized, monkeypatch):
    _, context, dispatcher = authorized
    real_spawn = asyncio.create_subprocess_exec
    captured = []

    async def spy(*args, **kwargs):
        captured.append((args, kwargs))
        return await real_spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    monkeypatch.setenv("SUPPLIER_API_KEY", "secret-parent-environment")
    monkeypatch.setenv("PYTHONPATH", "/not-an-allowed-module-directory")
    result = asyncio.run(dispatcher.execute(context, call()))
    assert result["status"] == "success"
    assert any(t["tool"] == "youzan.customer-search" for t in result["result"]["data"])
    args, options = captured[0]
    assert args == (str(dispatcher.executable), "help", "--format", "json")
    assert "shell" not in options
    assert options["stdin"] == asyncio.subprocess.DEVNULL
    assert options["env"]["GALAXY_USER_ID"] == context.user_id
    assert "SUPPLIER_API_KEY" not in options["env"] and "PYTHONPATH" not in options["env"]
    assert options["env"]["HOME"] == options["cwd"]
    assert context.session_token not in json.dumps(options["env"])
    assert Path(options["cwd"]).stat().st_mode & 0o777 == 0o700
    with dispatcher.audit.transaction(write=False) as state:
        audit = state["records"][result["id"]]
        assert audit["user_id"] == context.user_id
        assert audit["status"] == "success" and audit["command"] == "galaxy-cli help"
    assert context.session_token not in dispatcher.audit.file.read_text()


@pytest.mark.parametrize("attempt", [
    call("bash"), call("python"), call("exec"),
    call(args=["bash", "-c", "id"]), call(args=["../help"]), call(args=["help;id"]),
    call(args=["help", ";", "id"]), call(args=["help", "$(id)"]),
    call(args=["help", "--format", "text"]),
    call(raw='{"args":["help"],"user_id":"another-user"}'),
    call(raw='{"args":["help"],"env":{"TOKEN":"secret-invalid-input"}}'),
    call(raw='{"args":["help"],"cwd":"/tmp"}'),
    call(raw='{"args":["help"],"executable":"/bin/sh"}'),
    call(raw='{"args":["help"],"args":["bash"]}'),
    call(raw='{"args":[true]}'),
    call(args=["help", "--input", '{"tool":"help","token":"secret-invalid-input"}']),
])
def test_unapproved_tools_and_arguments_never_start_process(authorized, attempt):
    _, context, dispatcher = authorized
    dispatcher._run = AsyncMock()
    result = asyncio.run(dispatcher.execute(context, attempt))
    assert result["status"] == "denied"
    dispatcher._run.assert_not_awaited()
    audit = dispatcher.audit.file.read_text()
    assert "secret-invalid-input" not in audit and context.session_token not in audit


def test_logout_and_identity_spoof_are_rejected(authorized):
    auth, context, dispatcher = authorized
    dispatcher._run = AsyncMock()
    spoofed = ExecutionContext("a-different-user", context.workspace_id, context.permissions, context.conversation_id, context.session_token)
    result = asyncio.run(dispatcher.execute(spoofed, call()))
    assert result["result"]["error"]["code"] == "identity_mismatch"
    auth.logout(context.session_token)
    result = asyncio.run(dispatcher.execute(context, call()))
    assert result["result"]["error"]["code"] == "session_expired"
    dispatcher._run.assert_not_awaited()


def test_command_permission_is_rechecked_after_queue(authorized):
    auth, context, dispatcher = authorized
    identities = [auth.identity(context.session_token), auth.identity(context.session_token)]
    identities[1]["user"]["permissions"] = [GALAXY_PERMISSION]
    dispatcher.authorize = lambda _: identities.pop(0)
    dispatcher._run = AsyncMock()
    result = asyncio.run(dispatcher.execute(context, call()))
    assert result["result"]["error"]["code"] == "permission_denied"
    dispatcher._run.assert_not_awaited()


@pytest.mark.parametrize("kind", ["timeout", "large_output", "invalid_output"])
def test_process_limits_fail_without_returning_raw_output(authorized, tmp_path, kind):
    _, context, dispatcher = authorized
    executable = tmp_path / "test-process"
    source = {"timeout": "import time; time.sleep(5)",
              "large_output": "import time; print('sensitive-output'*1000,flush=True); time.sleep(5)",
              "invalid_output": "print('sensitive-output')"}[kind]
    executable.write_text("#!/usr/bin/python3\n" + source + "\n")
    executable.chmod(0o700)
    dispatcher.executable = executable
    if kind == "timeout":
        dispatcher.timeout = 0.05
    elif kind == "large_output":
        dispatcher.output_limit = 100
    result = asyncio.run(dispatcher.execute(context, call()))
    assert result["status"] == "error"
    assert result["result"]["error"]["code"] == {"timeout": "cli_timeout", "large_output": "output_limit", "invalid_output": "invalid_cli_output"}[kind]
    assert "sensitive-output" not in json.dumps(result)


def test_broken_audit_prevents_execution(authorized):
    _, context, dispatcher = authorized
    dispatcher.audit.file.write_text("{broken")
    dispatcher._run = AsyncMock()
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(dispatcher.execute(context, call()))
    dispatcher._run.assert_not_awaited()


@pytest.fixture
def tool_app(tmp_path):
    app = create_app(tmp_path)
    auth = app.state.auth
    auth.import_accounts([("admin", "test-admin-password"), ("employee", "test-employee-password")])
    identity = auth.login("employee", "test-employee-password", False, "127.0.0.1")
    app.state.ai.save_provider(ProviderInput(
        name="Mock provider", base_url="https://provider.example/v1", api_key="disposable-provider-secret", model="deepseek-flash"))
    return app, identity


def test_tool_loop_only_advertises_galaxy_and_preserves_provider_context(tool_app):
    app, identity = tool_app
    seen = []

    def upstream(request):
        payload = json.loads(request.content)
        seen.append(payload)
        assert [item["function"]["name"] for item in payload["tools"]] == ["galaxy_cli"]
        if len(seen) == 1:
            message = {"role": "assistant", "content": None, "reasoning_content": "requesting the catalog", "tool_calls": [call()]}
        else:
            if len(seen) == 2:
                result = json.loads(payload["messages"][-1]["content"])
                assert "error" not in result and any(t["tool"] == "youzan.customer-search" for t in result["data"])
                assert payload["messages"][-2]["reasoning_content"] == "requesting the catalog"
            else:
                assert any(item.get("reasoning_content") == "requesting the catalog" for item in payload["messages"])
                assert any(item["role"] == "tool" for item in payload["messages"])
            message = {"role": "assistant", "content": "已读取工具目录。", "reasoning_content": "catalog received"}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    app.state.ai.transport = httpx.MockTransport(upstream)
    headers = {"Authorization": "Bearer " + identity["access_token"]}
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/send", headers=headers, json={"message": "galaxy-cli help"})
        assert response.status_code == 200
        conversation = response.json()["conversation"]
        message = conversation["messages"][-1]
        assert message["tool_events"][0]["status"] == "success"
        assert "model_context" not in message
        assert client.post("/api/v1/chat/send", headers=headers, json={"message": "继续", "conversation_id": conversation["id"]}).status_code == 200


@pytest.mark.parametrize("method", ["unknown_tool", "revoked_session", "missing_permission", "injected_arguments"])
def test_adversarial_model_requests_are_denied_in_real_chat(tool_app, monkeypatch, method):
    app, identity = tool_app
    dispatcher = app.state.ai.dispatcher
    dispatcher._run = AsyncMock()
    seen = []
    request_call = call("bash") if method == "unknown_tool" else call()
    if method == "injected_arguments":
        request_call = call(raw='{"args":["help"],"executable":"/bin/sh"}')
    if method == "missing_permission":
        monkeypatch.setitem(ROLES, "employee", ["workspace:read", "ai:chat"])

    def upstream(request):
        payload = json.loads(request.content)
        seen.append(payload)
        if len(seen) == 1:
            if method == "revoked_session":
                app.state.auth.logout(identity["access_token"])
            message = {"content": None, "reasoning_content": "tool request", "tool_calls": [request_call]}
        else:
            result = json.loads(payload["messages"][-1]["content"])
            assert "error" in result
            message = {"content": "调用已被拒绝。", "reasoning_content": "request denied"}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    app.state.ai.transport = httpx.MockTransport(upstream)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/send", headers={"Authorization": "Bearer " + identity["access_token"]}, json={"message": "attempt"})
        assert response.status_code == 200
        assert response.json()["conversation"]["messages"][-1]["tool_events"][0]["status"] == "denied"
    dispatcher._run.assert_not_awaited()


def test_plain_text_is_not_executed_and_tools_continue_beyond_old_limits(tool_app):
    app, identity = tool_app
    headers = {"Authorization": "Bearer " + identity["access_token"]}
    dispatcher = app.state.ai.dispatcher
    dispatcher._run = AsyncMock()
    app.state.ai.transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "galaxy-cli help; bash -c id"}}]}))
    with TestClient(app) as client:
        assert client.post("/api/v1/chat/send", headers=headers, json={"message": "text only"}).status_code == 200
    dispatcher._run.assert_not_awaited()
    count = []

    def repeating(_request):
        count.append(1)
        if len(count) > 8:
            return httpx.Response(200, json={"choices": [{"message": {"content": "已完成多轮处理。"}}]})
        request_call = call("bash")
        request_call["id"] += str(len(count))
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "reasoning_content": "request", "tool_calls": [request_call]}}]})

    app.state.ai.transport = httpx.MockTransport(repeating)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/send", headers=headers, json={"message": "repeat"})
        assert response.status_code == 200
        assert len(response.json()['conversation']['messages'][-1]['tool_events']) == 8
    assert len(count) == 9
    dispatcher._run.assert_not_awaited()
