import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from playwright.async_api import Error
from archive.auth import Auth
from archive.browser import launch_browser
from archive.transport import ArchiveError


class InstalledBrowserTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'WEIBO_BROWSER_PATH': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.pw = Mock()
        self.pw.chromium.launch = AsyncMock()

    async def test_prefers_installed_edge_and_private_pipe(self):
        browser, name = await launch_browser(self.pw)
        self.assertEqual(name, 'Microsoft Edge')
        self.assertIs(browser, self.pw.chromium.launch.return_value)
        self.pw.chromium.launch.assert_awaited_once_with(
            headless=True, args=['--no-proxy-server'], channel='msedge')

    async def test_falls_back_to_installed_chrome(self):
        expected = object()
        self.pw.chromium.launch.side_effect = [Error('missing'), expected]
        browser, name = await launch_browser(self.pw, headless=False)
        self.assertIs(browser, expected)
        self.assertEqual(name, 'Google Chrome')
        self.assertEqual([call.kwargs['channel'] for call in self.pw.chromium.launch.await_args_list],
                         ['msedge', 'chrome'])
        self.assertFalse(self.pw.chromium.launch.await_args_list[1].kwargs['headless'])

    async def test_missing_browsers_never_fall_back_to_cached_chromium(self):
        self.pw.chromium.launch.side_effect = Error('missing')
        with self.assertRaisesRegex(ArchiveError, '不会下载浏览器'):
            await launch_browser(self.pw)
        self.assertEqual(self.pw.chromium.launch.await_count, 2)

    async def test_custom_path_and_invalid_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'browser.exe'
            path.touch()
            with patch.dict(os.environ, {'WEIBO_BROWSER_PATH': str(path)}):
                await launch_browser(self.pw)
                self.assertEqual(self.pw.chromium.launch.await_args.kwargs['executable_path'], str(path))
            with patch.dict(os.environ, {'WEIBO_BROWSER_PATH': 'relative.exe'}):
                with self.assertRaisesRegex(ArchiveError, '完整'):
                    await launch_browser(self.pw)

    async def test_cancellation_does_not_launch_another_browser(self):
        self.pw.chromium.launch.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await launch_browser(self.pw)
        self.assertEqual(self.pw.chromium.launch.await_count, 1)

    async def test_missing_browser_error_reaches_login_status(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = Auth(directory)
            manager = Mock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
            with patch('archive.auth.async_playwright', return_value=manager), \
                    patch('archive.auth.launch_browser', side_effect=ArchiveError('无法启动已安装的 Edge 或 Chrome')):
                await auth.begin()
                await auth.task
            self.assertEqual(auth.phase, 'error')
            self.assertIn('Edge', auth.message)
            self.assertFalse(auth.qr)
            self.assertFalse(auth.ready)
