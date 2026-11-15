"""Shop-scoped product groups, independent of products and image categories."""
import copy
import json

from jsonschema import Draft202012Validator

from ...registry import ToolError
from ...youzan_catalog import ID, IDS, BOOL, OUTPUT, object_schema, invalid, client_context, must, success, write_lock
from .product_update import DetailValidator

TAG = '/v4/goods/tag/'
CREATE = '/v4/goods/api/tag/item/_textarea_'
PRIORITIES = {'index_desc': '0', 'created_desc': '1', 'created_asc': '2', 'hot': '3'}
LIST_STYLES = {'big': '0', 'small': '1', 'mixed': '2', 'detail': '3'}
CARD_STYLES = {'card': '0', 'waterfall': '1', 'minimal': '2'}
OPTIONS = {
    'subtitle': {'type': 'string', 'maxLength': 10}, 'show_group_name': BOOL,
    'sort_rule': {'enum': ['smart', 'custom']},
    'first_priority': {'enum': list(PRIORITIES)}, 'second_priority': {'enum': list(PRIORITIES)},
    'show_search_bar': BOOL, 'list_style': {'enum': list(LIST_STYLES)},
    'card_style': {'enum': list(CARD_STYLES)}, 'show_buy_button': BOOL,
    'buy_button_style': {'type': 'integer', 'minimum': 1, 'maximum': 4},
    'button_text': {'type': 'string', 'maxLength': 50}, 'show_product_name': BOOL, 'show_price': BOOL,
    'group_filter': BOOL, 'color_style': {'enum': ['default', 'shop']},
    'content_html': {'type': 'string', 'maxLength': 500000}, 'hidden_shop_ids': IDS,
}
INPUT = {'type': 'object', 'oneOf': [
    object_schema({'action': {'const': 'list'}, 'page': {'type': 'integer', 'minimum': 1},
                   'page_size': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                   'keyword': {'type': 'string', 'maxLength': 50}, 'parent_id': ID}, ['action']),
    object_schema({'action': {'const': 'get'}, 'group_id': ID}, ['action', 'group_id']),
    object_schema({'action': {'const': 'stores'}}, ['action']),
    object_schema({'action': {'const': 'create'}, 'name': {'type': 'string', 'minLength': 1, 'maxLength': 50},
                   'parent_id': ID, **OPTIONS}, ['action', 'name']),
]}
DESCRIPTION = (
    '直接查询或创建当前店铺的商品分组，不需要 product_id。调用 galaxy-cli youzan product-group list/get/stores/create --input JSON。'
    'list 参数全部可选：page 默认1、page_size 默认20（1–100）、keyword 按名称查询；传 parent_id 则查询该一级分组下全部二级分组，不能同时传分页或关键词。'
    'get 必填 group_id，返回完整分组配置；stores 无参数，返回当前账号可操作分店的 id/name，供设置可见性时选用。'
    'create 仅 name 必填（1–50字），省略 parent_id 创建一级分组，传当前店铺一级分组的 parent_id 创建二级分组，不支持三级。'
    '其他创建字段全部可选，用户未提到就不要填写，由工具使用有赞页面默认值：subtitle 别名（最多10字，默认空）；show_group_name 页面显示分组名/别名（默认false）；'
    'sort_rule=smart智能排序（默认）或custom自定义；first_priority、second_priority 可选 index_desc序号降序/created_desc新建在前/created_asc早建在前/hot热度，'
    '自定义默认优先级 index_desc、created_desc，两个优先级不可相同或同时为创建时间；只传优先级时自动使用custom，未指定的另一优先级自动避开冲突。'
    'show_search_bar 搜索框（默认true）；list_style=big大图/small小图/mixed一大两小（默认）/detail详细列表；'
    'card_style=card卡片（默认）/waterfall瀑布流（仅small）/minimal极简；show_buy_button 显示购买按钮（默认true）；buy_button_style=1/2/3/4 对应页面样式1–4（默认1）；'
    'button_text 按钮文字；show_product_name、show_price 显示商品名、价格（均默认true）；group_filter 开启分组筛选（默认false）；'
    'color_style=default默认黑色或shop跟随店铺主色（默认）；content_html 为富文本简介（默认空），图片须先在当前店铺素材工具查询或上传，不能包含脚本；'
    'hidden_shop_ids 是指定不可见分店的十进制ID字符串数组，省略或空数组表示全部分店可见，只能选 stores 返回的分店。'
    'list/get/create 返回有赞完整JSON：code=0成功，创建的 data.id、data.alias 是新分组ID和别名，list 的 data.items 含 first/second、data.paginator 为分页，'
    '二级列表 data 为数组；get 的 data.components 是JSON字符串，解析后 type=config 的对象包含完整页面设置。创建后自动回读核对，结果不确定时先get，不要重复创建。'
    '同一父分组内已存在同名分组会返回 group_exists 和现有ID。创建分组不会将任何商品加入分组；商品归类另用 product-update 的 group_ids。'
    '这是商品分组，不是图片素材分类；分销商品不影响本店商品分组创建，也不改变供应商控制的价格、库存或配送。写入须指定一家店铺，员工默认只能读取。'
)


