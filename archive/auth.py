import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright
from .protocol import DEFAULT_HEADERS, parse_identity
from .weibo_api import account_uid, CONFIG_URL
from .transport import Transport, ArchiveError, backoff
from .browser import launch_browser

QR_SELECTOR = 'img[src^="https://v2.qr.weibo.cn/inf/gen?"]'


def scoped(cookies):
    result = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get('domain', '')).lstrip('.').lower()
        if any(domain == host or domain.endswith('.' + host) for host in ('weibo.com', 'weibo.cn')):
            result.append(dict(cookie))
    return result


class Auth:
    def __init__(self, root):
        self.path = Path(root) / 'private' / 'session.json'
        self.cookies, self.uid, self.name = [], '', ''
        self.phase, self.message = 'signed_out', '请先获取二维码并扫码登录'
        self.qr, self.qr_version = b'', 0
        self.task = None
        self.persist = False
        self.browser_name = ''
        self.generation = 0
        self.checks = {'qr_code': None, 'identity_http': None}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text('utf-8'))
                self.cookies = scoped(saved['cookies'])
                self.phase, self.message = 'unverified', '已读取保存的会话，正在验证账号'
            except (OSError, ValueError, KeyError, TypeError):
                self.message = '保存的会话无法读取，请重新扫码'

    @property
    def ready(self):
        return self.phase == 'ready' and bool(self.uid and self.cookies)

    def status(self):
        return {'phase': self.phase, 'message': self.message, 'uid': self.uid, 'name': self.name,
                'ready': self.ready, 'qr_ready': bool(self.qr), 'qr_version': self.qr_version,
                'checks': dict(self.checks), 'browser': self.browser_name}

    async def restore(self):
        if not self.cookies:
            return
        transport = Transport(self.cookies)
        generation = self.generation
        try:
            uid = await account_uid(transport)
            if not uid:
                raise ArchiveError('保存的登录会话已失效，请重新扫码')
            if generation == self.generation:
                self.uid, self.phase, self.message = uid, 'ready', '已验证保存的登录会话'
        except ArchiveError as error:
            if generation == self.generation:
                self.phase, self.message = 'error', str(error)
        finally:
            await transport.close()

    async def begin(self, persist=False, mode='qr'):
        if not isinstance(persist, bool) or mode not in ('qr', 'window'):
            raise ValueError('无效的登录选项')
        self.generation += 1
        await self.stop()
        self.persist = persist
        self.browser_name = ''
        self.qr, self.qr_version = b'', self.qr_version + 1
        self.phase, self.message = 'opening', '正在加载微博官方二维码…'
        self.checks = {'qr_code': None, 'identity_http': None}
        self.task = asyncio.create_task(self._login(mode, self.generation), name='official-weibo-login')

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None

    async def _observe(self, response, generation):
        address = urlsplit(response.url)
        if address.hostname != 'passport.weibo.com' or address.path != '/sso/v2/qrcode/check':
            return
        try:
            code = str((await response.json()).get('retcode', ''))
            if generation != self.generation or self.phase == 'ready':
                return
            self.checks['qr_code'] = code
            if code == '20000000':
                self.phase, self.message = 'verifying', '手机已确认，正在验证账号…'
                self.qr = b''
            elif code == '50114002':
                self.phase, self.message = 'confirm_on_phone', '已扫码，请在手机上确认登录'
            elif code in ('50114003', '50114004', '50114015'):
                self.phase, self.message = 'expired', '官方二维码已失效，请刷新二维码'
                self.qr = b''
        except Exception:
            pass

    async def _login(self, mode, generation):
        try:
            async with async_playwright() as pw:
                browser, self.browser_name = await launch_browser(pw, headless=(mode == 'qr'))
                try:
                    context = await browser.new_context()
                    context.on('response', lambda response: self._observe(response, generation))
                    home = await context.new_page()
                    await home.goto('https://weibo.com/', wait_until='domcontentloaded', timeout=60000)
                    async with home.expect_popup(timeout=20000) as popup:
                        await home.get_by_text('登录/注册', exact=True).click(timeout=20000)
                    login_page = await popup.value
                    await login_page.wait_for_load_state('domcontentloaded', timeout=30000)
                    if self.phase != 'verifying':
                        self.phase, self.message = 'waiting_scan', '请用微博 App 扫描本页二维码并在手机确认'
                    deadline, next_identity, failures = time.monotonic() + 600, 0, 0
                    while time.monotonic() < deadline:
                        if self.phase == 'expired':
                            return
                        if self.phase not in ('verifying', 'ready') and not login_page.is_closed():
                            try:
                                image = login_page.locator(QR_SELECTOR).first
                                if await image.count() and await image.is_visible() and await image.evaluate('(i) => i.complete && i.naturalWidth > 0'):
                                    png = await image.screenshot(timeout=2000)
                                    if self.phase not in ('verifying', 'ready') and png != self.qr:
                                        self.qr, self.qr_version = png, self.qr_version + 1
                            except Exception:
                                pass  # The official popup can close normally during SSO.
                        if time.monotonic() >= next_identity:
                            next_identity = time.monotonic() + 4
                            try:
                                response = await context.request.get(CONFIG_URL, headers=DEFAULT_HEADERS,
                                                                     timeout=15000, max_redirects=0)
                                try:
                                    self.checks['identity_http'] = response.status
                                    if response.status in (418, 429):
                                        failures += 1
                                        if failures >= 7:
                                            raise ArchiveError('账号验证连续限流，已停止；请稍后重新扫码')
                                        delay = backoff(response.headers, failures - 1)
                                        next_identity = time.monotonic() + delay
                                        self.message = f'账号验证限流，{delay:.0f} 秒后重试；无需重复扫码'
                                        continue
                                    payload = await response.json() if response.status == 200 else {}
                                finally:
                                    await response.dispose()
                            except ArchiveError:
                                raise
                            except Exception:
                                self.message = '官方账号验证暂未响应，正在重试'
                                payload = {}
                            uid = parse_identity(payload)
                            if uid:
                                cookies = scoped(await context.cookies())
                                # Verify the exact exported cookie jar using the same transport as backup.
                                verifier = Transport(cookies)
                                try:
                                    verified = await account_uid(verifier)
                                finally:
                                    await verifier.close()
                                if verified != uid:
                                    self.phase, self.message = 'verifying', '浏览器已登录，正在等待可用于备份的会话同步'
                                else:
                                    self.cookies, self.uid = cookies, uid
                                    data = payload.get('data') or {}
                                    user = data.get('user') or data.get('userInfo') or {}
                                    self.name = str(user.get('screen_name') or '') if isinstance(user, dict) else ''
                                    self.phase, self.message, self.qr = 'ready', '账号已验证，可以开始备份', b''
                                    if self.persist:
                                        self.save()
                                    else:
                                        self.path.unlink(missing_ok=True)
                                    return
                        await asyncio.sleep(.5)
                    self.phase, self.message = 'expired', '登录等待已超时，请重新获取二维码'
                    self.qr = b''
                finally:
                    await browser.close()
        except asyncio.CancelledError:
            self.qr = b''
            raise
        except ArchiveError as error:
            self.phase, self.message, self.qr = 'error', str(error), b''
        except Exception:
            self.phase, self.message, self.qr = 'error', '官方登录页加载失败；可刷新二维码或打开官方窗口完成验证', b''

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump({'version': 1, 'cookies': self.cookies}, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.path)

    async def logout(self):
        self.generation += 1
        await self.stop()
        self.cookies, self.uid, self.name, self.qr = [], '', '', b''
        self.path.unlink(missing_ok=True)
        self.phase, self.message = 'signed_out', '已清除本工具的登录会话，备份文件保留'
