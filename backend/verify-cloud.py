"""Verify cloud migration without printing credentials or business values."""
import io
import json
import ssl
import zipfile
from pathlib import Path
from urllib.parse import quote

import httpx

root = Path(__file__).resolve().parents[1]
client = None; token = None; phase = 'login'
try:
    lines = (root / '1.txt').read_text().lstrip('\ufeff').splitlines()
    client = httpx.Client(base_url='https://124.222.51.137', verify=ssl.create_default_context(cafile=str(root / 'desktop-client/electron/certs/server.crt')), trust_env=False, timeout=None)
    response = client.post('/api/v1/auth/login', json={'username': lines[0], 'password': lines[1], 'remember': False})
    assert response.status_code == 200
    token = response.json()['access_token']; client.headers['Authorization'] = 'Bearer ' + token
    phase = 'migrated_settings'
    ai = client.get('/api/v1/admin/ai').json()
    assert ai['providers'] and any(p['has_api_key'] for p in ai['providers'])
    youzan = client.get('/api/v1/admin/youzan').json()
    assert youzan['configured'] and youzan['has_password'] and youzan['sid']['present'] and youzan['csrf']['present']
    phase = 'legacy_attachment'
    previous = json.loads((root / 'desktop-client/artifacts/daily-comparison-live-check.json').read_text())
    legacy = client.get('/api/v1/files/' + previous['file_id'] + '/metadata')
    assert legacy.status_code == 200
    assert client.get(legacy.json()['download_path']).status_code == 200
    phase = 'real_ai_and_youzan'
    args = ['youzan', 'business-report', 'query', '--input', json.dumps({'start_date': '2026-10-01', 'end_date': '2026-10-31', 'period': 'month', 'dimensions': [4], 'page_size': 1})]
    prompt = ('服务器迁移联调：实际调用 galaxy_cli，args=' + json.dumps(args, ensure_ascii=False) + '，只确认查询接口是否成功，不复述经营数字。'
        '然后调用 excel 分类工具，生成标题「云端部署验证（演示）」的 Excel，一个工作表「连接验证」，两列「项目」「状态」，'
        '三行内容为「云端AI/接通」「有赞接口/接通」「文件名下载/接通」，仅使用这些演示文字，不把实际经营数据写进表。完成后简短回复。')
    response = client.post('/api/v1/chat/send', json={'message': prompt, 'thinking': False, 'reasoning_effort': 'low'})
    if response.status_code != 200:
        print(json.dumps({'verified': False, 'phase': phase, 'error_code': response.json().get('error', {}).get('code')}))
        raise SystemExit(1)
    chat = response.json()['conversation']
    events = [e for m in chat['messages'] for e in m.get('tool_events', [])]
    assert any(e['command'] == 'galaxy-cli youzan business-report query' and e['status'] == 'success' for e in events)
    file = next(e['result'] for e in events if e['command'] == 'galaxy-cli excel create' and e['status'] == 'success')
    phase = 'named_download'
    metadata = client.get('/api/v1/files/' + file['file_id'] + '/metadata').json()
    path = metadata['download_path']
    assert path.startswith('/files/') and file['file_id'] in path
    downloaded = client.get(path)
    assert downloaded.status_code == 200 and len(downloaded.content) == file['size']
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as book:
        assert 'xl/workbook.xml' in book.namelist()
    phase = 'ownership'
    unauthenticated = httpx.get('https://124.222.51.137' + path, verify=ssl.create_default_context(cafile=str(root / 'desktop-client/electron/certs/server.crt')), trust_env=False)
    assert unauthenticated.status_code == 401
    employee_token = None
    if len(lines) >= 4:
        response = client.post('/api/v1/auth/login', json={'username': lines[2], 'password': lines[3], 'remember': False})
        assert response.status_code == 200
        employee_token = response.json()['access_token']
        try:
            assert client.get(path, headers={'Authorization': 'Bearer ' + employee_token}).status_code == 404
        finally:
            client.post('/api/v1/auth/logout', headers={'Authorization': 'Bearer ' + employee_token})
    report = {'verified': True, 'server': '124.222.51.137', 'https_port': 443, 'original_account_login': True,
        'ai_configuration_migrated': True, 'youzan_configuration_migrated': True, 'real_cloud_ai_and_youzan': True,
        'old_attachment_download': True, 'filename_download': True, 'unauthenticated_denied': True,
        'cross_user_denied': bool(employee_token), 'conversation_id': chat['id'], **file, 'download_path': path}
    output = root / 'desktop-client/artifacts'
    (output / 'cloud-live-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (output / 'cloud-excel-demo.xlsx').write_bytes(downloaded.content)
    print(json.dumps(report, ensure_ascii=False))
except SystemExit:
    raise
except Exception:
    print(json.dumps({'verified': False, 'phase': phase}))
    raise SystemExit(1) from None
finally:
    if client:
        if token:
            try: client.post('/api/v1/auth/logout')
            except Exception: pass
        client.close()