def normalize(arguments):
    if not isinstance(arguments, dict) or not Draft202012Validator(INPUT).is_valid(arguments):
        invalid('请使用 product-group list/get/stores/create，创建只需 name；其他字段按工具说明选择填写。')
    args = copy.deepcopy(arguments)
    for key in ('name', 'subtitle', 'button_text', 'keyword'):
        if key in args:
            args[key] = args[key].strip()
            if any(ord(c) < 32 or ord(c) == 127 for c in args[key]) or key == 'name' and not args[key]:
                invalid('分组名称不能为空，文字参数不能含控制字符。')
    if args['action'] == 'list' and 'parent_id' in args and set(args) & {'page', 'page_size', 'keyword'}:
        invalid('查询二级分组时只传 parent_id，不传分页或关键词。')
    if args['action'] == 'create':
        if set(args) & {'first_priority', 'second_priority'}:
            if args.get('sort_rule') == 'smart': invalid('智能排序不能同时指定自定义优先级。')
            args.setdefault('sort_rule', 'custom')
        if 'first_priority' in args and 'second_priority' not in args:
            args['second_priority'] = 'index_desc' if args['first_priority'].startswith('created_') else 'created_desc'
        if 'second_priority' in args and 'first_priority' not in args:
            args['first_priority'] = 'created_desc' if args['second_priority'] == 'index_desc' else 'index_desc'
        first, second = args.get('first_priority', 'index_desc'), args.get('second_priority', 'created_desc')
        if first == second or first.startswith('created_') and second.startswith('created_'):
            invalid('两个排序优先级不可相同，也不能同时按创建时间排序。')
        if args.get('card_style') == 'waterfall':
            args.setdefault('list_style', 'small')
            if args['list_style'] != 'small': invalid('瀑布流仅支持小图列表。')
        if args.get('card_style') == 'minimal' and args.get('list_style') == 'detail' and args.get('buy_button_style') == 3:
            invalid('详细列表的极简样式不支持购买按钮样式3。')
    return args


def lookup(client, identifier):
    rows = must(client.request(TAG + 'queryGroupListJSON.json', params={'groupIds': identifier}))
    row = next((r for r in rows if str(r.get('groupId')) == identifier), None) if isinstance(rows, list) else None
    if row is None:
        raise ToolError('group_not_found', '分组不属于当前店铺或已失效，请先查询当前店铺分组。', 1)
    return row


def listing(client, args):
    if 'parent_id' in args:
        parent = lookup(client, args['parent_id'])
        if parent.get('upperId') not in (None, 0, '0'):
            raise ToolError('group_level_unsupported', '只支持一级分组和二级分组，不能在二级分组下再建分组。', 1)
        return client.request(TAG + 'querySubGroupList.json', params={'upperId': args['parent_id'], 'kdtId': client.credentials['kdt_id'], 'channel': 0})
    return client.request(TAG + 'listMultiLevelGroupPage.json', params={
        'p': args.get('page', 1), 'pageSize': args.get('page_size', 20), 'keyword': args.get('keyword', ''),
        'kdtId': client.credentials['kdt_id'], 'filterHiddenGroup': 'false', 'groupQueryScene': 1,
        'orderBy': '', 'order': 'desc'})


