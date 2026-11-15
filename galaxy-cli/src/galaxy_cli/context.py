"""Category credentials arrive from the authenticated server through a private pipe."""
import json
import sys
from contextvars import ContextVar
from .registry import ToolError

FRAME = ContextVar('galaxy_context', default=None)


def read_context():
    if FRAME.get() is not None:
        return FRAME.get()
    if sys.stdin.isatty():
        raise ToolError('server_context_required', '请通过聊天中的 galaxy_cli 调用此工具。', 1)
    raw = sys.stdin.buffer.read(1048577) if hasattr(sys.stdin, 'buffer') else sys.stdin.read(1048577)
    try:
        value = json.loads(raw) if len(raw) <= 1048576 else None
    except (ValueError, TypeError):
        value = None
    if not isinstance(value, dict):
        raise ToolError('invalid_context', '工具运行上下文无效。', 1)
    return value


def category_credentials(category):
    if FRAME.get() is not None:
        value = FRAME.get()
    elif sys.stdin.isatty():
        raise ToolError("connection_required", "请从 galaxy ai 登录后调用，分类凭证由服务器自动提供。", 1)
    else:
        raw = sys.stdin.buffer.read(32769) if hasattr(sys.stdin, "buffer") else sys.stdin.read(32769)
        if len(raw) > 32768:
            raise ToolError("invalid_context", "分类凭证上下文无效。", 1)
        try: value = json.loads(raw)
        except (ValueError, TypeError): value = None
    try: context = value["categories"][category]
    except (TypeError, KeyError):
        raise ToolError("connection_required", "当前用户尚未绑定可用的有赞分类凭证，请在控制台手动刷新。", 1) from None
    if not isinstance(context, dict) or not all(isinstance(context.get(k), str) and context[k] for k in ("sid", "csrf", "kdt_id")):
        raise ToolError("connection_required", "当前用户的有赞分类凭证不完整，请手动刷新。", 1)
    return context
