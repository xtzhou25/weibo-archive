"""Offline browser checks; run explicitly with -p browser_smoke.py."""
import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright, expect

from app import create_app
from archive.auth import Auth
from archive.browser import launch_browser
from archive.engine import Engine
from archive.transport import Transport
from archive.export import export_archive
from archive.store import Store
from archive.model import normalize, options
from archive.comments import normalize_comments

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aHYQAAAAASUVORK5CYII=')


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.auth = Auth(self.directory.name)
        self.engine = Engine(self.directory.name, self.auth)
        self.server = TestServer(create_app(self.directory.name, self.auth, self.engine))
        await self.server.start_server()
        self.pw = await async_playwright().start()
        self.browser, _ = await launch_browser(self.pw)
        self.page = await self.browser.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()
        await self.server.close()
        self.directory.cleanup()
        self.assertFalse(self.errors)

    async def open(self):
        await self.page.goto(str(self.server.make_url('/')), wait_until='networkidle')

    def ready(self):
        self.auth.uid, self.auth.phase = '123', 'ready'
        self.auth.cookies = [{'domain': '.weibo.cn', 'name': 'SUB', 'value': 'mock-only'}]

    async def test_qr_status_readiness_and_persistent_command_error(self):
        self.auth.phase = 'waiting_scan'
        self.auth.qr, self.auth.qr_version = PNG, 1
        await self.open()
        await expect(self.page.locator('#qr-panel')).to_be_visible()
        await self.page.wait_for_function('() => document.getElementById("qr").naturalWidth > 0')
        await expect(self.page.locator('#start')).to_be_disabled()
        self.auth.phase = 'verifying'
        await self.page.evaluate('refresh()')
        await expect(self.page.locator('#auth-badge')).to_have_text('验证账号')
        self.ready()
        self.auth.qr = b''
        await self.page.evaluate('refresh()')
        await expect(self.page.locator('#start')).to_be_enabled()
        self.engine.start = AsyncMock(side_effect=ValueError('TEST_ERROR_KEPT'))
        await self.page.locator('#start').click()
        await expect(self.page.locator('#error')).to_have_text('TEST_ERROR_KEPT')
        await self.page.evaluate('refresh()')
        await expect(self.page.locator('#error')).to_have_text('TEST_ERROR_KEPT')

    async def test_button_to_real_worker_and_offline_reader(self):
        release = asyncio.Event()
        async def route(request):
            if request.url.path == '/api/config':
                await release.wait()
                return httpx.Response(200, json={'ok': 1, 'data': {'uid': '123', 'login': True}})
            return httpx.Response(200, json={'data': {'list': [{'id': 1, 'text_raw': 'BROWSER_BACKUP_CONTENT'}] if request.url.params['page'] == '1' else []}})
        def factory(cookies, report, gate):
            client = httpx.AsyncClient(transport=httpx.MockTransport(route))
            transport = Transport(cookies, report, gate, interval=0, client=client, media_client=client)
            transport.wait = lambda seconds: asyncio.sleep(0)
            return transport
        self.engine.transport_factory = factory
        self.ready()
        await self.open()
        await self.page.locator('[name=comments]').uncheck()
        await self.page.locator('#start').click()
        await expect(self.page.locator('#phase')).to_have_text('正在启动')
        release.set()
        await expect(self.page.locator('#phase')).to_have_text('已完成', timeout=10000)
        await expect(self.page.locator('#posts')).to_have_text('1')
        reader = await self.browser.new_page()
        await reader.goto(str(self.server.make_url('/reader.html')))
        await expect(reader.locator('.text')).to_have_text('BROWSER_BACKUP_CONTENT')
        await reader.close()

    async def test_file_reader_search_and_script_injection_is_literal(self):
        store = Store(Path(self.directory.name) / 'offline')
        text = '<script>window.ARCHIVE_XSS=true</script>'
        store.save_post(normalize({'id': 1, 'text_raw': text}, options({})))
        export_archive(store, '123')
        await self.page.goto((store.root / 'reader.html').as_uri())
        await expect(self.page.locator('.text')).to_have_text(text)
        self.assertIsNone(await self.page.evaluate('window.ARCHIVE_XSS'))
        await self.page.locator('#search').fill('no-such-post')
        await expect(self.page.locator('.post')).to_have_count(0)
        await self.page.locator('#search').fill('script')
        await expect(self.page.locator('.post')).to_have_count(1)

    async def test_offline_reader_expands_multilevel_comments_and_searches_replies(self):
        store = Store(Path(self.directory.name) / 'thread-reader')
        config = options({'comments': True})
        store.save_post(normalize({'id': 1, 'text_raw': 'POST_WITH_COMMENTS'}, config))
        rows = normalize_comments([{'id': 10, 'text_raw': 'ROOT_COMMENT', 'comments': [
            {'id': 11, 'text_raw': 'CHILD_COMMENT', 'comments': [
                {'id': 12, 'text_raw': '</script><script>window.COMMENT_XSS=1</script> DEEP_REPLY'}]}]}], '1', config)
        for row in rows:
            store.save_comment(row)
        store.prepare_comments('1')
        job = store.comment_job('1', 'root', '1')
        job.update(scan_complete=True, complete=True, state='complete')
        store.save_comment_job(job)
        export_archive(store, '123')
        await self.page.goto((store.root / 'reader.html').as_uri())
        await self.page.locator('.comments > summary').click()
        await expect(self.page.locator('#comment-1-10')).to_be_visible()
        await self.page.locator('#comment-1-10 > .replies > summary').click()
        await expect(self.page.locator('#comment-1-11')).to_be_visible()
        await self.page.locator('#comment-1-11 > .replies > summary').click()
        await expect(self.page.locator('#comment-1-12')).to_be_visible()
        await expect(self.page.locator('#comment-1-12 > .comment-reference')).to_contain_text('11')
        self.assertIsNone(await self.page.evaluate('window.COMMENT_XSS'))
        await self.page.locator('#search').fill('DEEP_REPLY')
        await expect(self.page.locator('.post')).to_have_count(1)

    async def test_comments_only_button_uses_existing_posts(self):
        self.ready()
        self.engine.store = Store(Path(self.directory.name) / 'accounts' / '123')
        self.engine.store.save_post(normalize({'id': 1, 'text_raw': 'EXISTING_POST'}, options({})))
        async def route(request):
            if request.url.path == '/api/config':
                return httpx.Response(200, json={'ok': 1, 'data': {'uid': '123'}})
            if request.url.path == '/comments/hotflow':
                return httpx.Response(200, json={'ok': 1, 'data': {'data': [
                    {'id': 10, 'text_raw': 'NEW_COMMENT', 'total_number': 0}], 'max_id': 0}})
            raise AssertionError('Comment-only action requested unrelated content')
        def factory(cookies, report, gate):
            client = httpx.AsyncClient(transport=httpx.MockTransport(route))
            transport = Transport([], report, gate, interval=0, client=client, media_client=client)
            transport.wait = lambda seconds: asyncio.sleep(0)
            return transport
        self.engine.transport_factory = factory
        await self.open()
        await self.page.locator('#comments-only').click()
        await expect(self.page.locator('#phase')).to_have_text('已完成', timeout=10000)
        await expect(self.page.locator('#comments')).to_have_text('1')
        self.assertEqual(self.engine.store.post('1')['text'], 'EXISTING_POST')

    async def test_video_size_setting_is_remembered_and_sent_to_worker(self):
        self.ready()
        self.engine.start = AsyncMock()
        await self.open()
        await expect(self.page.locator('#video-max-mb')).to_have_value('500')
        await self.page.locator('#video-max-mb').fill('250')
        await self.page.reload()
        await expect(self.page.locator('#video-max-mb')).to_have_value('250')
        await self.page.locator('#start').click()
        await self.page.wait_for_function('() => document.getElementById("start").disabled === false')
        self.engine.start.assert_awaited_once()
        self.assertEqual(self.engine.start.call_args.args[0]['video_max_mb'], '250')

    async def test_skipped_video_has_reason_and_manual_link_without_remote_media(self):
        store = Store(Path(self.directory.name) / 'skip-reader')
        post = normalize({'id': 1, 'text_raw': 'POST_WITH_SKIPPED_VIDEO', 'page_info': {
            'type': 'video', 'media_info': {'stream_url': 'https://f.video.weibocdn.com/large.mp4'}}}, options({}))
        post['media'][0].update(status='skipped_size', skip_reason='视频超过 500 MB，已跳过本地下载')
        store.save_post(post)
        export_archive(store, '123')
        await self.page.goto((store.root / 'reader.html').as_uri())
        await expect(self.page.locator('.skipped-video')).to_contain_text('超过 500 MB')
        await expect(self.page.locator('.skipped-video a')).to_have_attribute('href', 'https://f.video.weibocdn.com/large.mp4')
        await expect(self.page.locator('video')).to_have_count(0)
        self.assertEqual(await self.page.locator('[src^="http"]').count(), 0)

    async def test_visibility_only_button_fetches_detail_without_media_or_comments(self):
        self.ready()
        self.engine.store = Store(Path(self.directory.name) / 'accounts' / '123')
        self.engine.store.save_post(normalize({'id': 1, 'text_raw': 'EXISTING_POST'}, options({})))
        calls = []
        async def route(request):
            calls.append(request.url.path)
            if request.url.path == '/api/config':
                return httpx.Response(200, json={'ok': 1, 'data': {'uid': '123'}})
            if request.url.path == '/ajax/statuses/show':
                return httpx.Response(200, json={'id': 1, 'visible': {'type': 6, 'list_id': 3}})
            raise AssertionError('Visibility-only action requested unrelated content')
        def factory(cookies, report, gate):
            client = httpx.AsyncClient(transport=httpx.MockTransport(route))
            transport = Transport([], report, gate, interval=0, client=client, media_client=client)
            transport.wait = lambda seconds: asyncio.sleep(0)
            return transport
        self.engine.transport_factory = factory
        await self.open()
        await self.page.locator('#visibility-only').click()
        await expect(self.page.locator('#phase')).to_have_text('已完成', timeout=10000)
        await expect(self.page.locator('#message')).to_contain_text('未下载媒体')
        self.assertEqual(calls, ['/api/config', '/ajax/statuses/show'])
        self.assertEqual(self.engine.store.post('1')['visibility']['label'], '好友圈可见')

    async def test_reader_shows_separate_post_repost_unknown_and_stale_ranges(self):
        store = Store(Path(self.directory.name) / 'visibility-reader')
        first = normalize({'id': 1, 'visible': {'type': 1},
                           'retweeted_status': {'id': 2, 'visible': {'type': 0}}}, options({}))
        second = normalize({'id': 3}, options({}))
        third = normalize({'id': 4, 'visible': {'type': 10}}, options({}))
        third['visibility_check'] = {'state': 'error', 'error': 'TEST_FAILURE'}
        for post in (first, second, third):
            store.save_post(post)
        export_archive(store, '123')
        await self.page.goto((store.root / 'reader.html').as_uri())
        await expect(self.page.locator('.post > .visibility').nth(0)).to_contain_text('可见范围：仅自己可见')
        await expect(self.page.locator('.repost > .visibility')).to_contain_text('可见范围：公开')
        await expect(self.page.locator('.post > .visibility').nth(1)).to_contain_text('未知')
        await expect(self.page.locator('.post > .visibility').nth(2)).to_contain_text('上次记录的可见范围：仅粉丝可见')
        await expect(self.page.locator('.visibility-warning')).to_have_text('本次核验失败，当前范围待确认')
        await expect(self.page.locator('.visibility-time').first).to_contain_text('核验于')

    async def dated_reader(self, name, rows):
        store = Store(Path(self.directory.name) / name)
        for row in rows:
            store.save_post(normalize(row, options({})))
        export_archive(store, '123')
        await self.page.goto((store.root / 'reader.html').as_uri())

    async def test_recent_dates_use_latest_archive_day_include_leap_day_and_restore(self):
        await self.dated_reader('date-relative', [
            {'id': 1, 'created_at': '2024-03-01', 'retweeted_status': {'id': 100, 'created_at': '2020-01-01'}},
            {'id': 2, 'created_at': '2024-02-29'}, {'id': 3, 'created_at': '2024-02-24'},
            {'id': 4, 'created_at': '2024-02-23'}, {'id': 5, 'created_at': '2023-12-31'}, {'id': 6}])
        await expect(self.page.locator('#time-year option')).to_have_count(3)
        await expect(self.page.locator('#time-year')).not_to_contain_text('2020')
        await self.page.locator('[data-time-preset="7"]').click()
        await expect(self.page.locator('#from')).to_have_value('2024-02-24')
        await expect(self.page.locator('#to')).to_have_value('2024-03-01')
        await expect(self.page.locator('.post')).to_have_count(3)
        await expect(self.page.locator('#range-hint')).to_contain_text('从档案最新日期 2024-03-01 倒推')
        await self.page.reload()
        await expect(self.page.locator('[data-time-preset="7"]')).to_have_attribute('aria-pressed', 'true')
        await expect(self.page.locator('.post')).to_have_count(3)
        await self.page.locator('#include-unknown').check()
        await expect(self.page.locator('.post')).to_have_count(4)

    async def test_month_navigation_skips_empty_months_and_crosses_years(self):
        await self.dated_reader('date-month', [
            {'id': 1, 'created_at': '2025-12-31'}, {'id': 2, 'created_at': '2026-02-01'},
            {'id': 3, 'created_at': '2026-02-10'}, {'id': 4, 'created_at': '2026-04-01'}])
        await self.page.locator('#time-year').select_option('2026')
        await expect(self.page.locator('.post')).to_have_count(3)
        await expect(self.page.locator('#time-month option')).to_have_count(3)
        await self.page.locator('#time-month').select_option('02')
        await expect(self.page.locator('.post')).to_have_count(2)
        await self.page.locator('#older-month').click()
        await expect(self.page.locator('#time-year')).to_have_value('2025')
        await expect(self.page.locator('#time-month')).to_have_value('12')
        await expect(self.page.locator('.post')).to_have_count(1)
        await expect(self.page.locator('#older-month')).to_be_disabled()
        await self.page.locator('#newer-month').click()
        await self.page.locator('#newer-month').click()
        await expect(self.page.locator('#time-month')).to_have_value('04')
        await expect(self.page.locator('#newer-month')).to_be_disabled()
        await self.page.reload()
        await expect(self.page.locator('#from')).to_have_value('2026-04-01')
        await expect(self.page.locator('#time-month')).to_have_value('04')

    async def test_custom_dates_errors_empty_range_and_clear_keep_other_filters(self):
        await self.dated_reader('date-custom', [
            {'id': 1, 'created_at': '2026-01-01', 'text_raw': 'KEY', 'pics': [{'url': 'https://wx1.sinaimg.cn/a.jpg'}]},
            {'id': 2, 'created_at': '2026-01-03', 'text_raw': 'KEY', 'pics': [{'url': 'https://wx1.sinaimg.cn/b.jpg'}]},
            {'id': 3, 'created_at': '2026-01-04', 'text_raw': 'OTHER'}])
        await self.page.locator('#search').fill('KEY')
        await self.page.locator('#kind').select_option('image')
        await self.page.locator('#to').fill('2026-01-01')
        await self.page.locator('#from').fill('2026-01-03')
        await expect(self.page.locator('#range-error')).to_contain_text('开始日期不能晚于结束日期')
        await expect(self.page.locator('.post')).to_have_count(0)
        await self.page.locator('#clear-dates').click()
        await expect(self.page.locator('.post')).to_have_count(2)
        await expect(self.page.locator('#search')).to_have_value('KEY')
        await expect(self.page.locator('#kind')).to_have_value('image')
        await self.page.locator('#from').fill('2026-01-02')
        await self.page.locator('#to').fill('2026-01-02')
        await expect(self.page.locator('#range-error')).to_be_hidden()
        await expect(self.page.locator('#empty')).to_be_visible()
        await self.page.locator('#empty-clear-dates').click()
        await expect(self.page.locator('.post')).to_have_count(2)
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))

    async def test_empty_and_unknown_date_only_archives_have_safe_date_controls(self):
        await self.dated_reader('date-empty', [])
        await expect(self.page.locator('#from')).to_be_disabled()
        await expect(self.page.locator('[data-time-preset="30"]')).to_be_disabled()
        await expect(self.page.locator('#empty')).to_be_visible()
        await self.dated_reader('date-unknown', [{'id': 1, 'text_raw': 'NO_DATE'}])
        await expect(self.page.locator('.post')).to_have_count(1)
        await expect(self.page.locator('#time-year')).to_be_disabled()
        await expect(self.page.locator('#archive-extent')).to_contain_text('暂无有效的微博日期')


if __name__ == '__main__':
    unittest.main()
