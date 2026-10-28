import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from archive.engine import Engine
from archive.media import download
from archive.model import normalize, options, merge
from archive.store import Store
from archive.comments import normalize_comments
from archive.transport import Transport


class CountedStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.reads, self.closed = chunks, 0, False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    async def aclose(self):
        self.closed = True


class VideoLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.item = {'id': 'a' * 32, 'url': 'https://f.video.weibocdn.com/v.mp4',
                     'kind': 'video', 'status': 'pending', 'path': ''}
        self.reports = []

    def transport(self, route):
        client = httpx.AsyncClient(transport=httpx.MockTransport(route))
        transport = Transport([], interval=0, client=client, media_client=client)
        self.addAsyncCleanup(transport.close)
        transport.wait = lambda seconds: asyncio.sleep(0)
        return transport

    async def fetch(self, transport, cap):
        await download(transport, self.item, self.root, lambda **value: self.reports.append(value), cap)

    def test_default_unlimited_and_invalid_settings(self):
        self.assertEqual(options({})['video_max_mb'], 500)
        self.assertEqual(options({'video_max_mb': '0'})['video_max_mb'], 0)
        for value in (-1, 1.5, True, '', None, 'nan', '1e3', 1048577):
            with self.subTest(value=value), self.assertRaises(ValueError):
                options({'video_max_mb': value})

    async def test_known_oversized_body_is_never_read_and_same_limit_reuses_skip(self):
        stream = CountedStream([b'body-must-not-be-read'])
        calls = []
        def route(request):
            calls.append(request)
            return httpx.Response(200, headers={'Content-Length': '11', 'Content-Type': 'video/mp4'}, stream=stream)
        transport = self.transport(route)
        await self.fetch(transport, 10)
        self.assertEqual(self.item['status'], 'skipped_size')
        self.assertEqual(self.item['size_bytes'], 11)
        self.assertEqual(stream.reads, 0)
        self.assertTrue(stream.closed)
        self.assertEqual(list((self.root / 'media').iterdir()), [])
        await self.fetch(transport, 10)
        self.assertEqual(len(calls), 1)
        self.assertFalse(any(row.get('bytes_delta') for row in self.reports))

    async def test_unknown_size_stops_stream_at_cap_and_discards_partial(self):
        stream = CountedStream([b'x' * (128 * 1024)] * 10)
        transport = self.transport(lambda request: httpx.Response(200, headers={'Content-Type': 'video/mp4'}, stream=stream))
        await self.fetch(transport, 256 * 1024)
        self.assertEqual(self.item['status'], 'skipped_size')
        self.assertTrue(self.item['size_is_lower_bound'])
        self.assertEqual(stream.reads, 3)
        self.assertTrue(stream.closed)
        self.assertEqual(list((self.root / 'media').iterdir()), [])
        self.assertEqual(sum(row.get('bytes_delta', 0) for row in self.reports), 384 * 1024)

    async def test_resuming_checks_full_content_range_size_before_reading(self):
        media = self.root / 'media'
        media.mkdir()
        part = media / ('a' * 32 + '.mp4.part')
        part.write_bytes(b'abc')
        part.with_suffix('.part.json').write_text(json.dumps({'validator': '"v1"'}))
        stream = CountedStream([b'defghijk'])
        def route(request):
            self.assertEqual(request.headers['Range'], 'bytes=3-')
            return httpx.Response(206, headers={'Content-Range': 'bytes 3-10/11', 'ETag': '"v1"'}, stream=stream)
        await self.fetch(self.transport(route), 10)
        self.assertEqual(stream.reads, 0)
        self.assertEqual(self.item['status'], 'skipped_size')
        self.assertEqual(list(media.iterdir()), [])

    async def test_exact_limit_downloads_and_larger_limit_retries_skipped_video(self):
        transport = self.transport(lambda request: httpx.Response(200, content=b'123456'))
        await self.fetch(transport, 5)
        self.assertEqual(self.item['status'], 'skipped_size')
        await self.fetch(transport, 6)
        self.assertEqual(self.item['status'], 'done')
        self.assertEqual((self.root / self.item['path']).read_bytes(), b'123456')
        self.assertNotIn('skip_reason', self.item)

    async def test_unlimited_retry_and_images_are_not_limited(self):
        transport = self.transport(lambda request: httpx.Response(200, content=b'123456'))
        await self.fetch(transport, 5)
        await self.fetch(transport, 0)
        self.assertEqual(self.item['status'], 'done')
        self.item = {'id': 'b' * 32, 'url': 'https://wx1.sinaimg.cn/a.jpg', 'kind': 'image', 'status': 'pending'}
        await self.fetch(transport, 1)
        self.assertEqual(self.item['status'], 'done')

    async def test_existing_complete_video_is_preserved_even_above_new_limit(self):
        media = self.root / 'media'
        media.mkdir()
        target = media / ('a' * 32 + '.mp4')
        target.write_bytes(b'EXISTING_VIDEO')
        before = target.stat().st_mtime_ns
        def route(request):
            self.fail('Completed video must not cause a network request')
        await self.fetch(self.transport(route), 1)
        self.assertEqual(self.item['status'], 'done')
        self.assertEqual(target.read_bytes(), b'EXISTING_VIDEO')
        self.assertEqual(target.stat().st_mtime_ns, before)

    def test_skips_are_counted_separately_for_posts_and_comments_after_restart(self):
        store = Store(self.root)
        raw = {'id': 1, 'page_info': {'type': 'video', 'media_info': {'stream_url': self.item['url']}}}
        post = normalize(raw, options({}))
        post['media'][0]['status'] = 'skipped_size'
        store.save_post(post)
        comment = normalize_comments([{**raw, 'id': 2}], '1', options({}))[0]
        comment['media'][0]['status'] = 'skipped_size'
        store.save_comment(comment)
        self.assertEqual(Store(self.root).counts(), {'posts': 1, 'media': 0, 'incomplete': 0, 'skipped': 2})
        comment['media'][0]['status'] = 'done'
        store.save_comment(comment)
        self.assertEqual(store.counts()['skipped'], 1)

    def test_metadata_refresh_preserves_skip_but_updates_expiring_video_link(self):
        raw = {'id': 1, 'page_info': {'type': 'video', 'object_id': 'v1',
               'media_info': {'stream_url': self.item['url'] + '?token=old'}}}
        old = normalize(raw, options({}))
        old['media'][0].update(status='skipped_size', size_bytes=2000, size_is_lower_bound=True,
                               size_limit_bytes=1000, skip_reason='size limit')
        raw['page_info']['media_info']['stream_url'] = self.item['url'] + '?token=new'
        refreshed = merge(normalize(raw, options({})), old)
        self.assertEqual(refreshed['media'][0]['status'], 'skipped_size')
        self.assertEqual(refreshed['media'][0]['size_bytes'], 2000)
        self.assertTrue(refreshed['media'][0]['url'].endswith('token=new'))
        raw['page_info']['media_info']['stream_url'] = self.item['url'].replace('v.mp4', 'replacement.mp4')
        self.assertEqual(merge(normalize(raw, options({})), old)['media'][0]['status'], 'pending')

    async def test_changed_limit_keeps_timeline_key_and_does_not_redownload_other_media(self):
        calls = []
        raw = {'id': 1, 'text_raw': 'saved post', 'pics': [{'pid': 'p', 'url': 'https://wx1.sinaimg.cn/a.jpg'}],
               'page_info': {'type': 'video', 'media_info': {'stream_url': self.item['url']}}}
        def route(request):
            calls.append(request.url.path)
            if request.url.path == '/api/config':
                return httpx.Response(200, json={'ok': 1, 'data': {'uid': '123'}})
            if request.url.path.endswith('/show'):
                return httpx.Response(200, json=raw)
            if request.url.path == '/a.jpg':
                return httpx.Response(200, content=b'image')
            if request.url.path == '/v.mp4':
                return httpx.Response(200, headers={'Content-Length': str(2 * 1024 ** 2)}, content=b'v' * (2 * 1024 ** 2))
            return httpx.Response(200, json={'data': {'list': [raw] if request.url.params['page'] == '1' else []}})
        def factory(cookies, report, gate):
            transport = self.transport(route)
            transport.report, transport.gate = report, gate
            return transport
        auth = type('Auth', (), {'uid': '123', 'ready': True, 'cookies': []})()
        engine = Engine(self.root, auth, factory)
        await engine.start({'video_max_mb': 1})
        await engine.task
        self.assertEqual(engine.status['phase'], 'complete')
        self.assertEqual(engine.status['skipped'], 1)
        self.assertEqual(engine.status['media'], 1)
        with engine.store.connection() as db:
            first_key = db.execute('SELECT key FROM jobs').fetchall()
        await engine.start({'video_max_mb': 2})
        await engine.task
        self.assertEqual(engine.status['skipped'], 0)
        self.assertEqual(engine.status['media'], 2)
        self.assertEqual(calls.count('/a.jpg'), 1)
        with engine.store.connection() as db:
            self.assertEqual(db.execute('SELECT key FROM jobs').fetchall(), first_key)


if __name__ == '__main__':
    unittest.main()
