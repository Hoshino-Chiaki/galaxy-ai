from pathlib import Path
import json,sqlite3

files=[]
for parent in ('/opt/mami/data','/opt/mami/shared','/opt/mami/runtime','/opt/mami/current/session-lab','/opt/mami/session-lab'):
    p=Path(parent)
    if not p.exists():continue
    for f in p.rglob('*'):
        if f.is_file() and f.suffix in ('.db','.sqlite','.sqlite3'):
            files.append(f)
print('SQLITE_PATHS',json.dumps([str(p) for p in files[:12]]))
for p in files[:12]:
    try:
        db=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True)
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        if 'orders' not in names:db.close();continue
        columns=[r[1] for r in db.execute('PRAGMA table_info(orders)')]
        if 'input_json' not in columns:db.close();continue
        items=[]
        for (raw,) in db.execute("SELECT input_json FROM orders WHERE state='recorded' ORDER BY updated_at DESC LIMIT 3"):
            try:order=json.loads(raw)
            except (ValueError,TypeError):continue
            for item in order.get('items',[])[:3]:
                items.append({k:v for k,v in item.items() if any(s in k.lower() for s in ('barcode','code','name','price')) and not any(s in k.lower() for s in ('user','member','buyer','customer'))})
        print('RECENT_PRODUCT_CANDIDATES',json.dumps(items,ensure_ascii=False))
        db.close()
    except (sqlite3.Error,OSError):pass
