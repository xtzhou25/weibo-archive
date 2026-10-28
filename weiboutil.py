"""Compatibility exports for earlier scripts; new code uses archive.weibo_api.

The original author supplied the earlier script. Network and parsing logic now
live in project-owned modules; importing this file performs no I/O.
"""
from archive.protocol import (
    DEFAULT_HEADERS, DESKTOP_UA, MOBILE_UA,
    parse_identity as identity,
    plain_text as clean_comment_html,
    post_id_from_url as get_weibo_post_id,
)
from archive.weibo_api import (
    account_uid as get_uid_from_weibo_api,
    post_detail as get_weibo_post_info,
    root_comments as fetch_post_comments,
    comment_replies as fetch_comment_children,
    reply_parents as fetch_comment_parent_links,
)

__all__ = [
    'DEFAULT_HEADERS', 'DESKTOP_UA', 'MOBILE_UA', 'identity', 'clean_comment_html',
    'get_weibo_post_id', 'get_uid_from_weibo_api', 'get_weibo_post_info',
    'fetch_post_comments', 'fetch_comment_children', 'fetch_comment_parent_links',
]
