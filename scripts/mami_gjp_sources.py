from pathlib import Path
import json,re,subprocess,urllib.parse,urllib.request,urllib.error

root=Path('/opt/mami/current').resolve()
props={}
for line in (root/'etc/business.properties').read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        k,v=line.split('=',1);props[k.strip()]=v.strip()


logs=[]
for folder in (root/'logs',Path('/opt/mami/logs'),Path('/var/log/mami')):
    if not folder.exists():continue
    for p in folder.glob('**/*'):
        if p.is_file() and p.suffix=='.log':logs.append({'path':str(p),'size':p.stat().st_size})
print('LOG_FILES',json.dumps(logs[:20]))

domain=props['openapi.gjp.domain'].rstrip('/')
parts=urllib.parse.urlsplit(domain)
print('ERP_ENDPOINT',json.dumps({'scheme':parts.scheme,'host':parts.hostname}))
request=urllib.request.Request(domain+'/CarpaServer/CarpaServer.LoginService.ajax/_GetServerContextData',
    data=b'{}',headers={'Content-Type':'application/json'},method='POST')
try:
    with urllib.request.urlopen(request,timeout=20) as response:
        raw=response.read(1024*1024)
        data=json.loads(raw.decode('utf-8-sig',errors='replace'))
        print('CONTEXT_RESPONSE',json.dumps({'http_status':response.status,'top_keys':list(data) if isinstance(data,dict) else [],'set_cookie_present':bool(response.headers.get('Set-Cookie'))}))
except urllib.error.HTTPError as error:print('CONTEXT_RESPONSE',{'http_status':error.code})
except Exception as error:print('CONTEXT_RESPONSE',{'error_type':type(error).__name__})
