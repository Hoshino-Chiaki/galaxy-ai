from typing import Annotated, Literal
from uuid import UUID
from urllib.parse import quote
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from .auth import AuthFailure


class StorageSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    quota_mb: int = Field(ge=1, strict=True)


class UploadInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filename: str = Field(min_length=1, max_length=240)
    size: int = Field(ge=0, strict=True)


def create_router(storage, identity, token, download):
    router = APIRouter()
    def user(value: Annotated[dict, Depends(identity)]):return value['user']
    def admin(value: Annotated[dict, Depends(user)]):
        if 'ai:configure' not in value['permissions']:raise AuthFailure('forbidden','没有访问权限。',403)
        return value

    @router.get('/api/v1/admin/ai/storage')
    def settings(_user: Annotated[dict, Depends(admin)]):return storage.settings()

    @router.put('/api/v1/admin/ai/storage')
    def save_settings(body: StorageSettings, value: Annotated[dict, Depends(admin)]):return storage.save_settings(value,body.quota_mb)

    @router.get('/api/v1/storage')
    def listing(value: Annotated[dict, Depends(user)]):return storage.list(value)

    @router.post('/api/v1/storage/uploads')
    def reserve(body: UploadInput, value: Annotated[dict, Depends(user)]):return storage.reserve(value,body.filename,body.size)

    @router.put('/api/v1/storage/uploads/{identifier}/content')
    async def receive(identifier: UUID, request: Request, value: Annotated[dict, Depends(user)], bearer: Annotated[str, Depends(token)]):
        length=request.headers.get('content-length','')
        if not length.isdigit():raise AuthFailure('file_size','上传必须声明文件大小。',411)
        if request.headers.get('content-type','').split(';')[0]!='application/octet-stream':
            raise AuthFailure('invalid_input','上传须使用文件数据流。',415)
        return await storage.receive(value,identifier,int(length),request.stream(),lambda:identity_token(bearer))

    def identity_token(bearer):
        # The router receives the same live authenticator as normal API requests.
        return storage.reauthorize(bearer)

    @router.delete('/api/v1/storage/uploads/{identifier}')
    def cancel(identifier: UUID, value: Annotated[dict, Depends(user)]):
        storage.permission(value,True);storage.release(value,str(identifier));return {'ok':True}

    @router.get('/api/v1/storage/files/{identifier}/metadata')
    def metadata(identifier: UUID, value: Annotated[dict, Depends(user)]):
        _,item=storage.resolve(value,identifier)
        return {**item,'download_path':f'/api/v1/storage/files/{identifier}/content'}

    @router.get('/api/v1/storage/files/{identifier}/content')
    def content(identifier: UUID, value: Annotated[dict, Depends(user)]):
        path,item=storage.resolve(value,identifier)
        return download(path,item['filename'],item['media_type'])

    @router.get('/api/v1/storage/files/{identifier}/thumbnail')
    def thumbnail(identifier: UUID, value: Annotated[dict, Depends(user)]):
        return Response(storage.thumbnail(value,identifier),media_type='image/jpeg')

    @router.delete('/api/v1/storage/files/{identifier}')
    def delete(identifier: UUID, value: Annotated[dict, Depends(user)]):return storage.delete(value,identifier)

    @router.get('/api/v1/storage/published')
    def published(value: Annotated[dict, Depends(user)]):return storage.published(value)

    @router.get('/api/v1/storage/published/{scope}/{name}/metadata')
    def published_metadata(scope: Literal['personal','shared'], name: str, value: Annotated[dict, Depends(user)]):
        _,item=storage.resolve_published(value,scope,name)
        return {**item,'download_path':'/api/v1/storage/published/'+scope+'/'+quote(name,safe='')+'/content'}

    @router.get('/api/v1/storage/published/{scope}/{name}/content')
    def published_content(scope: Literal['personal','shared'], name: str, value: Annotated[dict, Depends(user)]):
        path,item=storage.resolve_published(value,scope,name)
        return download(path,item['filename'],item['media_type'])

    return router
