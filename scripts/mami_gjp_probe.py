"""Read-only inspection of the user-authorized Mami bridge. Never print credentials."""
import shlex
import sys
from pathlib import Path
import paramiko

raw = Path('C:/Users/Administrator/Desktop/新建文本文档 (2).txt').read_bytes()
for encoding in ('utf-8-sig', 'utf-16', 'gb18030'):
    try:
        password = raw.decode(encoding).strip()
        break
    except UnicodeError:
        pass
assert password and '\n' not in password and '\r' not in password
client = paramiko.SSHClient()
client.load_host_keys(str(Path.home() / '.ssh/known_hosts'))
client.set_missing_host_key_policy(paramiko.RejectPolicy())
client.connect('8.163.128.150', username='root', password=password, look_for_keys=False, allow_agent=False, timeout=15)


def run_python(code):
    _, output, _ = client.exec_command('python3 -c ' + shlex.quote(code), timeout=90)
    output.channel.set_combine_stderr(True)
    content = output.read().decode('utf-8', errors='replace')
    status = output.channel.recv_exit_status()
    print(content.replace(password, '[hidden]'), end='', flush=True)
    if status:
        raise SystemExit(f'Read-only probe failed: {status}')


try:
    mode = sys.argv[1]
    if mode == 'inspect':
        run_python('''from pathlib import Path
import json,zipfile,shutil
root=Path('/opt/mami/current').resolve()
print(json.dumps({'root':str(root),'javap_available':bool(shutil.which('javap'))}))
for name in ('/AGENTS.md','/opt/AGENTS.md','/opt/mami/AGENTS.md',str(root/'AGENTS.md'),str(root/'app/AGENTS.md')):
 p=Path(name)
 if p.exists(): print('INSTRUCTIONS',name,p.read_text())
print('CONFIG_FILES',json.dumps([str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and len(p.relative_to(root).parts)<4 and p.suffix in ('.yml','.yaml','.properties','.env')]))
for p in root.rglob('business.jar'):
 with zipfile.ZipFile(p) as z:
  names=[n for n in z.namelist() if n.startswith('BOOT-INF/classes/') and ('gjp' in n.lower() or 'token' in n.lower() or 'sync' in n.lower())]
  configs=[n for n in z.namelist() if n.startswith('BOOT-INF/classes/') and n.endswith(('.yml','.yaml','.properties'))]
  print('BUSINESS_JAR',str(p),json.dumps({'classes':names,'configs':configs}))
''')
    elif mode == 'script':
        code = Path(sys.argv[2]).read_text(encoding='utf-8')
        run_python(code)
    elif mode in ('db-metadata','db-sample'):
        import json
        import pymysql
        from urllib.parse import urlsplit
        with client.open_sftp() as sftp:
            text=sftp.open('/opt/mami/current/etc/business.properties').read().decode()
        props=dict(line.split('=',1) for line in text.splitlines() if '=' in line and not line.lstrip().startswith('#'))
        prefix='spring.datasource.dynamic.datasource.master.'
        url=urlsplit(props[prefix+'url'][5:])
        channel=client.get_transport().open_channel('direct-tcpip',(url.hostname,url.port or 3306),('127.0.0.1',0))
        db=pymysql.connect(user=props[prefix+'username'],password=props[prefix+'password'],database=url.path.strip('/'),defer_connect=True,read_timeout=15,ssl_disabled=True)
        db.connect(sock=channel)
        with db.cursor() as cursor:
            cursor.execute('SET SESSION TRANSACTION READ ONLY')
            if mode=='db-metadata':
                cursor.execute("SELECT TABLE_NAME,COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND COLUMN_NAME REGEXP 'token|cost|price|gjp|usercode|jdbc' ORDER BY TABLE_NAME,ORDINAL_POSITION LIMIT 160")
            else:
                cursor.execute('SELECT UserCode,FullName,RecPrice,RecPrice1,RetailPrice FROM ptype LIMIT 3')
            print('DB_READ',json.dumps(cursor.fetchall(),ensure_ascii=False,default=str))
        db.rollback();db.close()
    elif mode == 'cost-call':
        import io,zipfile
        from jawa.cf import ClassFile
        from jawa.constants import String
        with client.open_sftp() as sftp, sftp.open('/opt/mami/current/app/business.jar','rb') as remote, zipfile.ZipFile(remote) as archive:
            cf=ClassFile(io.BytesIO(archive.read('BOOT-INF/classes/com/zhiai/service/impl/GjpServiceImpl.class')))
            for method in cf.methods:
                if not method.code:continue
                ins=list(method.code.disassemble())
                for i,current in enumerate(ins):
                    if not current.mnemonic.startswith('invoke'):continue
                    c=cf.constants.get(current.operands[0].value)
                    if not hasattr(c,'name_and_type') or c.name_and_type.name.value not in ('getCache','getPtypeCbPriceByNoIn'):continue
                    print('COST_CALL',method.name.value,c.name_and_type.name.value)
                    for part in ins[max(0,i-20):i+1]:
                        value=[o.value for o in part.operands]
                        if part.mnemonic in ('ldc','ldc_w'):
                            k=cf.constants.get(value[0])
                            value=[k.string.value if isinstance(k,String) and len(k.string.value)<40 else '[literal omitted]']
                        print(part.pos,part.mnemonic,value)
    elif mode == 'login-shape':
        import io,zipfile
        from jawa.cf import ClassFile
        from jawa.constants import String
        with client.open_sftp() as sftp, sftp.open('/opt/mami/current/app/business.jar','rb') as remote, zipfile.ZipFile(remote) as archive:
            cf=ClassFile(io.BytesIO(archive.read('BOOT-INF/classes/com/zhiai/client/GjpClient.class')))
            for method in cf.methods:
                target=sys.argv[2] if len(sys.argv)>2 else 'getToken'
                if method.name.value!=target or target=='getToken' and method.descriptor.value!='(Ljava/lang/String;Z)Ljava/lang/String;':continue
                for ins in method.code.disassemble():
                    if not ((85 <= ins.pos <= 254) if target=='getToken' else (140 <= ins.pos <= 280) if target=='queryGridBaseInfo_GetStockQty_KP' else (0 <= ins.pos <= 142)):continue
                    values=[o.value for o in ins.operands]
                    if ins.mnemonic in ('ldc','ldc_w','invokeinterface','invokevirtual','invokespecial','getfield','getstatic','new'):
                        c=cf.constants.get(values[0])
                        if isinstance(c,String):v=c.string.value if len(c.string.value)<25 else '[literal omitted]'
                        elif hasattr(c,'name_and_type'):v=c.class_.name.value+'.'+c.name_and_type.name.value
                        else:v=str(c)
                        values=[v]
                    print(ins.pos,ins.mnemonic,values)
    elif mode == 'disasm':
        import io, json, zipfile
        from jawa.cf import ClassFile
        from jawa.constants import String
        with client.open_sftp() as sftp, sftp.open('/opt/mami/current/app/business.jar', 'rb') as remote, zipfile.ZipFile(remote) as archive:
            for path in ['com/zhiai/client/GjpClient.class', 'com/zhiai/client/GjpClient$URI.class', 'com/zhiai/service/jieshun/GjpSessionLookup.class']:
                cf = ClassFile(io.BytesIO(archive.read('BOOT-INF/classes/' + path)))
                print('METHODS', path, json.dumps([m.name.value for m in cf.methods]))
                for method in cf.methods:
                    if method.name.value not in ('doRestPost', 'getToken', '<clinit>'):
                        continue
                    if not method.code:
                        continue
                    print('METHOD', method.name.value, method.descriptor.value)
                    for instruction in method.code.disassemble():
                        if path.endswith('GjpClient.class') and (method.name.value == 'doRestPost' and instruction.pos > 410 or method.name.value == 'getToken' and instruction.pos > 45 or method.name.value == '<clinit>'):
                            continue
                        if path.endswith('$URI.class') and instruction.mnemonic not in ('ldc', 'ldc_w'):
                            continue
                        if instruction.mnemonic not in ('ldc', 'ldc_w', 'getfield', 'getstatic', 'putstatic', 'invokevirtual', 'invokestatic', 'invokeinterface', 'invokespecial'):
                            continue
                        constant = cf.constants.get(instruction.operands[0].value)
                        if isinstance(constant, String):
                            value = constant.string.value
                            if value.startswith(('http://', 'https://')):
                                from urllib.parse import urlsplit
                                u=urlsplit(value);value=str(u.hostname)+u.path
                            elif value.startswith('/'):
                                value=value.split('?',1)[0]
                            elif path.endswith('$URI.class'):
                                pass
                            elif len(value)>20 or '\n' in value:
                                value=f'<literal: {len(value)} chars>'
                            description=repr(value)
                        elif hasattr(constant, 'name_and_type'):
                            description=constant.class_.name.value+'.'+constant.name_and_type.name.value+constant.name_and_type.descriptor.value
                        else:
                            description=str(constant)
                        print(instruction.pos, instruction.mnemonic, description)
    else:
        raise SystemExit('Unknown read-only probe mode')
finally:
    client.close()
