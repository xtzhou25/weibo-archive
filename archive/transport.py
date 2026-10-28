import asyncio
import email.utils
import random
import time
from urllib.parse import urlsplit, urljoin

import httpx
from .protocol import DEFAULT_HEADERS
from .errors import ArchiveError, LoginRequired, Throttled


def allowed(url, media=False):
    try:
        parts = urlsplit(url)
        domains = ('weibo.com', 'weibo.cn')
        if media:
            domains += ('sinaimg.cn', 'weibocdn.com', 'sina.com.cn')
        return (parts.scheme == 'https' and not parts.username and not parts.password
                and parts.port in (None, 443) and any(
                    parts.hostname == domain or (parts.hostname or '').endswith('.' + domain)
                    for domain in domains))
    except ValueError:
        return False


def backoff(headers, attempt, now=None):
    value = headers.get('retry-after', headers.get('Retry-After', ''))
    try:
        server = float(value)
    except (ValueError, TypeError):
        try:
            server = email.utils.parsedate_to_datetime(value).timestamp() - (time.time() if now is None else now)
        except (ValueError, TypeError, OverflowError):
            server = 0
    return max(0, server, min(300, 2 ** (attempt + 1)) + random.uniform(0, 1))


def cookie_jar(cookies):
    jar = httpx.Cookies()
    for cookie in cookies:
        domain = str(cookie.get('domain', '')).lstrip('.')
        if allowed('https://' + domain + '/'):
            jar.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie.get('path', '/'))
    return jar


class Transport:
    def __init__(self, cookies, report=lambda **kw: None, gate=None, interval=2, client=None, media_client=None):
        self.report, self.gate, self.interval = report, gate, interval
        self.next_request = 0
        self.lock = asyncio.Lock()
        self.client = client or httpx.AsyncClient(cookies=cookie_jar(cookies), headers=DEFAULT_HEADERS,
                                                 timeout=httpx.Timeout(90, connect=20, pool=30),
                                                 proxy=None, follow_redirects=False, trust_env=False)
        self.media_client = media_client or httpx.AsyncClient(
            headers={'User-Agent': DEFAULT_HEADERS['User-Agent'], 'Referer': 'https://weibo.com/'},
            timeout=httpx.Timeout(120, connect=20, pool=30), proxy=None,
            follow_redirects=False, trust_env=False)

    async def wait(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.gate:
                await self.gate()
            await asyncio.sleep(min(.25, max(0, deadline - time.monotonic())))

    async def request(self, url, params=None, headers=None, media=False, stream=False):
        client = self.media_client if media else self.client
        redirects = 0
        attempt = 0
        while attempt < 7:
            if not allowed(url, media):
                raise ArchiveError('接口或媒体地址不在允许的官方域名范围')
            async with self.lock:
                if self.gate:
                    await self.gate()
                await self.wait(max(0, self.next_request - time.monotonic()))
                self.next_request = time.monotonic() + self.interval + random.uniform(0, self.interval / 5)
                try:
                    request = client.build_request('GET', url, params=params, headers=headers)
                    response = await client.send(request, stream=stream)
                except httpx.TransportError:
                    if attempt == 6:
                        raise ArchiveError('网络重试耗尽，检查点已保留') from None
                    delay = backoff({}, attempt)
                    self.report(message=f'网络中断，{delay:.0f} 秒后重试', retry_in=delay)
                    await self.wait(delay)
                    attempt += 1
                    continue
            if response.status_code in (301, 302, 303, 307, 308):
                target = urljoin(url, response.headers.get('location', ''))
                await response.aclose()
                redirects += 1
                if redirects > 5 or not allowed(target, media):
                    raise ArchiveError('重定向异常，请重新登录或稍后继续')
                url, params = target, None
                continue
            if response.status_code in (418, 429, 500, 502, 503, 504):
                delay = backoff(response.headers, attempt)
                await response.aclose()
                if attempt == 6:
                    raise Throttled('平台限流重试耗尽，已安全停止；稍后可从检查点继续')
                self.report(message=f'平台限流或暂时错误，{delay:.0f} 秒后重试（{attempt + 1}/7）', retry_in=delay)
                await self.wait(delay)
                attempt += 1
                continue
            if response.status_code in (401, 403) and not media:
                await response.aclose()
                raise LoginRequired('登录失效或访问被拒绝，请重新登录后继续')
            self.report(retry_in=0)
            return response
        raise Throttled('重试耗尽')

    async def json(self, url, params=None, headers=None):
        response = await self.request(url, params, headers=headers)
        try:
            response.raise_for_status()
            result = response.json()
        except (ValueError, httpx.HTTPError):
            raise ArchiveError('官方接口未返回有效 JSON；未把异常当作历史结束') from None
        finally:
            await response.aclose()
        if not isinstance(result, dict):
            raise ArchiveError('官方接口数据格式变化')
        if str(result.get('errno', '')) in ('100001', '100005', '100006') or result.get('ok') == -100:
            raise LoginRequired('官方接口要求重新登录')
        if result.get('ok') == 0:
            raise ArchiveError('官方接口拒绝请求，检查点未推进')
        return result

    async def close(self):
        await self.client.aclose()
        if self.media_client is not self.client:
            await self.media_client.aclose()
