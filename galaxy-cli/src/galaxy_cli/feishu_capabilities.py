"""Pinned official business operations, with exact schemas and no raw CLI input."""
import copy
import csv
import io
import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from .registry import ToolError


@lru_cache(maxsize=1)
def catalog():
    return json.loads(Path(__file__).with_name('feishu_catalog.json').read_text())


DOMAINS = catalog()['domains']
# Galaxy's attendance contract defaults omitted employee IDs to the verified
# caller. The official endpoint remains unchanged; no identity is model supplied.
_attendance = DOMAINS['attendance']['commands']['user_tasks.query']
_attendance['risk'] = 'read'
_attendance['description'] = '查询员工考勤打卡结果。data 必填 check_date_from、check_date_to（八位 YYYYMMDD 工作日）；user_ids 可选，省略查询本人，最多 50 人。params 可选，employee_type 默认 employee_id，也可 employee_no；其他可选字段见输入结构。查询他人须由 Galaxy 权限层授予公司考勤读取权限，并受企业应用实际数据范围限制；服务器保存的管理员凭证不会自动向员工开放查询权限，不要求用户是飞书企业超级管理员。返回完整打卡结果、无效及无权限员工列表；一条员工结果不等于一次打卡，空结果不代表缺勤。'
_attendance['input_schema']['required'] = ['data']
_attendance['input_schema']['properties']['params']['required'] = []
_attendance['input_schema']['properties']['data']['required'] = ['check_date_from', 'check_date_to']
_attendance['input_schema']['properties']['data']['properties']['user_ids'].update(minItems=1, maxItems=50)
DOMAIN_SCHEMA = {'type':'object','properties':{
    'action':{'type':'string'},'command':{'type':'string'},'keyword':{'type':'string'},
    'params':{'type':'object'},'data':{'type':'object'},'options':{'type':'object'},
    'dry_run':{'type':'boolean'}},'additionalProperties':False}


def operation(domain, value):
    key=value.get('action','help')
    if key=='help':return None
    entry=DOMAINS[domain]['commands'].get(key)
    if entry is None:raise ToolError('invalid_input','飞书操作不存在，请先用 action=help 查询这个业务域。')
    return entry


def validate_strings(value):
    if isinstance(value,str) and ('\x00' in value or value.startswith('@') or value=='-'):
        raise ToolError('invalid_input','不接受 @本地文件、标准输入或控制字符；文件路径只能使用当前账号工作区相对路径。')
    if isinstance(value,dict):
        for item in value.values():validate_strings(item)
    elif isinstance(value,list):
        for item in value:validate_strings(item)


def normalize(domain, value):
    if domain not in DOMAINS or not isinstance(value,dict) or not Draft202012Validator(DOMAIN_SCHEMA).is_valid(value):
        raise ToolError('invalid_input','飞书输入须为 action、params/data 或 options 的 JSON 对象。')
    result=copy.deepcopy(value);result.setdefault('action','help')
    entry=operation(domain,result)
    if entry is None:
        if set(result)-{'action','command','keyword'}:raise ToolError('invalid_input','help 只接受可选 command 或 keyword。')
        if result.get('command') and result['command'] not in DOMAINS[domain]['commands']:
            raise ToolError('invalid_input','指定的飞书操作不存在。')
        return result
    if 'command' in result or 'keyword' in result:raise ToolError('invalid_input','command/keyword 仅用于 help。')
    body={k:v for k,v in result.items() if k not in ('action','dry_run')}
    if not Draft202012Validator(entry['input_schema']).is_valid(body):
        raise ToolError('invalid_input','参数不符合该飞书操作的输入结构；先调用 action=help、command='+result['action']+' 获取必填字段和类型。')
    validate_strings(body)
    return result


def describe(domain, value):
    commands=DOMAINS[domain]['commands']
    if value.get('command'):
        key=value['command'];entry=commands[key]
        return {'ok':True,'data':{'action':key,'description':entry['description'],'risk':entry['risk'],
            'required_identity':'application' if (domain,key) in APPLICATION_ONLY else 'user',
            'input_schema':entry['input_schema'],'output':'官方 CLI 完整 JSON；ok=true 成功，data 为完整业务结果。分页按实际结果继续读取。文件保存在账号工作区，发布下载使用 file.publish。',
            'scopes':entry['scopes'],'scope_mode':entry['scope_mode'],
            **({'conditional_scopes':entry['conditional_scopes']} if entry.get('conditional_scopes') else {}),
            **({'flag_schemas':entry['flag_schemas']} if entry.get('flag_schemas') else {})}}
    keyword=value.get('keyword','').casefold()
    return {'ok':True,'data':[{'action':k,'description':e['description'],'risk':e['risk']} for k,e in commands.items()
                             if not keyword or keyword in (k+' '+e['description']).casefold()]}


