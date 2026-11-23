from typing import Annotated
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from ..auth import AuthFailure


class LoginInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str = Field(min_length=1, max_length=160)
    password: str = Field(default='', max_length=256)
    database: str | None = Field(default=None, min_length=1, max_length=160, pattern=r'^[^\s\x00-\x1f]+$')

    @field_validator('password', mode='before')
    @classmethod
    def empty_password(cls, value):
        return '' if value is None else value

    @field_validator('account')
    @classmethod
    def account_required(cls, value):
        if not value.strip():
            raise ValueError('账号不能为空')
        return value.strip()


def create_router(service, identity):
    router = APIRouter(prefix='/api/v1/guanjia')

    def connection_user(value: Annotated[dict, Depends(identity)]):
        user = value['user']
        if 'guanjia:configure' not in user['permissions']:
            raise AuthFailure('forbidden', '没有访问权限。', 403)
        return user

    @router.get('')
    def settings(user: Annotated[dict, Depends(connection_user)]):
        return service.summary(user)

    @router.post('/login')
    def login(body: LoginInput, user: Annotated[dict, Depends(connection_user)]):
        return service.connect(user, body.account, body.password if 'password' in body.model_fields_set else None, body.database)

    @router.get('/databases')
    def databases(user: Annotated[dict, Depends(connection_user)]):
        return service.databases(user)

    return router
