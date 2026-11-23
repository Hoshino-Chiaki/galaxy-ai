"""One encrypted connection file per Galaxy account, never exposed to the model."""
import json
from contextlib import contextmanager
from pathlib import Path
from cryptography.fernet import InvalidToken
from ..account_storage import Accounts, enabled, owner_key, folder, locked, read_json, atomic_json
from ..auth import AuthFailure


class ClawbotStore:
    def __init__(self, directory, cipher):
        self.directory, self.cipher = Path(directory), cipher

    def base(self, user):
        if enabled(self.directory):
            return Accounts(self.directory).path(user, 'channels/clawbot')
        return folder(self.directory / 'clawbot' / owner_key(user))

    @contextmanager
    def transaction(self, user, write=True):
        base = self.base(user)
        with locked(base / '.lock', write):
            record = read_json(base / 'connection.json', {})
            try:
                state = json.loads(self.cipher.decrypt(record['encrypted'].encode())) if record else {}
            except (InvalidToken, ValueError, KeyError, TypeError):
                raise AuthFailure('clawbot_credentials_error', '微信连接信息无法读取，请联系管理员。', 503) from None
            yield state
            if write:
                atomic_json(base / 'connection.json', {'encrypted': self.cipher.encrypt(
                    json.dumps(state, ensure_ascii=False).encode()).decode()})
