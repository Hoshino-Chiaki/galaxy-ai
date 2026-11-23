import json
from types import SimpleNamespace
from unittest.mock import patch
import httpx
import pytest
from cryptography.fernet import Fernet
from xingxu.youzan.schemas import YouzanInput
from xingxu.youzan.service import YouzanService
from xingxu.guanjia.routes import LoginInput, create_router
from xingxu.guanjia.service import GuanjiaService
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_youzan_omitted_keeps_saved_but_null_and_blank_are_real_empty(tmp_path):
    service=YouzanService(tmp_path,Fernet(Fernet.generate_key()))
    user={'id':'u','workspace_id':'w','permissions':[]}
    service.save(user,YouzanInput(account='test',password='saved-secret',prepare_login=False))
    assert service.summary(user)['has_password']
    service.save(user,YouzanInput(account='test',prepare_login=False))
    with service.repo.transaction(write=False) as state:
        record=next(iter(state['connections'].values()))
        assert service._decrypt(record['password'])=='saved-secret'
    for value in (None,''):
        service.save(user,YouzanInput(account='test',password=value,prepare_login=False))
        assert not service.summary(user)['has_password']
        with service.repo.transaction(write=False) as state:
            record=next(iter(state['connections'].values()))
            assert record['password'] and service._decrypt(record['password'])==''
    service.save(user,YouzanInput(account='new-account',password=None,prepare_login=False))
    assert service.summary(user)['account']=='new-account'


def test_guanjia_route_omitted_keeps_saved_but_null_or_blank_submit_empty(tmp_path):
    passwords=[]
    def login(account,password,**kwargs):
        passwords.append(password)
        client=httpx.Client();client.cookies.set('ASP.NET_SessionId','fixture')
        return SimpleNamespace(session=client,sid='fixture',cookie='private-cookie',domain='https://example.com',database='test')
    service=GuanjiaService(tmp_path,Fernet(Fernet.generate_key()),login_runner=login)
    user={'id':'u','workspace_id':'w','permissions':['guanjia:configure']}
    app=FastAPI();app.include_router(create_router(service,lambda:{'user':user}))
    with TestClient(app) as client:
        for body,expected in [({'account':'a','password':'secret'},'secret'),({'account':'a'},'secret'),
                              ({'account':'a','password':None},''),({'account':'a','password':''},''),
                              ({'account':'new','password':None},'')]:
            response=client.post('/api/v1/guanjia/login',json=body)
            assert response.status_code==200,response.text
            assert passwords[-1]==expected
            assert response.json()['has_password']==bool(expected)
    record=service._record(user)
    assert record['password'] and service._decrypt(record['password'])==''


def test_null_is_not_stringified_and_password_validation_still_rejects_non_strings():
    for schema in (YouzanInput,LoginInput):
        assert schema(account='test',password=None).password==''
        for value in (123,False,{},[], 'x'*257):
            with pytest.raises(ValueError):schema(account='test',password=value)
