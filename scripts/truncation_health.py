import hashlib
import json
import subprocess
from pathlib import Path
import urllib.request

for unit in ('galaxy-ai.service','galaxy-dsh.service'):
    print(subprocess.check_output(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','Result','-p','ExecMainStartTimestamp'],text=True))
log=subprocess.check_output(['journalctl','-u','galaxy-ai.service','-n','100','--no-pager','-o','cat'],text=True)
# Only startup exceptions/known failure codes, never business inputs or tokens.
for line in log.splitlines():
    if any(key in line for key in ('Traceback','File "','Error:','SyntaxError','Exception:','Started ','Failed ','Failed with result','Main process exited','ModuleNotFoundError')):
        print(line)
print('source_sha256',hashlib.sha256(Path('/opt/galaxy-ai/backend/xingxu/ai.py').read_bytes()).hexdigest())
try:
    print(json.load(urllib.request.urlopen('http://127.0.0.1:18443/health',timeout=3)))
except Exception as e:
    print(type(e).__name__,str(e)[:200])
