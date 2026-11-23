"""Youzan CLI command schemas, aliases and argument normalization."""
import json
from galaxy_cli.registry import ToolError
from ..tool_context import DispatchDenied, strict_json
from galaxy_cli.contracts.youzan import normalize as normalize_customer_search
from galaxy_cli.tools.youzan.customer_get import normalize as normalize_customer_get
from galaxy_cli.tools.youzan.customer_count import normalize as normalize_customer_count
from galaxy_cli.tools.youzan.business_report import normalize as normalize_report, succeeded as report_succeeded, OPTION_KEYS
from galaxy_cli.youzan_catalog import NAMES as CATALOG_NAMES, ACTIONS as CATALOG_ACTIONS, permissions as catalog_permissions
from galaxy_cli.tools.youzan import material_category, material_list, material_upload, product_get, product_options, product_update, product_search, product_group, order_search
CATALOG_MODULES={module.__name__.rsplit('.',1)[-1].replace('_','-'):module for module in
    (material_category,material_list,material_upload,product_get,product_options,product_update,product_search,product_group,order_search)}


def normalize_catalog(args):
    if args[0] in {'youzan.'+name for name in CATALOG_NAMES}:
        args=['youzan',args[0].split('.',1)[1],*args[1:]]
    if args[0]=='youzan' and len(args)>=2 and args[1] in CATALOG_NAMES:
        name=args[1];position=2;body={};action=None;used_input=False;used_format=False
        if name in CATALOG_ACTIONS:
            if len(args)>position and args[position] in CATALOG_ACTIONS[name]: action=args[position];position+=1
            elif name=='material-category':action='list'
            else:raise DispatchDenied('invalid_arguments','请填写该工具的有效子命令。')
        while position<len(args):
            if args[position]=='--input' and not used_input and position+1<len(args):
                try:body=strict_json(args[position+1])
                except (ValueError,TypeError):raise DispatchDenied('invalid_arguments','--input 必须是有效 JSON。') from None
                used_input=True;position+=2
            elif args[position]=='--format' and not used_format and args[position+1:]==['json']:
                used_format=True;position+=2
            else:raise DispatchDenied('invalid_arguments','只接受 --input JSON 和可选 --format json。')
        if not isinstance(body,dict) or 'action' in body:raise DispatchDenied('invalid_arguments','action 必须使用子命令指定，不能放在输入中。')
        if action is not None:body={'action':action,**body}
        try:body=CATALOG_MODULES[name].normalize(body)
        except ToolError as error:raise DispatchDenied(error.code,error.message) from None
        body.pop('action',None)
        return ['youzan',name,*([action] if action is not None else []),'--input',json.dumps(body,ensure_ascii=False),'--format','json']


def normalize_customer_command(args):
    if args[0] == 'youzan.business-report':
        args = ['youzan', 'business-report', *args[1:]]
    if args[:2] == ['youzan', 'business-report']:
        if len(args) < 3 or args[2] not in ('options', 'query'):
            raise DispatchDenied('invalid_arguments', '报表需要 options 或 query 子命令。')
        action, position, body, used_input, used_format = args[2], 3, {}, False, False
        while position < len(args):
            if args[position] == '--input' and not used_input and position + 1 < len(args):
                try:
                    body = strict_json(args[position + 1])
                except (ValueError, TypeError):
                    raise DispatchDenied('invalid_arguments', '报表 --input 必须是有效 JSON。') from None
                used_input = True; position += 2
            elif args[position] == '--format' and not used_format and args[position + 1:] == ['json']:
                used_format = True; position += 2
            else:
                raise DispatchDenied('invalid_arguments', '报表仅接受 --input JSON 和 --format json。')
        if not isinstance(body, dict) or 'action' in body:
            raise DispatchDenied('invalid_arguments', 'action 使用子命令指定，不要放入 --input。')
        try:
            body = normalize_report({'action': action, **body})
        except ToolError as error:
            raise DispatchDenied('invalid_arguments', error.message) from None
        body.pop('action')
        return ['youzan', 'business-report', action, '--input', json.dumps(body, ensure_ascii=False), '--format', 'json']
    normalizers = {"customer-search": normalize_customer_search, "customer-get": normalize_customer_get,
                   "customer-count": normalize_customer_count}
    if args[0] in {"youzan." + name for name in normalizers}:
        args = ["youzan", args[0].split('.', 1)[1], *args[1:]]
    if args[0] == "youzan":
        if len(args) < 2 or args[1] not in normalizers:
            raise DispatchDenied("command_denied", "有赞工具命令不存在或尚未注册，请先执行 galaxy-cli help youzan 查看真实工具名。")
        body, used_format, position, used_input = None, False, 2, False
        while position < len(args):
            if args[position] == "--input" and not used_input and position + 1 < len(args):
                try:
                    body = strict_json(args[position + 1])
                except (ValueError, TypeError):
                    raise DispatchDenied("invalid_arguments", "--input 必须是有效 JSON。") from None
                used_input = True
                position += 2
            elif args[position] == "--format" and not used_format and position + 1 < len(args) and args[position + 1] == "json":
                used_format = True; position += 2
            else:
                raise DispatchDenied("invalid_arguments", "有赞工具只接受 --input JSON 和 JSON 输出。")
        try:
            normalized = normalizers[args[1]](body if used_input else {})
        except ToolError as error:
            raise DispatchDenied("invalid_arguments", error.message) from None
        return ["youzan", args[1], "--input", json.dumps(normalized, ensure_ascii=False), "--format", "json"]
