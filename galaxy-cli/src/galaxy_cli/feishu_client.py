"""Fixed official Feishu CLI wrapper; employee token enters only through private stdin."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from jsonschema import Draft202012Validator
from .context import read_context
from .file_scope import root_directory, source_file
from .registry import ToolError
from . import feishu_capabilities as capabilities
from .feishu_errors import raise_upstream
from .tools.feishu.authorized_users import INPUT as AUTHORIZED_USERS_INPUT, normalize as normalize_authorized_users

ROOT = Path(__file__).resolve().parents[3]
BINARY = ROOT/'backend/feishu/bin/lark-cli'
QUERY = {'type':'string','maxLength':30}
PAGE = {'page_size':{'type':'integer','minimum':1,'maximum':20},'page_token':{'type':'string','maxLength':2048}}
RESOURCE = {'type':'string','minLength':1,'maxLength':2048}
INPUTS = {
 'authorized-users':AUTHORIZED_USERS_INPUT,
 'search':{'type':'object','properties':{'query':QUERY,**PAGE},'additionalProperties':False},
 'document-get':{'type':'object','properties':{'document':RESOURCE,'scope':{'enum':['outline','keyword','section']},
     'keyword':{'type':'string','maxLength':300},'start_block_id':{'type':'string','pattern':'^[A-Za-z0-9_-]{1,200}$'}},'required':['document'],'additionalProperties':False},
 'folder-list':{'type':'object','properties':{'folder_token':{'type':'string','pattern':'^[A-Za-z0-9_-]{1,200}$'},**PAGE},'additionalProperties':False},
 'file-download':{'type':'object','properties':{'file':RESOURCE,'filename':{'type':'string','minLength':1,'maxLength':160}},'required':['file','filename'],'additionalProperties':False},
}
INPUTS.update({domain:capabilities.DOMAIN_SCHEMA for domain in capabilities.DOMAINS})

def resource(value, *, document=False):
    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,199}',value):return value
    try:
        parts=urlsplit(value)
        if parts.scheme!='https' or not parts.hostname or not parts.hostname.endswith('.feishu.cn') or parts.username or parts.password or parts.port not in (None,443):raise ValueError()
        patterns=r'/(?:docx|wiki)/[A-Za-z0-9_-]+' if document else r'/(?:drive/)?file/[A-Za-z0-9_-]+|/wiki/[A-Za-z0-9_-]+'
        if not re.fullmatch(patterns+'/?',parts.path):raise ValueError()
    except ValueError:raise ToolError('invalid_input','请提供正确的飞书文档/文件链接或资源 token。') from None
    return value


def normalize(name, arguments):
    if name=='authorized-users':return normalize_authorized_users(arguments)
    if name in capabilities.DOMAINS:return capabilities.normalize(name,arguments)
    if name not in INPUTS or not isinstance(arguments,dict) or not Draft202012Validator(INPUTS[name]).is_valid(arguments):
        raise ToolError('invalid_input','飞书参数无效，请先查看该工具 help。')
    value=dict(arguments)
    if any(isinstance(v,str) and (v.startswith('@') or '\x00' in v) for v in value.values()):
        raise ToolError('invalid_input','飞书输入不接受本地文件引用或控制字符。')
    if name=='document-get':
        resource(value['document'],document=True)
        if value.get('scope')=='keyword' and not value.get('keyword'):raise ToolError('invalid_input','keyword 范围需要 keyword。')
        if value.get('scope')=='section' and not value.get('start_block_id'):raise ToolError('invalid_input','section 范围需要 start_block_id。')
        if 'keyword' in value and value.get('scope')!='keyword' or 'start_block_id' in value and value.get('scope')!='section':
            raise ToolError('invalid_input','局部读取参数必须与 scope 对应。')
    if name=='file-download':
        resource(value['file'])
        if not re.fullmatch(r'[^./\\:%~\x00-\x1f][^/\\:%~\x00-\x1f]{0,159}',value['filename']) or value['filename'].endswith((' ','.')):
            raise ToolError('invalid_input','filename 只能是普通文件名，不能包含目录或特殊路径。')
    return value


def cli_arguments(name, value):
    if name in capabilities.DOMAINS:return capabilities.cli_arguments(name,value)
    if name=='search':return ['drive','+search','--query',value.get('query',''),'--page-size',str(value.get('page_size',15)),*(['--page-token',value['page_token']] if value.get('page_token') else [])]
    if name=='document-get':
        args=['docs','+fetch','--doc',value['document'],'--doc-format','markdown']
        for key in ('scope','keyword','start_block_id'):
            if key in value:args += ['--'+key.replace('_','-'),value[key]]
        return args
    if name=='folder-list':return ['drive','files','list','--params',json.dumps({'page_size':value.get('page_size',20),**{k:v for k,v in value.items() if k!='page_size'}})]
    field='--url' if value['file'].startswith('https://') else '--file-token'
    return ['drive','+download',field,value['file']]


def invoke(name, arguments):
    value=normalize(name,arguments)
    if name in capabilities.DOMAINS and value['action']=='help':return capabilities.describe(name,value)
    frame=read_context()
    credentials=frame.get('categories',{}).get('feishu',{})
    if not all(isinstance(credentials.get(k),str) and credentials[k] for k in ('app_id','access_token')):
        raise ToolError('feishu_connection_required','请先在控制台飞书页面授权当前账号。',1)
    application = (name, value.get('action')) in capabilities.APPLICATION_ONLY
    if application:
        from .tools.feishu.attendance import prepare
        value = prepare(value, credentials)
    elif credentials.get('identity') == 'application':
        raise ToolError('feishu_identity_denied', '当前工具不允许使用应用身份。',1)
    if not BINARY.is_file():raise ToolError('feishu_cli_unavailable','服务器飞书 CLI 尚未安装。',1)
    directory=frame.get('workspace_directory');temporary=None;download_complete=False
    try:
        if name=='file-download':
            if not isinstance(directory,str):raise ToolError('invalid_context','当前账号文件工作区不可用。',1)
            with root_directory(directory):pass
            temporary=Path(tempfile.mkdtemp(prefix='feishu-',dir=directory))
            destination=temporary/value['filename']
        args=cli_arguments(name,value)
        if name=='file-download':args += ['--output',str(destination)]
        args += ['--as','bot' if application else 'user','--format','json']
        completed=subprocess.run([str(BINARY),*args],input=json.dumps({**{k:credentials.get(k,'') for k in ('app_id','access_token','open_id','scopes')},'identity':'application' if application else 'user','workspace_directory':directory}).encode(),
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=150,cwd=directory,
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','HOME':os.environ.get('HOME','/nonexistent'),
                 'LARKSUITE_CLI_CONFIG_DIR':str(Path(os.environ.get('HOME','/nonexistent'))/'feishu-config'),'NO_COLOR':'1'})
        streams=[completed.stdout] if completed.returncode==0 else [completed.stderr,completed.stdout]
        result=None
        for raw in streams:
            if len(raw)>(64 if frame.get('result_cache') is True else 2)*1024*1024:raise ToolError('feishu_output_limit','飞书单次响应超出限制，请按章节、关键词或分页读取。',1)
            try:result=json.loads(raw);break
            except ValueError:continue
        if result is None:raise ToolError('feishu_cli_failed','飞书 CLI 未返回有效数据。',1)
        if completed.returncode or not isinstance(result,dict) or result.get('ok') is not True:
            raise_upstream(result,secrets=(credentials['access_token'],directory))
        if name=='file-download':
            relative=destination.relative_to(directory).as_posix()
            with source_file(directory,relative) as fd:size=os.fstat(fd).st_size
            download_complete=True
            return {'path':relative,'filename':value['filename'],'size':size}
        def redact(item):
            if isinstance(item,dict):return {k:redact(v) for k,v in item.items()}
            if isinstance(item,list):return [redact(v) for v in item]
            if isinstance(item,str):
                item=item.replace(credentials['access_token'],'[已隐藏凭证]')
                if directory and item.startswith(str(directory)+'/'):item=item[len(str(directory))+1:]
            return item
        return redact(result)
    except subprocess.TimeoutExpired:raise ToolError('feishu_timeout','飞书读取超时，请缩小范围后重试。',1) from None
    finally:
        # A successfully downloaded file remains for file.publish; incomplete files do not.
        if temporary is not None and not download_complete:
            shutil.rmtree(temporary,ignore_errors=True)
