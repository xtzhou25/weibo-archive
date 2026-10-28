import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx
from archive.engine import Engine
from archive.transport import Transport


class FakeAuth:
    uid = '123'
    cookies = [{'name': 'SUB', 'value': 'mock-only', 'domain': '.weibo.cn'}]
    ready = True


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def engine(self, route):
        def factory(cookies, report, gate):
            client = httpx.AsyncClient(transport=httpx.MockTransport(route))
            transport = Transport(cookies, report, gate, interval=0, client=client, media_client=client)
            async def wait(seconds):
                await asyncio.sleep(0)
            transport.wait = wait
            return transport
        return Engine(Path(self.directory.name), FakeAuth(), factory)

    async def run_engine(self, engine, config=None):
        await engine.start(config or {'text': True, 'images': False, 'video': False})
        await engine.task

    async def test_restart_from_checkpoint_and_then_incremental(self):
        pages, fail = [], [True]
        def route(request):
            if request.url.path == '/api/config':
                return httpx.Response(200, json={'ok': 1, 'data': {'login': True, 'uid': '123'}})
            page = int(request.url.params['page'])
            pages.append(page)
            if page == 2 and fail[0]:
                return httpx.Response(200, json={'ok': 0})
            return httpx.Response(200, json={'data': {'list': [{'id': page, 'text': str(page)}] if page < 3 else []}})
        first = self.engine(route)
        await self.run_engine(first)
        self.assertEqual(first.status['phase'], 'error')
        self.assertEqual(first.store.counts()['posts'], 1)
        fail[0], pages[:] = False, []
        restarted = self.engine(route)
        await self.run_engine(restarted)
        self.assertEqual(restarted.status['phase'], 'complete')
        self.assertEqual(restarted.store.counts()['posts'], 2)
        self.assertEqual(pages, [1, 2, 3, 3])
        pages.clear()
        await self.run_engine(restarted)
        self.assertEqual(pages, [1, 2])
        self.assertEqual(restarted.store.counts()['posts'], 2)

    async def test_mobile_timeline_fallback_preserves_repost(self):
        def route(request):
            if request.url.path == '/api/config':
                return httpx.Response(200, json={'ok': 1, 'data': {'uid': '123', 'login': True}})
            if request.url.path.endswith('mymblog'):
                return httpx.Response(403)
            posts = [{'card_type': 9, 'mblog': {'id': 1, 'text': 'own', 'retweeted_status': {'id': 2, 'text': 'original'}}}] if request.url.params['page'] == '1' else []
            return httpx.Response(200, json={'ok': 1, 'data': {'cards': posts}})
        engine = self.engine(route)
        await self.run_engine(engine)
        self.assertEqual(engine.status['phase'], 'complete')
        self.assertEqual(engine.store.post('1')['repost']['text'], 'original')

    async def test_media_failure_does_not_hide_posts_and_retries_next_run(self):
        requests, working = [], [False]
        post = {'id': 1, 'text': 'with image', 'pics': [{'pid': 'p', 'url': 'https://wx1.sinaimg.cn/a.jpg'}]}
        def route(request):
            requests.append(request.url.path)
            if request.url.path == '/api/config':
                return httpx.Response(200, json={'ok': 1, 'data': {'uid': '123'}})
            if request.url.path.endswith('/show'):
                return httpx.Response(200, json=post)
            if request.url.path == '/a.jpg':
                return httpx.Response(200, headers={'Content-Type': 'image/jpeg'}, content=b'image') if working[0] else httpx.Response(404)
            return httpx.Response(200, json={'data': {'list': [post, {'id': 2, 'text': 'after failure'}] if request.url.params['page'] == '1' else []}})
        engine = self.engine(route)
        config = {'text': True, 'images': True, 'video': False}
        await self.run_engine(engine, config)
        self.assertEqual(engine.status['phase'], 'partial')
        self.assertEqual(engine.store.counts()['posts'], 2)
        requests.clear()
        working[0] = True
        await self.run_engine(engine, config)
        self.assertIn('/a.jpg', requests)
        self.assertEqual(engine.status['phase'], 'complete')
        self.assertEqual(engine.store.counts()['incomplete'], 0)

    async def test_cancel_leaves_post_and_checkpoint_for_resume(self):
        started = asyncio.Event()
        def route(request):
            if request.url.path == '/api/config':
                return httpx.Response(200, json={'ok': 1, 'data': {'uid': '123'}})
            return httpx.Response(200, json={'data': {'list': [{'id': 1, 'text': 'saved before download'}]}})
        engine = self.engine(route)
        async def hold(*args):
            started.set()
            await asyncio.Event().wait()
        engine.save_media = hold
        await engine.start({'text': True, 'images': False, 'video': False})
        await asyncio.wait_for(started.wait(), 5)
        await engine.cancel()
        self.assertEqual(engine.status['phase'], 'stopped')
        self.assertEqual(engine.store.post('1')['text'], 'saved before download')
        self.assertTrue((engine.store.root / 'reader.html').exists())

    async def test_identity_change_never_backs_up_wrong_account(self):
        engine = self.engine(lambda request: httpx.Response(200, json={'ok': 1, 'data': {'uid': '999'}}))
        await self.run_engine(engine)
        self.assertEqual(engine.status['phase'], 'error')
        self.assertIsNone(engine.store)


if __name__ == '__main__':
    unittest.main()
