import argparse
import json
import sys

from . import __version__
from .registry import ToolError, load_registry
from .context import FRAME, read_context
from .youzan_catalog import NAMES, ACTIONS


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ToolError("invalid_arguments", "命令参数无效，请运行 galaxy-cli help。")


def text_help(result):
    lines = ["galaxy-cli " + result["version"]]
    lines.extend(tool["tool"] + " | " + (tool["category"] or "-") + " | " + tool["description"] for tool in result.get("data", []))
    return "\n".join(lines)


def main(argv=None):
    output_format = "json"
    raw_business = False
    frame_token = None
    details = {}
    try:
        parser = Parser(prog="galaxy-cli", add_help=False)
        parser.add_argument("command", nargs="?", default="help")
        parser.add_argument("target", nargs="?")
        parser.add_argument("action", nargs="?")
        parser.add_argument("--input")
        parser.add_argument("--input-stdin", action="store_true")
        parser.add_argument("--category")
        parser.add_argument("--format", choices=("json", "text"), default="json")
        parser.add_argument("-h", "--help", action="store_true")
        parser.add_argument("--version", action="store_true")
        args = parser.parse_args(argv)
        if args.input_stdin:
            supported = (args.command == 'excel' and args.target == 'create' or args.command == 'excel.create'
                or args.command == 'file' and args.target == 'publish' or args.command == 'file.publish'
                or args.command == 'youzan' and args.target in NAMES or args.command in {'youzan.'+name for name in NAMES}
                or args.command == 'task' and args.target == 'har' or args.command == 'task.har'
                or args.command == 'gjp' and args.target == 'inventory-cost' or args.command == 'gjp.inventory-cost')
            if args.input is not None or not supported:
                raise ToolError('invalid_arguments', '该命令不支持管道输入。')
            frame = read_context()
            frame_token = FRAME.set(frame)
            args.input = json.dumps(frame.get('input'), ensure_ascii=False)
        output_format = args.format
        if args.version:
            data = {}
        else:
            try:
                arguments = json.loads(args.input) if args.input is not None else {}
            except (json.JSONDecodeError, ValueError):
                raise ToolError("invalid_json", "--input 必须是有效的 JSON 对象。") from None
            if not isinstance(arguments, dict):
                raise ToolError("invalid_input", "--input 必须是 JSON 对象。")
            registry = load_registry()
            command = args.command
            target = args.target
            if command in registry.categories():
                if target in (None, 'help'):
                    arguments = {"category": command}
                    command, target = 'help', None
                else:
                    command, target = command + '.' + target, None
            if command == 'youzan.business-report' or command.startswith('youzan.') and command[7:] in ACTIONS:
                name=command.split('.',1)[1]
                choices=('options','query') if name=='business-report' else ACTIONS[name]
                action=args.action or target
                target=None
                if action is not None:
                    if action not in choices or 'action' in arguments:
                        raise ToolError('invalid_arguments','子命令无效或 action 重复指定。')
                    arguments['action']=action
                elif name=='material-category' and not args.help:
                    arguments.setdefault('action','list')
            elif args.action is not None:
                raise ToolError('invalid_arguments','该工具不接受额外子命令。')
            if args.help:
                arguments = {} if command == "help" else {"tool": command}
                command = "help"
            if target is not None:
                if command != "help" or args.input is not None:
                    raise ToolError("invalid_arguments", "仅 help 支持位置参数工具名；其它工具请使用 --input。")
                arguments = {"tool": target}
            if args.category is not None:
                if command != "help" or "category" in arguments:
                    raise ToolError("invalid_arguments", "--category 仅用于 help，且不能重复指定分类。")
                arguments["category"] = args.category
            raw_business = command != "help"
            data = registry.invoke(command, arguments)
        result = data if raw_business else {"version": __version__, **data}
        # Serialize before writing so failures never produce half a JSON document.
        encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        print(text_help(result) if output_format == "text" and not raw_business and isinstance(result.get("data"), list)  else encoded)
        return 0
    except ToolError as error:
        code, message, exit_code = error.code, error.message, error.exit_code
        details = error.details
    except Exception:
        code, message, exit_code = "execution_failed", "工具执行或加载失败。", 1
    finally:
        if frame_token is not None:
            FRAME.reset(frame_token)
    if output_format == "text":
        print(message, file=sys.stderr)
    else:
        print(json.dumps({"version": __version__, "error": {"code": code, "message": message, **details}}, ensure_ascii=False))
    return exit_code
