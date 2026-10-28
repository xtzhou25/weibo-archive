import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer
from app import create_app
from archive.auth import Auth, scoped
from archive.engine import Engine
from archive.store import Store
from archive.export import export_archive
from archive.model import normalize, options


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.auth = Auth(self.directory.name)
        self.engine = Engine(self.directory.name, self.auth)
        self.client = TestClient(TestServer(create_app(self.directory.name, self.auth, self.engine)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        response = await self.client.get('/api/status')
        self.token = (await response.json())['csrf']

    async def command(self, action, body=None, headers=None):
        return await self.client.post('/api/' + action, json=body or {}, headers=headers or {'X-CSRF-Token': self.token})

    async def test_csrf_host_and_origin_guards(self):
        response = await self.client.post('/api/login', json={})
        self.assertEqual(response.status, 403)
        for headers in ({'Origin': 'https://evil.test'}, {'Host': 'evil.test'}, {'Origin': 'null'}):
            response = await self.client.get('/api/status', headers=headers)
            self.assertEqual(response.status, 403)

    async def test_javascript_type_and_status_exclude_credentials(self):
        self.auth.cookies = [{'name': 'SUB', 'value': 'PRIVATE_TEST_COOKIE', 'domain': '.weibo.com'}]
        response = await self.client.get('/static/app.js')
        self.assertEqual(response.content_type, 'application/javascript')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        response = await self.client.get('/api/status')
        self.assertNotIn('PRIVATE_TEST_COOKIE', await response.text())
        for path in ('/private/session.json', '/data/private/session.json', '/archive.sqlite', '/static/../app.py'):
            response = await self.client.get(path)
            self.assertEqual(response.status, 404)

    async def test_start_requires_verified_account_and_error_is_specific(self):
        response = await self.command('start')
        self.assertEqual(response.status, 400)
        self.assertIn('登录', (await response.json())['error'])

    async def test_login_options_qr_and_logout(self):
        self.auth.begin = AsyncMock()
        response = await self.command('login', {'persist': False, 'mode': 'qr'})
        self.assertEqual(response.status, 200)
        self.auth.begin.assert_awaited_once_with(False, 'qr')
        self.auth.qr = b'\x89PNG\r\n\x1a\n'
        response = await self.client.get('/api/qr')
        self.assertEqual(response.content_type, 'image/png')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        await self.command('logout')
        self.assertFalse(self.auth.qr)
        self.assertFalse(self.auth.path.exists())

    async def test_reader_and_export_are_accessible_only_as_archive_files(self):
        self.engine.store = Store(Path(self.directory.name) / 'accounts' / '123')
        self.engine.store.save_post(normalize({'id': 1, 'text_raw': 'Offline text'}, options({})))
        export_archive(self.engine.store, '123')
        response = await self.client.get('/reader.html')
        self.assertEqual(response.status, 200)
        self.assertIn('Offline text', await response.text())
        response = await self.client.get('/archive.json')
        self.assertEqual((await response.json())['schema_version'], 2)
        response = await self.client.get('/media/not-valid.mp4')
        self.assertEqual(response.status, 400)


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_restore_uses_mobile_config_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = Auth(directory)
            auth.cookies = [{'name': 'SUB', 'value': 'mock', 'domain': '.weibo.cn'}]
            from unittest.mock import patch
            transport = AsyncMock()
            transport.json.return_value = {'ok': 1, 'data': {'uid': '123', 'login': True}}
            with patch('archive.auth.Transport', return_value=transport):
                await auth.restore()
            transport.json.assert_awaited_once_with('https://m.weibo.cn/api/config')
            self.assertTrue(auth.ready)

    async def test_stale_official_response_cannot_replace_new_login(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = Auth(directory)
            auth.generation = 2
            response = AsyncMock(url='https://passport.weibo.com/sso/v2/qrcode/check')
            response.json.return_value = {'retcode': 20000000}
            await auth._observe(response, 1)
            self.assertEqual(auth.phase, 'signed_out')
            await auth._observe(response, 2)
            self.assertEqual(auth.phase, 'verifying')
            self.assertFalse(auth.ready)

    def test_cookie_domain_scope(self):
        cookies = [{'domain': '.weibo.com'}, {'domain': 'm.weibo.cn'}, {'domain': 'evilweibo.com'}]
        self.assertEqual(scoped(cookies), cookies[:2])


if __name__ == '__main__':
    unittest.main()
