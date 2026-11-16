from pathlib import Path
import json,re,struct,zipfile,urllib.parse

root=Path('/opt/mami/current').resolve()

def strings(data):
    count=struct.unpack_from('>H',data,8)[0];pos=10;cp={};refs=[];i=1
    while i<count:
        tag=data[pos];pos+=1
        if tag==1:
            n=struct.unpack_from('>H',data,pos)[0];pos+=2;cp[i]=data[pos:pos+n].decode('utf-8',errors='replace');pos+=n
        elif tag==8:refs.append(struct.unpack_from('>H',data,pos)[0]);pos+=2
        elif tag in (3,4):pos+=4
        elif tag in (5,6):pos+=8;i+=1
        elif tag in (7,16,19,20):pos+=2
        elif tag in (9,10,11,12,17,18):pos+=4
        elif tag==15:pos+=3
        i+=1
    return [cp.get(r,'') for r in refs]

with zipfile.ZipFile(root/'app/business.jar') as z:
    for path in ('com/zhiai/client/GjpClient.class','com/zhiai/client/GjpClient$URI.class','com/zhiai/service/jieshun/GjpSessionLookup.class'):
        safe=[]
        for value in strings(z.read('BOOT-INF/classes/'+path)):
            if len(value)>180:continue
            if value.startswith(('http://','https://')):
                u=urllib.parse.urlsplit(value);safe.append('URL '+str(u.hostname)+u.path)
            elif re.fullmatch(r'[A-Za-z_][A-Za-z0-9_./:$= -]{0,100}',value) or re.search('令牌|成本|token获取',value):
                safe.append(value)
        print(path,json.dumps(safe,ensure_ascii=False))

for folder in ('session-lab','session_lab','bin'):
    for p in (root/folder).rglob('*.py'):
        matches=[]
        for i,line in enumerate(p.read_text(errors='replace').splitlines(),1):
            if re.search(r'gjp|sessionid|queryGridBaseInfo|ptypeprice',line,re.I):
                # Only names and line numbers: the source may contain literal credentials.
                matches.append({'line':i,'terms':sorted(set(re.findall(r'gjp|sessionid|queryGridBaseInfo|ptypeprice',line,re.I)))})
        if matches:print('PY_MATCHES',str(p.relative_to(root)),json.dumps(matches[:35]))
