"""Read-only API adapters. The injected transport owns cookies, routing and retries.

No global clients, filesystem writes, login side effects, or account mutations.
Endpoints are observed web interfaces; platform compatibility is not guaranteed.
"""
import httpx

from .errors import ArchiveError, LoginRequired
from .protocol import (DESKTOP_HEADERS, COMMENT_HEADERS, MOBILE_UA,
                       numeric_id, parse_identity)

CONFIG_URL = 'https://m.weibo.cn/api/config'
DETAIL_URL = 'https://weibo.com/ajax/statuses/show'
ROOT_COMMENTS_URL = 'https://m.weibo.cn/comments/hotflow'
REPLIES_URL = 'https://m.weibo.cn/comments/hotFlowChild'
DESKTOP_COMMENTS_URL = 'https://weibo.com/ajax/statuses/buildComments'
EMPTY_COMMENTS = frozenset({'还没有人评论哦~快来抢沙发！', '快来发表你的评论吧', '暂无评论', '还没有评论'})


def _id(value):
    identity = numeric_id(value)
    if not identity:
        raise ValueError('微博、评论或账号标识必须为正整数字符串')
    return identity


async def account_uid(transport):
    return parse_identity(await transport.json(CONFIG_URL))


async def post_detail(transport, post_id):
    post_id = _id(post_id)
    payload = await transport.json(DETAIL_URL,
        {'id': post_id, 'locale': 'zh-CN', 'isGetLongText': 'true'}, headers=DESKTOP_HEADERS)
    record = payload.get('data') if isinstance(payload.get('data'), dict) else payload
    if numeric_id(record.get('idstr') or record.get('id') or record.get('mid')) != post_id:
        raise ArchiveError('详情接口未返回对应微博，原记录已保留')
    return record


async def _comments(transport, url, params, headers=None):
    response = await transport.request(url, params=params, headers=headers or
                                      {'User-Agent': MOBILE_UA, 'Referer': 'https://m.weibo.cn/'})
    try:
        response.raise_for_status()
        payload = response.json()
    except (ValueError, httpx.HTTPError):
        raise ArchiveError('评论接口未返回有效 JSON，评论检查点保留') from None
    finally:
        await response.aclose()
    if not isinstance(payload, dict):
        raise ArchiveError('评论接口结构变化')
    if str(payload.get('errno')) in ('100001', '100005', '100006') or str(payload.get('ok')) == '-100':
        raise LoginRequired('评论接口要求重新登录')
    if str(payload.get('ok')) == '1':
        return payload
    data = payload.get('data')
    # Normalize only explicit empty first pages. Refusals and incomplete pages
    # must remain errors, even if they happen to have no records.
    first_page = str(params.get('max_id', 0)) == '0'
    no_error = payload.get('errno') in (None, '', 0, '0')
    message = str(payload.get('msg', '')).strip()
    explicit_empty = (isinstance(data, dict) and data.get('data') == []
                      and str(data.get('max_id', payload.get('max_id'))) == '0'
                      and any(word in message for word in ('暂无评论', '还没有评论')))
    if first_page and no_error and (explicit_empty or (message in EMPTY_COMMENTS and data in (None, []))):
        return {**payload, 'ok': 1, 'data': {'data': [], 'max_id': 0, 'max_id_type': 0},
                'empty_response_normalized': True}
    raise ArchiveError('评论接口拒绝请求，未将失败当作评论抓取完成')


async def root_comments(transport, post_id, max_id=0, max_id_type=0):
    post_id = _id(post_id)
    return await _comments(transport, ROOT_COMMENTS_URL,
        {'id': post_id, 'mid': post_id, 'max_id': max_id, 'max_id_type': max_id_type})


async def comment_replies(transport, comment_id, max_id=0, max_id_type=0):
    return await _comments(transport, REPLIES_URL,
        {'cid': _id(comment_id), 'max_id': max_id, 'max_id_type': max_id_type})


async def comment_thread(transport, target_id, account_id, max_id=0, *, root=False):
    """Desktop post-level comments (root=True) or one existing reply thread."""
    return await _comments(transport, DESKTOP_COMMENTS_URL,
        {'id': _id(target_id), 'uid': _id(account_id), 'is_reload': 1,
         'is_show_bulletin': 2, 'is_mix': 1, 'count': 20, 'fetch_level': 0 if root else 1,
         'locale': 'zh-CN', 'max_id': max_id}, COMMENT_HEADERS)


async def reply_parents(transport, comment_id, account_id, max_id=0):
    try:
        return await comment_thread(transport, comment_id, account_id, max_id)
    except LoginRequired:
        raise ArchiveError('桌面回复关联接口暂不可用；已保留移动评论，父评论关联待补齐') from None
