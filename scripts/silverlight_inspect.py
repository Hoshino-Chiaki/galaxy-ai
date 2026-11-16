"""Read CLR metadata and IL without loading or executing any target assembly."""
from pathlib import Path
import json
import dnfile
from dncil.cil.body import CilMethodBody
from dncil.cil.body.reader import CilMethodBodyReaderBytes
from dncil.clr.token import Token, StringToken

ROOT=Path(__file__).resolve().parents[1]/'artifacts'/'silverlight-static-20261116'
pe=dnfile.dnPE(str(ROOT/'client/CarpaClient.dll'))
tables=pe.net.mdtables
method_owner={}
for t in tables.TypeDef.rows:
    for index in t.MethodList:
        method_owner[index.row_index]=str(t.TypeNamespace)+'.'+str(t.TypeName)


def operand(value):
    if isinstance(value,StringToken):
        return repr(str(pe.net.user_strings.get(value.rid)))
    if isinstance(value,Token):
        try:
            table=getattr(tables,{0x01:'TypeRef',0x02:'TypeDef',0x04:'Field',0x06:'MethodDef',0x0a:'MemberRef',0x1b:'TypeSpec',0x2b:'MethodSpec'}[value.table])
            row=table.rows[value.rid-1]
            if value.table==0x06:return method_owner.get(value.rid,'')+'::'+str(row.Name)
            if value.table==0x0a:
                owner=row.Class.row
                return str(getattr(owner,'TypeNamespace',''))+'.'+str(getattr(owner,'TypeName',getattr(owner,'Name','')))+'::'+str(row.Name)
            return str(getattr(row,'TypeNamespace',''))+'.'+str(getattr(row,'TypeName',getattr(row,'Name','')))
        except (KeyError,IndexError,AttributeError):return str(value)
    return str(value) if value is not None else ''


lines=[]
for t in tables.TypeDef.rows:
    name=str(t.TypeName)
    if name.startswith('GoodsStock') or name in ('ShopGoodsStockQryAction','ShopGoodsStockQryMainAction'):
        lines.append('\nTYPE '+str(t.TypeNamespace)+'.'+name)
        for index in t.MethodList:
            m=index.row
            lines.append('\nMETHOD '+str(m.Name)+' RVA='+str(m.Rva))
            if not m.Rva:continue
            try:
                body=CilMethodBody(CilMethodBodyReaderBytes(pe.get_data(m.Rva)))
                for instruction in body.instructions:
                    lines.append(f'{instruction.offset:04x} {instruction.opcode.name} {operand(instruction.operand)}')
            except Exception as error:
                lines.append('ERROR '+type(error).__name__)
output=ROOT/'GoodsStock-actions.il.txt'
output.write_text('\n'.join(lines),encoding='utf-8')
print('Static IL saved',output,'lines',len(lines))
print('\n'.join(line for line in lines if line.startswith(('TYPE','METHOD'))))
