import os,signal
from pathlib import Path
for p in Path('/proc').iterdir():
 if p.name.isdigit():
  try:
   args=(p/'cmdline').read_bytes().split(b'\0')
   if any(b'/var/tmp/galaxy-ai-dsh-20261116-' in a and a.endswith(b'/.venv/bin/python') for a in args) and b'pytest' in args:os.kill(int(p.name),signal.SIGTERM);print('Stopped stale validation process',p.name)
  except (OSError,ProcessLookupError):pass
