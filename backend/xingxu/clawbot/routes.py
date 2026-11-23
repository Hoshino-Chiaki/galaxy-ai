from typing import Annotated
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field


class PollInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    verify_code: str | None = Field(default=None, pattern=r'^\d{1,12}$')


def create_router(service, identity):
    router = APIRouter(prefix='/api/v1/clawbot')

    def user(value: Annotated[dict, Depends(identity)]):
        service.check(value['user'])
        return value['user']

    @router.get('')
    def settings(actor: Annotated[dict, Depends(user)]):
        return service.summary(actor)

    @router.post('/connect')
    async def connect(actor: Annotated[dict, Depends(user)]):
        return await service.connect(actor)

    @router.post('/poll')
    def poll(body: PollInput, actor: Annotated[dict, Depends(user)]):
        return service.poll(actor, body.id, body.verify_code)

    @router.delete('')
    async def disconnect(actor: Annotated[dict, Depends(user)]):
        return await service.disconnect(actor)

    return router
