import os
import uuid
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
from .ai import AiService, ProviderInput, PromptInput, DefaultInput, ChatInput
from .youzan import YouzanService
from .youzan.routes import create_router as youzan_router
from .feishu import FeishuService
from .feishu.routes import create_router as feishu_router
from .tools import ExecutionContext
from .files import MIME
from .deployment import production_directory


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=256)
    remember: StrictBool = False


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


def create_app(data_dir=None, policy=None, ai_transport=None, youzan_transport=None, youzan_login_runner=None, feishu_transport=None):
    application = FastAPI(title="galaxy ai", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    directory = Path(data_dir) if data_dir is not None else production_directory()
    service = AuthService(JsonRepository(directory), policy or AuthPolicy())
    application.state.auth = service
    ai = AiService(directory, ai_transport, authorize=service.identity)
    application.state.ai = ai
    youzan = YouzanService(directory, ai.cipher, transport=youzan_transport,
                          login_runner=youzan_login_runner, authorize=service.identity)
    application.state.youzan = youzan
    from .youzan.renewal import YouzanRenewal
    youzan.renewal = YouzanRenewal(youzan, service)
    feishu = FeishuService(directory, ai.cipher, transport=feishu_transport)
    application.state.feishu = feishu
    ai.dispatcher.feishu_provider = feishu
    ai.dispatcher.category_provider = youzan.tool_credentials
    ai.dispatcher.shop_resolver = youzan.resolve_shop_credentials
    ai.dispatcher.operation_guard = youzan.operation_guard
    ai.dispatcher.shop_provider = youzan.tool_credentials

    @asynccontextmanager
    async def lifespan(_app):
        youzan.renewal.start()
        try:
            yield
        finally:
            await youzan.close()
    application.router.lifespan_context = lifespan
    bearer = HTTPBearer(auto_error=False)

    @application.middleware("http")
    async def response_policy(request: Request, call_next):
        # No credentials, validation bodies or bearer tokens enter access logs.
        length = request.headers.get("content-length", "0")
        maximum = 8192 if request.url.path == "/api/v1/auth/login" else 128 * 1024
        if not length.isdigit() or int(length) > maximum:
            return JSONResponse({"error": {"code": "invalid_input", "message": "请求内容过大。"}}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

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


    application.include_router(youzan_router(youzan, identity, token))
    application.include_router(feishu_router(feishu, identity, token))

    @application.get("/health")
    def health():
        return {"status": "ok", "service": "galaxy-ai", "version": "0.1.0"}

    @application.post("/api/v1/auth/login")
    def login(body: LoginRequest, request: Request):
        return service.login(body.username, body.password, body.remember,
                             request.client.host if request.client else "unknown")

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
        return await ai.test(identifier)

    @application.put("/api/v1/admin/ai/default")
    def default_provider(body: DefaultInput, _user: Annotated[dict, Depends(administrator)]):
        return ai.set_default(body.provider_id)

    @application.put("/api/v1/admin/ai/prompt")
    def global_prompt(body: PromptInput, _user: Annotated[dict, Depends(administrator)]):
        return ai.set_prompt(body.prompt)

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

    return application
