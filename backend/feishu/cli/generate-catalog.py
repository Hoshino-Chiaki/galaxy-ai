"""Generate Galaxy's pinned business catalog from official metadata exports.

This only reads source/JSON; the Go exporter runs on the build server.
"""
import argparse
import json
from pathlib import Path

TITLES = {'approval':'审批','attendance':'考勤','base':'多维表格','calendar':'日历',
          'contact':'通讯录','docs':'云文档','drive':'云空间','im':'消息与群聊',
          'mail':'邮箱','markdown':'Markdown 文件','mindnotes':'思维笔记','minutes':'妙记',
          'note':'会议笔记','okr':'OKR','sheets':'电子表格','slides':'幻灯片',
          'task':'任务','vc':'视频会议','whiteboard':'画板','wiki':'知识库'}
OMIT = {('docs','+script'),('drive','+sync'),('drive','+pull'),('mail','+watch')}
GLOBAL_FLAGS = {'as','format','profile','brand','token','access-token','config','config-dir',
                'print-schema','flag-name','help','dry-run'}


def field_schema(meta):
    kind=meta.get('type','string')
    result={'type':{'int':'integer','int64':'integer','float':'number','float64':'number','bool':'boolean'}.get(kind,kind)}
    if result['type'] not in {'object','array','string','integer','number','boolean'}:result['type']='string'
    if meta.get('description'):result['description']=meta['description']
    if result['type']=='object' and meta.get('properties'):
        result.update(object_schema(meta['properties']))
    if result['type']=='array':
        item=meta.get('items',{})
        result['items']=field_schema(item) if isinstance(item,dict) else {}
    options=meta.get('options',[])
    if options and result['type']=='string':
        values=[str(o['value']) for o in options if isinstance(o,dict) and 'value' in o]
        if values:result['enum']=values
    return result


def object_schema(fields):
    result={'type':'object','properties':{k:field_schema(v) for k,v in fields.items()},'additionalProperties':False}
    required=[k for k,v in fields.items() if v.get('required') is True]
    if required:result['required']=required
    return result


def generate(upstream, shortcuts, destination, commit):
    domains={key:{'title':title,'commands':{}} for key,title in TITLES.items()}
    api_total=0;excluded=[]
    for file in sorted((upstream/'internal/registry/catalog/services').glob('*.json')):
        service=json.loads(file.read_text());domain=service['name']
        def walk(resources, ancestors=()):
            nonlocal api_total
            for resource, value in resources.items():
                path=(*ancestors,resource)
                for method, meta in value.get('methods',{}).items():
                    api_total+=1
                    key='.'.join((*path,method))
                    if domain not in domains or 'user' not in meta.get('accessTokens',[]):
                        excluded.append({'command':domain+' '+key,'reason':'user_identity_not_supported'});continue
                    if meta.get('risk') not in ('read','write','high-risk-write'):
                        excluded.append({'command':domain+' '+key,'reason':'missing_risk_metadata'});continue
                    # Binary downloads keep their dedicated workspace adapter.
                    body=meta.get('requestBody',{})
                    if any(m.get('type') in ('file','binary') for m in body.values()):
                        excluded.append({'command':domain+' '+key,'reason':'multipart_requires_file_adapter'});continue
                    params=object_schema(meta.get('parameters',{}))
                    body_schema=object_schema(body)
                    input_schema={'type':'object','properties':{'params':params,'data':body_schema},'additionalProperties':False}
                    input_schema['required']=[k for k,s in input_schema['properties'].items() if s.get('required')]
                    domains[domain]['commands'][key]={'path':[domain,*path,method],
                        'description':meta.get('description',key),'risk':meta['risk'],'kind':'api',
                        'scopes':meta.get('scopes',[]),'scope_mode':'any',
                        'input_schema':input_schema,
                        'response_schema':object_schema(meta.get('responseBody',{})),
                        'doc_url':meta.get('docUrl','')}
                walk(value.get('resources',{}),path)
        walk(service.get('resources',{}))
    shortcut_total=len(shortcuts)
    for item in shortcuts:
        domain,key=item['service'],item['command']
        if domain not in domains or (domain,key) in OMIT or 'user' not in item['identities']:
            excluded.append({'command':domain+' '+key,'reason':'host_configuration_or_code_or_streaming' if domain in ('apps','application') or (domain,key) in OMIT else 'user_identity_not_supported'});continue
        flags={};required=[];details={}
        for flag in item.get('flags') or []:
            name=flag['Name']
            if flag.get('Hidden') or name in GLOBAL_FLAGS:continue
            kind=flag.get('Type') or 'string'
            schema={'type':{'bool':'boolean','int':'integer','float64':'number','string_array':'array','string_slice':'array','int_array':'array'}.get(kind,'string')}
            if schema['type']=='array':schema['items']={'type':'integer' if kind=='int_array' else 'string'}
            if flag.get('Desc'):schema['description']=flag['Desc']
            if flag.get('Enum'):schema['enum']=flag['Enum']
            if schema['type']=='string' and flag.get('Input') and not flag.get('Enum'):
                schema={'anyOf':[schema,{'type':'object'},{'type':'array'}]}
            flags[name]=schema
            details[name]={'type':kind,'input':flag.get('Input') or []}
            if flag.get('Required'):required.append(name)
        options={'type':'object','properties':flags,'additionalProperties':False}
        if required:options['required']=required
        domains[domain]['commands'][key]={'path':[domain,key],'kind':'shortcut',
            'description':item['description'],'risk':item['risk'] or 'read',
            'scopes':item['scopes'] or [],'conditional_scopes':item['conditional_scopes'] or [],'scope_mode':'all',
            'input_schema':{'type':'object','properties':{'options':options},'required':['options'] if required else [],'additionalProperties':False},
            'flags':details,'flag_schemas':item.get('schemas') or {}}
    domains={k:v for k,v in domains.items() if v['commands']}
    for domain in domains.values():domain['commands']=dict(sorted(domain['commands'].items()))
    result={'upstream_commit':commit,'upstream_version':json.loads((upstream/'package.json').read_text())['version'],
            'official_api_commands':api_total,'official_shortcuts':shortcut_total,
            'domains':domains,'excluded':excluded}
    destination.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n')
    allowlist=Path(__file__).with_name('allowed-commands.json')
    allowlist.write_text(json.dumps(sorted('/'.join(e['path']) for d in domains.values() for e in d['commands'].values()))+'\n')
    tool_dir=destination.parent/'tools/feishu'
    for domain in domains:
        # Attendance owns Galaxy's verified employee-scope adapter.
        if domain=='attendance' and (tool_dir/'attendance.py').exists():
            continue
        (tool_dir/(domain+'.py')).write_text('from ...feishu_capabilities import register_domain\n\ndef register(registry):\n    register_domain(registry, '+repr(domain)+')\n')
    print(json.dumps({'api_commands':api_total,'shortcuts':shortcut_total,'connected_actions':sum(len(v['commands']) for v in domains.values()),'domains':{k:len(v['commands']) for k,v in domains.items()},'excluded':len(excluded)},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('upstream',type=Path);p.add_argument('shortcuts',type=Path);p.add_argument('destination',type=Path);p.add_argument('commit')
    args=p.parse_args();generate(args.upstream,json.loads(args.shortcuts.read_text()),args.destination,args.commit)
