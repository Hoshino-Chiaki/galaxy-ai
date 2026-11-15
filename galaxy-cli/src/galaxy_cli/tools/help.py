"""Discovery lists only tool, category, and description."""
INPUT = {"type": "object", "properties": {
    "tool": {"type": "string", "minLength": 1},
    "category": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,63}$"}}, "additionalProperties": False}
OUTPUT = {"type": "object", "properties": {"data": {"type": "array", "items": {
    "type": "object", "properties": {"tool": {"type": "string"}, "category": {"type": ["string", "null"]},
    "description": {"type": "string"}}, "required": ["tool", "category", "description"], "additionalProperties": False}}},
    "required": ["data"], "additionalProperties": False}


def register(registry):
    @registry.tool(name="help", description="查看工具名称、分类和描述。", input_schema=INPUT, output_schema=OUTPUT)
    def help_tool(arguments):
        target = arguments.get("tool")
        return {"data": [{"tool": tool["name"], "category": tool["category"], "description": tool["description"]}
                         for tool in registry.describe(target, arguments.get("category"))
                         if tool["name"] != "help" or target == "help"]}