def config_for(args):
    # Official editor defaults are protocol details, not extra required model inputs.
    config = {'type': 'config', 'title': args['name'], 'subtitle': '', 'show_tag_title': '0',
        'sort_rule': '0', 'first_priority': '0', 'second_priority': '1', 'size': '2', 'size_type': '0',
        'buy_btn': '1', 'buy_btn_type': '6', 'show_title': '1', 'price': '1', 'cart': '1', 'content': '',
        'is_default': '0', 'hot_tag_order': '', 'show_ump_ambience': '0', 'group_select_switch': '0',
        'show_search_bar': '1', 'groupVisibleParam': {'visibleRange': '0', 'showSubKdtIds': [], 'hiddenSubKdtIds': []},
        'price_btn_style': '2'}
    for key, dest in {'subtitle': 'subtitle', 'button_text': 'button_text', 'content_html': 'content'}.items():
        if key in args: config[dest] = args[key]
    for key, dest in {'show_group_name': 'show_tag_title', 'show_search_bar': 'show_search_bar',
        'show_buy_button': 'buy_btn', 'show_product_name': 'show_title', 'show_price': 'price', 'group_filter': 'group_select_switch'}.items():
        if key in args: config[dest] = '1' if args[key] else '0'
    for key, dest, values in [('sort_rule', 'sort_rule', {'smart': '0', 'custom': '1'}),
        ('first_priority', 'first_priority', PRIORITIES), ('second_priority', 'second_priority', PRIORITIES),
        ('list_style', 'size', LIST_STYLES), ('card_style', 'size_type', CARD_STYLES),
        ('buy_button_style', 'buy_btn_type', {1: '6', 2: '2', 3: '3', 4: '4'}),
        ('color_style', 'price_btn_style', {'default': '1', 'shop': '2'})]:
        if key in args: config[dest] = values[args[key]]
    if 'parent_id' in args: config['upper_id'] = int(args['parent_id'])
    if args.get('hidden_shop_ids'):
        config['groupVisibleParam'] = {'visibleRange': '1', 'showSubKdtIds': [], 'hiddenSubKdtIds': args['hidden_shop_ids']}
    return config


def duplicate(client, args):
    if 'parent_id' in args:
        rows = must(listing(client, {'parent_id': args['parent_id']}))
        if not isinstance(rows, list): raise ToolError('invalid_upstream', '二级分组列表结构异常。', 1)
        return next((r for r in rows if r.get('title') == args['name']), None)
    page, seen = 1, set()
    while True:
        data = must(listing(client, {'keyword': args['name'], 'page': page}))
        if not isinstance(data, dict) or not isinstance(data.get('items'), list):
            raise ToolError('invalid_upstream', '商品分组列表结构异常。', 1)
        rows = [r.get('first', {}) for r in data['items']]
        match = next((r for r in rows if r.get('title') == args['name']), None)
        if match: return match
        ids = {str(r.get('groupId')) for r in rows}
        if not rows: return None
        if ids <= seen: raise ToolError('invalid_upstream', '商品分组分页未推进，请重新查询。', 1)
        seen.update(ids)
        total = data.get('paginator', {}).get('totalCount')
        if isinstance(total, int) and len(seen) >= total or len(rows) < 20: return None
        page += 1


def run(client, context, arguments):
    args = normalize(arguments)
    if args['action'] == 'list': return listing(client, args)
    if args['action'] == 'stores': return {'data': client.branches()}
    if args['action'] == 'get':
        lookup(client, args['group_id'])
        return client.request(TAG + 'item.json', params={'id': args['group_id']})
    with write_lock(context, 'product-groups'):
        match = duplicate(client, args)
        if match:
            raise ToolError('group_exists', f"同一父分组内已存在同名分组，ID：{match['groupId']}，请直接使用，不要重复创建。", 1)
        if args.get('hidden_shop_ids'):
            allowed = {row['id'] for row in client.branches()}
            if not set(args['hidden_shop_ids']) <= allowed:
                raise ToolError('shop_denied', '不可见分店必须来自当前店铺 stores 返回的可操作分店。', 1)
        if 'content_html' in args:
            validator = DetailValidator(client); validator.feed(args['content_html']); validator.close()
        config = config_for(args)
        result = client.request(CREATE, method='POST', form=True, body={'data': json.dumps([config], ensure_ascii=False)})
        if success(result):
            data = result.get('data')
            identifier = str(data.get('id', '')) if isinstance(data, dict) else ''
            if not identifier.isdecimal():
                raise ToolError('write_unverified', '创建请求成功但未返回分组ID，请先查询分组，不要重复创建。', 1)
            try:
                row = lookup(client, identifier)
                if row.get('title') != args['name'] or str(row.get('upperId', 0)) != args.get('parent_id', '0'):
                    raise ValueError()
                saved = must(client.request(TAG + 'item.json', params={'id': identifier}))
                components = json.loads(saved['components'])
                actual = next(c for c in components if c.get('type') == 'config')
                if any(actual.get(k) != v for k, v in config.items() if k not in ('upper_id',)):
                    raise ValueError()
            except (ToolError, ValueError, TypeError, KeyError, StopIteration):
                raise ToolError('write_unverified', f'分组创建请求成功，ID：{identifier}，但配置回读未确认；先用 get 查询，不要重复创建。', 1) from None
        return result


def register(registry):
    @registry.tool(name='youzan.product-group', category='youzan', description=DESCRIPTION, input_schema=INPUT, output_schema=OUTPUT)
    def invoke(arguments):
        args = normalize(arguments)
        client, context = client_context('product-group', args)
        return run(client, context, args)
