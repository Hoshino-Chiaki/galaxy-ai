import os
import asyncio
import contextlib
import re
import uuid
from urllib.parse import unquote
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from galaxy_cli.file_scope import source_file
from galaxy_cli.registry import ToolError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .auth import AuthFailure, AuthPolicy, AuthService
from .store import JsonRepository
from .ai import AiService, ProviderInput, PromptInput, DefaultInput, AdvancedInput, ChatInput
from .guanjia import GuanjiaService
from .guanjia.routes import create_router as guanjia_router
from .jushuitan import JushuitanService
from .jushuitan.routes import create_router as jushuitan_router
from .youzan import YouzanService
from .youzan.routes import create_router as youzan_router
from .feishu import FeishuService
from .feishu.bot import FeishuBot
from .feishu.routes import create_router as feishu_router
from .tools import ExecutionContext
from .files import MIME
from .deployment import production_directory
from .uploads import MAX_UPLOAD
from .usage_routes import create_router as usage_router
from .updates import create_router as updates_router
from .employees import create_router as employees_router
from .database import DatabaseUnavailable
from .profile import create_router as profile_router
from .departments import create_router as departments_router
from .permissions import create_router as permissions_router
from .account_info import AccountInfo
from .clawbot import ClawbotService
from .clawbot.routes import create_router as clawbot_router
from .schedule import ScheduleService
from .schedule.routes import create_router as schedule_router


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=256)
    remember: StrictBool = False


class ClarificationAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question_id: str = Field(min_length=1, max_length=64)
    option_id: str | None = Field(default=None, min_length=1, max_length=64)
    text: str | None = Field(default=None, min_length=1, max_length=4000)


class ClarificationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: uuid.UUID
    interaction_id: uuid.UUID
    answers: list[ClarificationAnswer] = Field(min_length=1, max_length=3)


class CancelChatInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: uuid.UUID


def scoped_download(path, filename, media_type):
    # Read the exact no-follow descriptor validated here, not a path reopened later.
    try:
        with source_file(path.parent, path.name) as descriptor:
            stream = os.fdopen(os.dup(descriptor), 'rb')
    except ToolError:
        raise AuthFailure('not_found', '文件不存在。', 404) from None
    headers = {'Content-Disposition': FileResponse(path, filename=filename).headers['content-disposition'],
        'Content-Length': str(os.fstat(stream.fileno()).st_size), 'X-Content-Type-Options': 'nosniff'}
    return StreamingResponse(iter(lambda: stream.read(512 * 1024), b''), media_type=media_type,
        headers=headers, background=BackgroundTask(stream.close))


