"""Isolated UI tests for the independent nine-grid reader."""
import asyncio
import base64
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright, expect

from archive.comments import normalize_comments
from archive.export import export_archive
from archive.model import normalize, options
from archive.reader_v2 import create_reader_app, export_v2
from archive.store import Store


def png(width=20, height=20):
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))
    scan = (b'\0' + b'\x79\xa2\x82' * width) * height
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(scan)) + chunk(b'IEND', b'')


class ReaderV2BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = Store(self.root)
        (self.root / 'media').mkdir()
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        self.errors = []
        self.requests = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('request', lambda request: self.requests.append(request.url))
        self.server = TestServer(create_reader_app(self.root))
        await self.server.start_server()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()
        await self.server.close()
        self.directory.cleanup()
        self.assertFalse(self.errors)

    def photo_post(self, post_id, count, text, long=False):
        post = normalize({'id': post_id, 'text_raw': text, 'created_at': '2026-10-27', 'visible': {'type': 6},
                          'pics': [{'pid': str(i), 'url': f'https://wx1.sinaimg.cn/{i}.png'} for i in range(count)]}, options({}))
        for item in post['media']:
            item.update(status='done', path=f'media/{item["id"]}.png')
            (self.root / item['path']).write_bytes(png(50, 300) if long else png())
        self.store.save_post(post)
        return post

    async def open(self):
        export_archive(self.store, '123')
        legacy = self.root / 'reader-v1-20261027-120000.html'
        legacy.write_bytes((self.root / 'reader.html').read_bytes())
        await self.page.goto(str(self.server.make_url('/')))

    async def test_single_four_nine_and_overflow_layouts(self):
        for count, name in ((1, 'SINGLE'), (4, 'FOUR'), (9, 'NINE'), (12, 'TWELVE')):
            self.photo_post(count, count, name)
        await self.open()
        for name, count, columns in (('SINGLE', 1, 1), ('FOUR', 4, 2), ('NINE', 9, 3), ('TWELVE', 9, 3)):
            grid = self.page.locator('.post').filter(has_text=name).locator('.media-grid')
            await expect(grid.locator('.media-tile')).to_have_count(count)
            self.assertEqual(await grid.evaluate('el => getComputedStyle(el).gridTemplateColumns.split(" ").length'), columns)
        await expect(self.page.locator('.media-overflow')).to_have_text('+3')
        self.assertFalse(any(url.startswith('https://') for url in self.requests))

    async def test_all_overflow_images_are_reachable_and_escape_restores_focus(self):
        self.photo_post(1, 12, 'TWELVE')
        await self.open()
        last_tile = self.page.locator('.media-tile').nth(8)
        await last_tile.click()
        await expect(self.page.locator('#media-viewer')).to_be_visible()
        await expect(self.page.locator('.viewer-counter')).to_have_text('9 / 12')
        for _ in range(3):
            await self.page.keyboard.press('ArrowRight')
        await expect(self.page.locator('.viewer-counter')).to_have_text('12 / 12')
        await expect(self.page.locator('.viewer-tools button').nth(1)).to_be_disabled()
        await self.page.keyboard.press('ArrowLeft')
        await expect(self.page.locator('.viewer-counter')).to_have_text('11 / 12')
        await self.page.keyboard.press('Escape')
        await expect(self.page.locator('#media-viewer')).to_be_hidden()
        await expect(last_tile).to_be_focused()
        self.assertFalse(await self.page.locator('body').evaluate('el => el.classList.contains("viewer-open")'))

    async def test_long_image_zoom_and_mobile_layout(self):
        self.photo_post(1, 1, 'LONG', long=True)
        await self.open()
        await expect(self.page.locator('.media-badge')).to_have_text('长图')
        await self.page.locator('.media-tile').click()
        await self.page.get_by_role('button', name='原尺寸查看').click()
        await expect(self.page.locator('#media-viewer')).to_have_class('media-viewer is-zoomed')
        await self.page.get_by_role('button', name='适应窗口').click()
        await self.page.get_by_role('button', name='关闭媒体查看器').click()
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    async def test_video_uses_local_preview_player_and_stops_when_closed(self):
        await self.page.goto(str(self.server.make_url('/reader.html')))
        encoded = await self.page.evaluate('''async () => {
            const c=document.createElement('canvas'); c.width=160;c.height=90;
            const ctx=c.getContext('2d');ctx.fillStyle='#355e4d';ctx.fillRect(0,0,160,90);
            const stream=c.captureStream(5),rec=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8'}),chunks=[];
            rec.ondataavailable=e=>chunks.push(e.data);
            const stopped=new Promise(resolve=>rec.onstop=resolve);rec.start();
            await new Promise(resolve=>setTimeout(resolve,300));ctx.fillStyle='#ffffff';ctx.fillRect(20,20,20,20);
            await new Promise(resolve=>setTimeout(resolve,300));rec.stop();await stopped;stream.getTracks().forEach(t=>t.stop());
            return await new Promise(resolve=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.readAsDataURL(new Blob(chunks,{type:'video/webm'}));});
        }''')
        post = normalize({'id': 1, 'text_raw': 'VIDEO', 'page_info': {'type': 'video', 'media_info': {'stream_url': 'https://f.video.weibocdn.com/a.webm'}}}, options({}))
        item = post['media'][0]
        item.update(status='done', path=f'media/{item["id"]}.webm')
        (self.root / item['path']).write_bytes(base64.b64decode(encoded))
        self.store.save_post(post)
        await self.open()
        await expect(self.page.locator('.media-grid')).to_have_class('media-grid single-video')
        await self.page.wait_for_function('document.querySelector(".media-tile video").readyState >= 1')
        self.assertTrue(await self.page.locator('.media-tile video').evaluate('v=>v.paused'))
        await self.page.locator('.media-tile').click()
        video = self.page.locator('.viewer-stage video')
        self.assertTrue(await video.evaluate('v=>v.controls && v.paused'))
        await video.evaluate('v=>v.play()')
        await self.page.get_by_role('button', name='关闭媒体查看器').click()
        await expect(self.page.locator('.viewer-stage video')).to_have_count(0)

    async def test_unavailable_media_stays_explicit_and_never_loads_remote_video(self):
        post = normalize({'id': 1, 'page_info': {'type': 'video', 'media_info': {'stream_url': 'https://f.video.weibocdn.com/large.mp4'}}}, options({}))
        post['media'][0].update(status='skipped_size', skip_reason='视频超出 500 MB 上限，已跳过')
        self.store.save_post(post)
        await self.open()
        await expect(self.page.locator('.unavailable-tile')).to_contain_text('视频超限已跳过')
        await self.page.locator('.media-tile').click()
        await expect(self.page.locator('.media-unavailable')).to_contain_text('500 MB')
        await expect(self.page.locator('.media-unavailable a')).to_have_attribute('href', 'https://f.video.weibocdn.com/large.mp4')
        self.assertFalse(any(url.startswith('https://') for url in self.requests))

    async def test_date_scope_comments_and_legacy_links_are_retained(self):
        self.photo_post(1, 9, 'POST')
        rows = normalize_comments([{'id': 10, 'text_raw': 'ROOT', 'comments': [{'id': 11, 'text_raw': 'REPLY', 'pic': {'url': 'https://wx1.sinaimg.cn/comment.png'}}]}], '1', options({}))
        for row in rows:
            for item in row['media']:
                item.update(status='done', path=f'media/{item["id"]}.png')
                (self.root / item['path']).write_bytes(png())
            self.store.save_comment(row)
        await self.open()
        await expect(self.page.locator('.post > .visibility')).to_contain_text('好友圈可见')
        await self.page.locator('[data-time-preset="7"]').click()
        await expect(self.page.locator('.post')).to_have_count(1)
        await self.page.locator('.comments > summary').click()
        await self.page.locator('#comment-1-10 > .replies > summary').click()
        await expect(self.page.locator('#comment-1-11 .media-tile')).to_be_visible()
        await self.page.locator('#comment-1-11 .media-tile').click()
        await expect(self.page.locator('.viewer-counter')).to_have_text('1 / 1')
        await self.page.keyboard.press('Escape')
        await expect(self.page.locator('#legacy-snapshot')).to_have_attribute('href', 'reader-v1-20261027-120000.html')
        await self.page.get_by_role('link', name='打开旧版阅读器').click()
        await expect(self.page.locator('.reader-versions')).to_have_count(0)


if __name__ == '__main__':
    unittest.main()
