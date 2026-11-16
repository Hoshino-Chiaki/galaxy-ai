from pathlib import Path
import dnfile
root=Path('artifacts/silverlight-static-20261116/client')
pe=dnfile.dnPE(str(root/'Proxy.CarpaServer.dll'))
for t in pe.net.mdtables.TypeDef.rows:
 if 'GoodsStock' in str(t.TypeName):
  print(str(t.TypeNamespace)+'.'+str(t.TypeName),[(str(m.row.Name),m.row.Rva) for m in t.MethodList])
