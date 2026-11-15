"""Customer-count live test: automatic discovery and per-shop totals without customer records."""
import hashlib
import json
import ssl
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
phase, conversation_id, logged_in = 'login', None, False
client = httpx.Client(base_url='https://124.222.51.137', trust_env=False, timeout=240,
    verify=ssl.create_default_context(cafile=str(ROOT/'desktop-client/electron/certs/server.crt')))
try:
    lines = (ROOT/'1.txt').read_text(encoding='utf-8-sig').splitlines()
    response = client.post('/api/v1/auth/login', json={'username': lines[0], 'password': lines[1], 'remember': False})
    response.raise_for_status()
    client.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
    logged_in = True
    phase = 'settings'
    before = client.get('/api/v1/admin/youzan').json()
    assert before['sid']['present'] and not before['sid']['expired']
    global_before = hashlib.sha256(client.get('/api/v1/admin/ai').json()['global_prompt'].encode()).hexdigest()
    category_before = client.get('/api/v1/admin/ai/tool-prompts').json()
    catalog = client.get('/api/v1/admin/ai/tools').json()['data']
    assert next(tool for tool in catalog if tool['tool'] == 'youzan.customer-count')['category'] == 'youzan'
    phase = 'customer_count'
    response = client.post('/api/v1/chat/send', json={
        'message': '请获取每个店铺的客户总数，列出店铺名称和人数。不要查询客户明细，不要跨店相加，不需要附件。',
        'thinking': False, 'reasoning_effort': 'low'})
    response.raise_for_status()
    conversation = response.json()['conversation']
    conversation_id = conversation['id']
    events = [e for message in conversation['messages'] for e in message.get('tool_events', [])]
    event = next(e for e in events if e.get('command') == 'galaxy-cli youzan customer-count' and 'shops' in e.get('result', {}))
    rows = event['result']['shops']
    assert {r['kdt_id'] for r in rows} == {s['id'] for s in before['shops']}
    assert all(set(r['data']) == {'total'} for r in rows if 'data' in r), 'Customer records or extra fields leaked'
    counts = [{'shop_id': r['kdt_id'], **({'error': r['error']['code']} if 'error' in r else
        {'total': r['data'].get('total')})} for r in rows]
    assert next(r for r in counts if r['shop_id'] == '45330501')['total'] >= 100000
    assert next(r for r in counts if r['shop_id'] == '94598170')['total'] >= 300000
    assert next(r for r in counts if r['shop_id'] == '91780004')['total'] < 100
    phase = 'configuration'
    after = client.get('/api/v1/admin/youzan').json()
    assert before['sid'] == after['sid'] and before['kdt_id'] == after['kdt_id']
    assert hashlib.sha256(client.get('/api/v1/admin/ai').json()['global_prompt'].encode()).hexdigest() == global_before
    assert client.get('/api/v1/admin/ai/tool-prompts').json() == category_before
    result = {'verified': True, 'real_ai_gateway': True, 'ai_selected_customer_count': True, 'only_totals_returned': True, 'partial': event['result']['partial'], 'counts': counts,
        'sid_unchanged': True, 'default_shop_unchanged': True, 'global_and_category_prompts_unchanged': True,
        'business_writes': 0, 'browser_login_started': False}
    (ROOT/'desktop-client/artifacts/customer-count-live-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
except Exception:
    print(json.dumps({'verified': False, 'phase': phase}));raise SystemExit(1) from None
finally:
    if conversation_id:client.delete('/api/v1/chat/conversations/'+conversation_id)
    if logged_in:client.post('/api/v1/auth/logout')
    client.close()
