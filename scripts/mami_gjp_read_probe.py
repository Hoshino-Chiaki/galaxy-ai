from pathlib import Path
import socket,json,os

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
    for barcode in (os.environ['GALAXY_SAMPLE_BARCODE'],''):
        result=query('/CarpaServer/CarpaServer.Selector.VchSelectPtypeFromCodeService.ajax/GetTypeFromEntryCode',
          {'Param':{'isUnique':False,'entrycode':barcode,'selecttype':1,'IsSelectMore':True,'vchtype':'12','btypeid':btype}})
        found=[];price_fields=set()
        def visit(value,depth=0):
            if depth>9:return
            if isinstance(value,str) and value.lstrip().startswith(('{','[')):
                try:value=json.loads(value)
                except ValueError:return
            if isinstance(value,dict):
                prices={k:v for k,v in value.items() if re.search('cost|price|成本|进价',k,re.I) and isinstance(v,(str,int,float))}
                price_fields.update(prices)
                if prices and len(found)<3:
                    record={k:v for k,v in value.items() if k.lower() in ('typeid','usercode','fullname','barcode') and isinstance(v,(str,int,float))}
                    record.update(prices);found.append(record)
                for v in value.values():visit(v,depth+1)
            elif isinstance(value,list):
                for v in value[:10]:visit(v,depth+1)
        visit(result)
        print('PRODUCT_READ',json.dumps({'query_kind':'known_product' if barcode else 'selector','price_fields':sorted(price_fields),'sample':found},ensure_ascii=False))
        rows=result.get('returndata',[]) if isinstance(result,dict) else []
        if isinstance(rows,str):
            try:rows=json.loads(rows)
            except ValueError:rows=[]
        if isinstance(rows,dict):rows=[rows]
        for row in rows[:1]:
            if not isinstance(row,dict):continue
            print('PRODUCT_FIELDS',json.dumps({'keys':list(row),'identity':{k:v for k,v in row.items() if k.lower() in ('typeid','usercode','fullname','barcode')}},ensure_ascii=False))
            ptype=next((v for k,v in row.items() if k.lower()=='typeid'),None)
            if ptype:
                details=query('/CarpaServer/CarpaServer.vch.GoodsBillService.ajax/QueryGridBaseInfo_EntryCode',
                    {'Params':{'gridmode':2,'griddatafield':'details','vchtype':'12','types':[{'colorid':1,'typeid':ptype,'sizeid':1}]}})
                visit(details)
                print('GRID_PRICE_READ',json.dumps({'price_fields':sorted(price_fields),'sample':found},ensure_ascii=False))
                for cache_type in ('ptype','商品'):
                    cached=query('/Carpa.XiWa/Carpa.XiWa.Script.XWDataService.ajax/GetCache',{'typeName':cache_type,'keyValues':[ptype]})
                    before=len(found);visit(cached)
                    print('CACHE_PRICE_READ',json.dumps({'cache_type':cache_type,'price_fields':sorted(price_fields),'new_samples':found[before:]},ensure_ascii=False))
                    if isinstance(cached,dict) and 'Message' not in cached:break
                for cache_type in ('ptypeprice','pricetype'):
                    cached=query('/Carpa.XiWa/Carpa.XiWa.Script.XWDataService.ajax/GetCache',{'typeName':cache_type,'keyValues':[ptype]})
                    found.clear();visit(cached)
                    print('PRICE_CACHE_RESULT',json.dumps({'cache_type':cache_type,'price_fields':sorted(price_fields),'sample':found},ensure_ascii=False))
                raw_warehouse=r.cmd('GET','sys_config:gjp.stock.head.code')
                try:warehouse=json.loads(raw_warehouse) if raw_warehouse else ''
                except ValueError:warehouse=raw_warehouse.decode() if raw_warehouse else ''
                stock=query('/CarpaServer/CarpaServer.vch.GoodsBillService.ajax/QueryGridBaseInfo_GetStockQty_KP',{'Params':{
                    'griddatafield':'details','nomessage':True,'vchtype':'12','btypeid':btype,'ktypeid':warehouse,
                    'ptypeid':ptype,'QtyShow':True,'bDisplayStop':0,'selecttype':15,'sqlforwhere':' and typeflag in (0,1) ',
                    'isaddnew':1,'pageparams':{'VchType':'12','VchCode':0,'VchOpenType':0}}})
                visit(stock)
                print('STOCK_PRICE_READ',json.dumps({'price_fields':sorted(price_fields),'sample':found},ensure_ascii=False))
                tooltip=query('/CarpaServer/CarpaServer.vch.GoodsBillService.ajax/PtypeQtyPriceToolTip',
                    {'ptypeid':ptype,'ktypeid':warehouse,'btypeid':btype,'vchtype':'12'})
                visit(tooltip)
                print('TOOLTIP_PRICE_READ',json.dumps({'price_fields':sorted(price_fields),'sample':found},ensure_ascii=False))
                tip_args={'in_strBranchid':'','in_strKtypeid':warehouse,'in_strPtypeid':ptype}
                for attempt in range(6):
                    tooltip=query('/CarpaServer/CarpaServer.vch.GoodsBillService.ajax/PtypeQtyPriceToolTip',tip_args)
                    message=tooltip.get('Message','') if isinstance(tooltip,dict) else ''
                    missing=re.search("missing value for parameter: '([^']+)'",message)
                    if not missing:
                        found.clear();visit(tooltip)
                        print('TOOLTIP_FINAL',json.dumps({'fields':sorted(price_fields),'sample':found,'result_type':type(tooltip).__name__},ensure_ascii=False))
                        break
                    name=missing.group(1)
                    low=name.lower()
                    tip_args[name]=ptype if 'ptype' in low else warehouse if 'ktype' in low else btype if 'btype' in low else '12' if 'vchtype' in low else ''



        if rows:break
    r.socket.close()
