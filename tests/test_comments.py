import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from archive.comments import CommentBackup, normalize_comments, parse_page
from archive.engine import Engine
from archive.export import export_archive
from archive.model import normalize, options
from archive.store import Store
from archive.transport import Transport, ArchiveError, Throttled


def root_page(items, cursor=0):
    return httpx.Response(200, json={'ok': 1, 'data': {'data': items, 'max_id': cursor, 'max_id_type': 0}})


def child_page(items, cursor=0, total=None):
    data = {'ok': 1, 'data': items, 'max_id': cursor, 'max_id_type': 0}
    if total is not None:
        data['total_number'] = total
    return httpx.Response(200, json=data)


class CommentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.config = options({'comments': True, 'images': False, 'video': False})
        self.post = normalize({'id': 1, 'text': 'original post'}, self.config)
        self.store.save_post(self.post)

    def worker(self, route, store=None, config=None, owner_uid=None):
        client = httpx.AsyncClient(transport=httpx.MockTransport(route))
        transport = Transport([], client=client, media_client=client, interval=0)
        async def wait(seconds):
            await asyncio.sleep(0)
        transport.wait = wait
        self.addAsyncCleanup(transport.close)
        async def gate():
            await asyncio.sleep(0)
        worker = CommentBackup(store or self.store, transport, config or self.config, lambda **kw: None, gate, owner_uid=owner_uid)
        worker.prepare(self.post)
        return worker

    def test_normalize_explicit_and_nested_multilevel_links(self):
        records = normalize_comments([{'id': 10, 'text': 'root', 'comments': [
            {'id': 11, 'text': 'child', 'comments': [{'id': 12, 'reply_comment': {'id': 11}, 'text': 'deep'}]}]}], '1', self.config)
        self.assertEqual([record['root_id'] for record in records], ['10'] * 3)
        self.assertEqual([record['parent_id'] for record in records], [None, '10', '11'])
        self.assertEqual(records[1]['parent_relation'], 'nested')

    def test_missing_parent_is_preserved_as_unknown(self):
        record = normalize_comments([{'id': 11, 'text': 'reply', 'user': {'id': 99}}], '1', self.config, '10')[0]
        self.assertIsNone(record['parent_id'])
        self.assertEqual(record['root_id'], '10')
        self.assertEqual(record['parent_relation'], 'thread_only')

    def test_mobile_flat_preview_does_not_invent_a_direct_parent(self):
        rows = normalize_comments([{'id': 10, 'comments': [
            {'id': 12, 'rootid': '10', 'rid': 'tracking-not-a-comment',
             'text': '回复@某人:回复内容', 'reply_original_text': '<b>引用内容</b>'}]}], '1', self.config)
        self.assertEqual(rows[1]['root_id'], '10')
        self.assertIsNone(rows[1]['parent_id'])
        self.assertEqual(rows[1]['parent_relation'], 'thread_only')
        self.assertEqual(rows[1]['reply_to_author'], '某人')
        self.assertEqual(rows[1]['reply_original_text'], '引用内容')

    def test_missing_or_invalid_cursor_is_never_end_of_history(self):
        for payload in ({'data': {'data': []}}, {'data': {'data': [], 'max_id': None}},
                        {'data': {'data': [], 'max_id': -1}}, {'data': {}}):
            with self.assertRaises(ArchiveError):
                parse_page(payload, 'root')

    async def test_all_pages_nested_threads_and_export_relationships(self):
        requests = []
        def route(request):
            requests.append((request.url.path, dict(request.url.params)))
            cursor = request.url.params['max_id']
            if request.url.path.endswith('/hotflow'):
                return root_page([{'id': 10, 'total_number': 2, 'text': 'root',
                                   'comments': [{'id': 11, 'reply_id': 10, 'total_number': 1, 'text': 'child'}]}], 100) if cursor == '0' else root_page([{'id': 20, 'total_number': 0, 'text': 'second root'}])
            target = request.url.params['cid']
            if target == '10':
                return child_page([{'id': 11, 'reply_id': 10, 'total_number': 1, 'text': 'child'}], 200, 2) if cursor == '0' else child_page([{'id': 12, 'reply_comment': {'id': 11}, 'text': 'deep'}], 0, 2)
            return child_page([{'id': 12, 'reply_comment': {'id': 11}, 'text': 'deep'}], 0, 1)
        worker = self.worker(route)
        await worker.run()
        self.assertEqual(self.store.comment_counts()['comments'], 4)
        records = {record['id']: record for record in self.store.comments_for_post('1')}
        self.assertEqual(records['12']['parent_id'], '11')
        self.assertEqual(records['12']['root_id'], '10')
        self.assertEqual(self.store.comment_status('1')['state'], 'complete')
        export_archive(self.store, '123')
        payload = json.loads((self.store.root / 'archive.json').read_text('utf-8'))
        self.assertEqual(len(payload['comments']), 4)
        self.assertEqual(payload['posts'][0]['comment_backup']['coverage'], 'api_exhausted')
        self.assertTrue(any(params.get('cid') == '11' for _, params in requests))

    async def test_root_checkpoint_resumes_after_error_without_restarting_first_page(self):
        failing, calls = [True], []
        def route(request):
            cursor = request.url.params['max_id']
            calls.append(cursor)
            if cursor == '0':
                return root_page([{'id': 10, 'total_number': 0}], 100)
            if failing[0]:
                return httpx.Response(200, json={'ok': 0, 'msg': 'refused'})
            return root_page([{'id': 20, 'total_number': 0}])
        await self.worker(route).run()
        self.assertEqual(self.store.comment_job('1', 'root', '1')['cursor'], '100')
        self.assertEqual(self.store.comment_counts()['comments'], 1)
        calls.clear(); failing[0] = False
        restored = Store(self.directory.name)
        await self.worker(route, restored).run()
        self.assertEqual(calls, ['100'])
        self.assertEqual(restored.comment_counts()['comments'], 2)
        self.assertEqual(restored.comment_status('1')['state'], 'complete')

    async def test_reply_checkpoint_survives_cancellation(self):
        blocked = asyncio.Event()
        first = [True]
        calls = []
        async def route(request):
            cursor = request.url.params['max_id']
            calls.append((request.url.path, cursor))
            if request.url.path.endswith('/hotflow'):
                return root_page([{'id': 10, 'total_number': 2}])
            if cursor == '0':
                return child_page([{'id': 11, 'reply_id': 10}], 200, 2)
            if first[0]:
                blocked.set()
                await asyncio.Event().wait()
            return child_page([{'id': 12, 'reply_id': 11}], 0, 2)
        worker = self.worker(route)
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(blocked.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.store.comment_job('1', 'replies', '10')['cursor'], '200')
        first[0] = False; calls.clear()
        await self.worker(route, Store(self.directory.name)).run()
        self.assertEqual(calls, [('/comments/hotFlowChild', '200')])
        self.assertEqual(self.store.comment_status('1')['state'], 'complete')

    async def test_incremental_discovers_deep_new_hot_page_and_new_replies(self):
        cycle = [1]
        calls = []
        def route(request):
            cursor = request.url.params['max_id']
            calls.append((request.url.path, cursor))
            if request.url.path.endswith('/hotflow'):
                if cursor == '0':
                    return root_page([{'id': 10, 'total_number': cycle[0]}], 100)
                return root_page([] if cycle[0] == 1 else [{'id': 20, 'total_number': 0}])
            replies = [{'id': 11, 'reply_id': 10}]
            if cycle[0] == 2:
                replies.append({'id': 12, 'reply_id': 11})
            return child_page(replies, total=len(replies))
        await self.worker(route).run()
        self.assertEqual(self.store.comment_counts()['comments'], 2)
        cycle[0] = 2; calls.clear()
        await self.worker(route).run()
        self.assertIn(('/comments/hotflow', '100'), calls)
        self.assertEqual(self.store.comment_counts()['comments'], 4)
        self.assertEqual(self.store.comment_job('1', 'root', '1')['cycle'], 2)

    async def test_rate_limit_exhaustion_preserves_queue(self):
        calls = []
        def route(request):
            calls.append(request)
            return httpx.Response(429)
        worker = self.worker(route)
        with self.assertRaises(Throttled):
            await worker.run()
        self.assertEqual(len(calls), 7)
        self.assertEqual(self.store.comment_job('1', 'root', '1')['cursor'], '0')
        self.assertEqual(self.store.comment_counts()['comments'], 0)

    async def test_repeated_cursor_marks_partial_instead_of_complete(self):
        worker = self.worker(lambda request: root_page([{'id': 10, 'total_number': 0}], 100))
        await worker.run()
        self.assertEqual(self.store.comment_status('1')['state'], 'error')
        self.assertEqual(self.store.comment_job('1', 'root', '1')['cursor'], '100')

    async def test_reported_reply_count_gap_remains_retryable(self):
        full, root_calls = [False], []
        def route(request):
            if request.url.path.endswith('/hotflow'):
                root_calls.append(1)
                return root_page([{'id': 10, 'total_number': 2}])
            replies = [{'id': 11, 'reply_id': 10}]
            if full[0]:
                replies.append({'id': 12, 'reply_id': 11})
            return child_page(replies, total=2)
        await self.worker(route).run()
        self.assertEqual(self.store.comment_status('1')['state'], 'partial')
        full[0] = True
        await self.worker(route).run()
        self.assertEqual(len(root_calls), 1)
        self.assertEqual(self.store.comment_status('1')['state'], 'complete')

    def test_page_records_and_checkpoint_commit_atomically(self):
        self.store.prepare_comments('1')
        job = self.store.comment_job('1', 'root', '1')
        job['cursor'] = '100'
        record = normalize_comments([{'id': 10, 'total_number': 0}], '1', self.config)[0]
        with self.assertRaises(KeyError):
            self.store.commit_comment_page(job, [record, {'post_id': '1'}])
        self.assertEqual(self.store.comment_counts()['comments'], 0)
        self.assertEqual(self.store.comment_job('1', 'root', '1')['cursor'], '0')

    async def test_comments_only_does_not_touch_timeline_or_existing_media(self):
        account = Store(Path(self.directory.name) / 'accounts' / '123')
        account.save_post(self.post)
        calls = []
        def route(request):
            calls.append(request.url.path)
            if request.url.path == '/api/config':
                return httpx.Response(200, json={'ok': 1, 'data': {'uid': '123', 'login': True}})
            if request.url.path == '/comments/hotflow':
                return root_page([{'id': 10, 'total_number': 0, 'text': 'saved comment'}])
            raise AssertionError('Unexpected request outside comment-only scope')
        def factory(cookies, report, gate):
            client = httpx.AsyncClient(transport=httpx.MockTransport(route))
            transport = Transport([], client=client, media_client=client, interval=0, report=report, gate=gate)
            transport.wait = lambda seconds: asyncio.sleep(0)
            return transport
        auth = type('Auth', (), {'ready': True, 'uid': '123', 'cookies': []})()
        engine = Engine(self.directory.name, auth, factory)
        await engine.start({'comments_only': True, 'images': False, 'video': False})
        await engine.task
        self.assertEqual(engine.status['phase'], 'complete')
        self.assertEqual(engine.status['comments'], 1)
        self.assertNotIn('/ajax/statuses/mymblog', calls)
        self.assertEqual(account.post('1')['text'], 'original post')

    async def test_comment_image_download_is_reused_across_incremental_runs(self):
        downloads = []
        config = {**self.config, 'images': True}
        def route(request):
            if request.url.path == '/comment.png':
                downloads.append(1)
                return httpx.Response(200, headers={'Content-Type': 'image/png'}, content=b'local-test-image')
            return root_page([{'id': 10, 'total_number': 0, 'pic': {'pid': 'picture', 'url': 'https://wx1.sinaimg.cn/comment.png'}}])
        await self.worker(route, config=config).run()
        await self.worker(route, config=config).run()
        self.assertEqual(len(downloads), 1)
        self.assertEqual(self.store.counts(), {'posts': 1, 'media': 1, 'incomplete': 0, 'skipped': 0})
        comment = self.store.comments_for_post('1')[0]
        self.assertEqual((self.store.root / comment['media'][0]['path']).read_bytes(), b'local-test-image')

    async def test_explicit_no_comments_response_is_accepted(self):
        worker = self.worker(lambda request: httpx.Response(200, json={
            'ok': 0, 'msg': '暂无评论', 'data': {'data': [], 'max_id': 0}}))
        await worker.run()
        self.assertEqual(self.store.comment_status('1')['state'], 'complete')
        self.assertEqual(self.store.comment_counts()['comments'], 0)

    async def test_actual_mobile_empty_messages_are_normalized_only_on_first_page(self):
        for message in ('还没有人评论哦~快来抢沙发！', '快来发表你的评论吧'):
            worker = self.worker(lambda request: httpx.Response(200, json={'ok': 0, 'msg': message}))
            await worker.run()
            self.assertEqual(self.store.comment_status('1')['state'], 'complete')
        def route(request):
            if request.url.params['max_id'] == '0':
                return root_page([{'id': 10, 'total_number': 0}], 100)
            return httpx.Response(200, json={'ok': 0, 'msg': '快来发表你的评论吧'})
        await self.worker(route).run()
        self.assertEqual(self.store.comment_status('1')['state'], 'error')
        self.assertEqual(self.store.comment_job('1', 'root', '1')['cursor'], '100')

    async def test_desktop_reply_comment_restores_real_multilevel_links(self):
        desktop_requests = []
        def route(request):
            if request.url.path == '/comments/hotflow':
                return root_page([{'id': 10, 'text': 'root', 'total_number': 2,
                                   'comments': [{'id': 11, 'rootid': 10, 'text': 'child'}]}])
            if request.url.path == '/comments/hotFlowChild':
                return child_page([{'id': 11, 'rootid': 10, 'text': 'child'},
                                   {'id': 12, 'rootid': 10, 'text': 'deep'}], total=2)
            if request.url.path == '/ajax/statuses/buildComments':
                desktop_requests.append(request)
                self.assertEqual(request.url.params['uid'], '123')
                self.assertEqual(request.headers['referer'], 'https://weibo.com/')
                return child_page([
                    {'id': 11, 'rootid': 10, 'text': 'child', 'reply_comment': {'id': 10, 'rootid': 10, 'text': 'root'}},
                    {'id': 12, 'rootid': 10, 'text': 'deep', 'reply_comment': {'id': 11, 'rootid': 10, 'text': 'child'}}
                ], total=2)
            raise AssertionError('Unexpected endpoint')
        await self.worker(route, owner_uid='123').run()
        records = {row['id']: row for row in self.store.comments_for_post('1')}
        self.assertEqual(records['11']['parent_id'], '10')
        self.assertEqual(records['12']['parent_id'], '11')
        self.assertEqual(records['12']['parent_relation'], 'explicit')
        self.assertEqual(records['10']['parent_relation'], 'root')
        self.assertEqual(self.store.comment_counts()['comment_parent_unresolved'], 0)
        self.assertEqual(self.store.comment_status('1')['state'], 'complete')
        self.assertEqual(len(desktop_requests), 1)

    async def test_quoted_parent_object_is_saved_without_synthetic_content(self):
        rows = normalize_comments([{'id': 12, 'rootid': 10, 'text': 'reply',
            'reply_comment': {'id': 11, 'rootid': 10, 'text': 'actual quoted parent'}}], '1', self.config, '10')
        records = {row['id']: row for row in rows}
        self.assertEqual(records['12']['parent_id'], '11')
        self.assertEqual(records['11']['text'], 'actual quoted parent')
        self.assertTrue(records['11']['referenced_only'])

    async def test_desktop_recovery_resolves_a_mobile_count_gap_in_same_run(self):
        def route(request):
            if request.url.path == '/comments/hotflow':
                return root_page([{'id': 10, 'text': 'root', 'total_number': 2}])
            if request.url.path == '/comments/hotFlowChild':
                return child_page([{'id': 11, 'rootid': 10, 'text': 'child'}], total=2)
            return child_page([
                {'id': 11, 'rootid': 10, 'text': 'child', 'reply_comment': {'id': 10, 'rootid': 10, 'text': 'root'}},
                {'id': 12, 'rootid': 10, 'text': 'recovered', 'reply_comment': {'id': 11, 'rootid': 10, 'text': 'child'}}
            ], total=2)
        await self.worker(route, owner_uid='123').run()
        self.assertEqual(self.store.comment_status('1')['state'], 'complete')
        self.assertEqual(self.store.comment_job('1', 'replies', '10')['state'], 'complete')
        self.assertEqual(self.store.comment_counts()['comments'], 3)

    async def test_pagination_has_no_original_twenty_or_forty_page_cap(self):
        def route(request):
            cursor = int(request.url.params['max_id'])
            return root_page([{'id': 1000 + cursor, 'total_number': 0}], cursor + 1 if cursor < 54 else 0)
        await self.worker(route).run()
        self.assertEqual(self.store.comment_counts()['comments'], 55)
        self.assertEqual(self.store.comment_status('1')['state'], 'complete')

    def test_legacy_database_and_timeline_checkpoint_survive_migration(self):
        import sqlite3
        legacy = Path(self.directory.name) / 'legacy'
        legacy.mkdir()
        with sqlite3.connect(legacy / 'archive.sqlite') as db:
            db.executescript('''CREATE TABLE posts(id TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE jobs(key TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE seen(job TEXT,id TEXT,PRIMARY KEY(job,id));
                CREATE TABLE totals(id INTEGER PRIMARY KEY,posts INTEGER,media INTEGER,incomplete INTEGER);
                INSERT INTO totals VALUES(1,1,51,0);''')
            db.execute('INSERT INTO posts VALUES(?,?)', ('1', json.dumps(self.post)))
            db.execute('INSERT INTO jobs VALUES(?,?)', ('legacy-key', '{"page":4,"source":"mobile","complete":false}'))
            db.execute('INSERT INTO seen VALUES(?,?)', ('legacy-key', '1'))
        db.close()
        migrated = Store(legacy)
        self.assertEqual(migrated.counts(), {'posts': 1, 'media': 51, 'incomplete': 0, 'skipped': 0})
        self.assertEqual(migrated.job('legacy-key')['page'], 4)
        self.assertTrue(migrated.known('legacy-key', '1'))
        self.assertEqual(migrated.comment_counts()['comments'], 0)


if __name__ == '__main__':
    unittest.main()
