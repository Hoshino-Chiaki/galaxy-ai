"""Single-host JSON repository with interprocess locking and atomic replacement.

Replace this repository when migrating to a database. Corrupt storage fails closed.
"""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


class JsonRepository:
    def __init__(self, directory: Path, name="auth", fields=("users", "sessions", "attempts")):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        if name not in ("auth", "ai", "chats", "tool_audit", "youzan", "files", "tool-prompts", "feishu", "uploads"):
            raise ValueError("Unknown repository")
        self.fields = fields
        self.file = self.directory / f"{name}.json"
        self.lock = self.directory / f"{name}.lock"

    @contextmanager
    def transaction(self, *, write=True):
        descriptor = os.open(self.lock, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            try:
                if self.file.exists():
                    state = json.loads(self.file.read_text())
                    if state.get("schema_version") != 1 or not all(
                        isinstance(state.get(key), dict) for key in self.fields
                    ):
                        raise RuntimeError("Unsupported or corrupt authentication storage")
                else:
                    state = {"schema_version": 1, **{key: {} for key in self.fields}}
                yield state
                if write:
                    self._save(state)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _save(self, state):
        descriptor, temporary = tempfile.mkstemp(prefix="auth-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(state, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.file)
            directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
