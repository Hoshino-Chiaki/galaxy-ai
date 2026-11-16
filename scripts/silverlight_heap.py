import dnfile
from pathlib import Path
p=dnfile.dnPE('artifacts/silverlight-static-20261116/client/CarpaClient.dll')
h=p.net.user_strings
print(type(h).__name__,[x for x in dir(h) if not x.startswith('_')]); print('heap rva',h.rva,'size',len(h.__data__))
