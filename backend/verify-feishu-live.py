"""Cloud readiness checks without supplying or changing enterprise app credentials."""
import json
import ssl
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
lines=(ROOT/'1.txt').read_text(encoding='utf-8-sig').splitlines()
client=httpx.Client(base_url='https://124.222.51.137',verify=ssl.create_default_context(cafile=str(ROOT/'desktop-client/electron/certs/server.crt')),trust_env=False,timeout=30)
results=[];headers=[]
try:
 for offset in (0,2):
  response=client.post('/api/v1/auth/login',json={'username':lines[offset],'password':lines[offset+1],'remember':False});response.raise_for_status()
  session=response.json();head={'Authorization':'Bearer '+session['access_token']};headers.append(head)
  feishu=client.get('/api/v1/feishu',headers=head);feishu.raise_for_status();state=feishu.json()
  youzan=client.get('/api/v1/admin/youzan',headers=head);youzan.raise_for_status()
  assert state['cli_ready'] is True and 'access_token' not in state and 'app_secret' not in state
  admin=session['user']['role']=='admin'
  assert client.get('/api/v1/admin/ai',headers=head).status_code==(200 if admin else 403)
  if not admin:
   assert 'has_app_secret' not in state
   assert client.put('/api/v1/feishu/app',headers=head,json={'app_id':'cli_fixtureapp','app_secret':'never-save-this'}).status_code==403
  results.append({'role':session['user']['role'],'cli_ready':state['cli_ready'],'app_configured':state['configured'],'connected':state['connected'],'youzan_configured':youzan.json()['configured'],'ai_config_allowed':admin})
 catalog=client.get('/api/v1/admin/ai/tools',headers=headers[0]);catalog.raise_for_status()
 tools=[t['tool'] for t in catalog.json()['data'] if t['category']=='feishu']
 from galaxy_cli.feishu_capabilities import DOMAINS
 assert set(tools)=={'feishu.search','feishu.document-get','feishu.folder-list','feishu.file-download'} | {'feishu.'+domain for domain in DOMAINS}
 report={'verified':True,'roles':results,'feishu_tools':tools,'real_documents_tested':False,'oauth_pending_user_configuration':not results[0]['app_configured'],'configuration_writes':0}
 (ROOT/'desktop-client/artifacts/feishu-cloud-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 print(json.dumps(report,ensure_ascii=False))
finally:
 for head in headers:client.post('/api/v1/auth/logout',headers=head)
 client.close()
