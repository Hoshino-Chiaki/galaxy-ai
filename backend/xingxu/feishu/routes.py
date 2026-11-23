"""Employee-owned Feishu connection endpoints; enterprise app configuration is admin-only."""
import uuid
from typing import Annotated
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from ..auth import AuthFailure


class AppInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    app_id: str = Field(pattern=r'^cli_[A-Za-z0-9]{4,100}$')
    app_secret: str = Field(default='', max_length=512)


class PollInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: uuid.UUID


class AuthorizeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # Accepted only for older clients; every new request uses automatic full scope.
    group: str | None = Field(default=None,pattern=r'^[a-z][a-z0-9-]{0,80}$')


def create_router(service, identity, token):
    router = APIRouter(prefix='/api/v1/feishu')
    def connection_user(value: Annotated[dict, Depends(identity)]):
        if 'feishu:configure' not in value['user']['permissions']:
            raise AuthFailure('forbidden', '当前账号没有飞书连接权限。', 403)
        return value['user']

    @router.get('')
    def settings(user: Annotated[dict, Depends(connection_user)]):return service.summary(user)

    @router.get('/bot')
    def bot_status(user: Annotated[dict, Depends(connection_user)]):
        return service.bot.summary(user) if service.bot else {'status':'not_configured'}

    @router.put('/app')
    def save_app(body: AppInput, user: Annotated[dict, Depends(connection_user)]):return service.save_app(user, body)

    @router.post('/authorize')
    async def authorize(user: Annotated[dict, Depends(connection_user)], value: Annotated[str, Depends(token)], body: AuthorizeInput | None = None):
        return await service.start(user, lambda: identity_from_token(value),group=body.group if body else None)

    @router.post('/poll')
    async def poll(body: PollInput, user: Annotated[dict, Depends(connection_user)], value: Annotated[str, Depends(token)]):
        return await service.poll(user, body.id, lambda: identity_from_token(value))

    @router.delete('')
    async def disconnect(user: Annotated[dict, Depends(connection_user)]):return await service.disconnect(user)

    def identity_from_token(value):
        # The identity dependency callable itself expects already-resolved credentials.
        current = identity(value)
        if 'feishu:configure' not in current['user']['permissions']:
            raise AuthFailure('forbidden', '飞书连接权限已变更。', 403)
        return current
    return router
