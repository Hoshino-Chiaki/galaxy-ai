from pathlib import Path
import zipfile,io,struct,json
root=Path("/opt/mami/current").resolve()
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
        strings=literal_strings(core.read('com/ruoyi/common/core/enums/GJPEnum$ORDER_TYPE.class'))
        print('ORDER_TYPES',json.dumps(strings,ensure_ascii=False))
