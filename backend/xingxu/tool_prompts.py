"""Workspace-scoped, administrator-managed instructions for tool categories."""
import copy
import re

from galaxy_cli.registry import load_registry

from .auth import AuthFailure
from .store import JsonRepository


class ToolPrompts:
    def __init__(self, directory):
        self.repo = JsonRepository(directory, 'tool-prompts', ('workspaces',))

    @staticmethod
    def catalog():
        return load_registry().invoke('help', {})['data']

    def settings(self, user):
        tools = self.catalog()
        categories = sorted({tool['category'] or 'general' for tool in tools})
        with self.repo.transaction(write=False) as state:
            prompts = state['workspaces'].get(user['workspace_id'], {})
            return {'categories': [{'category': category, 'prompt': prompts.get(category, ''),
                'tool_count': sum((tool['category'] or 'general') == category for tool in tools)} for category in categories]}

    def save(self, user, category, prompt):
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', category) or category not in {
                tool['category'] or 'general' for tool in self.catalog()}:
            raise AuthFailure('not_found', '工具分类不存在。', 404)
        with self.repo.transaction() as state:
            prompts = state['workspaces'].setdefault(user['workspace_id'], {})
            if prompt.strip():
                prompts[category] = prompt
            else:
                prompts.pop(category, None)
        return {'category': category, 'prompt': prompt if prompt.strip() else ''}

    def messages(self, user, permissions):
        if not {'tools:galaxy_cli', 'galaxy:help'} <= set(user.get('permissions', [])):
            return []
        allowed = {tool['category'] or 'general' for tool in self.catalog()
            if permissions.get(tool['tool']) in user['permissions']}
        with self.repo.transaction(write=False) as state:
            prompts = copy.deepcopy(state['workspaces'].get(user['workspace_id'], {}))
        return [{'role': 'system', 'content': f'【工具分类：{category}】\n{prompt}'}
            for category, prompt in sorted(prompts.items()) if category in allowed and prompt.strip()]
