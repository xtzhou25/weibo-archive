import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from archive.engine import Engine
from archive.model import normalize, options, update_visibility, visibility
from archive.store import Store
from archive.transport import Transport


class VisibilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root / 'accounts' / '123')

    def engine(self, handler):
        async def route(request):
            if request.url.path == '/api/config':
                return httpx.Response(200, json={'ok': 1, 'data': {'uid': '123'}})
            value = handler(request)
            return await value if asyncio.iscoroutine(value) else value
        def factory(cookies, report, gate):
            client = httpx.AsyncClient(transport=httpx.MockTransport(route))
            transport = Transport([], report, gate, interval=0, client=client, media_client=client)
            transport.wait = lambda seconds: asyncio.sleep(0)
            return transport
        auth = type('Auth', (), {'uid': '123', 'ready': True, 'cookies': []})()
        return Engine(self.root, auth, factory)

    async def refresh(self, engine):
        await engine.start({'visibility_only': True, 'text': False, 'images': False, 'video': False})
        await engine.task

    def post(self, post_id=1):
        post = normalize({'id': post_id, 'text_raw': 'EXISTING_TEXT', 'visible': {'type': 0},
                          'pics': [{'pid': 'saved', 'url': 'https://wx1.sinaimg.cn/saved.jpg'}]}, options({}))
        media = self.store.root / 'media'
        media.mkdir(exist_ok=True)
        item = post['media'][0]
        item.update(status='done', path=f'media/{item["id"]}.jpg')
        (self.store.root / item['path']).write_bytes(b'EXISTING_MEDIA')
        self.store.save_post(post)
        return post

    def test_modern_response_codes_and_unknown_never_default_public(self):
        for code, label in {0: '公开', 1: '仅自己可见', 5: '群可见', 6: '好友圈可见',
                            9: '仅付费会员可见', 10: '仅粉丝可见'}.items():
            result = visibility({'visible': {'type': str(code), 'list_idstr': '123'}})
            self.assertEqual(result['label'], label)
            self.assertEqual(result['raw']['list_idstr'], '123')
            self.assertTrue(result['checked_at'])
        for raw in ({}, {'visible': None}, {'visible': {}}, {'visible': {'type': True}},
                    {'visible': {'type': 2}}, {'visible': {'type': 999}}):
            self.assertEqual(visibility(raw)['state'], 'unknown')

    def test_repost_range_is_independent_and_missing_field_is_not_old_range(self):
        raw = {'id': 1, 'visible': {'type': 1}, 'retweeted_status': {'id': 2, 'visible': {'type': 0}}}
        post = normalize(raw, options({}))
        self.assertEqual(post['visibility']['type'], 1)
        self.assertEqual(post['repost']['visibility']['type'], 0)
        update_visibility(post, {'id': 1})
        self.assertEqual(post['visibility']['state'], 'unknown')
        self.assertEqual(post['repost']['visibility']['type'], 0)

    async def test_metadata_only_preserves_text_media_comments_and_timeline_checkpoint(self):
        post = self.post()
        target = self.store.root / post['media'][0]['path']
        before = (target.read_bytes(), target.stat().st_mtime_ns)
        self.store.save_job('timeline-snapshot', {'page': 8, 'cursor': 'saved'})
        self.store.mark('timeline-snapshot', '1')
        with self.store.connection() as db:
            comments_before = db.execute('SELECT * FROM comments').fetchall()
        calls = []
        def handler(request):
            calls.append(request.url.path)
            self.assertEqual(request.url.path, '/ajax/statuses/show')
            self.assertEqual(request.headers['referer'], 'https://weibo.com/')
            return httpx.Response(200, json={'id': 1, 'text_raw': 'DO_NOT_REPLACE_TEXT', 'visible': {'type': 6}})
        engine = self.engine(handler)
        await self.refresh(engine)
        saved = engine.store.post('1')
        self.assertEqual(saved['visibility']['label'], '好友圈可见')
        self.assertEqual(saved['text'], post['text'])
        self.assertEqual(saved['media'], post['media'])
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)
        self.assertEqual(engine.status['bytes'], 0)
        self.assertEqual(engine.status['phase'], 'complete')
        self.assertEqual(engine.store.job('timeline-snapshot'), {'page': 8, 'cursor': 'saved'})
        self.assertTrue(engine.store.known('timeline-snapshot', '1'))
        with engine.store.connection() as db:
            self.assertEqual(db.execute('SELECT * FROM comments').fetchall(), comments_before)
        self.assertEqual(calls, ['/ajax/statuses/show'])

    async def test_failed_scope_keeps_old_snapshot_and_retries_only_failed_posts(self):
        self.post(1)
        self.post(2)
        fail = [True]
        calls = []
        def handler(request):
            post_id = request.url.params['id']
            calls.append(post_id)
            if post_id == '2' and fail[0]:
                return httpx.Response(404)
            return httpx.Response(200, json={'id': post_id, 'visible': {'type': 10}})
        engine = self.engine(handler)
        await self.refresh(engine)
        self.assertEqual(engine.status['phase'], 'partial')
        failed = engine.store.post('2')
        self.assertEqual(failed['visibility']['type'], 0)
        self.assertEqual(failed['visibility_check']['state'], 'error')
        fail[0] = False
        restarted = self.engine(handler)
        await self.refresh(restarted)
        self.assertEqual(calls.count('1'), 1)
        self.assertEqual(calls.count('2'), 2)
        self.assertEqual(restarted.store.post('2')['visibility_check']['state'], 'ok')
        self.assertEqual(restarted.status['phase'], 'complete')

    async def test_cancelled_metadata_update_resumes_pending_posts_after_restart(self):
        self.post(1)
        self.post(2)
        started, hold, calls = asyncio.Event(), [True], []
        async def handler(request):
            post_id = request.url.params['id']
            calls.append(post_id)
            if post_id == '2' and hold[0]:
                started.set()
                await asyncio.Event().wait()
            return httpx.Response(200, json={'id': post_id, 'visible': {'type': 1}})
        engine = self.engine(handler)
        await engine.start({'visibility_only': True})
        await asyncio.wait_for(started.wait(), 5)
        await engine.cancel()
        hold[0] = False
        restarted = self.engine(handler)
        await self.refresh(restarted)
        self.assertEqual(calls.count('1'), 1)
        self.assertEqual(calls.count('2'), 2)
        self.assertEqual(restarted.status['phase'], 'complete')

    async def test_missing_scope_is_unknown_and_wrong_post_id_cannot_overwrite_snapshot(self):
        self.post()
        engine = self.engine(lambda request: httpx.Response(200, json={'id': 1}))
        await self.refresh(engine)
        self.assertEqual(engine.store.post('1')['visibility']['state'], 'unknown')
        self.assertEqual(engine.status['phase'], 'partial')
        other = self.engine(lambda request: httpx.Response(200, json={'id': 999, 'visible': {'type': 0}}))
        await self.refresh(other)
        self.assertEqual(other.store.post('1')['visibility']['state'], 'unknown')
        self.assertEqual(other.store.post('1')['visibility_check']['state'], 'error')

    async def test_incremental_timeline_updates_range_of_already_seen_post(self):
        code, media_calls = [0], []
        raw = {'id': 1, 'text_raw': 'POST', 'pics': [{'pid': 'p', 'url': 'https://wx1.sinaimg.cn/a.jpg'}]}
        def handler(request):
            if request.url.path == '/a.jpg':
                media_calls.append(request)
                return httpx.Response(200, content=b'IMAGE')
            self.assertEqual(request.url.path, '/ajax/statuses/mymblog')
            posts = [{**raw, 'visible': {'type': code[0]}}] if request.url.params['page'] == '1' else []
            return httpx.Response(200, json={'data': {'list': posts}})
        engine = self.engine(handler)
        await engine.start({'video': False})
        await engine.task
        self.assertEqual(engine.store.post('1')['visibility']['type'], 0)
        code[0] = 1
        await engine.start({'video': False})
        await engine.task
        self.assertEqual(engine.store.post('1')['visibility']['type'], 1)
        self.assertEqual(len(media_calls), 1)


if __name__ == '__main__':
    unittest.main()
