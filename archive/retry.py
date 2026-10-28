"""Retry only persisted error media; leave timeline and comment cursors alone."""
import copy

from . import weibo_api as api

from .comments import normalize_comments, parse_page
from .export import export_archive
from .media import download
from .model import media_items, selected, normalize
from .transport import ArchiveError, LoginRequired, Throttled


def comment_objects(value):
    if isinstance(value, dict):
        if value.get('idstr') or value.get('id'):
            yield value
        for key in ('data', 'comments', 'reply_comment', 'reply_to_comment'):
            yield from comment_objects(value.get(key))
    elif isinstance(value, list):
        for entry in value:
            yield from comment_objects(entry)


async def retry_failed(store, transport, config, report, gate, uid):
    posts = {post['id']: post for post in store.posts() if selected(post, config)}
    work = []
    for scope, records in (('post', posts.values()), ('comment', store.all_comments())):
        for record in records:
            if scope == 'comment' and record['post_id'] not in posts:
                continue
            failed = [item for item in media_items(record) if item['status'] == 'error'
                      and config.get('video' if item['kind'] == 'video' else 'images', False)]
            if failed:
                work.append((scope, record, failed))
    total = sum(len(items) for _, _, items in work)
    done = recovered = skipped = 0
    cache = {}

    async def refreshed_comment(record):
        # Fetch only the specific existing thread, never the whole timeline.
        is_root = record['id'] == record['root_id']
        key = (record['post_id'], record['root_id'] if not is_root else '', is_root)
        if key not in cache:
            cache[key] = {'cursor': '0', 'seen': set(), 'rows': {}, 'ended': False}
        state = cache[key]
        while record['id'] not in state['rows'] and not state['ended']:
            await gate()
            payload = await api.comment_thread(transport,
                record['post_id'] if is_root else record['root_id'], uid,
                state['cursor'], root=is_root)
            items, cursor, _, _ = parse_page(payload, 'replies')
            for raw in comment_objects(items):
                identity = str(raw.get('idstr') or raw.get('id'))
                # Prefer the enriched object over a stripped reply reference.
                if raw.get('url_struct') or identity not in state['rows']:
                    state['rows'][identity] = raw
            if cursor == '0':
                state['ended'] = True
            elif cursor == state['cursor'] or cursor in state['seen']:
                raise ArchiveError('刷新失败评论媒体时出现循环游标，原记录已保留')
            state['seen'].add(state['cursor'])
            state['cursor'] = cursor
        raw = state['rows'].get(record['id'])
        if raw is None:
            raise ArchiveError('官方回复接口未再返回该评论，无法刷新媒体地址')
        records = normalize_comments([raw], record['post_id'], config, record['root_id'])
        return next(row for row in records if row['id'] == record['id'])

    report(retry_total=total, retry_done=0, retry_recovered=0, comment_stage='idle',
           message=f'仅重试失败媒体：共 {total} 项，不扫描时间线，不更新评论队列')
    for scope, record, failed in work:
        replacement = None
        refresh_error = ''
        post_urls = {}
        if scope == 'post':
            node = record
            while node:
                requested = [item for item in node.get('media', []) if item in failed]
                if requested:
                    try:
                        raw = await api.post_detail(transport, node['id'])
                        if str(raw.get('idstr') or raw.get('id') or raw.get('mid')) != node['id']:
                            raise ArchiveError('刷新媒体的详情标识不匹配')
                        current = normalize(raw, config)
                        for item in requested:
                            candidates = [m for m in current['media'] if m['kind'] == item['kind']]
                            match = next((m for m in candidates if m['key'] == item['key']), None)
                            if match is None and len(candidates) == 1:
                                match = candidates[0]
                            if match and match.get('url'):
                                post_urls[item['id']] = match['url']
                    except (LoginRequired, Throttled):
                        raise
                    except ArchiveError:
                        pass  # Existing URL may still be usable; do not erase it.
                node = node.get('repost')
        if scope == 'comment':
            try:
                replacement = await refreshed_comment(record)
            except (LoginRequired, Throttled):
                raise
            except ArchiveError as error:
                refresh_error = str(error)
        for index, item in enumerate(failed):
            await gate()
            report(message=f'仅重试失败媒体：{done + 1}/{total}（已恢复 {recovered}）')
            original = copy.deepcopy(item)
            try:
                if item['id'] in post_urls:
                    item['url'] = post_urls[item['id']]
                if scope == 'comment':
                    if refresh_error:
                        raise ArchiveError(refresh_error)
                    candidates = [m for m in media_items(replacement) if m['kind'] == item['kind']]
                    matching = next((m for m in candidates if m.get('key') == item.get('key')), None)
                    candidate = matching or (candidates[index] if index < len(candidates) else None)
                    if not candidate or not candidate.get('url'):
                        raise ArchiveError('官方评论接口未提供可恢复的媒体地址')
                    # Keep the original stable id/path: do not duplicate media rows.
                    item['url'] = candidate['url']
                await download(transport, item, store.root, report, config['video_max_mb'] * 1024 ** 2)
                if item['status'] == 'done':
                    recovered += 1
                elif item['status'] == 'skipped_size':
                    skipped += 1
            except (LoginRequired, Throttled):
                raise
            except ArchiveError as error:
                item.update(status='error', error=str(error))
            if item.get('url') != original.get('url'):
                item.setdefault('previous_failed_url', original['url'])
            if scope == 'post':
                store.save_post(record)
            else:
                store.save_comment(record)
            done += 1
            report(retry_done=done, retry_recovered=recovered, **store.counts())
            if done % 10 == 0:
                export_archive(store, uid)
    report(phase='partial' if total - recovered - skipped else 'complete',
           message=f'失败媒体重试结束：检查 {total} 项，恢复 {recovered} 项，超限跳过 {skipped} 项，'
                   f'仍失败 {total - recovered - skipped} 项；已有文件未重新下载',
           **store.counts(), **store.comment_counts())
