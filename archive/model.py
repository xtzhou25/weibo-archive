import email.utils
import hashlib
import html
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit

from .protocol import plain_text
from .transport import ArchiveError

CST = timezone(timedelta(hours=8))
DEFAULT_VIDEO_MAX_MB = 500


def options(value):
    result = {key: bool(value.get(key, True)) for key in ('text', 'images', 'video')}
    result['comments'] = bool(value.get('comments', False))
    result['comments_only'] = bool(value.get('comments_only', False))
    result['visibility_only'] = bool(value.get('visibility_only', False))
    result['failed_only'] = bool(value.get('failed_only', False))
    if result['failed_only'] and (result['visibility_only'] or result['comments_only']):
        raise ValueError('请单独执行失败媒体重试')
    if result['visibility_only'] and result['comments_only']:
        raise ValueError('请分别执行评论更新和可见范围更新')
    if result['comments_only']:
        result['comments'] = True
    limit = value.get('video_max_mb', DEFAULT_VIDEO_MAX_MB)
    if isinstance(limit, bool) or not re.fullmatch(r'\d{1,7}', str(limit)) or not 0 <= int(limit) <= 1048576:
        raise ValueError('单个视频大小上限须为 0 到 1048576 的整数 MB；0 表示不限大小')
    result['video_max_mb'] = int(limit)
    for key in ('start', 'end'):
        result[key] = str(value.get(key) or '')
        if result[key]:
            datetime.strptime(result[key], '%Y-%m-%d')
    if not result['visibility_only'] and not any(result[key] for key in ('text', 'images', 'video', 'comments')):
        raise ValueError('请至少选择一种备份内容')
    if result['start'] and result['end'] and result['start'] > result['end']:
        raise ValueError('开始日期不能晚于结束日期')
    return result


def timestamp(value):
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (ValueError, TypeError):
        try:
            parsed = datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return ''
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CST)
    return parsed.astimezone(CST).isoformat()


def selected(post, config):
    day = post['created_at'][:10]
    return not day or ((not config['start'] or day >= config['start']) and
                       (not config['end'] or day <= config['end']))


def visibility(raw, source='weibo_api'):
    # Current official desktop frontend, checked 2026-10-27. These are
    # response visible.type codes, NOT the legacy update-request parameters.
    labels = {0: '公开', 1: '仅自己可见', 5: '群可见', 6: '好友圈可见',
              9: '仅付费会员可见', 10: '仅粉丝可见'}
    visible = raw.get('visible')
    code = visible.get('type') if isinstance(visible, dict) else None
    if isinstance(code, bool) or not re.fullmatch(r'\d+', str(code)):
        code = None
    else:
        code = int(code)
    checked = datetime.now(CST).isoformat(timespec='seconds')
    return {'type': code, 'label': labels.get(code, '未知（接口未提供明确范围）'),
            'state': 'known' if code in labels else 'unknown', 'raw': visible,
            'source': source, 'checked_at': checked}


def update_visibility(post, raw, source='weibo_api'):
    post['visibility'] = visibility(raw, source)
    post['visibility_check'] = {'state': 'ok', 'checked_at': post['visibility']['checked_at'], 'error': ''}
    nested = raw.get('retweeted_status')
    if (isinstance(nested, dict) and post.get('repost')
            and str(nested.get('idstr') or nested.get('id') or nested.get('mid')) == post['repost']['id']):
        update_visibility(post['repost'], nested, source)
    return post


