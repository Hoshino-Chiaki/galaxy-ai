"""One registry drives discovery, invocation and generated help."""
import copy
import importlib
import pkgutil
import re
from dataclasses import dataclass
from typing import Any, Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


class ToolError(Exception):
    def __init__(self, code, message, exit_code=2, *, details=None):
        self.code, self.message, self.exit_code = code, message, exit_code
        self.details = details or {}
        super().__init__(message)


def success_schema(result_schema):
    if result_schema.get("type") == "object":
        result = copy.deepcopy(result_schema)
        result.setdefault("properties", {})["version"] = {"type": "string"}
        result["required"] = ["version", *result.get("required", [])]
        return result
    return {"type": "object", "properties": {"version": {"type": "string"}, "data": copy.deepcopy(result_schema)},
            "required": ["version", "data"], "additionalProperties": False}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    result_schema: dict
    handler: Callable[[dict], Any]
    examples: tuple[str, ...] = ()
    category: str | None = None

    def describe(self):
        return {"name": self.name, "description": self.description,
                "category": self.category,
                "command": "galaxy-cli " + (self.name.replace('.', ' ', 1) if self.category else self.name),
                "input_schema": copy.deepcopy(self.input_schema),
                "output_schema": success_schema(self.result_schema),
                "examples": list(self.examples)}


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, *, name, description, input_schema, output_schema, handler, examples=(), category=None):
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", name):
            raise ToolError("invalid_registration", "工具名称必须是以小写字母开头的命令标识。", 1)
        if name in self._tools:
            raise ToolError("duplicate_tool", f"工具 {name} 重复注册。", 1)
        if category is not None and (not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", category) or not name.startswith(category + ".")):
            raise ToolError("invalid_registration", "分类名称或工具归属无效。", 1)
        if not description.strip() or not callable(handler) or input_schema.get("type") != "object":
            raise ToolError("invalid_registration", f"工具 {name} 缺少说明、对象输入结构或处理函数。", 1)
        try:
            Draft202012Validator.check_schema(input_schema)
            Draft202012Validator.check_schema(output_schema)
        except SchemaError:
            raise ToolError("invalid_registration", f"工具 {name} 的输入或输出结构不是有效 JSON Schema。", 1) from None
        tool = Tool(name, description, copy.deepcopy(input_schema), copy.deepcopy(output_schema), handler, tuple(examples), category)
        self._tools[name] = tool
        return tool

    def tool(self, **definition):
        """Decorator used within a tool module's register(registry) function."""
        def decorate(handler):
            self.register(**definition, handler=handler)
            return handler
        return decorate

    def get(self, name):
        if name not in self._tools:
            raise ToolError("unknown_tool", "工具不存在，请运行 galaxy-cli help 查看已注册工具。")
        return self._tools[name]

    def describe(self, name=None, category=None):
        if name in self.categories() and category is None:
            category, name = name, None
        tools = [self.get(name)] if name else [self._tools[key] for key in sorted(self._tools)]
        if category is not None:
            if category not in self.categories():
                raise ToolError("unknown_category", "分类不存在，请运行 galaxy-cli help 查看分类。")
            tools = [tool for tool in tools if tool.category == category]
        return [tool.describe() for tool in tools]

    def categories(self):
        return sorted({tool.category for tool in self._tools.values() if tool.category is not None})

    def invoke(self, name, arguments):
        tool = self.get(name)
        if not isinstance(arguments, dict) or not Draft202012Validator(tool.input_schema).is_valid(arguments):
            # Do not echo input values: later tools may accept credentials.
            raise ToolError("invalid_input", f"参数无效，请运行 galaxy-cli help {name} 查看参数。")
        result = tool.handler(copy.deepcopy(arguments))
        if not Draft202012Validator(tool.result_schema).is_valid(result):
            raise ToolError("invalid_output", "工具返回数据异常。", 1)
        return result


def load_registry(package_name="galaxy_cli.tools"):
    registry = ToolRegistry()
    package = importlib.import_module(package_name)
    modules = sorted(pkgutil.walk_packages(package.__path__, package.__name__ + "."), key=lambda item: item.name)
    for entry in modules:
        if entry.ispkg or entry.name.rsplit(".", 1)[-1].startswith("_"):
            continue
        module = importlib.import_module(entry.name)
        register = getattr(module, "register", None)
        if not callable(register):
            raise ToolError("invalid_registration", f"工具模块 {entry.name} 未提供 register(registry)。", 1)
        register(registry)
    return registry
