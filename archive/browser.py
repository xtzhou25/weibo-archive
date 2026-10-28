"""Use installed browsers only; never resolve a bundled/downloaded browser."""
import os
from pathlib import Path

from playwright.async_api import Error as BrowserError

from .transport import ArchiveError

BROWSER_ARGS = ['--no-proxy-server']


async def launch_browser(pw, *, headless=True):
    """A fresh, temporary profile keeps the user's daily browser session intact.

    Playwright controls the installed executable over its private pipe. There is
    no remote debugging TCP port and no access to the user's existing profile.
    """
    explicit = os.environ.get('WEIBO_BROWSER_PATH', '').strip()
    if explicit:
        path = Path(explicit)
        if not path.is_absolute() or not path.is_file() or path.suffix.lower() != '.exe':
            raise ArchiveError('WEIBO_BROWSER_PATH 必须指向已安装的 Edge 或 Chrome 的完整 .exe 路径')
        candidates = [('指定的浏览器', {'executable_path': str(path), 'channel': 'chromium'})]
    else:
        candidates = [('Microsoft Edge', {'channel': 'msedge'}),
                      ('Google Chrome', {'channel': 'chrome'})]
    for name, options in candidates:
        try:
            browser = await pw.chromium.launch(headless=headless, args=BROWSER_ARGS, **options)
            return browser, name
        except BrowserError:
            continue
    if explicit:
        raise ArchiveError('无法启动指定的浏览器，请检查路径、版本或组织策略；也可清除 WEIBO_BROWSER_PATH 后重试')
    raise ArchiveError('无法启动已安装的 Edge 或 Chrome。请确认至少安装其中一种且可正常打开；'
                       '本工具不会下载浏览器。非标准安装位置可通过 WEIBO_BROWSER_PATH 指定')