def cli_arguments(domain, value):
    entry=operation(domain,value);args=list(entry['path'])
    if entry['kind']=='api':
        for key in ('params','data'):
            if key in value:args+=['--'+key,json.dumps(value[key],ensure_ascii=False,separators=(',',':'))]
    else:
        for name,item in value.get('options',{}).items():
            kind=entry['flags'][name]['type']
            if kind=='bool':values=['true' if item else 'false']
            elif kind=='string_array':values=item
            elif kind in ('string_slice','int_array'):
                output=io.StringIO();csv.writer(output,lineterminator='').writerow(item);values=[output.getvalue()]
            elif isinstance(item,(dict,list)):values=[json.dumps(item,ensure_ascii=False,separators=(',',':'))]
            else:values=[str(item)]
            args += ['--'+name+'='+str(v) for v in values]
    if value.get('dry_run'):args.append('--dry-run')
    return args


def is_write(domain,value):
    entry=operation(domain,value)
    return bool(entry and entry['risk']!='read' and not value.get('dry_run'))


def scopes_satisfied(entry, scopes):
    required=entry.get('scopes') or []
    return not required or (bool(set(required)&set(scopes)) if entry['scope_mode']=='any' else set(required)<=set(scopes))


APPLICATION_ONLY = {('attendance','user_tasks.query')}


@lru_cache(maxsize=2)
def all_scopes(write=True):
    """Cover every supported user operation, without requesting redundant OR alternatives.

    Device OAuth accepts one aggregate request; the old 50-scope groups were
    unnecessary. Native API scope alternatives are OR, shortcut dependencies AND.
    """
    entries=[entry for domain,info in DOMAINS.items() for action,entry in info['commands'].items()
             if (domain,action) not in APPLICATION_ONLY and (write or entry['risk']=='read')]
    scopes={'offline_access'}
    for entry in entries:
        if entry['scope_mode']=='all':
            scopes.update(entry['scopes']);scopes.update(entry.get('conditional_scopes',[]))
    for entry in entries:
        choices=set(entry['scopes'])
        if entry['scope_mode']=='any' and choices and not choices&scopes:
            scopes.add(min(choices,key=lambda s:(s.count(':'),len(s),s)))
    return sorted(scopes)


def register_domain(registry, domain):
    info=DOMAINS[domain]
    description=(f"飞书{info['title']}，支持 {len(info['commands'])} 个官方读取/写入操作。输入 action 可选，省略或 help 列出操作；"
        "action=help 时可选 keyword 筛选，command 查询指定操作完整输入结构、必填字段、权限和输出说明。"
        "执行业务时 action 必填为目录实际返回的操作名：快捷操作用 options 对象（键是 help 中原样的参数名）；"
        "API 操作用 params 对象传路径/查询参数，data 对象传请求体；其余字段、可选值和嵌套结构以该操作 help 为准。"
        "可选 dry_run=true 预览请求。输出官方完整 JSON，按 ok、分页和实际业务结果判断。"
        "创建、修改、删除或发送会直接影响飞书数据；只按用户明确请求执行，执行结果不确定时先回读再重试。"
        "文件仅限当前账号工作目录，生成或下载文件需用 file.publish 才能提供聊天附件。"
        "服务器按当前员工校验访问权限并自动提供和更新相应身份凭证，不能传入凭证或使用其他员工身份。"
        "连接账号时统一申请全部已接入的用户身份权限，无需选择业务范围；个人业务缺权限应补全飞书授权，不能据此判定本人不是管理员。")
    if domain=='attendance':description+=' 考勤由服务器自动使用企业应用身份，缺应用权限需管理员开通并发布，不能靠补个人授权解决。data 的 user_ids 可省略以查询本人；params.employee_type 默认为 employee_id。查询他人需要 Galaxy 的公司考勤读取权限（当前默认授予 Galaxy 管理员），并核验账号属于当前企业应用；普通员工默认仅本人。平台的超级管理员标记只作身份信息，不代替 Galaxy 权限层。feishu_attendance_forbidden 是本系统拒绝，不是飞书要求超级管理员；提供名单不能改变调用者权限。实际可读人员以应用授权范围及飞书返回为准，不能把部分授权人员称为全公司。员工 ID 来源使用 feishu.authorized-users，先读取 scope 并按分页和部门展开；不依赖个人关键词搜索权限，不用虚构 ID 探测角色。飞书业务错误保留 upstream_code，不据此猜测管理员身份。工作日为八位 YYYYMMDD，最多每次 50 人，返回完整考勤结果和无权限/无效员工列表。'
    def handler(value):
        from .feishu_client import invoke
        return invoke(domain,value)
    registry.register(name='feishu.'+domain,category='feishu',description=description,
        input_schema=DOMAIN_SCHEMA,output_schema={'type':'object'},handler=handler)
