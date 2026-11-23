"""Encrypted application inbox; platform identity is resolved to an employee before AI execution."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
from ..account_storage import folder, locked, read_json, atomic_json


class BotStore:
    def __init__(self, directory, feishu):
        self.directory, self.feishu = Path(directory), feishu

    @contextmanager
    def transaction(self, workspace, app_id, write=True):
        key = hashlib.sha256(json.dumps([workspace, app_id]).encode()).hexdigest()
        base = folder(self.directory / 'feishu-bot' / key)
        with locked(base / '.lock', write):
            record = read_json(base / 'inbox.json', {})
            state = self.feishu._decrypt(record['encrypted']) if record else {'messages': {}, 'conversations': {}}
            yield state
            if write:
                atomic_json(base / 'inbox.json', {'encrypted': self.feishu._encrypt(state)})
