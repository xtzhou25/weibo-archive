import asyncio
import hashlib
import json
import time
from pathlib import Path

from . import weibo_api as api
from .protocol import DESKTOP_HEADERS
from .transport import Transport, ArchiveError, LoginRequired, Throttled
from .model import options, normalize, selected, merge, media_items, update_visibility, CST
from datetime import datetime
from .media import download, exceeds_video_limit
from .store import Store
from .export import export_archive
from .comments import CommentBackup
from .retry import retry_failed


def timeline_posts(cards):
    result = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        if isinstance(card.get('mblog'), dict):
            result.append(card['mblog'])
        if isinstance(card.get('card_group'), list):
            result.extend(timeline_posts(card['card_group']))
    return result


class Engine:
    def __init__(self, root, auth, transport_factory=Transport):
        self.root, self.auth = Path(root), auth
        self.transport_factory = transport_factory
        self.task, self.store = None, None
        self.comment_backup = None
        self.resume_gate = asyncio.Event()
        self.resume_gate.set()
        self.status = {'phase': 'idle', 'message': '登录后可开始备份', 'posts': 0, 'media': 0,
                       'incomplete': 0, 'skipped': 0, 'bytes': 0, 'page': 0, 'retry_in': 0, 'events': [],
                       'comments': 0, 'comment_jobs_pending': 0, 'comment_parent_unresolved': 0,
                       'comment_stage': 'idle'}
        self.status.update(visibility_done=0, visibility_total=0, visibility_errors=0,
                           visibility_unknown=0, visibility_stage='idle')

    @property
    def active(self):
        return bool(self.task and not self.task.done())

    def report(self, **values):
        self.status['bytes'] += values.pop('bytes_delta', 0)
        message = values.get('message')
        if 'retry_in' in values:
            values['retry_until'] = time.time() + values['retry_in'] if values['retry_in'] else 0
        if message and message != self.status.get('message'):
            self.status['events'] = (self.status['events'] + [message])[-40:]
        self.status.update(values)

    async def gate(self):
        await self.resume_gate.wait()

    async def start(self, value):
        if self.active:
            raise ValueError('已有备份任务，请先暂停或停止')
        if not self.auth.ready:
            raise ValueError('尚未确认登录，请先扫码并等待账号验证成功')
        config = options(value)
        self.resume_gate.set()
        self.report(phase='starting', message='正在验证登录身份、读取断点…', bytes=0, page=0)
        self.report(visibility_done=0, visibility_total=0, visibility_errors=0,
                    visibility_unknown=0, visibility_stage='idle')
        self.report(retry_total=0, retry_done=0, retry_recovered=0, comment_stage='idle')
        self.task = asyncio.create_task(self._run(config), name='weibo-backup')

    def pause(self):
        if not self.active:
            raise ValueError('当前没有运行中的备份')
        self.resume_gate.clear()
        self.report(phase='paused', message='已暂停，检查点保留')

    def resume(self):
        if not self.active:
            raise ValueError('请点击“开始 / 继续备份”从持久化检查点恢复')
        self.resume_gate.set()
        self.report(phase='running', message='继续备份')

    async def cancel(self):
        if self.active:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def enrich(self, transport, raw, config, depth=0):
        if depth > 8:
            raise ArchiveError('转发关系过深')
        if config['text'] and (raw.get('isLongText') or raw.get('is_long_text')):
            post_id = str(raw.get('idstr') or raw['id'])
            try:
                detail = await api.post_detail(transport, post_id)
                raw = {**raw, **detail}
            except (LoginRequired, ArchiveError) as error:
                if isinstance(error, Throttled):
                    raise
            long = (raw.get('longText') or {}).get('longTextContent') or raw.get('longTextContent')
            if not long:
                result = await transport.json('https://m.weibo.cn/statuses/extend', {'id': post_id})
                long = (result.get('data') or {}).get('longTextContent')
            if not long:
                raise ArchiveError('长微博未完整返回，未把摘要当作完整备份')
            raw = {**raw, 'text': long}
            raw.pop('text_raw', None)
        if raw.get('retweeted_status'):
            raw = {**raw, 'retweeted_status': await self.enrich(transport, raw['retweeted_status'], config, depth + 1)}
        return raw

    async def save_media(self, transport, post):
        for item in media_items(post):
            if item['kind'] in ('image', 'video') and not self.config['video' if item['kind'] == 'video' else 'images']:
                continue
            await self.gate()
            try:
                await download(transport, item, self.store.root, self.report, self.video_max_bytes)
            except (LoginRequired, Throttled):
                raise
            except ArchiveError as error:
                item.update(status='error', error=str(error))
            self.store.save_post(post)
            self.report(**self.store.counts())

    async def page(self, transport, uid, source, page, cursor=None):
        if source == 'desktop':
            result = await transport.json('https://weibo.com/ajax/statuses/mymblog',
                                          {'uid': uid, 'page': page, 'feature': 0},
                                          headers=DESKTOP_HEADERS)
            data = result.get('data')
            if not isinstance(data, dict) or not isinstance(data.get('list'), list):
                raise ArchiveError('桌面时间线格式变化')
            return data['list'], None
        params = {'containerid': '107603' + uid, 'page': page}
        if cursor:
            params['since_id'] = cursor
        response = await transport.request('https://m.weibo.cn/api/container/getIndex', params=params)
        try:
            response.raise_for_status()
            result = response.json()
        except Exception:
            raise ArchiveError('移动时间线没有返回有效 JSON，检查点保留') from None
        finally:
            await response.aclose()
        data = result.get('data') if isinstance(result, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get('cards'), list):
            raise ArchiveError('移动时间线格式异常，未作为历史结束')
        if result.get('ok') != 1:
            empty_message = any(word in str(result.get('msg', '')) for word in ('暂无', '还没有内容'))
            if not data['cards'] and empty_message:
                return [], None
            raise ArchiveError('移动时间线拒绝请求，未推进检查点')
        return timeline_posts(data['cards']), (data.get('cardlistInfo') or {}).get('since_id')

    async def refresh_visibility(self, transport, config):
        posts = {post['id']: post for post in self.store.posts() if selected(post, config)}
        key = 'visibility:' + hashlib.sha256(json.dumps(
            {key: config[key] for key in ('start', 'end')}, sort_keys=True).encode()).hexdigest()
        previous = self.store.job(key)
        pending = [post_id for post_id in previous['pending'] if post_id in posts] if previous and not previous.get('complete') else list(posts)
        job = {'pending': pending, 'complete': False}
        self.store.save_job(key, job)
        self.report(visibility_stage='fetching', visibility_total=len(pending), visibility_done=0,
                    message=f'仅更新可见范围：共 {len(pending)} 条微博，不下载媒体')
        failed, unknown = [], 0
        for index, post_id in enumerate(pending):
            await self.gate()
            post = posts[post_id]
            try:
                raw = await api.post_detail(transport, post_id)
                if str(raw.get('idstr') or raw.get('id') or raw.get('mid') or '') != post_id:
                    raise ArchiveError('详情接口未返回对应微博，未更新其可见范围')
                update_visibility(post, raw, 'weibo.com/ajax/statuses/show')
                unknown += int(post['visibility']['state'] != 'known')
            except (LoginRequired, Throttled):
                raise
            except ArchiveError as error:
                post['visibility_check'] = {'state': 'error', 'checked_at': datetime.now(CST).isoformat(timespec='seconds'),
                                            'error': str(error)}
                failed.append(post_id)
            self.store.save_post(post)
            job['pending'] = failed + pending[index + 1:]
            self.store.save_job(key, job)
            self.report(visibility_done=index + 1, visibility_errors=len(failed), visibility_unknown=unknown,
                        message=f'更新可见范围：{index + 1}/{len(pending)}，{len(failed)} 条待重试，{unknown} 条范围未知')
            if (index + 1) % 10 == 0:
                export_archive(self.store, self.store.root.name)
        job['complete'] = not job['pending']
        self.store.save_job(key, job)
        self.report(phase='partial' if failed or unknown else 'complete', visibility_stage='finished',
                    message=f'可见范围更新结束：检查 {len(pending)} 条，{len(failed)} 条待重试，{unknown} 条接口未提供明确范围；未下载媒体',
                    **self.store.counts())

    async def _run(self, config):
        self.config = config
        self.video_max_bytes = config['video_max_mb'] * 1024 ** 2
        transport = self.transport_factory(self.auth.cookies, report=self.report, gate=self.gate)
        try:
            uid = await api.account_uid(transport)
            if not uid or uid != self.auth.uid:
                raise LoginRequired('登录身份失效或变化，请重新扫码后继续')
            self.store = Store(self.root / 'accounts' / uid)
            self.report(phase='running', message='账号验证成功，恢复媒体队列…', **self.store.counts())
            if config['failed_only']:
                await retry_failed(self.store, transport, config, self.report, self.gate, uid)
                return
            if config['visibility_only']:
                await self.refresh_visibility(transport, config)
                return
            for post in ([] if config['comments_only'] else self.store.posts()):
                pending = [item for item in media_items(post)
                           if item['status'] in ('pending', 'error', 'skipped_size')
                           and config.get('video' if item['kind'] == 'video' else 'images', False)]
                if not pending:
                    continue
                if any(item['status'] != 'skipped_size' or not exceeds_video_limit(item, self.video_max_bytes)
                       for item in pending):
                    try:
                        raw = await api.post_detail(transport, post['id'])
                        refreshed = normalize(await self.enrich(transport, raw, config), config)
                        post = merge(refreshed, post)
                    except (LoginRequired, Throttled):
                        raise
                    except ArchiveError:
                        pass
                await self.save_media(transport, post)
            self.comment_backup = None
            if config['comments']:
                self.comment_backup = CommentBackup(
                    self.store, transport, config, self.report, self.gate,
                    lambda: export_archive(self.store, uid), owner_uid=uid)
                for post in self.store.posts():
                    if selected(post, config):
                        self.comment_backup.prepare(post)
                self.report(**self.store.comment_counts(self.comment_backup.order))
            if not config['comments_only']:
                await self._scan(transport, config, uid)
            if self.comment_backup:
                await self.comment_backup.run()
            counts = self.store.counts()
            comment_counts = self.store.comment_counts(self.comment_backup.order if self.comment_backup else None)
            comments_incomplete = config['comments'] and (
                comment_counts['comment_jobs_pending'] or comment_counts['comment_parent_unresolved'])
            partial = counts['incomplete'] or comments_incomplete
            message = (f"本次扫描结束；{counts['incomplete']} 项媒体、{comment_counts['comment_jobs_pending']} 个评论队列未完成，"
                       f"{comment_counts['comment_parent_unresolved']} 条回复未返回父评论标识") if partial else (
                       f"可访问内容已备份：{counts['posts']} 条微博、{comment_counts['comments']} 条评论")
            if counts['skipped']:
                message += f"；{counts['skipped']} 个视频因大小上限跳过本地下载"
            self.report(phase='partial' if partial else 'complete', message=message,
                        **counts, **comment_counts)
        except asyncio.CancelledError:
            self.report(phase='stopped', message='已停止；再次开始会从检查点继续', retry_in=0)
            raise
        except LoginRequired as error:
            self.auth.phase, self.auth.message = 'error', str(error)
            self.report(phase='error', message=str(error), retry_in=0)
        except ArchiveError as error:
            self.report(phase='error', message=str(error), retry_in=0)
        except Exception:
            self.report(phase='error', message='备份遇到异常，检查点保留；请检查磁盘和接口兼容性', retry_in=0)
        finally:
            await transport.close()
            if self.store:
                try:
                    export_archive(self.store, self.store.root.name)
                except OSError:
                    self.report(phase='error', message='离线阅读器导出失败，请检查磁盘空间')

    async def _scan(self, transport, config, uid):
        key = hashlib.sha256(json.dumps({key: config[key] for key in ('text', 'images', 'video', 'start', 'end')}, sort_keys=True).encode()).hexdigest()
        previous_job = self.store.job(key)
        incremental = bool(previous_job and previous_job.get('complete'))
        job = {'page': 1, 'cursor': None, 'complete': False, 'source': None} if not previous_job or incremental else previous_job
        if job.get('source') == 'desktop' and not incremental:
            job['page'] = max(1, job['page'] - 2)
        first = None
        if not job.get('source'):
            try:
                first = await self.page(transport, uid, 'desktop', 1)
                job['source'] = 'desktop'
            except (LoginRequired, ArchiveError) as error:
                if isinstance(error, Throttled):
                    raise
                self.report(message='桌面时间线不可用，尝试已登录的移动时间线')
                first = await self.page(transport, uid, 'mobile', 1)
                job['source'] = 'mobile'
        self.store.save_job(key, job)
        overlap, empty, previous_ids = 0, 0, None
        while True:
            await self.gate()
            self.report(message=f'{"增量更新" if incremental else "历史扫描"}：第 {job["page"]} 页', page=job['page'])
            items, next_cursor = first if first is not None else await self.page(
                transport, uid, job['source'], job['page'], job.get('cursor'))
            first = None
            if not items:
                empty += 1
                if empty >= 2:
                    job['complete'] = True
                    self.store.save_job(key, job)
                    break
                await transport.wait(3)
                continue
            empty = 0
            ids = [str(raw.get('idstr') or raw.get('id') or raw.get('mid') or '') for raw in items]
            if ids == previous_ids:
                raise ArchiveError('平台返回重复分页，已停止以避免漏抓或无限循环')
            previous_ids = ids
            known_page = all(self.store.known(key, post_id) for post_id in ids)
            for raw, post_id in zip(items, ids):
                await self.gate()
                if self.store.known(key, post_id):
                    existing = self.store.post(post_id)
                    if existing and selected(existing, config):
                        update_visibility(existing, raw, job['source'] + '_timeline')
                        self.store.save_post(existing)
                    continue
                post = normalize(raw, config)
                if selected(post, config):
                    post = normalize(await self.enrich(transport, raw, config), config)
                    post = merge(post, self.store.post(post_id))
                    self.store.save_post(post)
                    if self.comment_backup:
                        self.comment_backup.prepare(post)
                    await self.save_media(transport, post)
                self.store.mark(key, post_id)
                self.report(**self.store.counts())
            overlap = overlap + 1 if known_page else 0
            if incremental and overlap >= 2:
                job['complete'] = True
                self.store.save_job(key, job)
                break
            if next_cursor and str(next_cursor) == str(job.get('cursor')):
                raise ArchiveError('平台游标重复，检查点保留')
            job.update(page=job['page'] + 1, cursor=next_cursor)
            self.store.save_job(key, job)
            if job['page'] % 5 == 0:
                export_archive(self.store, uid)
