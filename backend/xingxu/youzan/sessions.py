"""Isolated CRM sessions for authorized shops, using the existing account SID."""
import asyncio
import copy
import hashlib
import json
import time

from ..auth import AuthFailure


def sid_stamp(record):
    return hashlib.sha256(record.get('sid', {}).get('secret', '').encode()).hexdigest()


class ShopSessions:
    def __init__(self, service):
        self.service = service
        self.locks = {}

    @staticmethod
    def ready(record, entry):
        return (entry.get('status') == 'ready' and entry.get('sid_stamp') == sid_stamp(record)
            and bool(entry.get('csrf', {}).get('secret')) and bool(entry.get('category_credentials', {}).get('secret')))

    @staticmethod
    def public(record):
        sessions = record.get('shop_sessions', {})
        expired = bool(record.get('sid', {}).get('expires_at') and record['sid']['expires_at'] <= time.time())
        return [{'kdt_id': shop['id'], 'name': shop['name'], 'ready': not expired and ShopSessions.ready(record, sessions.get(shop['id'], {})),
            'updated_at': sessions.get(shop['id'], {}).get('updated_at'),
            'error': sessions.get(shop['id'], {}).get('error')} for shop in record.get('shops', [])]

    def credentials(self, record, shop_id):
        entry = record.get('shop_sessions', {}).get(shop_id, {})
        if not self.ready(record, entry):
            error = entry.get('error') or {'code': 'shop_session_missing', 'message': '该店铺的 CRM 会话尚未就绪，请刷新店铺会话。'}
            raise AuthFailure(error['code'], error['message'], 409)
        context = json.loads(self.service._decrypt(entry['category_credentials']['secret']))
        return {'sid': self.service._decrypt(record['sid']['secret']), 'csrf': self.service._decrypt(entry['csrf']['secret']),
            'kdt_id': shop_id, 'shop_name': next(shop['name'] for shop in record['shops'] if shop['id'] == shop_id),
            'cookies': context.get('cookies', {}), 'origin': context.get('origin', 'https://store.youzan.com')}

    async def collect(self, key, record, selection, *, force=False, prepare=True, validate=lambda: None):
        async with self.locks.setdefault(key, asyncio.Lock()):
            validate()
            with self.service.repo.transaction(write=False) as state:
                current = copy.deepcopy(state['connections'].get(key))
            if not current or current['revision'] != record['revision'] or sid_stamp(current) != sid_stamp(record) or current.get('job', {}).get('id') != record.get('job', {}).get('id'):
                raise AuthFailure('connection_changed', '有赞连接已变更，请重新查询。', 409)
            record = current
            # This never starts a browser or logs in; an expired SID is reported to the user.
            shops = await self.service._list_shops(record)
            validate()
            available = {shop['id']: shop for shop in shops}
            if selection == 'all':
                identifiers = list(available)
            elif selection == 'current':
                identifiers = [record['kdt_id']]
            else:
                identifiers = []
                for value in selection:
                    if value in available:
                        matches = [value]
                    else:
                        matches = [shop['id'] for shop in shops if shop['name'] == value]
                        if not matches and not value.isdigit() and len(value) >= 2:
                            matches = [shop['id'] for shop in shops if value in shop['name']]
                    if len(matches) != 1:
                        raise AuthFailure('shop_denied' if not matches else 'shop_ambiguous', '未找到唯一的授权店铺，请提供店铺 ID 或完整名称。', 403)
                    if matches[0] not in identifiers:
                        identifiers.append(matches[0])
            if not identifiers or any(identifier not in available for identifier in identifiers):
                raise AuthFailure('shop_denied', '请求的店铺不在当前有赞账号的授权店铺列表中。', 403)
            self._commit(key, record, shops=shops)
            for identifier in identifiers:
                if not prepare:
                    continue
                validate()
                entry = record.get('shop_sessions', {}).get(identifier, {})
                if not force and self.ready(record, entry):
                    continue
                # Each shop enters CRM with an independent cookie jar and an explicit kdtId.
                candidate = copy.deepcopy(record)
                candidate.update(kdt_id=identifier, _collect_category_credentials=True)
                try:
                    csrf = await self.service._crm_csrf(candidate)
                    validate()
                    now = int(time.time())
                    entry = {'status': 'ready', 'sid_stamp': sid_stamp(record), 'updated_at': now,
                        'csrf': {'secret': self.service._encrypt(csrf), 'updated_at': now},
                        'category_credentials': {'secret': self.service._encrypt(json.dumps(candidate.get('_category_session', {}))), 'updated_at': now}}
                except AuthFailure as error:
                    if error.code in ('forbidden', 'connection_changed', 'session_expired', 'identity_mismatch', 'permission_denied'):
                        raise
                    from .constants import MESSAGES
                    entry = {**entry, 'status': 'error', 'sid_stamp': sid_stamp(record), 'updated_at': int(time.time()),
                        'error': {'code': error.code, 'message': MESSAGES.get(error.code, '该店铺 CRM 会话获取失败，请稍后刷新。')}}
                self._commit(key, record, shop_id=identifier, entry=entry)
                record.setdefault('shop_sessions', {})[identifier] = entry
            with self.service.repo.transaction(write=False) as state:
                final = copy.deepcopy(state['connections'][key])
            results = []
            for identifier in identifiers:
                try:
                    results.append({'kdt_id': identifier, 'shop_name': available[identifier]['name'], 'credentials': self.credentials(final, identifier)})
                except AuthFailure as error:
                    results.append({'kdt_id': identifier, 'shop_name': available[identifier]['name'], 'error': {'code': error.code, 'message': error.message}})
            return results

    def _commit(self, key, snapshot, *, shops=None, shop_id=None, entry=None):
        with self.service.repo.transaction() as state:
            current = state['connections'].get(key)
            if not current or current['revision'] != snapshot['revision'] or sid_stamp(current) != sid_stamp(snapshot) or current.get('job', {}).get('id') != snapshot.get('job', {}).get('id'):
                raise AuthFailure('connection_changed', '有赞连接已变更，请重新查询。', 409)
            current['multi_shop'] = True
            if shops is not None:
                current.update(shops=shops, shops_updated_at=int(time.time()))
                allowed = {shop['id'] for shop in shops}
                current['shop_sessions'] = {identifier: value for identifier, value in current.get('shop_sessions', {}).items() if identifier in allowed}
            if shop_id is not None:
                current.setdefault('shop_sessions', {})[shop_id] = entry
                if shop_id == current['kdt_id'] and self.ready(current, entry):
                    current.update(csrf=entry['csrf'], category_credentials=entry['category_credentials'])
