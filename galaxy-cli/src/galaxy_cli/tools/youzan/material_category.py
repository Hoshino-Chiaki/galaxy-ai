"""Image material category discovery and explicit, scoped writes."""
from jsonschema import Draft202012Validator
from ...youzan_catalog import ID, IDS, MATERIAL, OUTPUT, object_schema, invalid, client_context, must, success, write_lock
from ...registry import ToolError

INPUT = {'oneOf': [object_schema({'action': {'const': 'list'}}, ['action']),
    object_schema({'action': {'const':'create'},'name':{'type':'string','minLength':1,'maxLength':20},'parent_id':ID}, ['action','name']),
    object_schema({'action': {'const':'rename'},'category_id':ID,'name':{'type':'string','minLength':1,'maxLength':20}}, ['action','category_id','name']),
    object_schema({'action': {'const':'move'},'category_ids':{**IDS,'minItems':1},'parent_id':ID}, ['action','category_ids','parent_id'])],
    'type': 'object'}
DESCRIPTION = ('查询、创建、改名或移动当前店铺的图片素材分类，调用 galaxy-cli youzan material-category list/create/rename/move，可用 --input JSON。'
    'list 无其他参数；create 必填 name（最多 20 字符），可选 parent_id，省略时使用当前店铺自定义分组根目录；rename 必填 category_id、name；'
    'move 必填 category_ids 数组及目标 parent_id。分类 ID 均为十进制字符串；不能修改系统分类、移到自身或后代。'
    '查询返回完整有赞响应，data 数组含 categoryId/categoryName/parentCategoryId/categoryType/count；创建成功 data 为新分类 ID。'
    '改名、移动后回读核对；移动 data 非空表示存在失败分类，多原父分类移动返回 results 内各次完整响应，需逐项看结果。'
    '不提供分类或素材删除。分销商品不影响本店素材分类管理；素材分类不等于商品分组或行业类目，建分类不改变商品归类。')


def normalize(arguments):
    if not isinstance(arguments,dict) or not Draft202012Validator(INPUT).is_valid(arguments):
        invalid('分类需要 list/create/rename/move 子命令；请按说明填写 name、category_id/category_ids、parent_id。')
    result=dict(arguments)
    if 'name' in result:
        result['name']=result['name'].strip()
        if not result['name'] or any(ord(c)<32 or ord(c)==127 for c in result['name']): invalid('分类名称不能为空或包含控制字符。')
    return result


def run(client, context, args):
    args=normalize(args)
    if args['action']=='list': return client.categories()
    with write_lock(context,'material-categories'):
        listing=client.categories(); rows=must(listing)
        if not isinstance(rows,list): raise ToolError('invalid_upstream','分类列表结构无效。',1)
        root=next((r for r in rows if r.get('categoryType')==5 and not r.get('parentCategoryId')),None)
        parent_id=args.get('parent_id',str(root['categoryId']) if root else '')
        if args['action'] in ('create','move'):
            parent=client.category(parent_id,listing)
            if parent.get('categoryType') not in (1,5): raise ToolError('category_read_only','只能在当前店铺自定义分组中创建或移动分类。',1)
        if args['action']=='create':
            if any(r.get('categoryName')==args['name'] and str(r.get('parentCategoryId'))==parent_id for r in rows):
                raise ToolError('category_exists','该父分类下已存在同名分类，请直接使用现有分类。',1)
            result=client.request(MATERIAL+'multi-category/create.json',method='POST',body={
                'categoryName':args['name'],'parentCategoryId':int(parent_id),'mediaType':1})
            if success(result):
                check=client.category(str(result['data']))
                if check.get('categoryName')!=args['name'] or str(check.get('parentCategoryId'))!=parent_id:
                    raise ToolError('write_unverified','分类可能已创建，但回读不一致，请重新查询，不要重复创建。',1)
            return result
        if args['action']=='rename':
            row=client.category(args['category_id'],listing)
            if row.get('categoryType')!=1: raise ToolError('category_read_only','系统分类及自定义分组根目录不能改名。',1)
            result=client.request(MATERIAL+'multi-category/update.json',method='PUT',body={
                'categoryId':int(args['category_id']),'categoryName':args['name'],'parentCategoryId':row['parentCategoryId'],'mediaType':1})
            if success(result) and client.category(args['category_id']).get('categoryName')!=args['name']:
                raise ToolError('write_unverified','分类可能已改名，但回读不一致，请先查询当前状态。',1)
            return result
        selected=[client.category(identifier,listing) for identifier in args['category_ids']]
        if any(row.get('categoryType')!=1 for row in selected): raise ToolError('category_read_only','不能移动系统分类或根目录。',1)
        by_id={str(r['categoryId']):r for r in rows}
        ancestor=parent_id;seen=set()
        while ancestor in by_id:
            if ancestor in args['category_ids'] or ancestor in seen: invalid('分类不能移动到自身或后代。')
            seen.add(ancestor);ancestor=str(by_id[ancestor].get('parentCategoryId'))
        groups={}
        for row in selected: groups.setdefault(str(row['parentCategoryId']),[]).append(int(row['categoryId']))
        results=[]
        for old,identifiers in groups.items():
            result=client.request(MATERIAL+'multi-category/move.json',method='PUT',body={
                'categoryIds':identifiers,'mediaType':1,'oldParentCategoryId':int(old),'targetCategoryId':int(parent_id)})
            results.append(result)
            if success(result):
                failed={str(x.get('categoryId')) if isinstance(x,dict) else str(x) for x in result.get('data',[]) or []}
                for identifier in identifiers:
                    if str(identifier) not in failed and str(client.category(str(identifier)).get('parentCategoryId'))!=parent_id:
                        raise ToolError('write_unverified','分类移动回读不一致，请查询当前目录。',1)
        return results[0] if len(results)==1 else {'results':results}


def register(registry):
    @registry.tool(name='youzan.material-category',category='youzan',description=DESCRIPTION,input_schema=INPUT,output_schema=OUTPUT)
    def invoke(arguments):
        arguments=normalize(arguments);client,context=client_context('material-category',arguments)
        return run(client,context,arguments)
