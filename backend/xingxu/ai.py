"""Server-owned provider credentials, global prompt and per-user conversation history."""
import copy
import asyncio
import json
import os
import time
import uuid
import logging
import datetime as dt
from typing import Literal
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .auth import AuthFailure
from .store import JsonRepository
from .tools import COMMAND_PERMISSIONS, ExecutionContext, GalaxyDispatcher
from .tool_prompts import ToolPrompts
from .chat_runs import ChatRuns
from .tool_context import ToolInterrupted


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=1, max_length=1024)
    api_key: str | None = Field(default=None, max_length=4096)
    model: str = Field(min_length=1, max_length=160)
    max_tokens: int | None = Field(default=None, ge=1)
    enabled: StrictBool = True

    @field_validator("name", "model")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Empty field")
        return value.strip()

    @field_validator("api_key")
    @classmethod
    def key_format(cls, value):
        if value is not None and any(c in value for c in "\r\n\x00"):
            raise ValueError("Invalid key")
        return value.strip() if value is not None else None

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value):
        parts = urlsplit(value.strip())
        if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment or any(c.isspace() for c in value)):
            raise ValueError("Invalid base URL")
        try:
            parts.port
        except ValueError:
            raise ValueError("Invalid port") from None
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


class PromptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(max_length=20000)


class DefaultInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: uuid.UUID


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: uuid.UUID | None = None
    request_id: uuid.UUID | None = None
    thinking: StrictBool | None = None
    reasoning_effort: Literal['low', 'high', 'max'] | None = None

    @field_validator("message")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Empty message")
        return value.strip()


def public_provider(provider):
    return {key: provider[key] for key in ("id", "name", "base_url", "model", "max_tokens", "enabled", "created_at", "updated_at")} | {
        "has_api_key": bool(provider.get("encrypted_api_key"))}


def summary(conversation):
    return {key: conversation[key] for key in ("id", "title", "created_at", "updated_at", "model")}


