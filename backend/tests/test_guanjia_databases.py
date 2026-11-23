import asyncio
import json
from types import SimpleNamespace
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from xingxu.app import create_app
from xingxu.auth import AuthFailure
from xingxu.guanjia.login import list_databases, login, LoginError
from xingxu.guanjia.service import GuanjiaService


BOOKS = [{'database': 'book-a', 'name': '正式账套'}, {'database': 'book-b', 'name': '测试账套'}]


def test_database_list_is_readonly_and_returns_only_choices():
    calls = []
    def upstream(request):
        calls.append(request)
        if request.method == 'GET':
            return httpx.Response(200, headers={'Set-Cookie': 'ASP.NET_SessionId=temporary; Path=/'}, text='fixture login')
        assert request.url.path.endswith('/_GetServerContextData')
        assert json.loads(request.content)['clientInfo']['__Params'] == {'name': None, 'password': None}
        return httpx.Response(200, json={'dbList': BOOKS, 'selectedIndex': 1,
            'softDogInfo': 'private-license', 'ValidateCode': 'private-validation'})
    result = list_databases(domain='http://fixture.example', transport=httpx.MockTransport(upstream))
    assert result == {'databases': BOOKS, 'default_database': 'book-b'}
    assert len(calls) == 2 and 'private' not in json.dumps(result)


@pytest.mark.parametrize('password',['fixture-password','',None])
def test_selected_database_and_cookie_use_correct_server_fields(monkeypatch,password):
    calls = []
    def upstream(request):
        calls.append(request)
        if request.method == 'GET':
            return httpx.Response(200, headers={'Set-Cookie': 'ASP.NET_SessionId=fixture; Path=/'}, text="Silverlight.__initParams = '{\"hasLogin\":true}'")
        path = request.url.path
        if 'LoginService' in path and path.endswith('_GetServerContextData'):
            return httpx.Response(200, json={'dbList': BOOKS, 'selectedIndex': 0, 'softDogInfo': 'license', 'isSoftDog': False})
        if path.endswith('CheckServerInfo') or path.endswith('CheckRds'):
            return httpx.Response(200, json=True)
        if path.endswith('UserLogin'):
            body = json.loads(request.content)
            assert body['user']['database'] == '测试账套'
            assert body['user']['password'] == (password or '')
            assert 'fzZTName=book-b' in request.headers['cookie']
            return httpx.Response(200, json={'code': 0})
        return httpx.Response(200, json={'menus': {}})
    original = httpx.Client
    monkeypatch.setattr('xingxu.guanjia.login.httpx.Client', lambda **kwargs: original(transport=httpx.MockTransport(upstream), **kwargs))
    session = login('fixture-user', password, domain='http://fixture.example', database='book-b')
    assert session.database == 'book-b' and session.database_name == '测试账套'
    session.session.close()
    calls.clear()
    with pytest.raises(LoginError):
        login('fixture-user', 'fixture-password', domain='http://fixture.example', database='missing-book')
    assert not any(r.url.path.endswith('UserLogin') for r in calls)


def test_failed_book_switch_preserves_previous_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    calls = []
    def runner(account, password, **kwargs):
        calls.append((account, password, kwargs))
        if kwargs.get('database') == 'book-b':
            raise LoginError('此账号无权登录测试账套')
        client = httpx.Client()
        return SimpleNamespace(session=client, domain='http://fixture.example', sid='private-sid', cookie='private-cookie',
            database=kwargs.get('database', 'book-a'), database_name='正式账套')
    service = GuanjiaService(tmp_path, Fernet(Fernet.generate_key()), login_runner=runner, database_runner=lambda: {'databases': BOOKS, 'default_database': 'book-a'})
    user = {'id': 'owner', 'workspace_id': 'fixture', 'permissions': ['guanjia:configure']}
    first = service.connect(user, 'user', 'password', 'book-a')
    original = service._record(user)
    assert service.databases(user)['databases'] == BOOKS
    with pytest.raises(AuthFailure):
        service.connect(user, 'user', '', 'book-b')
    assert service._record(user) == original
    service.connect(user, 'user', '')
    assert calls[-1][2]['database'] == 'book-a'
    assert first['database_name'] == '正式账套'
    assert not service.summary({**user, 'id': 'other'})['connected']


def test_database_routes_require_login_and_permission(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG', raising=False)
    app = create_app(tmp_path)
    app.state.auth.seed('owner', 'fixture-password')
    logged = app.state.auth.login('owner', 'fixture-password', False, 'fixture')
    headers = {'Authorization': 'Bearer ' + logged['access_token']}
    app.state.guanjia.database_runner = lambda: {'databases': BOOKS, 'default_database': 'book-a'}
    client = TestClient(app)
    assert client.get('/api/v1/guanjia/databases').status_code == 401
    result = client.get('/api/v1/guanjia/databases', headers=headers)
    assert result.status_code == 200 and result.json()['databases'] == BOOKS
    calls = []
    app.state.guanjia.connect = lambda *args: calls.append(args) or {'database': args[-1]}
    result = client.post('/api/v1/guanjia/login', headers=headers, json={'account': 'user', 'password': 'fixture', 'database': 'book-b'})
    assert result.status_code == 200 and calls[0][-1] == 'book-b'
    for invalid in ('', '../bad\n', 1):
        assert client.post('/api/v1/guanjia/login', headers=headers, json={'account': 'user', 'database': invalid}).status_code == 422
    original = app.state.auth._public
    app.state.auth._public = lambda raw: {**original(raw), 'permissions': []}
    assert client.get('/api/v1/guanjia/databases', headers=headers).status_code == 403
    asyncio.run(app.state.clawbot.close())
