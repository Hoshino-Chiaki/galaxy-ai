from pathlib import Path
import json,re,zipfile,io,struct

root=Path('/opt/mami/current').resolve()
env=root/'etc/session.env'
print('SESSION_ENV_KEYS',json.dumps([l.split('=',1)[0] for l in env.read_text().splitlines() if '=' in l and not l.lstrip().startswith('#')]))
for relative,ranges in [('session-lab/session_runtime.py',[(180,208),(405,430),(480,535),(604,625)]),('session-lab/retail_bridge.py',[(45,64),(85,103)])]:
    lines=(root/relative).read_text().splitlines()
    print('SOURCE',relative)
    for start,end in ranges:
        for i in range(start,min(end,len(lines))+1):
            text=lines[i-1]
            if re.search('password|secret|token|cookie|authorization',text,re.I): text='[credential handling omitted]'
            print(str(i)+': '+text)

def literal_strings(data):
    count=struct.unpack_from('>H',data,8)[0];off=10;cp={};refs=[];i=1
    while i<count:
        tag=data[off];off+=1
        if tag==1:
            n=struct.unpack_from('>H',data,off)[0];off+=2;cp[i]=data[off:off+n].decode('utf-8',errors='replace');off+=n
        elif tag==8:refs.append(struct.unpack_from('>H',data,off)[0]);off+=2
        elif tag in (3,4):off+=4
        elif tag in (5,6):off+=8;i+=1
        elif tag in (7,16,19,20):off+=2
        elif tag in (9,10,11,12,17,18):off+=4
        elif tag==15:off+=3
        i+=1
    return [cp.get(r,'') for r in refs]

with zipfile.ZipFile(root/'app/business.jar') as z:
    name=next(n for n in z.namelist() if n.startswith('BOOT-INF/lib/ruoyi-common-core-'))
    with zipfile.ZipFile(io.BytesIO(z.read(name))) as core:
        strings=literal_strings(core.read('com/ruoyi/common/core/enums/GJPEnum$Const.class'))
        print('TOKEN_KEY_CONSTANTS',json.dumps([s for s in strings if re.search('token|gjp|format|%s',s,re.I) and len(s)<100]))
