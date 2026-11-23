import asyncio
import importlib.util
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from xingxu.app import create_app
from xingxu.auth import AuthFailure
from xingxu.file_storage import FileStorage, MIB
from xingxu.files import FileService
from xingxu.store import JsonRepository


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG',raising=False)
    files=FileService(tmp_path)
    config=JsonRepository(tmp_path,'ai',('providers','settings'))
    storage=FileStorage(files,config)
    a={'id':'a','username':'alice','workspace_id':'w','permissions':['ai:chat','file:read','task:upload','ai:configure']}
    b={**a,'id':'b','username':'bob'}
    return storage,a,b


async def chunks(*parts):
    for part in parts:yield part


def upload(storage,user,name,data):
    ticket=storage.reserve(user,name,len(data))
    return asyncio.run(storage.receive(user,ticket['id'],len(data),chunks(data),lambda:user))


def test_persistent_upload_download_delete_and_cross_account_isolation(env):
    storage,a,b=env
    first=upload(storage,a,'报表 ~100%.txt',b'private data')
    assert storage.list(a)['used_bytes']==12
    assert storage.list(b)['files']==[]
    assert storage.resolve(a,first['id'])[0].read_bytes()==b'private data'
    with pytest.raises(AuthFailure):storage.resolve(b,first['id'])
    with pytest.raises(AuthFailure):storage.delete(b,first['id'])
    assert storage.list(a)['quota_bytes']==512*MIB
    storage.delete(a,first['id'])
    assert storage.list(a)['used_bytes']==0
    # Storage is independent of conversations and retains zero-length files.
    empty=upload(storage,a,'.env',b'')
    assert storage.resolve(a,empty['id'])[0].read_bytes()==b''
    assert first['filename']=='报表 ~100%.txt'


def test_concurrent_reservations_cannot_exceed_total_quota(env):
    storage,a,_=env
    storage.save_settings(a,1)
    def reserve(index):
        try:return storage.reserve(a,str(index)+'.bin',400000)
        except AuthFailure as e:return e.code
    with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(reserve,range(6)))
    assert sum(isinstance(r,dict) for r in results)==2
    assert results.count('storage_quota')==4
    assert storage.list(a)['reserved_bytes']==800000
    assert storage.list(a)['used_bytes']==0
    for r in results:
        if isinstance(r,dict):storage.release(a,r['id'])
    assert storage.list(a)['reserved_bytes']==0


def test_overlong_incomplete_cancelled_and_identity_change_release_reservations(env):
    storage,a,b=env
    for data,reauth in ((b'long-data',lambda:a),(b'x',lambda:a),(b'1234',lambda:b)):
        ticket=storage.reserve(a,'file.txt',4)
        with pytest.raises(AuthFailure):asyncio.run(storage.receive(a,ticket['id'],4,chunks(data),reauth))
        assert storage.list(a)['reserved_bytes']==0 and storage.list(a)['files']==[]
    ticket=storage.reserve(a,'file.txt',4)
    async def cancelled():
        yield b'x'
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):asyncio.run(storage.receive(a,ticket['id'],4,cancelled(),lambda:a))
    assert storage.list(a)['reserved_bytes']==0
    assert not list((storage.metadata_root(a)/'incoming').glob('*'))


def test_quota_is_live_and_shared_and_legacy_uploads_count(env):
    storage,a,b=env
    storage.save_settings(a,1)
    assert storage.settings()['quota_bytes']==MIB
    path=storage.files.workspace(a)/'uploads'/'legacy';path.mkdir(parents=True)
    (path/'payload').write_bytes(b'x'*MIB)
    with pytest.raises(AuthFailure) as err:storage.reserve(a,'another.txt',1)
    assert err.value.code=='storage_quota'
    assert storage.list(b)['available_bytes']==MIB
    storage.save_settings(a,2)
    assert storage.reserve(a,'another.txt',1)['size']==1
    with pytest.raises(AuthFailure):storage.save_settings({**a,'permissions':[]},100)


