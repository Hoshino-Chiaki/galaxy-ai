"""Read-only DeepSeek billing metadata; provider credentials stay on the server."""
import copy
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
import httpx
from cryptography.fernet import InvalidToken
from .auth import AuthFailure


async def deepseek_balance(ai, identifier):
    with ai.config.transaction(write=False) as state:
        provider = copy.deepcopy(state['providers'].get(str(identifier)))
    if not provider:
        raise AuthFailure('not_found', 'AI 连接不存在。', 404)
    url = urlsplit(provider['base_url'])
    if (url.scheme != 'https' or url.hostname != 'api.deepseek.com' or url.port not in (None, 443)
            or url.username or url.password or url.query or url.fragment
            or url.path.rstrip('/') not in ('', '/v1', '/chat/completions', '/v1/chat/completions')):
        raise AuthFailure('balance_unsupported', '余额查询仅支持 DeepSeek 官方 API 连接。', 422)
    try:
        secret = ai.cipher.decrypt(provider['encrypted_api_key'].encode()).decode()
    except (InvalidToken, KeyError):
        raise AuthFailure('key_unavailable', 'AI 凭证无法读取，请重新保存密钥。', 503) from None
    try:
        async with httpx.AsyncClient(transport=ai.transport, timeout=8, follow_redirects=False, trust_env=False) as client:
            response = await client.get('https://api.deepseek.com/user/balance', headers={'Authorization': 'Bearer ' + secret})
        if response.status_code in (401, 403):
            raise AuthFailure('balance_unauthorized', 'DeepSeek 拒绝余额查询，请检查 API Key。', 502)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError()
        if type(value.get('is_available')) is not bool or not isinstance(value.get('balance_infos'), list):
            raise ValueError()
        balances = []
        for item in value['balance_infos']:
            if item.get('currency') not in ('CNY', 'USD'):
                raise ValueError()
            result = {'currency': item['currency']}
            for field in ('total_balance', 'granted_balance', 'topped_up_balance'):
                amount = Decimal(str(item[field]))
                if not amount.is_finite():
                    raise ValueError()
                result[field] = str(item[field])
            balances.append(result)
        return {'is_available': value['is_available'], 'balance_infos': balances, 'checked_at': int(time.time())}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, InvalidOperation):
        raise AuthFailure('balance_unavailable', '暂时无法读取 DeepSeek 余额，请稍后刷新。', 502) from None
