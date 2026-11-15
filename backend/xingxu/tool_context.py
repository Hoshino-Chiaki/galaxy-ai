"""Integration-neutral tool identity, execution errors and strict JSON parsing."""
import json
import asyncio
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExecutionContext:
    user_id: str
    workspace_id: str
    permissions: tuple[str, ...]
    conversation_id: str
    session_token: str = field(repr=False)


class DispatchDenied(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


class ToolInterrupted(asyncio.CancelledError):
    def __init__(self, event):
        self.event = event


def strict_json(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate key")
            result[key] = item
        return result

    def constant(_value):
        raise ValueError("Non-JSON number")

    return json.loads(value, object_pairs_hook=unique, parse_constant=constant)