def test_symlinks_and_traversal_are_never_read_or_written(env,tmp_path):
    storage,a,_=env
    for name in ('../secret','folder/file','folder\\file','\x00file'):
        with pytest.raises(AuthFailure):storage.reserve(a,name,1)
    item=upload(storage,a,'a.txt',b'data')
    path=storage.resolve(a,item['id'])[0]
    path.unlink();path.symlink_to('/etc/passwd')
    with pytest.raises(AuthFailure):storage.resolve(a,item['id'])
    # Client-supplied roots and paths are never accepted by reserve.
    with pytest.raises(AuthFailure):storage.resolve(a,'../../alice')


def test_images_get_safe_raster_previews_and_other_files_do_not(env):
    storage,a,_=env
    image=io.BytesIO();Image.new('RGB',(800,600),'navy').save(image,format='PNG')
    item=upload(storage,a,'photo.png',image.getvalue())
    with Image.open(io.BytesIO(storage.thumbnail(a,item['id']))) as preview:
        assert preview.format=='JPEG' and preview.width<=420 and preview.height<=280
    svg=upload(storage,a,'vector.svg',b'<svg><script>alert(1)</script></svg>')
    with pytest.raises(AuthFailure):storage.thumbnail(a,svg['id'])


def test_published_lists_own_files_and_intentional_shared_files_only(env):
    storage,a,b=env
    mine=storage.files.published_root(a);mine.mkdir(parents=True,exist_ok=True)
    other=storage.files.published_root(b);other.mkdir(parents=True,exist_ok=True)
    # Test production's physically separate published directories.
    storage.files.published_root=lambda u:storage.files.account_root(u)/'files'/'published'
    for user,name in ((a,'my-report.xlsx'),(b,'private-other.xlsx')):
        root=storage.files.published_root(user);root.mkdir(parents=True,exist_ok=True);(root/name).write_bytes(b'data')
    (storage.files.root/'shared.pdf').write_bytes(b'shared')
    (storage.files.root/'11111111-1111-4111-8111-111111111111_private.xlsx').write_bytes(b'private')
    names={r['filename'] for r in storage.published(a)['files']}
    assert names=={'my-report.xlsx','shared.pdf'}
    with pytest.raises(AuthFailure):storage.resolve_published(a,'personal','private-other.xlsx')


def test_http_routes_authenticate_upload_quota_and_enforce_ownership(tmp_path,monkeypatch):
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG',raising=False)
    app=create_app(tmp_path);auth=app.state.auth
    auth.import_accounts([('admin','fixture-password'),('employee','fixture-password')])
    admin=auth.login('admin','fixture-password',False,'local');employee=auth.login('employee','fixture-password',False,'local')
    a={'Authorization':'Bearer '+admin['access_token']};b={'Authorization':'Bearer '+employee['access_token']}
    with TestClient(app) as client:
        assert client.get('/api/v1/storage').status_code==401
        assert client.put('/api/v1/admin/ai/storage',headers=b,json={'quota_mb':1}).status_code==403
        assert client.put('/api/v1/admin/ai/storage',headers=a,json={'quota_mb':1}).status_code==200
        ticket=client.post('/api/v1/storage/uploads',headers=a,json={'filename':'notes.txt','size':4}).json()
        identifier=ticket['id']
        assert client.put('/api/v1/storage/uploads/'+identifier+'/content',headers={**b,'Content-Type':'application/octet-stream'},content=b'data').status_code==409
        response=client.put('/api/v1/storage/uploads/'+identifier+'/content',headers={**a,'Content-Type':'application/octet-stream'},content=b'data')
        assert response.status_code==200,response.text
        assert client.get('/api/v1/storage/files/'+identifier+'/content',headers=b).status_code==404
        download=client.get('/api/v1/storage/files/'+identifier+'/content',headers=a)
        assert download.content==b'data' and download.headers['content-disposition'].startswith('attachment')
        assert client.post('/api/v1/storage/uploads',headers=a,json={'filename':'huge','size':MIB}).status_code==413
        assert client.delete('/api/v1/storage/files/'+identifier,headers=a).status_code==200
        assert client.get('/api/v1/storage',headers=a).json()['used_bytes']==0


