import json
import tempfile
import unittest
from pathlib import Path

import httpx
from archive.transport import Transport, Throttled, ArchiveError, LoginRequired, backoff, allowed
from archive.media import download


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def transport(self, route, cookies=None):
        jar = httpx.Cookies()
        for cookie in cookies or []:
            jar.set(cookie['name'], cookie['value'], domain=cookie['domain'])
        client = httpx.AsyncClient(transport=httpx.MockTransport(route), cookies=jar)
        media = httpx.AsyncClient(transport=httpx.MockTransport(route))
        transport = Transport([], client=client, media_client=media, interval=0)
        self.addAsyncCleanup(transport.close)
        delays = []
        async def wait(delay):
            delays.append(delay)
        transport.wait = wait
        transport.delays = delays
        return transport

    def test_url_allowlist_and_retry_after(self):
        for url in ('http://weibo.com/', 'https://weibo.com.evil.test/', 'https://127.0.0.1/', 'https://user@weibo.com/', 'https://weibo.com:abc/'):
            self.assertFalse(allowed(url, True))
        self.assertGreaterEqual(backoff({'Retry-After': '800'}, 0), 800)
        self.assertEqual(backoff({'Retry-After': 'Thu, 01 Jan 1970 00:02:00 GMT'}, 0, now=0), 120)

    async def test_429_exponential_retry_then_success(self):
        requests = []
        def route(request):
            requests.append(request)
            return httpx.Response(429, headers={'Retry-After': '70'}) if len(requests) < 3 else httpx.Response(200, json={'ok': 1})
        transport = await self.transport(route)
        self.assertEqual(await transport.json('https://weibo.com/api'), {'ok': 1})
        self.assertEqual(len(requests), 3)
        self.assertEqual(sum(delay >= 70 for delay in transport.delays), 2)

    async def test_rate_limit_exhaustion_not_empty_history(self):
        requests = []
        def route(request):
            requests.append(request)
            return httpx.Response(429)
        transport = await self.transport(route)
        with self.assertRaises(Throttled):
            await transport.json('https://weibo.com/api')
        self.assertEqual(len(requests), 7)

    async def test_auth_non_json_and_redirect_errors_are_explicit(self):
        for response, error in ((httpx.Response(401), LoginRequired),
                                (httpx.Response(200, text='<html>login</html>'), ArchiveError),
                                (httpx.Response(302, headers={'Location': 'https://127.0.0.1/private'}), ArchiveError)):
            transport = await self.transport(lambda request: response)
            with self.assertRaises(error):
                await transport.json('https://weibo.com/api')

    async def test_media_client_does_not_receive_account_cookie(self):
        requests = []
        def route(request):
            requests.append(request)
            return httpx.Response(200, content=b'image')
        transport = await self.transport(route, [{'name': 'SUB', 'value': 'test-only', 'domain': '.weibo.com'}])
        response = await transport.request('https://weibo.com/a.jpg', media=True)
        await response.aclose()
        self.assertNotIn('cookie', requests[0].headers)

    async def test_partial_file_resumes_and_complete_file_is_reused(self):
        requests = []
        def route(request):
            requests.append(request)
            return httpx.Response(206, headers={'Content-Range': 'bytes 3-5/6', 'ETag': '"v1"', 'Content-Type': 'image/jpeg'}, content=b'def')
        transport = await self.transport(route)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'media').mkdir()
            item = {'id': 'a' * 32, 'url': 'https://wx1.sinaimg.cn/a.jpg', 'kind': 'image', 'status': 'pending'}
            part = root / 'media' / ('a' * 32 + '.jpg.part')
            part.write_bytes(b'abc')
            part.with_suffix('.part.json').write_text(json.dumps({'validator': '"v1"'}))
            await download(transport, item, root, lambda **kw: None)
            self.assertEqual((root / item['path']).read_bytes(), b'abcdef')
            self.assertEqual(requests[0].headers['range'], 'bytes=3-')
            await download(transport, item, root, lambda **kw: None)
            self.assertEqual(len(requests), 1)

    async def test_server_ignoring_range_restarts_file(self):
        transport = await self.transport(lambda request: httpx.Response(200, headers={'Content-Type': 'image/jpeg'}, content=b'new'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'media').mkdir()
            item = {'id': 'b' * 32, 'url': 'https://wx1.sinaimg.cn/a.jpg', 'kind': 'image', 'status': 'pending'}
            part = root / 'media' / ('b' * 32 + '.jpg.part')
            part.write_bytes(b'old')
            part.with_suffix('.part.json').write_text(json.dumps({'validator': '"v1"'}))
            await download(transport, item, root, lambda **kw: None)
            self.assertEqual((root / item['path']).read_bytes(), b'new')

    async def test_changed_validator_never_splices_versions(self):
        calls = []
        def route(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(206, headers={'ETag': '"new"', 'Content-Range': 'bytes 3-5/6'}, content=b'xyz')
            return httpx.Response(200, headers={'Content-Type': 'image/jpeg'}, content=b'whole-new')
        transport = await self.transport(route)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'media').mkdir()
            item = {'id': 'c' * 32, 'url': 'https://wx1.sinaimg.cn/a.jpg', 'kind': 'image', 'status': 'pending'}
            part = root / 'media' / ('c' * 32 + '.jpg.part')
            part.write_bytes(b'old')
            part.with_suffix('.part.json').write_text(json.dumps({'validator': '"old"'}))
            await download(transport, item, root, lambda **kw: None)
            self.assertEqual((root / item['path']).read_bytes(), b'whole-new')


if __name__ == '__main__':
    unittest.main()
