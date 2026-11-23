"""Youzan-specific tool results, shop binding and per-shop execution."""
from galaxy_cli.youzan_catalog import NAMES as CATALOG_NAMES, ACTIONS as CATALOG_ACTIONS
from galaxy_cli.tools.youzan.business_report import succeeded as report_succeeded
from ..auth import AuthFailure
from ..tool_context import DispatchDenied, strict_json


def is_customer_count_result(arguments, result):
    return (arguments[:2] == ['youzan', 'customer-count'] and isinstance(result, dict)
            and set(result) == {'total'} and type(result['total']) is int and result['total'] >= 0)


def catalog_body(arguments):
    name=arguments[1]
    body=strict_json(arguments[arguments.index('--input')+1])
    if name in CATALOG_ACTIONS: body={'action':arguments[2],**body}
    return body


def readonly_youzan(arguments):
    name = arguments[1]
    if name == 'product-group': return arguments[2] in ('list', 'get', 'stores')
    if name in ('customer-search', 'customer-get', 'customer-count', 'business-report', 'material-list', 'product-get', 'product-options', 'product-search', 'order-search'):
        return True
    return name == 'material-category' and arguments[2] == 'list' or name == 'product-update' and arguments[2] == 'preview'


def tool_failed(command, arguments, result):
    parts = list(result.values()) if command == 'youzan.customer-get' and 'customer' in result else [result]
    failed = any(not isinstance(part, dict) or 'error' in part or part.get('code', 0) not in (0, 200) for part in parts)
    if command == 'youzan.order-search':
        failed = failed or result.get('success') is False
    if command == 'youzan.business-report':
        failed = not report_succeeded(arguments[2], result)
    if command == 'youzan.material-category' and arguments[2] == 'move' and 'error' not in result:
        responses = result.get('results', [result])
        failed = any(response.get('code') != 0 or bool(response.get('data')) for response in responses)
    return failed


def redact_category(value, credentials):
    secrets = [credentials["sid"], credentials["csrf"], *credentials.get("cookies", {}).values()]
    if isinstance(value, dict):
        return {key: redact_category(item, credentials) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_category(item, credentials) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if isinstance(secret, str) and len(secret) >= 8:
                value = value.replace(secret, "[已隐藏凭证]")
    return value


async def run_scoped(dispatcher, context, command, arguments, scope):
    if dispatcher.shop_resolver is None or dispatcher.shop_provider is None:
        raise DispatchDenied('connection_required', '多店铺会话尚未配置。')
    user = dispatcher._check_command(context, command, arguments)
    try:
        shops = await dispatcher.shop_resolver(user, scope, prepare=False, validate=lambda: dispatcher._check_command(context, command, arguments))
    except AuthFailure as error:
        raise DispatchDenied(error.code, error.message) from None
    if not readonly_youzan(arguments) and len(shops) != 1:
        raise DispatchDenied('shop_scope_denied', '写入操作必须明确指定一家店铺。')
    rows, credentials_used = [], []
    for shop in shops:
        user = dispatcher._check_command(context, command, arguments)
        row = {key: shop[key] for key in ('kdt_id', 'shop_name')}
        try:
            # Binding must immediately precede the request: upstream SID has a current shop.
            shop = (await dispatcher.shop_resolver(user, [shop['kdt_id']], force=True,
                validate=lambda: dispatcher._check_command(context, command, arguments)))[0]
        except AuthFailure as error:
            rows.append({**row, 'error': {'code': error.code, 'message': error.message}})
            continue
        if 'error' in shop:
            rows.append({**row, 'error': shop['error']})
            continue
        credentials = shop['credentials']
        credentials_used.append(credentials)
        try:
            catalog_context = dispatcher.files.catalog_context(user, credentials['kdt_id']) if command[7:] in CATALOG_NAMES else None
            if command == 'youzan.material-upload' and 'path' in catalog_body(arguments):
                dispatcher.files.inspect_source(user, catalog_body(arguments)['path'])
            result = await dispatcher._run(arguments, user, credentials=credentials, catalog_context=catalog_context)
        except (DispatchDenied, AuthFailure) as error:
            row['error'] = {'code': error.code, 'message': error.message}
        else:
            row['data'] = result
        user = dispatcher._check_command(context, command, arguments)
        try:
            current = dispatcher.shop_provider(user, credentials['kdt_id'])
        except AuthFailure:
            raise DispatchDenied('connection_changed', '店铺会话已变化，本次查询结果未返回，请重试。') from None
        if current != credentials:
            raise DispatchDenied('connection_changed', '店铺会话已变化，本次查询结果未返回，请重试。')
        rows.append(row)
    dispatcher._check_command(context, command, arguments)
    aggregate = {'shops': rows, 'partial': any('error' in row or tool_failed(command, arguments, row['data']) for row in rows)}
    for credentials in credentials_used:
        aggregate = redact_category(aggregate, credentials)
    return aggregate


async def run_single(dispatcher, context, command, canonical):
    user = dispatcher._check_command(context, command, canonical)
    try:
        if dispatcher.shop_resolver is not None:
            current = (await dispatcher.shop_resolver(user, 'current', force=True,
                validate=lambda: dispatcher._check_command(context, command, canonical)))[0]
            if 'error' in current:
                raise AuthFailure(current['error']['code'], current['error']['message'], 409)
        credentials = dispatcher.category_provider(user)
    except AuthFailure as error:
        raise DispatchDenied(error.code, error.message) from None
    if command.startswith('youzan.') and command[7:] in CATALOG_NAMES:
        try:
            catalog_context = dispatcher.files.catalog_context(user, credentials['kdt_id'])
            if command == 'youzan.material-upload' and 'path' in catalog_body(canonical):
                dispatcher.files.inspect_source(user, catalog_body(canonical)['path'])
        except AuthFailure as error:
            raise DispatchDenied(error.code, error.message) from None
        result = await dispatcher._run(canonical, user, credentials=credentials, catalog_context=catalog_context)
    else:
        result = await dispatcher._run(canonical, user, credentials=credentials)
    user = dispatcher._check_command(context, command, canonical)
    try:
        current = dispatcher.category_provider(user)
    except AuthFailure as error:
        raise DispatchDenied('connection_changed', '有赞连接已切换或刷新，请重新查询。') from None
    if current != credentials:
        raise DispatchDenied('connection_changed', '有赞连接已切换或刷新，请重新查询。')
    return result
