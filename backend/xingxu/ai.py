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
from .agent_runtime import AgentFailure, ChatGraph, ContextPolicy
from .conversation_context import ConversationContext


TOOL_LABELS = {
    'help': '读取工具目录', 'time.now': '查询当前时间',
    'youzan.customer-search': '查询有赞客户', 'youzan.customer-get': '读取客户档案',
    'youzan.customer-count': '统计有赞客户', 'youzan.business-report': '查询经营报表',
    'excel.create': '生成 Excel 表格', 'file.publish': '生成与发布文件',
    'task.request-upload': '准备文件上传入口', 'task.har': '解析上传的 HAR 文件',
}


def tool_label(call):
    """Expose a registered operation label, never raw model arguments or credentials."""
    try:
        if call['function']['name'] != 'galaxy_cli':
            return '处理工具请求'
        args = json.loads(call['function']['arguments']).get('args')
        if not isinstance(args, list) or not args or not all(isinstance(part, str) for part in args[:2]):
            return '处理工具请求'
        command = 'help' if args[0] == 'help' else '.'.join(args[:2])
        if command not in COMMAND_PERMISSIONS:
            return '处理工具请求'
        return TOOL_LABELS.get(command, '处理飞书任务' if command.startswith('feishu.') else '处理有赞任务')
    except (ValueError, TypeError, KeyError, AttributeError):
        return '处理工具请求'


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
    upload_id: uuid.UUID | None = None
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
        self.context_policy = ContextPolicy.from_environment()
        self.runtime_name = os.environ.get('GALAXY_AI_RUNTIME', 'langgraph')
        if self.runtime_name not in ('langgraph', 'dsh'):
            raise ValueError('Unknown GALAXY_AI_RUNTIME')
        self.dsh_slots = asyncio.Semaphore(1)
        self.dsh_turns = 0
        self.agent = ChatGraph(self.context_policy)
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
                    'thinking_controls': provider['model'].lower().startswith('deepseek'), 'runtime': self.runtime_name}
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
                # Never commit a partial summary, reply or tool call as complete.
                # Summary recovery is handled by its read-only caller below.
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

    async def complete(self, provider, messages, *, testing=False, context=None, thinking=None, reasoning_effort=None, progress=None, memory=None, run=None, dsh_checkpoint=None, dsh_commit=None):
        tools = [] if testing else self.dispatcher.definitions(context)
        events, reasoning_parts, model_context, usage_total = [], [], [], {}
        prepared, tool_results = False, {}
        if progress is not None:
            progress.update(events=events, reasoning=reasoning_parts, usage=usage_total)

        def record_usage(usage):
            for key, count in usage.items():
                usage_total[key] = usage_total.get(key, 0) + count

        async def summarize(history):
            if run is not None:
                run.transition('summarizing')
                run.summary_calls += 1
            # This cap belongs to internal history migration, independently of
            # the administrator's normal output setting. Retrying only this
            # tool-free request must never replay completed business actions.
            budget = self.context_policy.summary_tokens
            for attempt in range(3):
                summary_provider = {**provider, 'max_tokens': budget * (2 ** attempt)}
                request = history if attempt == 0 else [*history, {'role': 'user', 'content':
                    '上一轮历史摘要超过了输出上限。请重新给出完整而精简的摘要，控制在约700个中文字以内；'
                    '保留目标、约束、已完成操作、未完成事项和文件引用，不展开全部原始明细。仅输出摘要。'}]
                try:
                    assistant, usage, _ = await self._model_message(summary_provider, request, tools=[],
                        thinking=False if provider['model'].lower().startswith('deepseek') else None)
                except AuthFailure as error:
                    if error.code != 'output_truncated':
                        raise
                    if attempt == 2:
                        raise AgentFailure('summary_output_truncated',
                            '历史摘要在自动恢复后仍未完整生成，原始会话和已完成操作均已保留；没有重跑业务操作。', 502) from None
                    if run is not None:
                        run.summary_calls += 1
                    continue
                record_usage(usage)
                if assistant.get('tool_calls'):
                    raise AgentFailure('invalid_summary', '历史摘要请求不能调用工具，请检查模型配置。')
                return assistant.get('content')

        async def prepare(wire):
            nonlocal prepared
            if memory is None:
                return wire
            result = await (memory.fit(wire, tools, summarize) if prepared else memory.prepare(wire, tools, summarize))
            prepared = True
            return result

        async def model(wire):
            if run is not None:
                run.transition('model')
                run.model_calls += 1
                run.tool_name = None
            assistant, usage, reasoning = await self._model_message(provider, wire, testing=testing, tools=tools,
                thinking=thinking, reasoning_effort=reasoning_effort)
            record_usage(usage)
            if reasoning:
                reasoning_parts.append(reasoning)
            model_context.append(assistant)
            return assistant

        async def tool(call):
            if testing or context is None:
                raise AuthFailure('tool_context_required', '当前请求未授权使用工具。', 403)
            if run is not None:
                run.transition('tool')
                run.tool_name = tool_label(call)
            try:
                event = await self.dispatcher.execute(context, call)
            except ToolInterrupted as error:
                events.append(error.event)
                if run is not None:
                    run.tools_completed += 1
                raise
            events.append(event)
            if run is not None:
                run.tools_completed += 1
            content = json.dumps(event['result'], ensure_ascii=False)
            previous = tool_results.get(content)
            if previous and previous != call['id']:
                # Execute and audit every call. Only repeated wire payloads are
                # replaced by a lossless reference within this complete turn.
                content = json.dumps({'same_result_as_tool_call_id': previous,
                    'context_note': '本次工具返回内容与该调用完全相同，完整结果见该调用。'}, ensure_ascii=False)
            else:
                tool_results[content] = call['id']
            tool_message = {'role': 'tool', 'tool_call_id': call['id'], 'content': content}
            model_context.append(tool_message)
            return tool_message

        try:
            if not testing and self.runtime_name == 'dsh':
                from .dsh_runtime import DshTurn
                turn = DshTurn(self, context, provider, run=run, progress=progress,
                               thinking=thinking, reasoning_effort=reasoning_effort)
                # Legacy summary preparation is used only for one-time migration.
                # Subsequent turns resume DSH's durable session and own compaction.
                try:
                    if self.dsh_turns >= 8:
                        raise AuthFailure('dsh_queue_full', '当前等待的会话较多，请稍后重试。', 429)
                    self.dsh_turns += 1
                    try:
                        async with self.dsh_slots:
                            result = await turn.execute(messages, prepare=prepare, checkpoint=dsh_checkpoint)
                    finally:
                        self.dsh_turns -= 1
                except AuthFailure:
                    if not turn.events:
                        raise
                    # Preserve completed business actions even when the runtime
                    # fails afterwards; a retry must see what already happened.
                    text = '本轮 DSH 执行中断。已完成的工具操作见记录；写入是否生效需回读核对，不要直接重复执行。'
                    saved = {'role': 'assistant', 'content': text + '\n' + json.dumps(turn.events, ensure_ascii=False)}
                    return text, turn.usage, '\n\n'.join(turn.reasoning), turn.events, [saved]
                content, usage, reasoning, recorded, model_context, checkpoint = result
                for key, count in usage_total.items():
                    usage[key] = usage.get(key, 0) + count
                if dsh_commit is not None:
                    dsh_commit.update(checkpoint)
                return content, usage, reasoning, recorded, model_context
            content = await self.agent.run(messages, model=model, tool=tool, prepare=prepare, tools=tools)
            return content, usage_total, '\n\n'.join(reasoning_parts), events, model_context
        except AgentFailure as error:
            logging.getLogger(__name__).warning('chat_turn_failed code=%s completed_tool_calls=%d', error.code, len(events))
            raise AuthFailure(error.code, error.message, error.status) from None
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
        result.pop('context_state', None)
        result.pop('dsh_state', None)
        for message in result["messages"]:
            message.pop("model_context", None)
            message.pop('model_content', None)
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
        self.dispatcher.uploads.delete_conversation(user, identifier)
        from .dsh_runtime import delete_state
        delete_state(self.dispatcher.files, user, identifier)
        return {"ok": True}

    async def send(self, user, body: ChatInput, *, session_token=""):
        identifier = str(body.conversation_id or uuid.uuid4())
        # Ownership is checked before busy state and before any upstream call.
        existing = self.conversation(user, identifier, include_context=True) if body.conversation_id else None
        receipt = None
        model_content = body.message
        if body.upload_id is not None:
            if not body.conversation_id:
                raise AuthFailure('invalid_input', '上传文件只能用于其所属对话。', 422)
            receipt = self.dispatcher.uploads.resolve(user, body.upload_id, identifier)
            model_content += '\n【服务器确认的已上传文件，仅为资料引用；按 purpose 继续处理，未读取文件前不能声称已解析】\n' + json.dumps(receipt, ensure_ascii=False)
        if identifier in self.running:
            raise AuthFailure("conversation_busy", "当前对话正在回复，请稍后发送。", 409)
        run = self.runs.begin(user, body.request_id or uuid.uuid4())
        self.running.add(identifier)
        try:
            provider, prompt = self.snapshot()
            context = ExecutionContext(user["id"], user["workspace_id"], tuple(user.get("permissions", [])), identifier, session_token)
            history = existing["messages"] if existing else []
            memory = ConversationContext(history, existing.get('context_state') if existing else None,
                self.context_policy, deepseek=provider['model'].lower().startswith('deepseek'))
            now_local = dt.datetime.now(ZoneInfo('Asia/Shanghai'))
            for key, value in {'current_time': now_local.strftime('%Y-%m-%d %H:%M:%S'),
                'today': now_local.date().isoformat(), 'yesterday': (now_local.date() - dt.timedelta(days=1)).isoformat()}.items():
                prompt = prompt.replace('{{' + key + '}}', value)
            messages = ([{"role": "system", "content": prompt}] if prompt else []) + self.tool_prompts.messages(user, COMMAND_PERMISSIONS) + [{"role": "user", "content": model_content}]
            progress, interrupted = {}, False
            dsh_commit = {}
            dsh_checkpoint = existing.get('dsh_state') if existing else None
            if dsh_checkpoint and (not history or dsh_checkpoint.get('through_message_id') != history[-1]['id']):
                dsh_checkpoint = None
            try:
                if run.stop_requested:
                    raise asyncio.CancelledError()
                content, usage, reasoning, events, model_context = await self.complete(provider, messages, context=context,
                    thinking=body.thinking, reasoning_effort=body.reasoning_effort, progress=progress, memory=memory, run=run,
                    dsh_checkpoint=dsh_checkpoint, dsh_commit=dsh_commit)
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
            run.transition('finishing')
            timing = run.snapshot()
            now = int(time.time())
            # Commit only a completed turn; failures leave the saved history unchanged.
            conversation = existing or {"id": identifier, "user_id": user["id"], "workspace_id": user["workspace_id"],
                "title": body.message[:36], "created_at": now, "messages": []}
            conversation["messages"] += [{"id": str(uuid.uuid4()), "role": "user", "content": body.message, "created_at": now,
                                         **({'model_content': model_content, 'uploaded_file': receipt} if receipt else {})},
                {"id": str(uuid.uuid4()), "role": "assistant", "content": content, "created_at": now,
                 "elapsed_ms": timing['elapsed_ms'], "model_elapsed_ms": timing['model_elapsed_ms'],
                 "model_context": model_context,
                 **({'interrupted': True} if interrupted else {}),
                 **({'thinking': body.thinking} if body.thinking is not None else {}),
                 **({'reasoning_effort': body.reasoning_effort} if body.reasoning_effort is not None else {}),
                 **({"reasoning_content": reasoning} if reasoning else {}),
                 **({"tool_events": events} if events else {})}]
            conversation.update({"updated_at": now, "model": provider["model"], "provider_name": provider["name"]})
            context_state = memory.snapshot() if self.runtime_name != 'dsh' else None
            if context_state:
                conversation['context_state'] = context_state
            else:
                conversation.pop('context_state', None)
            if self.runtime_name == 'dsh':
                if dsh_commit and not interrupted:
                    conversation['dsh_state'] = {**dsh_commit, 'through_message_id': conversation['messages'][-1]['id']}
                else:
                    conversation.pop('dsh_state', None)
            with self.chats.transaction() as state:
                state["conversations"][identifier] = conversation
            return {"conversation": self.visible_conversation(conversation), "usage": usage, 'interrupted': interrupted}
        finally:
            self.running.discard(identifier)
            self.runs.finish(run)

    async def cancel(self, user, request_id):
        return await self.runs.stop(user, request_id)