class AiService:
    def __init__(self, directory: Path, transport=None, authorize=None):
        self.config = JsonRepository(directory, "ai", ("providers", "settings"))
        self.chats = JsonRepository(directory, "chats", ("conversations",))
        self.transport = transport
        self.running = set()
        self.runs = ChatRuns()
        self.dispatcher = GalaxyDispatcher(directory, authorize)
        self.tool_prompts = ToolPrompts(directory)
        # Lock also protects first-time key creation across processes.
        with self.config.transaction() as state:
            file = Path(directory) / "ai-secret.key"
            if not file.exists():
                if state["providers"] or any((Path(directory) / name).exists() and json.loads((Path(directory) / name).read_text()).get("connections") for name in ("youzan.json", "feishu.json")):
                    raise RuntimeError("AI encryption key is missing; restore it from backup")
                descriptor = os.open(file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(Fernet.generate_key())
                    output.flush()
                    os.fsync(output.fileno())
            file.chmod(0o600)
            self.cipher = Fernet(file.read_bytes())

    def settings(self):
        with self.config.transaction(write=False) as state:
            return {"providers": [public_provider(p) for p in state["providers"].values()],
                    "default_provider_id": state["settings"].get("default_provider_id"),
                    "global_prompt": state["settings"].get("global_prompt", "")}

    def save_provider(self, body: ProviderInput, identifier=None):
        updating = identifier is not None
        identifier = str(identifier or uuid.uuid4())
        now = int(time.time())
        with self.config.transaction() as state:
            old = state["providers"].get(identifier)
            if updating and not old:
                raise AuthFailure("not_found", "配置不存在。", 404)
            if not old and not body.api_key:
                raise AuthFailure("missing_api_key", "请输入 API Key。", 422)
            encrypted = self.cipher.encrypt(body.api_key.encode()).decode() if body.api_key else old["encrypted_api_key"]
            provider = {**body.model_dump(exclude={"api_key"}), "id": identifier,
                        "encrypted_api_key": encrypted, "created_at": old["created_at"] if old else now, "updated_at": now}
            state["providers"][identifier] = provider
            if state["settings"].get("default_provider_id") == identifier and not body.enabled:
                state["settings"]["default_provider_id"] = next((p["id"] for p in state["providers"].values() if p["enabled"]), None)
            if not state["settings"].get("default_provider_id") and body.enabled:
                state["settings"]["default_provider_id"] = identifier
        return public_provider(provider)

    def delete_provider(self, identifier):
        identifier = str(identifier)
        with self.config.transaction() as state:
            if identifier not in state["providers"]:
                raise AuthFailure("not_found", "配置不存在。", 404)
            del state["providers"][identifier]
            if state["settings"].get("default_provider_id") == identifier:
                state["settings"]["default_provider_id"] = next((p["id"] for p in state["providers"].values() if p["enabled"]), None)
        return {"ok": True}

    def set_default(self, identifier):
        identifier = str(identifier)
        with self.config.transaction() as state:
            provider = state["providers"].get(identifier)
            if not provider or not provider["enabled"]:
                raise AuthFailure("invalid_provider", "请选择已启用的 AI 配置。", 422)
            state["settings"]["default_provider_id"] = identifier
        return {"ok": True}

    def set_prompt(self, prompt):
        with self.config.transaction() as state:
            state["settings"]["global_prompt"] = prompt
        return {"ok": True}

    def snapshot(self, identifier=None):
        with self.config.transaction(write=False) as state:
            identifier = str(identifier) if identifier else state["settings"].get("default_provider_id")
            provider = state["providers"].get(identifier)
            if not provider or not provider["enabled"]:
                raise AuthFailure("ai_not_configured", "AI 尚未配置，请联系管理员。", 409)
            return copy.deepcopy(provider), state["settings"].get("global_prompt", "")

    def availability(self):
        try:
            provider, _ = self.snapshot()
            return {"available": True, "name": provider["name"], "model": provider["model"],
                    'thinking_controls': provider['model'].lower().startswith('deepseek')}
        except AuthFailure:
            return {"available": False, "name": None, "model": None}

    async def _model_message(self, provider, messages, *, testing=False, tools=None, thinking=None, reasoning_effort=None):
        try:
            secret = self.cipher.decrypt(provider["encrypted_api_key"].encode()).decode()
        except InvalidToken:
            raise AuthFailure("key_unavailable", "AI 凭证无法读取，请联系管理员。", 503) from None
        base = provider["base_url"].rstrip("/")
        endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
        payload = {"model": provider["model"], "messages": messages, "stream": False}
        if testing:
            payload['max_tokens'] = min(provider.get('max_tokens') or 512, 512)
        elif provider.get('max_tokens') is not None:
            payload['max_tokens'] = provider['max_tokens']
        if tools:
            payload.update({"tools": tools, "tool_choice": "auto"})
        if thinking is not None or reasoning_effort is not None:
            if not provider['model'].lower().startswith('deepseek'):
                raise AuthFailure('unsupported_thinking', '当前模型未开放思考参数，请切换到支持的模型。', 422)
            enabled = thinking is not False
            payload['thinking'] = {'type': 'enabled' if enabled else 'disabled'}
            payload['reasoning_effort'] = (reasoning_effort or 'high') if enabled else 'none'
        try:
            async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(120 if testing else None, connect=10)) as client:
                async with client.stream("POST", endpoint, headers={"Authorization": "Bearer " + secret}, json=payload) as response:
                    if response.status_code != 200:
                        message = {401: "API Key 无效，请检查配置。", 403: "API Key 无权访问该模型。",
                                   404: "接口地址或模型不存在。", 429: "AI 服务请求过多或额度不足，请稍后重试。"}.get(response.status_code, "AI 服务返回异常，请检查配置或稍后重试。")
                        raise AuthFailure("provider_error", message, 502)
                    chunks, length = [], 0
                    async for chunk in response.aiter_bytes():
                        length += len(chunk)
                        if length > 2 * 1024 * 1024:
                            raise AuthFailure("provider_error", "AI 返回内容过大，请缩小输出长度。", 502)
                        chunks.append(chunk)
            value = json.loads(b"".join(chunks))
            if value['choices'][0].get('finish_reason') == 'length':
                raise AuthFailure('output_truncated', '模型输出被供应商截断，回复未完成。可在控制台将最大输出 Token 留空或提高额度，或调整思考强度后重试。', 502)
            message = value["choices"][0]["message"]
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
            if content is not None and not isinstance(content, str):
                raise ValueError("Invalid content")
            content = content.replace(secret, "[已隐藏凭证]") if content else None
            reasoning = message.get("reasoning_content")
            if not isinstance(reasoning, str) or not reasoning.strip():
                reasoning = message.get("reasoning")
            reasoning = reasoning.replace(secret, "[已隐藏凭证]").strip() if isinstance(reasoning, str) else ""
            assistant = {"role": "assistant", "content": content}
            # Thinking-mode tool continuations require the provider's original field.
            if isinstance(message.get("reasoning_content"), str) or provider["model"].lower().startswith("deepseek"):
                assistant["reasoning_content"] = (message.get("reasoning_content") or "").replace(secret, "[已隐藏凭证]")
            raw_calls = message.get("tool_calls") or []
            if not isinstance(raw_calls, list):
                raise ValueError("Invalid tool batch")
            if raw_calls:
                calls, seen = [], set()
                for call in raw_calls:
                    function = call.get("function", {})
                    identifier, name, arguments = call.get("id"), function.get("name"), function.get("arguments")
                    if (call.get("type") != "function" or not isinstance(identifier, str) or not 1 <= len(identifier) <= 128
                            or identifier in seen or not isinstance(name, str) or not 1 <= len(name) <= 128
                            or not isinstance(arguments, str) or len(arguments.encode()) > 1024 * 1024):
                        raise ValueError("Invalid tool call")
                    seen.add(identifier)
                    calls.append({"id": identifier.replace(secret, "[已隐藏凭证]"), "type": "function",
                                  "function": {"name": name.replace(secret, "[已隐藏凭证]"),
                                               "arguments": arguments.replace(secret, "[已隐藏凭证]")}})
                assistant["tool_calls"] = calls
            raw_usage = value.get("usage", {})
            usage = {key: raw_usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                     if isinstance(raw_usage, dict) and isinstance(raw_usage.get(key), int)}
            return assistant, usage, reasoning
        except httpx.TimeoutException:
            raise AuthFailure("provider_timeout", "AI 响应超时，请稍后重试。", 504) from None
        except httpx.TransportError:
            raise AuthFailure("provider_unreachable", "无法连接 AI 服务，请检查 Base URL 和网络。", 502) from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AuthFailure("invalid_response", "接口返回格式不兼容，请检查 Chat Completions 及工具调用格式。", 502) from None

    async def complete(self, provider, messages, *, testing=False, context=None, thinking=None, reasoning_effort=None, progress=None):
        tools = [] if testing else self.dispatcher.definitions(context)
        wire = copy.deepcopy(messages)
        events, reasoning_parts, model_context, usage_total = [], [], [], {}
        if progress is not None:
            progress.update(events=events, reasoning=reasoning_parts, usage=usage_total)
        try:
            while True:
                assistant, usage, reasoning = await self._model_message(provider, wire, testing=testing, tools=tools,
                    thinking=thinking, reasoning_effort=reasoning_effort)
                for key, count in usage.items():
                    usage_total[key] = usage_total.get(key, 0) + count
                if reasoning:
                    reasoning_parts.append(reasoning)
                model_context.append(assistant)
                calls = assistant.get('tool_calls', [])
                if not calls:
                    content = assistant.get('content')
                    if not content or not content.strip():
                        raise AuthFailure('empty_response', '模型没有返回正文，请重试或调整思考设置。', 502)
                    return content, usage_total, '\n\n'.join(reasoning_parts), events, model_context
                if testing or context is None:
                    raise AuthFailure('tool_context_required', '当前请求未授权使用工具。', 403)
                wire.append(assistant)
                for call in calls:
                    try:
                        event = await self.dispatcher.execute(context, call)
                    except ToolInterrupted as error:
                        events.append(error.event)
                        raise
                    events.append(event)
                    tool_message = {'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(event['result'], ensure_ascii=False)}
                    wire.append(tool_message)
                    model_context.append(tool_message)
        except AuthFailure as error:
            logging.getLogger(__name__).warning('chat_turn_failed code=%s completed_tool_calls=%d', error.code, len(events))
            raise

    async def test(self, identifier):
        provider, _ = self.snapshot(identifier)
        start = time.monotonic()
        content, _, _, _, _ = await self.complete(provider, [{"role": "user", "content": "请只回复 OK。"}], testing=True)
        return {"ok": True, "latency_ms": round((time.monotonic() - start) * 1000), "model": provider["model"], "reply": content[:160]}

    def list_conversations(self, user):
        with self.chats.transaction(write=False) as state:
            values = [summary(c) for c in state["conversations"].values() if c["user_id"] == user["id"] and c["workspace_id"] == user["workspace_id"]]
        return {"conversations": sorted(values, key=lambda c: c["updated_at"], reverse=True)[:100]}

    def conversation(self, user, identifier, *, include_context=False):
        with self.chats.transaction(write=False) as state:
            conversation = state["conversations"].get(str(identifier))
            if not conversation or conversation["user_id"] != user["id"] or conversation["workspace_id"] != user["workspace_id"]:
                raise AuthFailure("not_found", "对话不存在。", 404)
            return copy.deepcopy(conversation) if include_context else self.visible_conversation(conversation)

    @staticmethod
    def visible_conversation(conversation):
        result = copy.deepcopy(conversation)
        for message in result["messages"]:
            message.pop("model_context", None)
        return result

    def delete_conversation(self, user, identifier):
        identifier = str(identifier)
        with self.chats.transaction() as state:
            conversation = state["conversations"].get(identifier)
            if not conversation or conversation["user_id"] != user["id"] or conversation["workspace_id"] != user["workspace_id"]:
                raise AuthFailure("not_found", "对话不存在。", 404)
            if identifier in self.running:
                raise AuthFailure("conversation_busy", "请等待当前回复完成。", 409)
            del state["conversations"][identifier]
        self.dispatcher.files.delete_conversation(user, identifier)
        return {"ok": True}

    async def send(self, user, body: ChatInput, *, session_token=""):
        identifier = str(body.conversation_id or uuid.uuid4())
        # Ownership is checked before busy state and before any upstream call.
        existing = self.conversation(user, identifier, include_context=True) if body.conversation_id else None
        if identifier in self.running:
            raise AuthFailure("conversation_busy", "当前对话正在回复，请稍后发送。", 409)
        run = self.runs.begin(user, body.request_id or uuid.uuid4())
        self.running.add(identifier)
        try:
            provider, prompt = self.snapshot()
            context = ExecutionContext(user["id"], user["workspace_id"], tuple(user.get("permissions", [])), identifier, session_token)
            history = existing["messages"] if existing else []
            selected, total = [], len(body.message)
            for item in reversed(history[-40:]):
                bundle = copy.deepcopy(item.get("model_context")) if item["role"] == "assistant" else None
                if not bundle:
                    entry = {"role": item["role"], "content": item["content"]}
                    if item["role"] == "assistant" and provider["model"].lower().startswith("deepseek"):
                        entry["reasoning_content"] = item.get("reasoning_content", "")
                    bundle = [entry]
                total += len(json.dumps(bundle, ensure_ascii=False))
                if total > 80000:
                    break
                selected[0:0] = bundle
            while selected and selected[0]["role"] != "user":
                selected.pop(0)
            now_local = dt.datetime.now(ZoneInfo('Asia/Shanghai'))
            for key, value in {'current_time': now_local.strftime('%Y-%m-%d %H:%M:%S'),
                'today': now_local.date().isoformat(), 'yesterday': (now_local.date() - dt.timedelta(days=1)).isoformat()}.items():
                prompt = prompt.replace('{{' + key + '}}', value)
            messages = ([{"role": "system", "content": prompt}] if prompt else []) + self.tool_prompts.messages(user, COMMAND_PERMISSIONS) + selected + [{"role": "user", "content": body.message}]
            progress, interrupted = {}, False
            try:
                if run.stop_requested:
                    raise asyncio.CancelledError()
                content, usage, reasoning, events, model_context = await self.complete(provider, messages, context=context,
                    thinking=body.thinking, reasoning_effort=body.reasoning_effort, progress=progress)
            except asyncio.CancelledError:
                if not run.stop_requested:
                    raise
                interrupted = True
                content, usage = '已停止回复。', progress.get('usage', {})
                reasoning, events = '\n\n'.join(progress.get('reasoning', [])), progress.get('events', [])
                # Store a valid assistant message, never an unfinished tool-call bundle.
                context_entry = {'role': 'assistant', 'content': content + (
                    '\n已记录的工具结果：' + json.dumps(events, ensure_ascii=False) if events else '')}
                if provider['model'].lower().startswith('deepseek'):
                    context_entry['reasoning_content'] = ''
                model_context = [context_entry]
            now = int(time.time())
            # Commit only a completed turn; failures leave the saved history unchanged.
            conversation = existing or {"id": identifier, "user_id": user["id"], "workspace_id": user["workspace_id"],
                "title": body.message[:36], "created_at": now, "messages": []}
            conversation["messages"] += [{"id": str(uuid.uuid4()), "role": "user", "content": body.message, "created_at": now},
                {"id": str(uuid.uuid4()), "role": "assistant", "content": content, "created_at": now,
                 "model_context": model_context,
                 **({'interrupted': True} if interrupted else {}),
                 **({'thinking': body.thinking} if body.thinking is not None else {}),
                 **({'reasoning_effort': body.reasoning_effort} if body.reasoning_effort is not None else {}),
                 **({"reasoning_content": reasoning} if reasoning else {}),
                 **({"tool_events": events} if events else {})}]
            conversation.update({"updated_at": now, "model": provider["model"], "provider_name": provider["name"]})
            with self.chats.transaction() as state:
                state["conversations"][identifier] = conversation
            return {"conversation": self.visible_conversation(conversation), "usage": usage, 'interrupted': interrupted}
        finally:
            self.running.discard(identifier)
            self.runs.finish(run)

    async def cancel(self, user, request_id):
        return await self.runs.stop(user, request_id)
