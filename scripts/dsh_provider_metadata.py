import json
from pathlib import Path
s=json.loads(Path('/opt/galaxy-ai/backend/private/ai.json').read_text());p=s['providers'][s['settings']['default_provider_id']]
print(json.dumps({k:p.get(k) for k in ('name','model','max_tokens','enabled')},ensure_ascii=False))
from urllib.parse import urlsplit
u=urlsplit(p['base_url']);print('provider_host',u.hostname,'provider_path',u.path)
