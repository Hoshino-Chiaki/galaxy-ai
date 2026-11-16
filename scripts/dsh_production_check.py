import asyncio, json, subprocess
from pathlib import Path
from xingxu.dsh_runtime import worker_request
print(json.dumps(asyncio.run(worker_request({'command':'status'}))))
print(subprocess.check_output(['systemctl','show','galaxy-ai.service','-p','ActiveState','-p','Environment'],text=True))
print(subprocess.check_output(['systemctl','show','galaxy-dsh.service','-p','ActiveState','-p','User','-p','MemoryCurrent','-p','TasksCurrent'],text=True))
print(Path('/etc/systemd/system/galaxy-ai.service.d/dsh.conf').read_text())
import urllib.request
print(urllib.request.urlopen('http://127.0.0.1:18443/health',timeout=3).read().decode())
from urllib.parse import urlsplit
config=json.loads(Path('/opt/galaxy-ai/backend/private/ai.json').read_text())
provider=config['providers'][config['settings']['default_provider_id']]
print(json.dumps({'provider_model':provider['model'],'max_tokens':provider.get('max_tokens'),'base_host':urlsplit(provider['base_url']).hostname}))
