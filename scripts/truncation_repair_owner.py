"""Restore service ownership of the exact config file touched by diagnostics."""
import json
import os
import pwd
import stat
import subprocess
from pathlib import Path

directory=Path('/opt/galaxy-ai/backend/private')
user=pwd.getpwnam('galaxy-ai')
for name in ('ai.json','ai.lock','ai-secret.key'):
    path=directory/name
    if not path.exists():
        continue
    info=path.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not path.is_symlink()
    print(json.dumps({'name':name,'uid':info.st_uid,'gid':info.st_gid,'mode':oct(stat.S_IMODE(info.st_mode))}))
    if info.st_uid==0:
        os.chown(path,user.pw_uid,user.pw_gid)
        path.chmod(0o600)
        print('Restored service ownership:',name)
subprocess.run(['systemctl','restart','galaxy-ai.service'],check=True)
