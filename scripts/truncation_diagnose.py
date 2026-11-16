"""Read-only diagnosis: metadata and known log codes, never keys or message bodies."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

root = Path('/opt/galaxy-ai/backend/private')
config = json.loads((root/'ai.json').read_text())
provider = config['providers'][config['settings']['default_provider_id']]
print(json.dumps({'provider': {k: provider.get(k) for k in ('model','max_tokens')},
    'endpoint_host': urlsplit(provider['base_url']).hostname}))
for name in ('galaxy-ai.service','galaxy-dsh.service'):
    print(subprocess.check_output(['systemctl','show',name,'-p','ActiveState','-p','ExecMainStartTimestamp'],text=True).strip())
    logs = subprocess.check_output(['journalctl','-u',name,'-n','600','--no-pager','-o','cat'],text=True)
    codes = re.findall(r'chat_turn_failed code=([a-z_]+) completed_tool_calls=(\d+)',logs)
    print(json.dumps({'unit':name,'recent_failure_codes':codes[-30:]}))
chats = json.loads((root/'chats.json').read_text())['conversations']
for chat in chats.values():
    messages = chat.get('messages',[])
    if chat['id'] == os.environ.get('GALAXY_SMOKE_CHAT_ID'):
        print(json.dumps({'chat_id':chat['id'],'messages':len(messages),'context_state':bool(chat.get('context_state')),
            'dsh_state':bool(chat.get('dsh_state')),'updated_at':chat.get('updated_at'),
            'last_roles':[m['role'] for m in messages[-4:]],
            'history_bytes':len(json.dumps(messages,ensure_ascii=False).encode()),
            'last_tool_events':len(messages[-1].get('tool_events',[])) if messages else 0,
            'interrupted':messages[-1].get('interrupted',False) if messages else False}))
for path in ('ai.py','dsh_runtime.py','dsh_protocol.py'):
    data=Path('/opt/galaxy-ai/backend/xingxu',path).read_bytes()
    print(path,hashlib.sha256(data).hexdigest())
