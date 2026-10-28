import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from archive.comments import normalize_comments
from archive.engine import Engine
from archive.model import normalize, options
from archive.store import Store
from archive.transport import Transport


class Auth:
    uid = '123'
    cookies = []
    ready = True


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_only_errors_and_refreshes_comment_photo_without_touching_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'accounts' / '123')
            post = normalize({'id': 1, 'text': 'saved', 'pics': [{'url': 'https://wx1.sinaimg.cn/old.jpg'}],
                              'page_info': {'type': 'video', 'media_info': {'stream_url': 'http://f.video.weibocdn.com/new.mp4'}}}, options({}))
            image, video = post['media']
            image.update(status='done', path='media/' + image['id'] + '.jpg')
            (store.root / 'media').mkdir()
            target = store.root / image['path']
            target.write_bytes(b'EXISTING')
            stamp = target.stat().st_mtime_ns
            video.update(status='error', error='old')
            store.save_post(post)
            comment = normalize_comments([{'id': 20, 'rootid': 10, 'text': 'keep text',
                'pics': [{'large': {'url': 'https://ss1.sinaimg.cn/large/&690'}}]}], '1', options({}))[0]
            comment['media'][0].update(status='error', error='404')
            old_id = comment['media'][0]['id']
            store.save_comment(comment)
            store.prepare_comments('1')
            before = store.comment_jobs('1')
            calls = []
            def route(request):
                calls.append(str(request.url))
                if request.url.path == '/api/config':
                    return httpx.Response(200, json={'ok': 1, 'data': {'uid': '123'}})
                if request.url.path.endswith('/show'):
                    return httpx.Response(200, json={'id': 1, 'page_info': {'type': 'video',
                        'media_info': {'stream_url': 'http://f.video.weibocdn.com/new.mp4?fresh=yes'}}})
                if request.url.path.endswith('buildComments'):
                    return httpx.Response(200, json={'ok': 1, 'max_id': 0, 'data': [{
                        'id': 20, 'rootid': 10, 'text': 'different metadata', 'url_struct': [{
                            'url_type': 39, 'pic_infos': {'real': {'large': {'url': 'http://wx2.sinaimg.cn/large/real.jpg'}}},
                            'pic_ids': ['real']}]}]})
                if request.url.path in ('/new.mp4', '/large/real.jpg'):
                    self.assertEqual(request.url.scheme, 'https')
                    if request.url.path == '/new.mp4':
                        self.assertEqual(request.url.params.get('fresh'), 'yes')
                    return httpx.Response(200, content=b'NEW')
                self.fail('Unexpected request: ' + request.url.path)
            def factory(cookies, report, gate):
                client = httpx.AsyncClient(transport=httpx.MockTransport(route))
                result = Transport([], report, gate, interval=0, client=client, media_client=client)
                result.wait = lambda seconds: asyncio.sleep(0)
                return result
            engine = Engine(directory, Auth(), factory)
            await engine.start({'failed_only': True})
            await engine.task
            self.assertEqual(engine.status['retry_recovered'], 2)
            self.assertEqual(store.counts()['incomplete'], 0)
            saved = store.comments_for_post('1')[0]
            self.assertEqual(saved['text'], 'keep text')
            self.assertEqual(saved['media'][0]['id'], old_id)
            self.assertEqual(store.comment_jobs('1'), before)
            self.assertEqual(target.read_bytes(), b'EXISTING')
            self.assertEqual(target.stat().st_mtime_ns, stamp)
            self.assertFalse(any('/old.jpg' in url for url in calls))
            calls.clear()
            await engine.start({'failed_only': True})
            await engine.task
            self.assertEqual(engine.status['retry_total'], 0)
            self.assertEqual(len(calls), 1)
            self.assertIn('/api/config', calls[0])
            self.assertEqual(store.comment_jobs('1'), before)
            calls.clear()
            await engine.start({'failed_only': True})
            await engine.task
            self.assertEqual(engine.status['retry_total'], 0)
            self.assertEqual(len(calls), 1)
            self.assertIn('/api/config', calls[0])
            self.assertEqual(store.comment_jobs('1'), before)

    def test_modes_are_exclusive(self):
        with self.assertRaises(ValueError):
            options({'failed_only': True, 'comments_only': True})

    def test_comment_pic_infos_replaces_mobile_placeholder(self):
        raw = {'id': 20, 'pics': [{'url': 'https://ss1.sinaimg.cn/large/&690'}],
               'url_struct': [{'url_type': 39, 'pic_infos': {'real': {'large': {'url': 'https://wx2.sinaimg.cn/real.jpg'}}}}]}
        records = normalize_comments([raw], '1', options({}))
        self.assertEqual(len(records[0]['media']), 1)
        self.assertEqual(records[0]['media'][0]['url'], 'https://wx2.sinaimg.cn/real.jpg')
