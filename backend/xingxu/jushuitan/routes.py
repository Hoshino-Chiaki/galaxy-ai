from typing import Annotated
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from uuid import UUID
from ..auth import AuthFailure


class AccountInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str = Field(min_length=1, max_length=160)

    @field_validator('account')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():raise ValueError('账号不能为空')
        return value.strip()


class LoginInput(AccountInput):
    password: str = Field(default='', max_length=256)
    challenge_id: UUID | None = None
    verify_code: str | None = Field(default=None, min_length=1, max_length=64, pattern=r'^[A-Za-z0-9]+$')

    @field_validator('password', mode='before')
    @classmethod
    def empty_password(cls, value):return '' if value is None else value

    @model_validator(mode='after')
    def paired_verification(self):
        if (self.challenge_id is None) != (self.verify_code is None):
            raise ValueError('验证码图片标识和填写内容必须同时提交')
        return self


def create_router(service, identity):
    router = APIRouter(prefix='/api/v1/jushuitan')
    def connection_user(value: Annotated[dict, Depends(identity)]):
        user = value['user']
        if 'jushuitan:configure' not in user['permissions']:
            raise AuthFailure('forbidden', '没有聚水潭渠道权限。', 403)
        return user

    @router.get('')
    def settings(user: Annotated[dict, Depends(connection_user)]):return service.summary(user)

    @router.post('/login')
    async def login(body: LoginInput, response: Response, user: Annotated[dict, Depends(connection_user)]):
        response.headers['Cache-Control'] = 'no-store'
        try:
            return await service.connect(user, body.account, body.password if 'password' in body.model_fields_set else None,
                challenge_id=body.challenge_id, verify_code=body.verify_code)
        except AuthFailure as error:
            if error.code != 'jushuitan_captcha_required':raise
            return {'captcha_required': True, 'captcha': await service.captcha(user, body.account),
                    'message': '请填写下方图形验证码，再点击登录。'}

    @router.post('/captcha')
    async def captcha(body: AccountInput, response: Response, user: Annotated[dict, Depends(connection_user)]):
        response.headers['Cache-Control'] = 'no-store'
        return await service.captcha(user, body.account)
    return router
