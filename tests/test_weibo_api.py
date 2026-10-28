import asyncio
import unittest

import httpx

from archive import weibo_api as api
from archive.errors import ArchiveError, LoginRequired
from archive.protocol import parse_identity
from archive.transport import Transport


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def transport(self, handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        result = Transport([], interval=0, client=client, media_client=client)
        result.wait = lambda seconds: asyncio.sleep(0)
        self.addAsyncCleanup(result.close)
        return result

    def test_identity_rejects_string_guest_codes_and_boolean_ids(self):
        for payload in ({'ok': '0', 'uid': '123'}, {'ok': '-100', 'uid': '123'}, {'uid': True}):
            self.assertEqual(parse_identity(payload), '')
        self.assertEqual(parse_identity({'ok': '1', 'data': {'userInfo': {'id': '123'}}}), '123')

    async def test_detail_accepts_both_shapes_and_checks_requested_identity(self):
        for payload in ({'id': 123}, {'data': {'idstr': '123'}}):
            def route(request):
                self.assertEqual(request.headers['referer'], 'https://weibo.com/')
                self.assertEqual(request.url.params['id'], '123')
                return httpx.Response(200, json=payload)
            record = await api.post_detail(self.transport(route), '123')
            self.assertEqual(str(record.get('id') or record.get('idstr')), '123')
        transport = self.transport(lambda request: httpx.Response(200, json={'id': 999}))
        with self.assertRaises(ArchiveError):
            await api.post_detail(transport, '123')

    async def test_invalid_identity_is_rejected_before_any_network_call(self):
        def route(request):
            self.fail('Invalid identity must not reach the transport')
        transport = self.transport(route)
        for identity in ('0', '../private', True, 1.5):
            with self.assertRaises(ValueError):
                await api.post_detail(transport, identity)

    async def test_empty_first_page_does_not_mask_later_page_refusal(self):
        payload = {'ok': 0, 'msg': '暂无评论', 'data': {'data': [], 'max_id': 0}}
        transport = self.transport(lambda request: httpx.Response(200, json=payload))
        self.assertEqual((await api.root_comments(transport, '123'))['ok'], 1)
        with self.assertRaises(ArchiveError):
            await api.root_comments(transport, '123', max_id=99)

    async def test_bad_json_and_login_response_close_the_http_response(self):
        for payload, expected in ((b'not json', ArchiveError), (b'{"ok":"-100"}', LoginRequired)):
            response = httpx.Response(200, content=payload)
            transport = self.transport(lambda request: response)
            with self.assertRaises(expected):
                await api.comment_replies(transport, '123')
            self.assertTrue(response.is_closed)

    async def test_desktop_thread_selects_root_and_reply_levels(self):
        levels = []
        def route(request):
            levels.append(request.url.params['fetch_level'])
            self.assertEqual(request.headers['referer'], 'https://weibo.com/')
            return httpx.Response(200, json={'ok': 1, 'data': [], 'max_id': 0})
        transport = self.transport(route)
        await api.comment_thread(transport, '123', '456', root=True)
        await api.reply_parents(transport, '123', '456')
        self.assertEqual(levels, ['0', '1'])

    def test_old_public_names_are_compatible_exports(self):
        import weiboutil
        self.assertIs(weiboutil.get_uid_from_weibo_api, api.account_uid)
        self.assertIs(weiboutil.get_weibo_post_info, api.post_detail)
        self.assertIs(weiboutil.fetch_post_comments, api.root_comments)