def test_worker_mounts_only_verified_owner_library_readonly(tmp_path,monkeypatch):
    file=Path(__file__).resolve().parents[2]/'deploy/dsh/worker.py'
    spec=importlib.util.spec_from_file_location('storage_worker_test',file)
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    accounts=tmp_path/'accounts';accounts.mkdir()
    alice=accounts/'alice';bob=accounts/'bob'
    for account in (alice,bob):
        (account/'workspace/library').mkdir(parents=True)
        (account/'tasks/dsh').mkdir(parents=True)
    (accounts/'.owners.json').write_text(json.dumps({'a':{'runtime_owner':'a'*64,'directory':'alice'},'b':{'runtime_owner':'b'*64,'directory':'bob'}}))
    monkeypatch.setenv('GALAXY_ACCOUNT_STORAGE','files-v1');monkeypatch.setattr(worker,'ACCOUNTS',accounts)
    own=worker.account_library('a'*64)
    assert own==alice/'workspace/library'
    args=worker.jail_arguments(tmp_path,tmp_path/'broker','token',own)
    index=args.index('/account-files')
    assert args[index-2:index+1]==['--ro-bind',str(own),'/account-files']
    assert str(bob) not in args and str(accounts) not in args
    with pytest.raises(ValueError):worker.account_library('c'*64)
    own.rmdir();own.symlink_to(bob/'workspace/library',target_is_directory=True)
    with pytest.raises(ValueError):worker.account_library('a'*64)


def test_legacy_shared_directory_never_lists_other_accounts_published_files(env):
    storage,a,b=env
    for user,name in ((a,'mine.xlsx'),(b,'other.xlsx')):
        (storage.files.root/name).write_bytes(b'data')
        with storage.files.repo.transaction() as state:
            state['files'][user['id']]={'user_id':user['id'],'workspace_id':'w','stored_name':name,'filename':name}
    (storage.files.root/'shared.txt').write_bytes(b'shared')
    assert {r['filename'] for r in storage.published(a)['files']}=={'mine.xlsx','shared.txt'}
    with pytest.raises(AuthFailure):storage.resolve_published(a,'shared','other.xlsx')


def test_chat_attachments_are_owner_checked_and_saved_as_file_references(tmp_path,monkeypatch):
    import httpx
    from xingxu.ai import ProviderInput
    monkeypatch.delenv('GALAXY_DATABASE_CONFIG',raising=False)
    payloads=[]
    def upstream(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'附件引用已接收。'}}]})
    app=create_app(tmp_path,ai_transport=httpx.MockTransport(upstream));auth=app.state.auth
    auth.import_accounts([('admin','fixture-password'),('employee','fixture-password')])
    owner=auth.login('admin','fixture-password',False,'local');other=auth.login('employee','fixture-password',False,'local')
    storage=app.state.file_storage
    one=upload(storage,owner['user'],'one.txt',b'one')
    two=upload(storage,owner['user'],'two.csv',b'a,b\n1,2')
    headers={'Authorization':'Bearer '+owner['access_token']}
    app.state.ai.save_provider(ProviderInput(name='Fixture',base_url='https://fixture.invalid/v1',model='fixture-model',api_key='fixture-only'))
    with TestClient(app) as client:
        body={'message':'分析这两份附件','file_ids':[one['id'],two['id']]}
        denied=client.post('/api/v1/chat/send',headers={'Authorization':'Bearer '+other['access_token']},json=body)
        assert denied.status_code==404 and not payloads
        duplicate=client.post('/api/v1/chat/send',headers=headers,json={**body,'file_ids':[one['id'],one['id']]})
        assert duplicate.status_code==422 and not payloads
        response=client.post('/api/v1/chat/send',headers=headers,json=body)
        assert response.status_code==200,response.text
        conversation=response.json()['conversation']
        sent=conversation['messages'][0]
        assert [r['id'] for r in sent['attached_files']]==[one['id'],two['id']]
        assert 'model_content' not in sent
        prompt=payloads[-1]['messages'][-1]['content']
        assert '/account-files/' in prompt and 'one.txt' in prompt and 'two.csv' in prompt
        assert '/opt/' not in prompt
        assert client.get('/api/v1/chat/conversations/'+conversation['id'],headers=headers).json()['messages'][0]['attached_files']==sent['attached_files']
        assert client.delete('/api/v1/chat/conversations/'+conversation['id'],headers=headers).status_code==200
        assert storage.resolve(owner['user'],one['id'])[0].read_bytes()==b'one'
        storage.delete(owner['user'],one['id'])
        missing=client.post('/api/v1/chat/send',headers=headers,json=body)
        assert missing.status_code==404
