import uuid
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from xingxu.permissions import create_router,PermissionService
from xingxu.auth import AuthFailure


def test_management_api_rejects_employee_and_validates_input(monkeypatch):
    session={'user':{'id':'fixture','workspace_id':'fixture','permissions':[]}}
    app=FastAPI();app.include_router(create_router(lambda:session))
    @app.exception_handler(AuthFailure)
    async def failure(_,error):return JSONResponse({'code':error.code},status_code=error.status)
    identifier=str(uuid.uuid4());url='/api/v1/permissions/department/'+identifier
    with TestClient(app) as client:
        assert client.get(url).status_code==403
        assert client.put(url,json={'revision':0,'effects':{}}).status_code==403
        session['user']['permissions']=['permissions:manage']
        monkeypatch.setattr(PermissionService,'get',lambda *args:{'ok':True})
        assert client.get(url).status_code==200
        assert client.get('/api/v1/permissions/bad/'+identifier).status_code==422
        assert client.put(url,json={'revision':0,'effects':{'ai:chat':'false'}}).status_code==422
        assert client.put(url,json={'revision':0,'effects':{},'workspace_id':'other'}).status_code==422
