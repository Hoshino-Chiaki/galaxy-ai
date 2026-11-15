"""Live feature verification: fake workbook data only; never print credentials."""
import io
import json
import ssl
import zipfile
from pathlib import Path
import httpx

root=Path(__file__).resolve().parents[1]
phase='login'; client=None; token=None
try:
    lines=(root/'1.txt').read_text().lstrip('\ufeff').splitlines()
    tls=ssl.create_default_context(cafile=str(root/'desktop-client/electron/certs/server.crt'))
    client=httpx.Client(base_url='https://124.222.51.137',verify=tls,trust_env=False,timeout=None)
    login=client.post('/api/v1/auth/login',json={'username':lines[0],'password':lines[1],'remember':False})
    assert login.status_code==200
    token=login.json()['access_token']; client.headers['Authorization']='Bearer '+token
    phase='configuration'
    config=client.get('/api/v1/admin/ai').json()
    assert config['providers'] and any(p['has_api_key'] for p in config['providers'])
    prompt=config['global_prompt']
    phase='catalog'
    catalog=client.get('/api/v1/admin/ai/tools').json()['data']
    assert len(catalog)==5 and {t['category'] for t in catalog}=={'youzan','excel','file'}
    assert all(set(t)=={'tool','category','description'} for t in catalog)
    phase='legacy_download'
    old=json.loads((root/'desktop-client/artifacts/cloud-live-check.json').read_text())
    assert client.get('/api/v1/files/'+old['file_id']).status_code==200
    phase='real_ai_generate_then_publish'
    response=client.post('/api/v1/chat/send',json={'message':
        '文件托管功能验证：用 Excel 工具生成标题「文件托管验证（演示）」的工作簿，一个工作表「流程验证」，两列「项目」「结果」，三行「生成文件/成功」「托管附件/成功」「目录隔离/启用」。这些都是演示文字。生成后使用返回的 path 调用 file.publish，最后告诉我可以下载。',
        'thinking':False,'reasoning_effort':'low'})
    assert response.status_code==200
    chat=response.json()['conversation']
    events=[event for m in chat['messages'] for event in m.get('tool_events',[])]
    generated=next(e['result'] for e in events if e['command']=='galaxy-cli excel create' and e['status']=='success')
    assert 'path' in generated and 'file_id' not in generated
    publication=next(e['result'] for e in events if e['command']=='galaxy-cli file publish' and e['status']=='success')
    assert publication['filename']==generated['filename']
    phase='download'
    result=client.get('/api/v1/files/'+publication['file_id']+'/metadata')
    assert result.status_code==200 and result.json()==publication
    data=client.get(publication['download_path'])
    assert data.status_code==200 and len(data.content)==publication['size']
    with zipfile.ZipFile(io.BytesIO(data.content)) as book:
        assert '流程验证' in book.read('xl/workbook.xml').decode()
    assert httpx.get('https://124.222.51.137'+publication['download_path'],verify=tls,trust_env=False).status_code==401
    phase='ownership'
    other=client.post('/api/v1/auth/login',json={'username':lines[2],'password':lines[3],'remember':False})
    assert other.status_code==200
    other_token=other.json()['access_token']
    try:
        assert client.get(publication['download_path'],headers={'Authorization':'Bearer '+other_token}).status_code==404
    finally: client.post('/api/v1/auth/logout',headers={'Authorization':'Bearer '+other_token})
    assert client.get('/api/v1/admin/ai').json()['global_prompt']==prompt
    artifact={'verified':True,'server':'124.222.51.137','conversation_id':chat['id'],**publication,
        'source_path':generated['path'],'generate_then_publish':True,'old_attachment_compatible':True,
        'login_required':True,'cross_user_denied':True,'categories':['excel','file','youzan']}
    output=root/'desktop-client/artifacts'
    (output/'file-publish-live-check.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2)+'\n')
    (output/'file-publish-demo.xlsx').write_bytes(data.content)
    print(json.dumps(artifact,ensure_ascii=False))
except Exception:
    print(json.dumps({'verified':False,'phase':phase}));raise SystemExit(1)
finally:
    if client:
        if token:
            try: client.post('/api/v1/auth/logout')
            except Exception: pass
        client.close()
