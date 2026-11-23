"""Owner-scoped schedule and run records with atomic replacement and file locks."""
from contextlib import contextmanager
from pathlib import Path
from ..account_storage import Accounts, enabled, owner_key, folder, locked, read_json, atomic_json


class ScheduleStore:
    def __init__(self,directory):self.directory=Path(directory)

    def base(self,user):
        if enabled(self.directory):return Accounts(self.directory).path(user,'schedule')
        return folder(self.directory/'schedules'/owner_key(user))

    @contextmanager
    def transaction(self,user,write=True):
        base=self.base(user)
        with locked(base/'.lock',write):
            state=read_json(base/'tasks.json',{'schema_version':1,'tasks':{},'runs':{}})
            if state.get('schema_version')!=1 or not isinstance(state.get('tasks'),dict) or not isinstance(state.get('runs'),dict):
                raise RuntimeError('Invalid schedule repository')
            yield state
            if write:atomic_json(base/'tasks.json',state)
