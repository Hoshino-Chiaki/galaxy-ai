from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from .auth import AuthFailure
from .file_storage_routes import UploadInput


class PromptInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prompt: str = Field(max_length=20000)


def create_router(prompts, identity, token, download):
    router = APIRouter(prefix='/api/v1/personal-prompt')

    def user(value: Annotated[dict, Depends(identity)]):
        prompts.permission(value['user'])
        return value['user']

    @router.get('')
    def get(value: Annotated[dict, Depends(user)]):
        return prompts.snapshot(value)

    @router.put('')
    def save(body: PromptInput, value: Annotated[dict, Depends(user)]):
        return prompts.save(value, body.prompt)

    @router.post('/uploads')
    def reserve(body: UploadInput, value: Annotated[dict, Depends(user)]):
        return prompts.reserve(value, body.filename, body.size)

    @router.put('/uploads/{identifier}/content')
    async def receive(identifier: UUID, request: Request, value: Annotated[dict, Depends(user)], bearer: Annotated[str, Depends(token)]):
        length = request.headers.get('content-length', '')
        if not length.isdigit():
            raise AuthFailure('file_size', '上传必须声明文件大小。', 411)
        if request.headers.get('content-type', '').split(';')[0] != 'application/octet-stream':
            raise AuthFailure('invalid_input', '上传须使用文件数据流。', 415)
        return await prompts.receive(value, identifier, int(length), request.stream(),
                                     lambda: identity_from_bearer(bearer))

    def identity_from_bearer(bearer):
        return prompts.reauthorize(bearer)

    @router.delete('/uploads/{identifier}')
    def cancel(identifier: UUID, value: Annotated[dict, Depends(user)]):
        prompts.release(value, str(identifier))
        return {'ok': True}

    @router.get('/files/{identifier}/metadata')
    def metadata(identifier: UUID, value: Annotated[dict, Depends(user)]):
        _, item = prompts.resolve(value, identifier)
        return {**item, 'download_path': f'/api/v1/personal-prompt/files/{identifier}/content'}

    @router.get('/files/{identifier}/content')
    def content(identifier: UUID, value: Annotated[dict, Depends(user)]):
        path, item = prompts.resolve(value, identifier)
        return download(path, item['filename'], item['media_type'])

    @router.delete('/files/{identifier}')
    def delete(identifier: UUID, value: Annotated[dict, Depends(user)]):
        return prompts.delete(value, identifier)

    return router
