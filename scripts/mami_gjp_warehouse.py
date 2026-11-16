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


"""ERP login and read-only queries; no save, update, sync or business write calls."""
from pathlib import Path
import http.cookiejar,json,re,urllib.request,urllib.error

root=Path('/opt/mami/current').resolve();props={}
for line in (root/'etc/business.properties').read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key,value=line.split('=',1);props[key.strip()]=value.strip()
domain=props['openapi.gjp.domain'].rstrip('/')
private=[v for k,v in props.items() if re.search('password|username|secret|token',k,re.I) and len(v)>2]
jar=http.cookiejar.CookieJar()

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*_):return None

opener=urllib.request.build_opener(NoRedirect(),urllib.request.HTTPCookieProcessor(jar))

def safe(value):
    value=str(value)
    for secret in private:value=value.replace(secret,'[hidden]')
    return re.sub(r'(?i)(password|pwd|token|sessionid|cookie)([=: ]+)[^;,\s]+',r'\1\2[hidden]',value)[:400]

def query(path,body):
    req=urllib.request.Request(domain+path,data=json.dumps(body,ensure_ascii=False).encode(),method='POST',
        headers={'Content-Type':'application/json;charset=UTF-8','Accept':'application/json','Origin':domain,'Referer':domain+'/'})
    try:
        response=opener.open(req,timeout=20)
    except urllib.error.HTTPError as error:response=error
    raw=response.read(2*1024*1024)
    data=None
    for encoding in ('utf-8-sig','gb18030'):
        try:data=json.loads(raw.decode(encoding));break
        except (ValueError,UnicodeError):pass
    report={'path':path,'http_status':response.code,'response_bytes':len(raw),'cookie_count':len(jar)}
    if isinstance(data,dict):
        report['top_keys']=list(data)
        report['messages']={k:safe(v) for k,v in data.items() if k.lower() in ('message','msg','error','code','retcode','success') and isinstance(v,(str,int,bool))}
    else:
        text=raw.decode('utf-8',errors='replace')
        title=re.search(r'<title>(.*?)</title>',text,re.I|re.S)
        report['html_title']=safe(title.group(1).strip()) if title else None
        # Emit only known diagnostic categories, never a raw error page or stack trace.
        report['diagnostic_flags']=[s for s in ('参数','登录','会话','验证码','不存在','Invalid','Exception','Error','404','Unauthorized') if s.lower() in text.lower()]
    print(json.dumps(report,ensure_ascii=False))
    return data

login={'user':{'LoginType':1,'mode':0,'name':props['openapi.gjp.username'],
               'password':props['openapi.gjp.password'],'database':props['openapi.gjp.database']},
       'isSoftDog':True,'softDogInfo':{},'cloudDbLst':[{}]}
result=query('/CarpaServer/CarpaServer.LoginService.ajax/UserLogin',login)
print(json.dumps({'authenticated_cookie_present':bool(list(jar)),'login_result_type':type(result).__name__}))
if list(jar):
    r=Redis()
    raw_btype=r.cmd('GET','sys_config:gjp.orders.btypeid')
    try:btype=json.loads(raw_btype) if raw_btype else ''
    except ValueError:btype=raw_btype.decode() if raw_btype else ''
    if not isinstance(btype,str):btype=''
    raw_warehouse=r.cmd('GET','sys_config:gjp.stock.head.code')
    try:warehouse=json.loads(raw_warehouse) if raw_warehouse else ''
    except ValueError:warehouse=raw_warehouse.decode() if raw_warehouse else ''
    checked=query('/Carpa.XiWa/Carpa.XiWa.Script.XWDataService.ajax/GetCache',{'typeName':'ktype','keyValues':[warehouse]})
    print('WAREHOUSE_CHECK',json.dumps({'code_or_id':warehouse,'found':bool(checked.get(warehouse)) if isinstance(checked,dict) else False,
        'identity':{k:v for obj in checked.values() if isinstance(obj,dict) for k,v in obj.items() if k.lower() in ('usercode','fullname','typeid')} if isinstance(checked,dict) else {}},ensure_ascii=False))
    r.socket.close()
