import dnfile,json
from pathlib import Path
p=dnfile.dnPE('artifacts/silverlight-static-20261116/client/CarpaClient.dll');h=p.net.user_strings;b=h.__data__;at=1;hits=[]
while at<len(b):
 start=at;c=b[at]
 if c<128:n=c;size=1
 elif c<192:n=((c&63)<<8)|b[at+1];size=2
 elif c<224:n=((c&31)<<24)|(b[at+1]<<16)|(b[at+2]<<8)|b[at+3];size=4
 else:break
 if n==0:at+=size;continue
 s=b[at+size:at+size+n-1].decode('utf-16le',errors='replace')
 if 'avgtotal' in s.lower() or ('sum(' in s.lower() and any(t in s.lower() for t in ('good','stock','total'))):hits.append({'offset':start,'value':s})
 at+=size+n
print(json.dumps(hits,ensure_ascii=False,indent=2)[:10000])
Path('artifacts/silverlight-static-20261116/stock-string-evidence.json').write_text(json.dumps(hits,ensure_ascii=False,indent=2),encoding='utf-8')
