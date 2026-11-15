import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from xingxu.ai import AiService, ChatInput, ProviderInput
from xingxu.app import create_app
from xingxu.auth import AuthFailure

SECRET = "disposable-test-api-key-123456789"
PROVIDER = {"name": "Test AI", "base_url": "https://provider.example/v1", "api_key": SECRET,
            "model": "fixture-model", "max_tokens": 4096, "enabled": True}


@pytest.fixture
def setup(tmp_path):
    requests = []

    def upstream(request):
        body = json.loads(request.content)
        requests.append((str(request.url), dict(request.headers), body))
        return httpx.Response(200, json={"choices": [{"message": {"content": "回复：" + body["messages"][-1]["content"]}}],
                                         "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18}})

    app = create_app(tmp_path, ai_transport=httpx.MockTransport(upstream))
    auth = app.state.auth
    auth.import_accounts([("admin", "test-admin-password"), ("employee", "test-employee-password")])
    admin = auth.login("admin", "test-admin-password", False, "127.0.0.1")
    employee = auth.login("employee", "test-employee-password", False, "127.0.0.1")
    with TestClient(app) as client:
        yield app, client, {"Authorization": "Bearer " + admin["access_token"]}, {"Authorization": "Bearer " + employee["access_token"]}, requests


def test_provider_encryption_keep_key_and_real_test_request(setup, tmp_path):
    app, client, admin, _, requests = setup
    created = client.post("/api/v1/admin/ai/providers", headers=admin, json=PROVIDER)
    assert created.status_code == 200
    provider = created.json()
    assert provider["has_api_key"] is True
    assert "api_key" not in provider and "encrypted_api_key" not in provider
    assert SECRET not in (tmp_path / "ai.json").read_text()
    assert (tmp_path / "ai-secret.key").stat().st_mode & 0o777 == 0o600
    updated = client.put(f"/api/v1/admin/ai/providers/{provider['id']}", headers=admin, json={**PROVIDER, "api_key": "", "name": "Updated AI"})
    assert updated.status_code == 200
    tested = client.post(f"/api/v1/admin/ai/providers/{provider['id']}/test", headers=admin)
    assert tested.status_code == 200
    assert requests[-1][0] == "https://provider.example/v1/chat/completions"
    assert requests[-1][1]["authorization"] == "Bearer " + SECRET
    assert requests[-1][2]["model"] == PROVIDER["model"]
    assert tested.json()["latency_ms"] >= 0
    restarted = AiService(tmp_path, app.state.ai.transport)
    assert restarted.snapshot()[0]["name"] == "Updated AI"
    assert asyncio.run(restarted.test(provider["id"]))["ok"] is True
    (tmp_path / "ai-secret.key").unlink()
    with pytest.raises(RuntimeError):
        AiService(tmp_path)


def test_employee_cannot_read_write_or_test_admin_configuration(setup):
    _, client, admin, employee, _ = setup
    identifier = client.post("/api/v1/admin/ai/providers", headers=admin, json=PROVIDER).json()["id"]
    cases = [("GET", "/api/v1/admin/ai", None), ("POST", "/api/v1/admin/ai/providers", PROVIDER),
             ("PUT", f"/api/v1/admin/ai/providers/{identifier}", PROVIDER),
             ("DELETE", f"/api/v1/admin/ai/providers/{identifier}", None),
             ("POST", f"/api/v1/admin/ai/providers/{identifier}/test", None),
             ("PUT", "/api/v1/admin/ai/default", {"provider_id": identifier}),
             ("PUT", "/api/v1/admin/ai/prompt", {"prompt": "an unauthorized change"})]
    for method, url, body in cases:
        response = client.request(method, url, headers=employee, json=body)
        assert response.status_code == 403
        assert SECRET not in response.text
    status = client.get("/api/v1/ai/status", headers=employee).json()
    assert status["available"] is True
    assert "base_url" not in status and "global_prompt" not in status


def test_global_prompt_server_context_and_cross_user_history(setup):
    _, client, admin, employee, requests = setup
    client.post("/api/v1/admin/ai/providers", headers=admin, json=PROVIDER)
    prompt = "你是公司的业务助手，请回答简洁。" * 500
    assert client.put("/api/v1/admin/ai/prompt", headers=admin, json={"prompt": prompt}).status_code == 200
    result = client.post("/api/v1/chat/send", headers=employee, json={"message": "员工的问题"})
    assert result.status_code == 200
    conversation = result.json()["conversation"]
    assert "reasoning_content" not in conversation["messages"][-1]
    assert requests[-1][2]["messages"][0] == {"role": "system", "content": prompt}
    assert requests[-1][2]["messages"][-1] == {"role": "user", "content": "员工的问题"}
    cid = conversation["id"]
    assert len(client.get("/api/v1/chat/conversations", headers=employee).json()["conversations"]) == 1
    assert client.get("/api/v1/chat/conversations", headers=admin).json()["conversations"] == []
    count = len(requests)
    assert client.get(f"/api/v1/chat/conversations/{cid}", headers=admin).status_code == 404
    assert client.delete(f"/api/v1/chat/conversations/{cid}", headers=admin).status_code == 404
    assert client.post("/api/v1/chat/send", headers=admin, json={"message": "cross-user attempt", "conversation_id": cid}).status_code == 404
    assert len(requests) == count
    assert client.post("/api/v1/chat/send", headers=employee, json={"message": "下一个问题", "conversation_id": cid}).status_code == 200
    roles = [item["role"] for item in requests[-1][2]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    invalid = client.post("/api/v1/chat/send", headers=employee, json={"message": "override", "system_prompt": "wrong"})
    assert invalid.status_code == 422
    assert client.delete(f"/api/v1/chat/conversations/{cid}", headers=employee).status_code == 200
    assert client.get(f"/api/v1/chat/conversations/{cid}", headers=employee).status_code == 404


def test_provider_errors_do_not_leak_keys_or_save_failed_turns(setup):
    app, client, admin, employee, _ = setup
    identifier = client.post("/api/v1/admin/ai/providers", headers=admin, json=PROVIDER).json()["id"]
    app.state.ai.transport = httpx.MockTransport(lambda _: httpx.Response(401, json={"error": "rejected key: " + SECRET}))
    response = client.post(f"/api/v1/admin/ai/providers/{identifier}/test", headers=admin)
    assert response.status_code == 502 and SECRET not in response.text
    assert client.post("/api/v1/chat/send", headers=employee, json={"message": "test failure"}).status_code == 502
    assert client.get("/api/v1/chat/conversations", headers=employee).json()["conversations"] == []
    invalid = client.post("/api/v1/admin/ai/providers", headers=admin, json={**PROVIDER, "base_url": "https://user:password@provider.example/v1"})
    assert invalid.status_code == 422
    assert "password" not in invalid.text and SECRET not in invalid.text


def test_default_switch_and_disable_fallback(setup):
    _, client, admin, employee, _ = setup
    first = client.post("/api/v1/admin/ai/providers", headers=admin, json=PROVIDER).json()["id"]
    second = client.post("/api/v1/admin/ai/providers", headers=admin, json={**PROVIDER, "name": "Second", "model": "second-model"}).json()["id"]
    assert client.put("/api/v1/admin/ai/default", headers=admin, json={"provider_id": second}).status_code == 200
    assert client.get("/api/v1/ai/status", headers=employee).json()["model"] == "second-model"
    client.put(f"/api/v1/admin/ai/providers/{second}", headers=admin, json={**PROVIDER, "enabled": False})
    assert client.get("/api/v1/admin/ai", headers=admin).json()["default_provider_id"] == first
    client.delete(f"/api/v1/admin/ai/providers/{first}", headers=admin)
    assert client.get("/api/v1/ai/status", headers=employee).json()["available"] is False
    assert client.post("/api/v1/chat/send", headers=employee, json={"message": "no model"}).status_code == 409


def test_concurrent_users_and_same_conversation_busy_are_isolated(tmp_path):
    async def upstream(request):
        body = json.loads(request.content)
        await asyncio.sleep(0.02)
        return httpx.Response(200, json={"choices": [{"message": {"content": body["messages"][-1]["content"]}}]})

    service = AiService(tmp_path, httpx.MockTransport(upstream))
    service.save_provider(ProviderInput(**PROVIDER))
    a, b = {"id": "user-a", "workspace_id": "default"}, {"id": "user-b", "workspace_id": "default"}

    async def run():
        first, second = await asyncio.gather(service.send(a, ChatInput(message="a-only")), service.send(b, ChatInput(message="b-only")))
        assert first["conversation"]["messages"][-1]["content"] == "a-only"
        assert second["conversation"]["messages"][-1]["content"] == "b-only"
        cid = first["conversation"]["id"]
        results = await asyncio.gather(service.send(a, ChatInput(message="next", conversation_id=cid)),
                                       service.send(a, ChatInput(message="duplicate", conversation_id=cid)), return_exceptions=True)
        assert sum(isinstance(item, AuthFailure) and item.status == 409 for item in results) == 1
        assert len(service.list_conversations(a)["conversations"]) == 1
        assert len(service.list_conversations(b)["conversations"]) == 1
        with pytest.raises(AuthFailure):
            service.conversation(b, cid)

    asyncio.run(run())


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning"])
def test_provider_reasoning_persists_and_preserves_tool_mode_protocol(setup, tmp_path, field):
    app, client, admin, employee, _ = setup
    sent = []

    def upstream(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "最终回复", field: "供应商返回的推理文本 " + SECRET}}]})

    app.state.ai.transport = httpx.MockTransport(upstream)
    client.post("/api/v1/admin/ai/providers", headers=admin, json=PROVIDER)
    result = client.post("/api/v1/chat/send", headers=employee, json={"message": "测试推理字段"})
    assert result.status_code == 200
    conversation = result.json()["conversation"]
    reply = conversation["messages"][-1]
    assert reply["content"] == "最终回复"
    assert reply["reasoning_content"] == "供应商返回的推理文本 [已隐藏凭证]"
    assert SECRET not in result.text
    saved = client.get(f"/api/v1/chat/conversations/{conversation['id']}", headers=employee).json()
    assert saved["messages"][-1]["reasoning_content"] == reply["reasoning_content"]
    assert client.get(f"/api/v1/chat/conversations/{conversation['id']}", headers=admin).status_code == 404
    restarted = AiService(tmp_path)
    user = app.state.auth.identity(employee["Authorization"].removeprefix("Bearer "))["user"]
    assert restarted.conversation(user, conversation["id"])["messages"][-1]["reasoning_content"] == reply["reasoning_content"]
    followup = client.post("/api/v1/chat/send", headers=employee, json={"message": "继续", "conversation_id": conversation["id"]})
    assert followup.status_code == 200
    assert all("供应商返回的推理文本" not in message["content"] for message in sent[-1]["messages"])
    if field == "reasoning_content":
        previous = next(item for item in sent[-1]["messages"] if item["role"] == "assistant")
        assert previous["reasoning_content"] == reply["reasoning_content"]
    else:
        assert all(set(message) == {"role", "content"} for message in sent[-1]["messages"])
    assert "model_context" not in saved["messages"][-1]
