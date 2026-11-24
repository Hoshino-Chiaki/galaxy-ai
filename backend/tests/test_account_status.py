"""Account status mirrors channel settings; it never logs in or probes upstream."""
from types import SimpleNamespace
import time
import pytest
from xingxu.account_info import AccountInfo
from xingxu.auth import AuthFailure

USER = {'id': 'own', 'workspace_id': 'fixture', 'username': 'fixture', 'role': 'employee',
        'permissions': ['youzan:configure', 'guanjia:configure', 'feishu:configure']}


def provider(value):
    def summary(user):
        assert user['id'] == 'own'
        return value
    return SimpleNamespace(summary=summary)


def test_guanjia_online_matches_the_channel_page():
    info = AccountInfo(guanjia=provider({'account': 'own-channel', 'connected': True})).summary(USER)['channels']['guanjia']
    assert info['status'] == 'connected' and info['checked_live'] is False
    assert info['status_label'].startswith('已连接')


@pytest.mark.parametrize('refresh_expires', [time.time() + 3600, time.time() - 3600, None])
def test_feishu_connected_is_not_reinterpreted_as_expired(refresh_expires):
    value = {'configured': True, 'connected': True, 'expires_at': time.time() - 60,
             'refresh_expires_at': refresh_expires, 'identity': {'name': 'Fixture'}}
    info = AccountInfo(feishu=provider(value)).summary(USER)['channels']['feishu']
    assert info['status'] == 'connected'


def test_one_failed_youzan_shop_does_not_make_the_channel_expired():
    value = {'configured': True, 'account': 'own-channel', 'sid': {'present': True, 'expired': False},
             'csrf': {'present': True}, 'shop_sessions': [{'ready': True}] * 6 + [{'ready': False}]}
    info = AccountInfo(youzan=provider(value)).summary(USER)['channels']['youzan']
    assert info['status'] == 'connected' and info['ready_shops'] == 6 and info['total_shops'] == 7


def test_cached_youzan_expiry_alone_cannot_assert_a_live_login_failure():
    value = {'configured': True, 'account': 'own-channel', 'sid': {'present': True, 'expired': True}, 'csrf': {'present': False}}
    info = AccountInfo(youzan=provider(value)).summary(USER)['channels']['youzan']
    assert info['status'] == 'unknown' and info['checked_live'] is False
    assert '状态待确认' in info['status_label']


@pytest.mark.parametrize('value,expected', [
    ({'configured': False, 'sid': {}}, 'not_configured'),
    ({'configured': True, 'sid': {'present': False}}, 'not_connected'),
    ({'configured': True, 'sid': {'present': True, 'expired': False}, 'csrf': {'present': False}}, 'partial'),
])
def test_unconfigured_disconnected_and_partial_are_distinct(value, expected):
    info = AccountInfo(youzan=provider(value)).summary(USER)['channels']['youzan']
    assert info['status'] == expected


def test_status_lookup_does_not_call_login_refresh_or_business_requests():
    class Passive:
        def summary(self, user):return {'connected': True, 'account': 'own-channel'}
        def connect(self, *_):raise AssertionError('Login must not run')
        def credentials(self, *_):raise AssertionError('Credentials must not be fetched')
        def reconnect(self, *_):raise AssertionError('Recovery must not run')
    assert AccountInfo(guanjia=Passive()).summary(USER)['channels']['guanjia']['status'] == 'connected'


def test_status_read_failure_is_not_reported_as_expiry():
    def fail(_):raise AuthFailure('forbidden', 'private detail')
    info = AccountInfo(youzan=SimpleNamespace(summary=fail)).summary(USER)['channels']['youzan']
    assert info['status'] == 'unavailable' and 'private detail' not in str(info)


def test_another_channels_permission_is_not_inherited():
    def forbidden(_):raise AssertionError('Unpermitted provider must not be read')
    info = AccountInfo(youzan=SimpleNamespace(summary=forbidden)).summary({**USER, 'permissions': []})['channels']['youzan']
    assert info['status'] == 'not_enabled'
