"""LangGraph chat execution. Credentials and tool identity stay outside graph state."""
from __future__ import annotations

import copy
import json
import math
import os
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime


class AgentFailure(Exception):
    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class ContextPolicy:
    # Input allowance only: the operator must leave room for the model's output.
    input_tokens: int = 48000
    summary_tokens: int = 1024
    recent_turns: int = 4

    @classmethod
    def from_values(cls, values):
        return cls(input_tokens=int(values['input_token_budget']),
                   summary_tokens=int(values['summary_tokens']),
                   recent_turns=int(values['recent_turns']))

    @classmethod
    def from_environment(cls):
        def setting(name, default, minimum, maximum):
            raw = os.environ.get(name, str(default))
            try:
                value = int(raw)
            except ValueError:
                raise ValueError(f'{name} must be an integer') from None
            if not minimum <= value <= maximum:
                raise ValueError(f'{name} must be between {minimum} and {maximum}')
            return value

        return cls(input_tokens=setting('GALAXY_AI_INPUT_TOKEN_BUDGET', 48000, 4096, 1000000),
                   summary_tokens=setting('GALAXY_AI_SUMMARY_TOKEN_BUDGET', 1024, 256, 8192),
                   recent_turns=setting('GALAXY_AI_RECENT_TURNS', 4, 1, 20))


ASCII = re.compile(r'[\x00-\x7f]+')


def estimate_tokens(value) -> int:
    """Provider-neutral estimate, not a model tokenizer or a context-window claim.

    Budget roughly three ASCII characters or two non-ASCII UTF-8 bytes per token.
    Count serialized envelopes too, including tool schemas and reasoning fields.
    """
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    ascii_size = sum(len(match[0]) for match in ASCII.finditer(text))
    return math.ceil(ascii_size / 3 + (len(text.encode('utf-8')) - ascii_size) / 2) + 16


def input_size(messages, tools) -> int:
    return estimate_tokens({'messages': messages, 'tools': tools})


def check_input(messages, tools, policy: ContextPolicy):
    if input_size(messages, tools) > policy.input_tokens:
        raise AgentFailure('context_too_large',
            '本次输入或工具结果超出上下文预算，请缩小查询范围，或由管理员调整服务器的上下文预算。', 422)


class AgentState(TypedDict):
    messages: list[dict]
    assistant: dict
    model_calls: int


@dataclass
class AgentDependencies:
    model: Callable[[list[dict]], Awaitable[dict]] = field(repr=False)
    tool: Callable[[dict], Awaitable[dict]] = field(repr=False)
    prepare: Callable[[list[dict]], Awaitable[list[dict]]] = field(repr=False)
    tools: list[dict]


class ChatGraph:
    def __init__(self, policy: ContextPolicy):
        self.policy = policy
        builder = StateGraph(AgentState, context_schema=AgentDependencies)
        builder.add_node('prepare_context', self._prepare)
        builder.add_node('model', self._model)
        builder.add_node('tools', self._tools)
        builder.add_edge(START, 'prepare_context')
        builder.add_edge('prepare_context', 'model')
        builder.add_conditional_edges('model', self._route, {'tools': 'tools', 'finish': END})
        builder.add_edge('tools', 'model')
        # A turn is committed atomically by AiService, not replayed from checkpoints.
        # In particular, reconnecting must never automatically repeat business writes.
        self.graph = builder.compile()

    async def _prepare(self, state: AgentState, runtime: Runtime[AgentDependencies]):
        messages = await runtime.context.prepare(state['messages'])
        check_input(messages, runtime.context.tools, self.policy)
        return {'messages': messages}

    async def _model(self, state: AgentState, runtime: Runtime[AgentDependencies]):
        messages = await self._bounded_messages(state['messages'], runtime)
        assistant = await runtime.context.model(messages)
        count = state['model_calls'] + 1
        if not assistant.get('tool_calls') and (not isinstance(assistant.get('content'), str) or not assistant['content'].strip()):
            raise AgentFailure('empty_response', '模型没有返回正文，请重试或调整思考设置。')
        return {'assistant': assistant, 'messages': messages + [assistant], 'model_calls': count}

    async def _bounded_messages(self, messages, runtime):
        if input_size(messages, runtime.context.tools) > self.policy.input_tokens:
            messages = await runtime.context.prepare(messages)
        check_input(messages, runtime.context.tools, self.policy)
        return messages

    @staticmethod
    def _route(state: AgentState):
        return 'tools' if state['assistant'].get('tool_calls') else 'finish'

    async def _tools(self, state: AgentState, runtime: Runtime[AgentDependencies]):
        messages = await self._bounded_messages(state['messages'], runtime)
        results = []
        # Preserve order and use the existing gateway for every call, including denials.
        # No node retries: a completed write must not be repeated after a later error.
        for call in state['assistant']['tool_calls']:
            results.append(await runtime.context.tool(call))
        return {'messages': messages + results}

    async def run(self, messages, *, model, tool, prepare, tools):
        result = await self.graph.ainvoke(
            {'messages': copy.deepcopy(messages), 'assistant': {}, 'model_calls': 0},
            context=AgentDependencies(model, tool, prepare, tools),
            # Match Galaxy's existing user-controlled stop behavior, rather than
            # introducing LangGraph's small default recursion limit as a task quota.
            config={'recursion_limit': sys.maxsize},
        )
        return result['assistant']['content']