def create_app(data_dir=None, policy=None, ai_transport=None, youzan_transport=None, youzan_login_runner=None, feishu_transport=None, clawbot_transport=None):
    application = FastAPI(title="galaxy ai", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    directory = Path(data_dir) if data_dir is not None else production_directory()
    service = AuthService(JsonRepository(directory), policy or AuthPolicy())
    application.state.auth = service
    ai = AiService(directory, ai_transport, authorize=service.identity)
    application.state.ai = ai
    from .file_storage import FileStorage
    storage = FileStorage(ai.dispatcher.files, ai.config)
    storage.reauthorize = lambda bearer: service.identity(bearer)['user']
    application.state.file_storage = storage
    ai.dispatcher.uploads.storage = storage
    storage.uploads = ai.dispatcher.uploads
    guanjia = GuanjiaService(directory, ai.cipher)
    application.state.guanjia = guanjia
    ai.dispatcher.gjp_provider = guanjia
    jushuitan = JushuitanService(directory, ai.cipher)
    application.state.jushuitan = jushuitan
    ai.dispatcher.jushuitan_provider = jushuitan
    youzan = YouzanService(directory, ai.cipher, transport=youzan_transport,
                          login_runner=youzan_login_runner, authorize=service.identity)
    application.state.youzan = youzan
    from .youzan.renewal import YouzanRenewal
    youzan.renewal = YouzanRenewal(youzan, service)
    feishu = FeishuService(directory, ai.cipher, transport=feishu_transport)
    application.state.feishu = feishu
    feishu.bot = FeishuBot(directory, service, ai, feishu)
    application.state.feishu_bot = feishu.bot
    ai.dispatcher.feishu_provider = feishu
    ai.dispatcher.category_provider = youzan.tool_credentials
    ai.dispatcher.shop_choices = youzan.tool_shop_options
    ai.dispatcher.shop_resolver = youzan.resolve_shop_credentials
    ai.dispatcher.operation_guard = youzan.operation_guard
    ai.dispatcher.shop_provider = youzan.tool_credentials
    schedules = ScheduleService(directory, service, ai)
    application.state.schedules = schedules
    clawbot = ClawbotService(directory, service, ai, transport=clawbot_transport)
    application.state.clawbot = clawbot
    ai.dispatcher.clawbot_provider = clawbot
    ai.dispatcher.account_provider = AccountInfo(youzan=youzan, feishu=feishu, guanjia=guanjia, jushuitan=jushuitan, clawbot=clawbot)
    ai.dispatcher.schedule_provider = schedules

    @asynccontextmanager
    async def lifespan(_app):
        youzan.renewal.start()
        schedules.start()
        clawbot.start()
        feishu.bot.start()
        housekeeping = asyncio.create_task(ai.dispatcher.uploads.housekeeping())
        try:
            yield
        finally:
            housekeeping.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await housekeeping
            await feishu.bot.close()
            await clawbot.close()
            await schedules.close()
            await youzan.close()
    application.router.lifespan_context = lifespan
    bearer = HTTPBearer(auto_error=False)

    @application.middleware("http")
    async def response_policy(request: Request, call_next):
        # No credentials, validation bodies or bearer tokens enter access logs.
        length = request.headers.get("content-length", "0")
        maximum = 8192 if request.url.path == "/api/v1/auth/login" else 128 * 1024
        if request.method == 'PUT' and re.fullmatch(r'/api/v1/uploads/[0-9a-f-]{36}/content', request.url.path):
            maximum = MAX_UPLOAD
        if request.method == 'PUT' and re.fullmatch(r'/api/v1/storage/uploads/[0-9a-f-]{36}/content', request.url.path):
            maximum = storage.settings()['quota_bytes']
        if not length.isdigit() or int(length) > maximum:
            return JSONResponse({"error": {"code": "invalid_input", "message": "请求内容过大。"}}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.exception_handler(DatabaseUnavailable)
    async def database_failure(_request, _error):
        return JSONResponse({"error":{"code":"database_unavailable","message":"数据库暂不可用，请稍后重试。"}},status_code=503)

    @application.exception_handler(AuthFailure)
    async def auth_failure(_request, error):
        return JSONResponse({"error": {"code": error.code, "message": error.message}},
            status_code=error.status,
            headers={"Retry-After": str(error.retry_after)} if error.retry_after else None)

    @application.exception_handler(RequestValidationError)
    async def invalid_input(_request, _error):
        return JSONResponse({"error": {"code": "invalid_input", "message": "请检查输入格式、必填项及内容长度。"}}, status_code=422)

    def token(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise AuthFailure("unauthenticated", "请先登录。")
        return credentials.credentials

    def identity(value: Annotated[str, Depends(token)]):
        return service.identity(value)

    def administrator(value: Annotated[dict, Depends(identity)]):
        if "ai:configure" not in value["user"]["permissions"]:
            raise AuthFailure("forbidden", "没有访问权限。", 403)
        return value["user"]

    def chat_user(value: Annotated[dict, Depends(identity)]):
        if "ai:chat" not in value["user"]["permissions"]:
            raise AuthFailure("forbidden", "没有访问权限。", 403)
        return value["user"]


    application.include_router(guanjia_router(guanjia, identity))
    application.include_router(jushuitan_router(jushuitan, identity))
    application.include_router(youzan_router(youzan, identity, token))
    application.include_router(feishu_router(feishu, identity, token))
    application.include_router(updates_router())
    application.include_router(usage_router(ai.usage_store, service, identity))
    application.include_router(employees_router(identity))
    application.include_router(departments_router(identity))
    application.include_router(permissions_router(identity))
    application.include_router(profile_router(identity, token))
    from .file_storage_routes import create_router as file_storage_router
    application.include_router(file_storage_router(storage, identity, token, scoped_download))
    from .personal_prompt_routes import create_router as personal_prompt_router
    ai.personal_prompts.reauthorize = lambda bearer: service.identity(bearer)['user']
    application.include_router(personal_prompt_router(ai.personal_prompts, identity, token, scoped_download))
    application.include_router(schedule_router(schedules, identity))
    application.include_router(clawbot_router(clawbot, identity))

    @application.get("/health")
    def health():
        from .account_storage import enabled
        return {"status": "ok", "service": "galaxy-ai", "version": "0.1.0", "storage": "mysql+account-files" if enabled(directory) else "mysql" if os.environ.get("GALAXY_DATABASE_CONFIG") else "json"}

    @application.post("/api/v1/auth/login")
    def login(body: LoginRequest, request: Request):
        result = service.login(body.username, body.password, body.remember,
                               request.client.host if request.client else "unknown")
        from .account_storage import enabled, Accounts
        if enabled(directory):
            Accounts(directory).ensure(result['user'])
        return result

    @application.get("/api/v1/auth/me")
    def current_user(value: Annotated[dict, Depends(identity)]):
        return value

    @application.post("/api/v1/auth/logout")
    def logout(value: Annotated[str, Depends(token)]):
        service.logout(value)
        return {"ok": True}

    @application.get("/api/v1/workspace")
    def workspace(value: Annotated[dict, Depends(identity)]):
        return {"user": value["user"], "features": {"chat": "available", "marketing_canvas": "coming_soon"}}

    @application.get("/api/v1/admin/status")
    def admin(value: Annotated[dict, Depends(identity)]):
        if "ai:configure" not in value["user"]["permissions"]:
            raise AuthFailure("forbidden", "没有访问权限。", 403)
        return {"ok": True, "can_configure_ai": True}

    @application.get("/api/v1/admin/ai")
    def ai_settings(_user: Annotated[dict, Depends(administrator)]):
        return ai.settings()

    @application.get("/api/v1/admin/ai/tools")
    async def tool_catalog(user: Annotated[dict, Depends(administrator)], value: Annotated[str, Depends(token)]):
        context = ExecutionContext(user["id"], user["workspace_id"], tuple(user["permissions"]), "console-catalog", value)
        event = await ai.dispatcher.execute(context, {"id": str(uuid.uuid4()), "type": "function", "function": {
            "name": "galaxy_cli", "arguments": '{"args":["help"]}'}})
        if event["status"] != "success":
            raise AuthFailure("catalog_unavailable", "无法读取 Galaxy CLI help，请稍后重试。", 503)
        return event["result"]

    @application.post("/api/v1/admin/ai/providers")
    def add_provider(body: ProviderInput, _user: Annotated[dict, Depends(administrator)]):
        return ai.save_provider(body)

    @application.put("/api/v1/admin/ai/providers/{identifier}")
    def update_provider(identifier: uuid.UUID, body: ProviderInput, _user: Annotated[dict, Depends(administrator)]):
        with ai.config.transaction(write=False) as state:
            if str(identifier) not in state["providers"]:
                raise AuthFailure("not_found", "配置不存在。", 404)
        return ai.save_provider(body, identifier)

    @application.delete("/api/v1/admin/ai/providers/{identifier}")
    def remove_provider(identifier: uuid.UUID, _user: Annotated[dict, Depends(administrator)]):
        return ai.delete_provider(identifier)

    @application.post("/api/v1/admin/ai/providers/{identifier}/test")
    async def test_provider(identifier: uuid.UUID, _user: Annotated[dict, Depends(administrator)]):
        return await ai.test(identifier, _user)

    @application.get("/api/v1/admin/ai/providers/{identifier}/balance")
    async def provider_balance(identifier: uuid.UUID, _user: Annotated[dict, Depends(administrator)]):
        from .provider_balance import deepseek_balance
        return await deepseek_balance(ai, identifier)

    @application.put("/api/v1/admin/ai/default")
    def default_provider(body: DefaultInput, _user: Annotated[dict, Depends(administrator)]):
        return ai.set_default(body.provider_id)

    @application.put("/api/v1/admin/ai/prompt")
    def global_prompt(body: PromptInput, _user: Annotated[dict, Depends(administrator)]):
        return ai.set_prompt(body.prompt)

    @application.put("/api/v1/admin/ai/advanced")
    def advanced_settings(body: AdvancedInput, _user: Annotated[dict, Depends(administrator)]):
        return ai.save_advanced(body)

    @application.get('/api/v1/admin/ai/tool-prompts')
    def tool_prompts(user: Annotated[dict, Depends(administrator)]):
        return ai.tool_prompts.settings(user)

    @application.put('/api/v1/admin/ai/tool-prompts/{category}')
    def save_tool_prompt(category: str, body: PromptInput, user: Annotated[dict, Depends(administrator)]):
        return ai.tool_prompts.save(user, category, body.prompt)

    @application.get("/api/v1/ai/status")
    def ai_status(_user: Annotated[dict, Depends(chat_user)]):
        return ai.availability()

    @application.get("/api/v1/chat/conversations")
    def conversations(user: Annotated[dict, Depends(chat_user)]):
        return ai.list_conversations(user)

    @application.get('/api/v1/files/{identifier}/metadata')
    def file_metadata(identifier: uuid.UUID, user: Annotated[dict, Depends(chat_user)]):
        _path, metadata = ai.dispatcher.files.resolve(user, identifier)
        return metadata

    @application.get('/api/v1/uploads/{identifier}')
    def upload_metadata(identifier: uuid.UUID, user: Annotated[dict, Depends(chat_user)]):
        return ai.dispatcher.uploads.metadata(user, identifier)

    @application.put('/api/v1/uploads/{identifier}/content')
    async def upload_content(identifier: uuid.UUID, request: Request,
                             user: Annotated[dict, Depends(chat_user)], value: Annotated[str, Depends(token)]):
        if request.headers.get('content-type', '').split(';', 1)[0] != 'application/octet-stream':
            raise AuthFailure('invalid_input', '上传须使用文件数据流。', 415)
        length = request.headers.get('content-length', '')
        if not length.isdigit():
            raise AuthFailure('invalid_input', '上传须提供文件大小。', 411)
        if len(request.headers.get('x-upload-filename', '')) > 2048:
            raise AuthFailure('invalid_filename', '文件名过长。', 422)
        return await ai.dispatcher.uploads.receive(user, identifier, unquote(request.headers.get('x-upload-filename', '')),
            int(length), request.stream(), lambda: service.identity(value)['user'])

    @application.delete('/api/v1/uploads/{identifier}')
    def delete_upload(identifier: uuid.UUID, user: Annotated[dict, Depends(chat_user)]):
        return ai.dispatcher.uploads.delete(user, identifier)

    @application.get('/api/v1/files/{identifier}')
    def download_file(identifier: uuid.UUID, user: Annotated[dict, Depends(chat_user)]):
        path, metadata = ai.dispatcher.files.resolve(user, identifier)
        return scoped_download(path, metadata['filename'], metadata.get('media_type', MIME))

    @application.get('/files/{filename}')
    def hosted_file(filename: str, user: Annotated[dict, Depends(chat_user)]):
        path, name, media_type = ai.dispatcher.files.resolve_filename(user, filename)
        return scoped_download(path, name, media_type)

    @application.get("/api/v1/chat/conversations/{identifier}")
    def conversation(identifier: uuid.UUID, user: Annotated[dict, Depends(chat_user)]):
        return ai.conversation(user, identifier)

    @application.delete("/api/v1/chat/conversations/{identifier}")
    def delete_conversation(identifier: uuid.UUID, user: Annotated[dict, Depends(chat_user)]):
        return ai.delete_conversation(user, identifier)

    @application.post("/api/v1/chat/send")
    async def send_message(body: ChatInput, user: Annotated[dict, Depends(chat_user)], value: Annotated[str, Depends(token)]):
        return await ai.send(user, body, session_token=value)

    @application.post('/api/v1/chat/cancel')
    async def cancel_message(body: CancelChatInput, user: Annotated[dict, Depends(chat_user)]):
        return await ai.cancel(user, body.request_id)

    @application.post('/api/v1/chat/answer')
    async def answer_question(body: ClarificationInput, user: Annotated[dict, Depends(chat_user)]):
        return ai.runs.answer(user, body.request_id, body.interaction_id,
            [item.model_dump(exclude_none=True) for item in body.answers])

    @application.get('/api/v1/chat/runs/{identifier}')
    async def chat_progress(identifier: uuid.UUID, user: Annotated[dict, Depends(chat_user)]):
        return ai.runs.status(user, identifier)

    return application
