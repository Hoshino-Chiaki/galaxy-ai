from pathlib import Path
import json,re,struct,zipfile
from urllib.parse import urlsplit

root=Path('/opt/mami/current').resolve()
for p in (root/'etc').glob('*'):
    if not p.is_file() or p.suffix not in ('.properties','.env'): continue
    pairs=[]
    for line in p.read_text(errors='replace').splitlines():
        if not line.strip() or line.lstrip().startswith('#') or '=' not in line: continue
        k,v=line.split('=',1);k=k.strip();v=v.strip()
        if re.search(r'gjp|datasource|jdbc|redis|token|jieshun',k,re.I):
            item={'key':k,'configured':bool(v)}
            if v.startswith(('jdbc:', 'http://','https://')):
                u=urlsplit(v[5:] if v.startswith('jdbc:') else v)
                item['endpoint']=(u.hostname or '')+u.path
            pairs.append(item)
    if pairs: print('CONFIG_KEYS',p.name,json.dumps(pairs))

def constants(data):
    count=struct.unpack_from('>H',data,8)[0];off=10;i=1;values=[]
    while i<count:
        tag=data[off];off+=1
        if tag==1:
            n=struct.unpack_from('>H',data,off)[0];off+=2;values.append(data[off:off+n].decode('utf-8',errors='replace'));off+=n
        elif tag in (3,4):off+=4
        elif tag in (5,6):off+=8;i+=1
        elif tag in (7,8,16,19,20):off+=2
        elif tag in (9,10,11,12,17,18):off+=4
        elif tag==15:off+=3
        else:raise ValueError('class format')
        i+=1
    return values

jar=root/'app/business.jar'
with zipfile.ZipFile(jar) as z:
    paths=['com/zhiai/config/GjpOpenApiConfig.class','com/zhiai/client/GjpClient.class','com/zhiai/client/GjpClient$URI.class',
           'com/zhiai/service/impl/GjpServiceImpl.class','com/zhiai/service/jieshun/GjpSessionLookup.class']
    for path in paths:
        full='BOOT-INF/classes/'+path
        if full not in z.namelist():continue
        found=[]
        for v in constants(z.read(full)):
            if len(v)<180 and re.search(r'cost|price|gjp|token|query|get|stock|ptype|http|session|openapi|secret|sign|redis',v,re.I):
                if v.startswith(('http://','https://')):
                    u=urlsplit(v);v=(u.hostname or '')+u.path
                found.append(v)
        print('CLASS_CONSTANTS',path,json.dumps(found,ensure_ascii=False))
    for name in ('BOOT-INF/classes/mapper/zhiai/GjpMapper.xml','BOOT-INF/classes/jieshun-bridge.properties'):
        if name.endswith('.xml'):
            text=z.read(name).decode(errors='replace')
            print('MAPPER',name,text[:12000])
    print('SDK_LIBS',json.dumps([n for n in z.namelist() if n.startswith('BOOT-INF/lib/') and re.search('gjp|redis|mysql|jdbc|http|yaml|gson',n,re.I)]))
print('JAVAP',json.dumps([str(p) for p in Path('/usr/lib/jvm').glob('*/bin/javap')]))
