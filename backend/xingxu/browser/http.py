"""Isolated cookie sessions and bounded page reads without automatic redirects."""
import httpx


class ResponseLimitExceeded(Exception):
    pass


def open_session(*, cookies=None, transport=None, timeout=25):
    # Redirect destinations and credential domains belong to the integration.
    return httpx.AsyncClient(transport=transport, trust_env=False, timeout=timeout,
                             follow_redirects=False, cookies=cookies)


async def read_page(client, url, *, headers=None, params=None, limit=6 * 1024 * 1024):
    async with client.stream('GET', url, headers=headers, params=params) as response:
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > limit:
                raise ResponseLimitExceeded()
            chunks.append(chunk)
        return response.status_code, response.headers, b''.join(chunks).decode('utf-8', 'replace')