def normalize(raw, config, depth=0):
    if depth > 8 or not isinstance(raw, dict):
        raise ArchiveError('转发嵌套结构异常')
    post_id = str(raw.get('idstr') or raw.get('id') or raw.get('mid') or '')
    if not post_id.isascii() or not post_id.isdigit():
        raise ArchiveError('帖子没有有效 ID')
    text = raw.get('text_raw')
    if text is None:
        text = html.unescape(plain_text(re.sub(r'<br\s*/?>', '\n', raw.get('text') or '', flags=re.I)))
    user = raw.get('user') or {}
    post = {'id': post_id, 'created_at': timestamp(raw.get('created_at')),
            'author': {'id': str(user.get('id') or ''), 'name': user.get('screen_name') or ''},
            'text': str(text) if config['text'] else '', 'media': [], 'repost': None}
    post['comments_reported'] = raw.get('comments_count')
    seen = set()

    def add(kind, source, key, error=''):
        source = str(source or '')
        if source.startswith('//'):
            source = 'https:' + source
        key = str(key)
        stable = hashlib.sha256(f'{post_id}:{kind}:{key}'.encode()).hexdigest()[:32]
        if stable in seen:
            return
        seen.add(stable)
        unsupported = '.m3u8' in urlsplit(source).path.lower()
        post['media'].append({'id': stable, 'kind': kind, 'key': key, 'url': source,
                              'path': '', 'status': 'unsupported' if error or unsupported else 'pending',
                              'error': error or ('HLS 视频需要额外处理，未标为下载成功' if unsupported else '')})

    def image(item, fallback):
        if not isinstance(item, dict):
            return
        large = item.get('largest') or item.get('original') or item.get('large') or item
        url = large.get('url') if isinstance(large, dict) else ''
        if url:
            add('image', url, item.get('pid') or item.get('pic_id') or fallback)

    def video(page, fallback='video'):
        if not isinstance(page, dict):
            return
        info = page.get('media_info') or page
        urls = page.get('urls') or {}
        source = (info.get('mp4_hd_url') or info.get('stream_url_hd') or info.get('stream_url') or
                  info.get('mp4_sd_url') or urls.get('mp4_720p_mp4') or urls.get('mp4_hd_mp4') or urls.get('mp4_ld_mp4'))
        if not source:
            for playback in info.get('playback_list') or []:
                source = (playback.get('play_info') or {}).get('url')
                if source:
                    break
        if source or page.get('type') == 'video' or page.get('object_type') == 'video':
            add('video', source, page.get('object_id') or fallback,
                '' if source else '官方接口未提供可下载视频地址')

    if config['images']:
        infos = raw.get('pic_infos') or {}
        pictures = raw.get('pics') or [infos[key] for key in (raw.get('pic_ids') or list(infos)) if key in infos]
        for index, picture in enumerate(pictures):
            image(picture, index)
    if config['video']:
        video(raw.get('page_info') or {})
    mixed = raw.get('mix_media_info') or {}
    for index, component in enumerate(mixed.get('items') or []):
        data = component.get('data') or {}
        kind = component.get('type')
        if kind in ('pic', 'image', 1) and config['images']:
            image(data, f'mix-{index}')
        elif kind in ('video', 2) and config['video']:
            video(data, f'mix-{index}')
        elif kind not in ('pic', 'image', 1, 'video', 2) and (config['images'] or config['video']):
            add('other', '', f'mix-{index}', '未知混合媒体组件，需要人工检查')
    if raw.get('pic_video') and config['video']:
        add('video', '', 'live-photo', '动态照片的视频部分暂未适配，照片部分单独保留')
    if raw.get('retweeted_status'):
        post['repost'] = normalize(raw['retweeted_status'], config, depth + 1)
    return update_visibility(post, raw)


def merge(new, old):
    if not old:
        return new
    new['text'] = new.get('text') or old.get('text', '')
    items = {item['id']: item for item in old.get('media', [])}
    for item in new['media']:
        previous = items.get(item['id'])
        if previous and previous['status'] == 'done':
            items[item['id']] = previous
        elif (previous and previous['status'] == 'skipped_size' and item['status'] == 'pending'
              and urlsplit(previous.get('url', ''))._replace(query='', fragment='')
              == urlsplit(item.get('url', ''))._replace(query='', fragment='')):
            # Signed URL refreshes must not make an unknown-size oversized
            # video consume the same probe traffic on every incremental run.
            items[item['id']] = {**previous, 'url': item['url']}
        else:
            items[item['id']] = item
    new['media'] = list(items.values())
    if old.get('repost'):
        if not new.get('repost'):
            new['repost'] = old['repost']
        elif new['repost']['id'] == old['repost']['id']:
            new['repost'] = merge(new['repost'], old['repost'])
    return new


def media_items(post):
    yield from post.get('media', [])
    if post.get('repost'):
        yield from media_items(post['repost'])
