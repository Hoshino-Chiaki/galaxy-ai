"""Native tests for category prompt defaults; only Linux storage is substituted."""
import importlib.util
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

from galaxy_cli.tools.task.har import DESCRIPTION
from galaxy_cli.tool_categories import TASK_SYSTEM_PROMPT


@pytest.fixture
def prompts(monkeypatch):
    name = '_local_task_prompt_test'
    package = types.ModuleType(name)
    package.__path__ = []
    monkeypatch.setitem(sys.modules, name, package)

    class Repository:
        def __init__(self, *_):
            self.state = {'workspaces': {}}

        @contextmanager
        def transaction(self, **_):
            yield self.state

    class AuthFailure(Exception):
        def __init__(self, code, message, status):
            self.code, self.status = code, status
            super().__init__(message)

    for suffix, attributes in [('auth', {'AuthFailure': AuthFailure}), ('store', {'JsonRepository': Repository})]:
        module = types.ModuleType(name + '.' + suffix)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    source = Path(__file__).resolve().parents[1] / 'xingxu' / 'tool_prompts.py'
    spec = importlib.util.spec_from_file_location(name + '.tool_prompts', source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.ToolPrompts, 'catalog', staticmethod(lambda: [
        {'tool': 'task.har', 'category': 'task', 'description': DESCRIPTION},
        {'tool': 'time.now', 'category': 'time', 'description': 'Time'}]))
    return module.ToolPrompts('unused')


def test_task_default_custom_override_and_explicit_clear(prompts):
    user = {'workspace_id': 'a', 'permissions': ['tools:galaxy_cli', 'galaxy:help', 'task:har:read']}
    permissions = {'task.har': 'task:har:read', 'time.now': 'workspace:read'}
    assert next(c for c in prompts.settings(user)['categories'] if c['category'] == 'task')['prompt'] == TASK_SYSTEM_PROMPT
    assert prompts.messages(user, permissions) == [{'role': 'system', 'content': '【工具分类：任务系统】\n' + TASK_SYSTEM_PROMPT}]
    prompts.save(user, 'task', 'Custom preparation')
    assert prompts.messages(user, permissions)[0]['content'].endswith('Custom preparation')
    prompts.save(user, 'task', '  \n')
    assert prompts.messages(user, permissions) == []
    assert next(c for c in prompts.settings(user)['categories'] if c['category'] == 'task')['prompt'] == ''


def test_task_prompt_is_scoped_to_workspace_and_tool_permission(prompts):
    user = {'workspace_id': 'a', 'permissions': ['tools:galaxy_cli', 'galaxy:help', 'task:har:read']}
    permissions = {'task.har': 'task:har:read'}
    prompts.save(user, 'task', 'Workspace a')
    other = {**user, 'workspace_id': 'b'}
    assert prompts.messages(other, permissions)[0]['content'].endswith(TASK_SYSTEM_PROMPT)
    assert prompts.messages({**user, 'permissions': ['tools:galaxy_cli', 'galaxy:help']}, permissions) == []
    assert prompts.messages({**user, 'permissions': ['task:har:read']}, permissions) == []
