"""Read-only live checks against the saved, selected Youzan connection."""
import datetime as dt
import hashlib,json,ssl,subprocess
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
ROOT=Path(__file__).resolve().parents[1]
report={'checks':[]};client=None;token=None;phase='login'

def config_digest():
 p=subprocess.run(['ssh','root@124.222.51.137',"sha256sum /opt/galaxy-ai/backend/private/youzan.json"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20,check=True)
 return p.stdout.decode().split()[0]

def check(name,args):
 frame={'token':token,'args':args}
 p=subprocess.run(['ssh','root@124.222.51.137','runuser -u galaxy-ai -- env GALAXY_FILES_DIR=/opt/galaxy-ai/hosted-files PYTHONPATH=/opt/galaxy-ai/backend /opt/galaxy-ai/backend/.venv/bin/python /tmp/galaxy-catalog-dispatch-runner.py'],
  input=json.dumps(frame).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=150)
 if p.returncode:raise RuntimeError('verification process failed')
 e=json.loads(p.stdout);r=e['result'];item={'name':name,'status':e['status'],'code':r.get('code'),'error_code':r.get('error',{}).get('code')}
 if e['status']!='success':item['message']=r.get('error',{}).get('message',r.get('msg'));report['checks'].append(item);raise RuntimeError('tool failed')
 report['checks'].append(item);print(json.dumps(item,ensure_ascii=False),flush=True);return r

try:
 before=config_digest()
 lines=(ROOT/'1.txt').read_text().lstrip('\ufeff').splitlines()
 client=httpx.Client(base_url='https://124.222.51.137',verify=ssl.create_default_context(cafile=str(ROOT/'desktop-client/electron/certs/server.crt')),trust_env=False,timeout=30)
 response=client.post('/api/v1/auth/login',json={'username':lines[0],'password':lines[1],'remember':False});assert response.status_code==200
 token=response.json()['access_token'];client.headers['Authorization']='Bearer '+token
 settings=client.get('/api/v1/admin/youzan').json()
 shop=next((s['name'] for s in settings.get('shops',[]) if s['id']==settings['kdt_id']),settings['kdt_id'])
 report.update({'selected_shop_id':settings['kdt_id'],'selected_shop_name':shop,'saved_sid_expired_flag':settings['sid']['expired']})
 phase='report_options'
 options=check('经营报表筛选选项',['youzan','business-report','options'])
 assert options.get('sales_channels',{}).get('code')==200
 phase='today_report'
 today=dt.datetime.now(ZoneInfo('Asia/Shanghai')).date()
 for label,date in [('今日经营报表',today),('昨日经营报表',today-dt.timedelta(days=1))]:
  result=check(label,['youzan','business-report','query','--input',json.dumps({'start_date':str(date),'end_date':str(date),'period':'day','dimensions':[4],'page_size':1})])
  assert result.get('code')==200 and result.get('success') is True
  data=result.get('data',{});assert data.get('status')==1 and data.get('data',{}).get('code')==200
 phase='customer_search'
 check('客户搜索',['youzan','customer-search','--input',json.dumps({'page':1,'page_size':1})])
 phase='existing_download'
 old=json.loads((ROOT/'desktop-client/artifacts/file-publish-live-check.json').read_text())
 meta=client.get('/api/v1/files/'+old['file_id']+'/metadata');assert meta.status_code==200
 file=client.get(meta.json()['download_path']);assert file.status_code==200 and len(file.content)==meta.json()['size']
 report['checks'].append({'name':'已有 Excel 附件下载','status':'success'})
 after=config_digest();report['youzan_configuration_unchanged']=before==after
 assert before==after
 report.update({'verified':True,'date':str(today),'session_replaced':False,'shop_changed':False,'business_data_modified':False})
 print(json.dumps({'verified':True,'selected_shop':shop,'youzan_configuration_unchanged':True},ensure_ascii=False))
except Exception:
 report.update({'verified':False,'failed_phase':phase});print(json.dumps({'verified':False,'phase':phase},ensure_ascii=False));raise SystemExit(1)
finally:
 (ROOT/'desktop-client/artifacts/current-session-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 if client:
  if token:
   try:client.post('/api/v1/auth/logout')
   except Exception:pass
  client.close()
