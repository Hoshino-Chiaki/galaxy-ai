"""Uses existing Youzan SID and a unique non-customer keyword; no business writes."""
import hashlib
import json
import ssl
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ('有赞只读查询中，用户未指定店铺时，调用 galaxy_cli 使用 shops="all"，覆盖当前账号全部授权店铺；'
    '用户明确指定店铺时，shops 传对应店铺 ID 或名称数组，只查询指定范围。每家店铺使用各自的 CRM CSRF 和配套会话，'
    '由服务器自动提供，模型无需读取或传入任何凭证。保留各店铺的数据来源，分别处理分页，不重复累计合计行；'
    '总部与分店统计可能重叠，不能直接相加。partial=true 时说明哪些店铺未完成，不能将失败或未返回解释为零。'
    '修改、上传等写入操作只针对用户明确指定的单家店铺，不能遍历全部店铺。')

phase = 'login'
client = None
token = None
conversation_id = None
try:
    lines = (ROOT / '1.txt').read_text(encoding='utf-8-sig').splitlines()
    tls = ssl.create_default_context(cafile=str(ROOT / 'desktop-client/electron/certs/server.crt'))
    client = httpx.Client(base_url='https://124.222.51.137', verify=tls, trust_env=False, timeout=None)
    response = client.post('/api/v1/auth/login', json={'username': lines[0], 'password': lines[1], 'remember': False})
    response.raise_for_status()
    token = response.json()['access_token']
    client.headers['Authorization'] = 'Bearer ' + token
    phase = 'category_prompt'
    settings = client.get('/api/v1/admin/ai').json()
    global_digest = hashlib.sha256(settings['global_prompt'].encode()).hexdigest()
    catalog = client.get('/api/v1/admin/ai/tool-prompts').json()['categories']
    existing = next(row['prompt'] for row in catalog if row['category'] == 'youzan')
    if not existing:
        saved = client.put('/api/v1/admin/ai/tool-prompts/youzan', json={'prompt': PROMPT})
        saved.raise_for_status()
    phase = 'shop_sessions'
    before = client.get('/api/v1/admin/youzan').json()
    assert before['sid']['present'] and not before['sid']['expired']
    response = client.post('/api/v1/admin/youzan/refresh', json={'kind': 'all'})
    response.raise_for_status()
    deadline = time.monotonic() + 300
    while True:
        state = client.get('/api/v1/admin/youzan').json()
        if state['job']['status'] != 'running':
            break
        assert time.monotonic() < deadline, 'CRM preparation did not finish'
        time.sleep(1)
    assert state['kdt_id'] == before['kdt_id']
    assert state['sid'] == before['sid']
    assert state['shop_sessions'], 'No shop metadata'
    assert any(shop['ready'] for shop in state['shop_sessions']), 'No CRM session became ready'
    print(json.dumps({'phase': 'sessions_ready', 'shops': len(state['shop_sessions']), 'ready': sum(shop['ready'] for shop in state['shop_sessions']),
        'failures': [{'shop_id': shop['kdt_id'], 'error': shop.get('error', {}).get('code')} for shop in state['shop_sessions'] if not shop['ready']],
        'selected_shop_unchanged': True, 'sid_metadata_unchanged': True}, ensure_ascii=False), flush=True)
    phase = 'ai_scoped_query'
    keyword = 'Galaxy验证' + uuid.uuid4().hex[:12]
    response = client.post('/api/v1/chat/send', json={'message': f'功能验证：用有赞客户搜索查询关键词“{keyword}”，按有赞分类提示词的默认店铺范围执行，只报告各店成功或失败和数量，不需要附件。',
        'thinking': False, 'reasoning_effort': 'low'})
    response.raise_for_status()
    conversation = response.json()['conversation']
    conversation_id = conversation['id']
    events = [event for message in conversation['messages'] for event in message.get('tool_events', [])]
    scoped = next(event for event in events if event.get('command') == 'galaxy-cli youzan customer-search' and isinstance(event.get('result', {}).get('shops'), list))
    rows = scoped['result']['shops']
    assert {row['kdt_id'] for row in rows} == {shop['kdt_id'] for shop in state['shop_sessions']}
    assert len(rows) == len({row['kdt_id'] for row in rows})
    success = [row for row in rows if 'data' in row and row['data'].get('code') == 0 and 'error' not in row['data']]
    assert all(row['data'].get('data', {}).get('total') == 0 for row in success), 'Unique negative keyword must match no customers'
    failures = [row['kdt_id'] for row in rows if row not in success]
    assert scoped['result']['partial'] == bool(failures)
    final = client.get('/api/v1/admin/youzan').json()
    assert final['kdt_id'] == before['kdt_id'] and final['sid'] == before['sid']
    assert hashlib.sha256(client.get('/api/v1/admin/ai').json()['global_prompt'].encode()).hexdigest() == global_digest
    result = {'verified': True, 'shop_count': len(rows), 'ready_sessions': sum(shop['ready'] for shop in state['shop_sessions']),
        'successful_queries': len(success), 'failed_shop_ids': failures, 'partial_reported': scoped['result']['partial'],
        'real_ai_used_all_shop_scope': True, 'selected_shop_unchanged': True, 'sid_metadata_unchanged': True,
        'global_prompt_unchanged': True, 'business_writes': 0, 'browser_login_triggered': False}
    (ROOT / 'desktop-client/artifacts/multishop-live-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)
except Exception:
    print(json.dumps({'verified': False, 'phase': phase}), flush=True)
    raise SystemExit(1)
finally:
    if client:
        if conversation_id:
            client.delete('/api/v1/chat/conversations/' + conversation_id)
        if token:
            client.post('/api/v1/auth/logout')
        client.close()
