"""Read actual Ptype parent relationships; never infer classes from ID prefixes."""
from collections import defaultdict


def fields(status, categories=None):
    categories = categories or []
    return {'category_status': status, 'categories': categories,
            'category_path': '/'.join(item['name'] for item in categories) if status == 'matched' else None}


def from_ancestors(rows):
    if not rows:
        return fields('unavailable')
    chain = sorted(rows, key=lambda row: row['depth'])
    seen = set()
    for index, row in enumerate(chain):
        if row['depth'] != index or index and str(chain[index - 1]['ParID']) != str(row['TypeID']):
            return fields('missing_parent')
        if str(row['TypeID']) in seen:
            return fields('cycle')
        seen.add(str(row['TypeID']))
        if row['Deleted']:
            return fields('deleted_node')
    last = chain[-1]
    if last['Leveal'] != 0:
        return fields('cycle' if str(last['ParID']) in seen else 'depth_limit' if last['depth'] == 32 else 'missing_parent')
    categories = []
    for row in reversed(chain[1:]):
        if row['Leveal'] == 0:
            continue
        if not isinstance(row['FullName'], str) or not row['FullName'].strip():
            return fields('invalid_name')
        categories.append({'id': str(row['TypeID']), 'name': row['FullName'], 'level': row['Leveal']})
    return fields('matched', categories) if categories else fields('unclassified')


def query(client, product_ids):
    result = {}
    ids = list(dict.fromkeys(product_ids))
    for offset in range(0, len(ids), 500):
        batch = ids[offset:offset + 500]
        values = ','.join('(CAST(%s AS varchar(128)))' for _ in batch)
        sql = f'''WITH wanted AS (SELECT type_id FROM (VALUES {values}) inp(type_id)),
        ancestors AS (
            SELECT p.TypeID AS product_id,p.TypeID,p.ParID,p.FullName,p.Leveal,p.Deleted,0 AS depth,
                CAST('|' + p.TypeID + '|' AS varchar(max)) AS visited
            FROM dbo.Ptype p JOIN wanted w ON w.type_id=p.TypeID
            UNION ALL
            SELECT a.product_id,p.TypeID,p.ParID,p.FullName,p.Leveal,p.Deleted,a.depth+1,
                CAST(a.visited + p.TypeID + '|' AS varchar(max))
            FROM dbo.Ptype p JOIN ancestors a ON p.TypeID=a.ParID
            WHERE a.depth<32 AND CHARINDEX('|' + p.TypeID + '|',a.visited)=0
        ) SELECT product_id,TypeID,ParID,FullName,Leveal,Deleted,depth
          FROM ancestors ORDER BY product_id,depth OPTION(MAXRECURSION 32)'''
        try:
            with client.connection.cursor() as cursor:
                cursor.execute(sql, tuple(batch))
                rows = cursor.fetchall()
        except client.driver.Error:
            # Valid cost data remains usable; category failures are explicit.
            result.update({identifier: fields('query_failed') for identifier in batch})
            continue
        grouped = defaultdict(list)
        for row in rows:
            grouped[str(row['product_id'])].append(row)
        result.update({identifier: from_ancestors(grouped[identifier]) for identifier in batch})
    return result
