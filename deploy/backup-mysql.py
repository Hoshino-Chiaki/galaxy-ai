#!/usr/bin/env python3
"""Root-only SQL + encryption-key backup; never copies secrets to public files."""
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import fcntl
from contextlib import ExitStack


def add_account_files(archive):
    accounts = Path('/opt/galaxy-ai/backend/private/accounts')
    if not (accounts / '.ready.json').is_file():
        return
    with ExitStack() as held:
        paths = [accounts / ('.' + name + '.lock') for name in
                 sorted(('chats','files','uploads','tool_audit','youzan','feishu','guanjia'))]
        paths += sorted(accounts.glob('*/browser/.galaxy-profile.lock'))
        for path in paths:
            if not path.exists():
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            stream = held.enter_context(os.fdopen(fd, 'r'))
            fcntl.flock(stream, fcntl.LOCK_SH)
        def safe(member):
            if member.issym() or member.islnk() or any(part.startswith('Singleton') for part in Path(member.name).parts):
                return None
            return member
        archive.add(accounts, arcname='accounts', filter=safe)

def add_feishu_bot_files(archive):
    inbox = Path('/opt/galaxy-ai/backend/private/feishu-bot')
    if not inbox.is_dir():
        return
    with ExitStack() as held:
        for path in sorted(inbox.glob('*/.lock')):
            stream = held.enter_context(os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'r'))
            fcntl.flock(stream, fcntl.LOCK_SH)
        archive.add(inbox, arcname='feishu-bot', filter=lambda member: None if member.issym() or member.islnk() else member)


os.umask(0o077)
root=Path('/var/backups/galaxy-ai');root.mkdir(mode=0o700,parents=True,exist_ok=True)
settings=Path('/etc/galaxy-ai/database-backup.json')
keep=int(json.loads(settings.read_text()).get('keep',7)) if settings.exists() else 7
keep=max(1,min(keep,90))
with tempfile.TemporaryDirectory(dir=root,prefix='.backup-') as temporary:
    folder=Path(temporary)
    sql=folder/'galaxy_ai.sql.gz'
    process=subprocess.Popen(['mysqldump','--defaults-extra-file=/etc/galaxy-ai/mysql-root.cnf',
        '--single-transaction','--quick','--no-tablespaces','galaxy_ai'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    with gzip.open(sql,'wb') as output:
        while chunk:=process.stdout.read(1024*1024):output.write(chunk)
    _error=process.stderr.read()
    if process.wait()!=0:raise SystemExit('Database backup failed')
    destination=root/('galaxy-ai-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')+'.tgz')
    staging=folder/'backup.tgz'
    with tarfile.open(staging,'w:gz') as archive:
        archive.add(sql,arcname='galaxy_ai.sql.gz')
        archive.add('/opt/galaxy-ai/backend/private/ai-secret.key',arcname='ai-secret.key')
        add_account_files(archive)
        add_feishu_bot_files(archive)
    os.replace(staging,destination)
    destination.chmod(0o600)
for old in sorted(root.glob('galaxy-ai-*.tgz'),reverse=True)[keep:]:old.unlink()
print('Database, account files and encryption key backed up:',destination.name)
