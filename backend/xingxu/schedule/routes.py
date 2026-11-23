from typing import Annotated
from fastapi import APIRouter, Depends
from pydantic import BaseModel,ConfigDict
from galaxy_cli.tools.schedule._schemas import CREATE,UPDATE,DELETE,normalize
from galaxy_cli.registry import ToolError
from ..auth import AuthFailure


def create_router(service,identity):
    router=APIRouter(prefix='/api/v1/schedule')
    def user(value:Annotated[dict,Depends(identity)]):return value['user']
    def body(value,schema):
        try:return normalize(value,schema)
        except ToolError as e:raise AuthFailure(e.code,e.message,422) from None
    @router.get('')
    def listing(actor:Annotated[dict,Depends(user)]):return service.list(actor)
    @router.get('/{task_id}')
    def detail(task_id:str,actor:Annotated[dict,Depends(user)]):return service.list(actor,task_id)
    @router.post('',status_code=201)
    def create(value:dict,actor:Annotated[dict,Depends(user)]):return service.create(actor,body(value,CREATE))
    @router.put('/{task_id}/time')
    def update(task_id:str,value:dict,actor:Annotated[dict,Depends(user)]):return service.update_time(actor,body({**value,'id':task_id},UPDATE))
    @router.delete('/{task_id}')
    async def delete(task_id:str,revision:str,actor:Annotated[dict,Depends(user)]):return await service.delete(actor,body({'id':task_id,'revision':revision},DELETE))
    return router
