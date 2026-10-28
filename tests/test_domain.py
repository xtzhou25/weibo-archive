import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from archive.model import normalize, options, selected, merge, timestamp
from archive.store import Store
from archive.export import export_archive, safe_media
from weiboutil import identity


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.config = options({})

    def test_identity_uses_explicit_official_uid_and_rejects_guest(self):
        self.assertEqual(identity({'ok': 1, 'data': {'login': True, 'uid': '123'}}), '123')
        for payload in ({'ok': 0, 'data': {'uid': '123'}}, {'data': {'login': False, 'uid': '123'}},
                        {'data': {'uid': '0'}}, {}, [], None):
            self.assertEqual(identity(payload), '')

    def test_validation_and_inclusive_dates(self):
        for value in ({'text': False, 'images': False, 'video': False},
                      {'start': '2026-13-01'}, {'start': '2026-10-27', 'end': '2026-10-26'}):
            with self.assertRaises(ValueError):
                options(value)
        config = options({'start': '2026-10-27', 'end': '2026-10-27'})
        self.assertTrue(selected({'created_at': '2026-10-27T23:59:00+08:00'}, config))
        self.assertTrue(selected({'created_at': ''}, config))
        self.assertFalse(selected({'created_at': '2026-10-26T23:59:00+08:00'}, config))

    def test_dates_are_converted_to_beijing(self):
        self.assertTrue(timestamp('2026-10-26T17:00:00Z').startswith('2026-10-27T01:00'))
        self.assertTrue(timestamp('2026-10-27T09:00:00').endswith('+08:00'))

    def test_plain_text_lossless_and_html_breaks_preserved(self):
        raw = '<script>literal</script> &amp; test'
        self.assertEqual(normalize({'id': 1, 'text_raw': raw}, self.config)['text'], raw)
        self.assertEqual(normalize({'id': 1, 'text': 'a<br>b &amp; c'}, self.config)['text'], 'a\nb & c')

    def test_media_and_nested_repost_relationships(self):
        post = normalize({'id': 1, 'pics': [{'pid': 'p', 'large': {'url': 'https://wx1.sinaimg.cn/a.jpg'}}],
                          'mix_media_info': {'items': [{'type': 'video', 'data': {'media_info': {'stream_url': 'https://f.video.weibocdn.com/v.mp4'}}}]},
                          'retweeted_status': {'id': 2, 'text': 'original'}}, self.config)
        self.assertEqual([item['kind'] for item in post['media']], ['image', 'video'])
        self.assertEqual(post['repost']['id'], '2')
        refreshed = normalize({'id': 1, 'pics': [{'pid': 'p', 'large': {'url': 'https://wx1.sinaimg.cn/new.jpg'}}]}, self.config)
        self.assertEqual(post['media'][0]['id'], refreshed['media'][0]['id'])

    def test_hls_live_and_unknown_media_not_reported_complete(self):
        post = normalize({'id': 1, 'page_info': {'type': 'video', 'media_info': {'stream_url': 'https://f.video.weibocdn.com/a.m3u8'}},
                          'pic_video': ['unknown'], 'mix_media_info': {'items': [{'type': 'unknown'}]}}, self.config)
        self.assertEqual(len(post['media']), 3)
        self.assertTrue(all(item['status'] == 'unsupported' for item in post['media']))

    def test_merge_preserves_downloaded_and_nested_media(self):
        old = normalize({'id': 1, 'text': 'text', 'retweeted_status': {'id': 2, 'pics': [{'pid': 'p', 'url': 'https://wx1.sinaimg.cn/a.jpg'}]}}, self.config)
        old['repost']['media'][0].update(status='done', path='media/' + 'a' * 32 + '.jpg')
        new = normalize({'id': 1, 'retweeted_status': {'id': 2}}, options({'text': False, 'images': False}))
        merged = merge(new, old)
        self.assertEqual(merged['text'], 'text')
        self.assertEqual(merged['repost']['media'][0]['status'], 'done')

    def test_database_closes_and_counts_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            post = normalize({'id': 1, 'pics': [{'pid': 'p', 'url': 'https://wx1.sinaimg.cn/a.jpg'}]}, self.config)
            store.save_post(post)
            self.assertEqual(store.counts(), {'posts': 1, 'media': 0, 'incomplete': 1, 'skipped': 0})
            post['media'][0]['status'] = 'done'
            store.save_post(post)
            restored = Store(directory)
            self.assertEqual(restored.counts(), {'posts': 1, 'media': 1, 'incomplete': 0, 'skipped': 0})
            with store.connection() as connection:
                connection.execute('SELECT 1')
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')

    def test_export_safe_embedded_json_and_only_local_media(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            text = '</script><script>alert(1)</script>'
            store.save_post(normalize({'id': 1, 'text_raw': text}, self.config))
            export_archive(store, '123')
            raw = (Path(directory) / 'reader.html').read_text('utf-8')
            self.assertNotIn(text, raw)
            self.assertIn('\\u003c/script', raw)
            self.assertEqual(json.loads((Path(directory) / 'archive.json').read_text('utf-8'))['posts'][0]['text'], text)
            for path in ('../private/session.json', 'media/../../secret', 'https://evil/a.jpg', 'media/a.svg'):
                with self.assertRaises(ValueError):
                    safe_media(directory, path)


if __name__ == '__main__':
    unittest.main()
