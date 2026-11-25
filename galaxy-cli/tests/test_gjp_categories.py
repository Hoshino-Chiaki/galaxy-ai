from unittest.mock import Mock

from galaxy_cli.gjp.categories import from_ancestors, query, fields
from galaxy_cli.gjp.inventory_cost import price_row, match_item


def node(identifier, parent, name, level, depth, **values):
    return {'product_id':'product','TypeID':identifier,'ParID':parent,'FullName':name,
            'Leveal':level,'depth':depth,'Deleted':0,**values}


def chain():
    return [node('product','leaf','商品名称',3,0),node('leaf','category','护肤类-乳',2,1),
            node('category','root','用品',1,2),node('root','0','库存商品',0,3)]


def test_parent_chain_returns_ordered_categories_excluding_product_and_root():
    value=from_ancestors(list(reversed(chain())))
    assert value['category_status']=='matched' and value['category_path']=='用品/护肤类-乳'
    assert value['categories']==[{'id':'category','name':'用品','level':1},{'id':'leaf','name':'护肤类-乳','level':2}]
    assert from_ancestors([node('product','root','商品',1,0),node('root','0','库存商品',0,1)])['category_status']=='unclassified'


def test_missing_deleted_cyclic_and_depth_limited_paths_are_not_guessed():
    assert from_ancestors(chain()[:-1])['category_status']=='missing_parent'
    broken=chain();broken[1]['ParID']='wrong'
    assert from_ancestors(broken)['category_status']=='missing_parent'
    deleted=chain();deleted[1]['Deleted']=1
    assert from_ancestors(deleted)['category_status']=='deleted_node'
    cyclic=chain()[:-1];cyclic[-1]['ParID']='product'
    assert from_ancestors(cyclic)['category_status']=='cycle'
    deep=[node(str(i),str(i+1),'分类',40-i,i) for i in range(33)]
    assert from_ancestors(deep)['category_status']=='depth_limit'
    for nodes in (broken,deleted,cyclic,deep):assert from_ancestors(nodes)['category_path'] is None


def test_category_survives_unpriced_cost_and_ambiguous_candidates_remain_separate():
    row={'TypeID':'product','FullName':'商品名称','qty':0,'amount':-41.23,**from_ancestors(chain())}
    value=price_row(row)
    assert value['avg_cost'] is None and value['status']=='negative_stock_amount_review'
    assert value['category_status']=='matched' and value['category_path']=='用品/护肤类-乳'
    other={**row,'TypeID':'another','FullName':'其他商品',**fields('matched',[{'id':'toys','name':'玩具','level':1}])}
    ambiguous=match_item({'barcode':'001'},[row,other])
    assert ambiguous['category_status']=='ambiguous' and ambiguous['category_path'] is None
    assert [c['category_path'] for c in ambiguous['candidates']]==['用品/护肤类-乳','玩具']
    matched=match_item({'barcode':'001','product_name':'其他商品'},[row,other])
    assert matched['category_path']=='玩具'
    assert match_item({'barcode':'missing'},[])['category_status']=='not_found'


def test_category_sql_is_batched_parameterized_and_failure_keeps_explicit_status():
    captured=[]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def execute(self,sql,params):captured.append((sql,params))
        def fetchall(self):return chain()
    client=Mock();client.connection.cursor.side_effect=lambda:Cursor();client.driver.Error=RuntimeError
    ids=['product']+[str(i) for i in range(1000)]+['product']
    result=query(client,ids)
    assert len(captured)==3 and list(map(lambda row:len(row[1]),captured))==[500,500,1]
    assert result['product']['category_path']=='用品/护肤类-乳'
    assert result['123']['category_status']=='unavailable'
    assert 'JOIN ancestors' in captured[0][0] and 'ParID' in captured[0][0]
    attack="x'); DROP TABLE Ptype;--"
    query(client,[attack]);assert attack not in captured[-1][0] and captured[-1][1]==(attack,)
    client.connection.cursor.side_effect=RuntimeError('private SQL details')
    failed=query(client,['product'])
    assert failed['product']==fields('query_failed') and 'private' not in str(failed)
