"""Local validation entry point; does not load Linux-only platform adapters."""
import argparse
import json
import sys

from .registry import ToolError, load_registry


def main(argv=None):
    parser = argparse.ArgumentParser(description='任务系统：本地 HAR 解析验证，不执行任何抓包请求。')
    parser.add_argument('--input', help='task.har 的 JSON 参数对象；省略时输出工具说明。')
    args = parser.parse_args(argv)
    try:
        registry = load_registry('galaxy_cli.tools.task')
        if args.input is None:
            result = registry.get('task.har').describe()
        else:
            try:
                arguments = json.loads(args.input)
            except ValueError:
                raise ToolError('invalid_json', '--input 必须是有效 JSON 对象。') from None
            result = registry.invoke('task.har', arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except ToolError as error:
        print(json.dumps({'error': {'code': error.code, 'message': error.message}}, ensure_ascii=False))
        return error.exit_code
    except Exception:
        print(json.dumps({'error': {'code': 'har_failed', 'message': 'HAR 解析失败；未执行任何网络请求。'}}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
