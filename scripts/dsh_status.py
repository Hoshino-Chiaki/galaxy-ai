import subprocess,asyncio
from xingxu.dsh_runtime import worker_request
print(asyncio.run(worker_request({'command':'status'})))
print(subprocess.check_output(['systemctl','show','galaxy-dsh.service','-p','ActiveState','-p','MemoryCurrent','-p','TasksCurrent'],text=True))
print(subprocess.check_output(['journalctl','-u','galaxy-dsh.service','-n','12','--no-pager','-o','cat'],text=True))
