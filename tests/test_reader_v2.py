import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
from archive.export import export_archive, STATIC
from archive.model import normalize, options
from archive.reader_v2 import create_reader_app, render_v2
from archive.store import Store


class ReaderV2Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root)
        self.store.save_post(normalize({'id': 1, 'text_raw': 'VERSION_TEST'}, options({})))
        export_archive(self.store, '123')
        self.legacy = (self.root / 'reader.html').read_bytes()
        self.raw = (self.root / 'archive.json').read_bytes()
        self.asset_hashes = {name: hashlib.sha256((STATIC / name).read_bytes()).hexdigest()
                             for name in ('reader.html', 'reader.js', 'reader.css')}
        self.snapshot = 'reader-v1-20261027-120000.html'
        (self.root / self.snapshot).write_bytes(self.legacy)
        self.client = TestClient(TestServer(create_reader_app(self.root)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_new_entry_exports_separate_file_and_preserves_originals(self):
        response = await self.client.get('/')
        text = await response.text()
        self.assertEqual(response.status, 200)
        self.assertIn('九宫格版 v2', text)
        self.assertIn(self.snapshot, text)
        self.assertTrue((self.root / 'reader-v2.html').exists())
        self.assertEqual((self.root / 'reader.html').read_bytes(), self.legacy)
        self.assertEqual((self.root / 'archive.json').read_bytes(), self.raw)
        for name, expected in self.asset_hashes.items():
            self.assertEqual(hashlib.sha256((STATIC / name).read_bytes()).hexdigest(), expected)

    async def test_legacy_live_entry_and_archived_snapshot_still_open(self):
        for path in ('/reader.html', '/' + self.snapshot):
            response = await self.client.get(path)
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), self.legacy)
        response = await self.client.get('/private/session.json')
        self.assertEqual(response.status, 404)

    async def test_read_only_origin_guards_and_local_media_range(self):
        self.assertEqual((await self.client.post('/')).status, 405)
        self.assertEqual((await self.client.get('/', headers={'Host': 'evil.test'})).status, 403)
        self.assertEqual((await self.client.get('/', headers={'Origin': 'https://evil.test'})).status, 403)
        media = self.root / 'media'
        media.mkdir()
        name = 'a' * 32 + '.mp4'
        (media / name).write_bytes(b'0123456789')
        response = await self.client.get('/media/' + name, headers={'Range': 'bytes=2-5'})
        self.assertEqual(response.status, 206)
        self.assertEqual(await response.read(), b'2345')
        self.assertEqual(response.content_type, 'video/mp4')
        self.assertEqual((await self.client.get('/media/not-valid.mp4')).status, 404)

    def test_v2_embedded_data_and_snapshot_name_are_not_executable_markup(self):
        payload = {'account_uid': '123', 'posts': [{'id': '1', 'text': '</script><script>BAD</script>'}], 'comments': []}
        html = render_v2(payload, '</script><script>BAD</script>')
        self.assertNotIn('</script><script>BAD', html)
        self.assertIn('\\u003c/script', html)
        self.assertIn('const LEGACY_SNAPSHOT = "";', html)


if __name__ == '__main__':
    unittest.main()
