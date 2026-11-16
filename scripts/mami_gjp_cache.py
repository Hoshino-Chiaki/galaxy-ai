from pathlib import Path
import socket,json

root=Path('/opt/mami/current').resolve()
props={}
for line in (root/'etc/business.properties').read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        k,v=line.split('=',1);props[k.strip()]=v.strip()

class Redis:
    def __init__(self):
        self.socket=socket.create_connection((props.get('spring.redis.host','127.0.0.1'),int(props.get('spring.redis.port','6379'))),timeout=5)
        self.reader=self.socket.makefile('rb')
        if props.get('spring.redis.password'):self.cmd('AUTH',props['spring.redis.password'])
        self.cmd('SELECT',props.get('spring.redis.database','0'))
    def read(self):
        tag=self.reader.read(1);line=self.reader.readline().rstrip(b'\r\n')
        if tag==b'+':return line
        if tag==b'-':raise RuntimeError('Redis command rejected')
        if tag==b':':return int(line)
        if tag==b'$':
            n=int(line)
            if n<0:return None
            data=self.reader.read(n);self.reader.read(2);return data
        if tag==b'*':return [self.read() for _ in range(int(line))]
        raise RuntimeError('Redis response unavailable')
    def cmd(self,*args):
        values=[a if isinstance(a,bytes) else str(a).encode() for a in args]
        self.socket.sendall(b'*'+str(len(values)).encode()+b'\r\n'+b''.join(b'$'+str(len(v)).encode()+b'\r\n'+v+b'\r\n' for v in values))
        return self.read()

r=Redis();keys=set()
print('REDIS_CONNECTION',json.dumps({'host':props.get('spring.redis.host'),'port':props.get('spring.redis.port'),'database':props.get('spring.redis.database')}))
print('REDIS_KEYSPACE',r.cmd('INFO','keyspace').decode().strip())
for pattern in ('*[gG][jJ][pP]*','*[tT][oO][kK][eE][nN]*'):
    cursor=b'0'
    for _ in range(100):
        cursor,page=r.cmd('SCAN',cursor,'MATCH',pattern,'COUNT',200);keys.update(page)
        if cursor==b'0' or len(keys)>300:break
output=[]
for key in sorted(keys):
    name=key.decode(errors='replace');kind=r.cmd('TYPE',key).decode();item={'key':name if not name.startswith('login_tokens:') else 'login_tokens:[hidden]','type':kind,'ttl_seconds':r.cmd('TTL',key)}
    if 'token' in name.lower() and kind=='string':
        data=r.cmd('GET',key);item['value_present']=bool(data);item['value_bytes']=len(data or b'')
    output.append(item)
print(json.dumps({'matching_cache_keys':output},ensure_ascii=False))
r.socket.close()
