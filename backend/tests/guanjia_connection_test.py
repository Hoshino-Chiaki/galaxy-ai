import os
import tempfile
import unittest
from types import SimpleNamespace
import httpx
from cryptography.fernet import Fernet
from xingxu.guanjia.service import GuanjiaService
from xingxu.guanjia.login import LoginError
from xingxu.auth import AuthFailure


class ConnectionTest(unittest.TestCase):
    def test_encrypted_user_isolation_and_failed_login(self):
        os.environ.pop('GALAXY_DATABASE_CONFIG', None)
        with tempfile.TemporaryDirectory() as directory:
            def runner(account, password, **kwargs):
                if password == 'bad':
                    raise LoginError('登录失败：密码错误')
                client = httpx.Client()
                client.cookies.set('ASP.NET_SessionId', 'test-session', domain='example.com')
                return SimpleNamespace(session=client, sid='test-session', cookie='secret-cookie', domain='http://example.com', database='test-db')
            service = GuanjiaService(directory, Fernet(Fernet.generate_key()), login_runner=runner)
            a = {'id': 'a', 'workspace_id': 'w', 'permissions': ['guanjia:configure']}
            b = {**a, 'id': 'b'}
            value = service.connect(a, '9005', 'test-password')
            self.assertTrue(value['cookie']['present'] and value['sid']['present'])
            self.assertFalse(service.summary(b)['connected'])
            self.assertEqual(service.credentials(a)['sid'], 'test-session')
            with service.repo.transaction(write=False) as state:
                text = str(state)
                self.assertNotIn('test-password', text)
                self.assertNotIn('secret-cookie', text)
                self.assertNotIn('test-session', text)
            service.connect(a, '9005', '')
            with self.assertRaises(AuthFailure):
                service.connect(a, '9005', 'bad')
            self.assertTrue(service.summary(a)['connected'])
            with self.assertRaises(AuthFailure):
                service.credentials(b)
            with self.assertRaises(AuthFailure):
                service.credentials({**a, 'permissions': []})


if __name__ == '__main__':
    unittest.main()
