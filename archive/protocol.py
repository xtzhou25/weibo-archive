"""Pure helpers and request headers for Weibo's web protocol."""
import re

DESKTOP_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
MOBILE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
DEFAULT_HEADERS = {'User-Agent': DESKTOP_UA, 'Referer': 'https://m.weibo.cn/',
                   'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json, text/plain, */*'}
DESKTOP_HEADERS = {**DEFAULT_HEADERS, 'Referer': 'https://weibo.com/'}
COMMENT_HEADERS = {**DESKTOP_HEADERS, 'client-version': 'v2.47.42'}


def numeric_id(value):
    text = str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else ''
    return text if text.isascii() and text.isdigit() and int(text) > 0 else ''


def parse_identity(payload):
    if not isinstance(payload, dict) or str(payload.get('ok')) in ('0', '-100'):
        return ''
    data = payload.get('data') if isinstance(payload.get('data'), dict) else payload
    if data.get('login') is False or payload.get('login') is False:
        return ''
    user = data.get('user') or data.get('userInfo') or {}
    return numeric_id(data.get('uid') or (user.get('id') if isinstance(user, dict) else None))


def plain_text(value):
    # Entity decoding remains the caller's responsibility; preserve existing archives.
    return re.sub(r'<[^>]+>', '', value or '').strip()


def post_id_from_url(value):
    match = re.search(r'(\d{16,})', str(value))
    return match.group(1) if match else None
