import hashlib
import html
import re

from . import weibo_api as api
from .protocol import plain_text
from .model import normalize, media_items
from .media import download
from .transport import ArchiveError, LoginRequired, Throttled


def identifier(value):
    value = str(value or '')
    return value if value.isascii() and value.isdigit() and int(value) > 0 else None


def reply_count(raw):
    for key in ('total_number', 'reply_count', 'comments_count'):
        value = raw.get(key)
        if value is not None:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                pass
    return None


def normalize_comments(items, post_id, config, root_id=None, nested_parent=None, depth=0):
    if depth > 64:
        raise ArchiveError('评论嵌套超过安全深度，未把缺失层级标为完成')
    result = []
    for raw in items:
        if not isinstance(raw, dict):
            raise ArchiveError('评论条目格式异常')
        comment_id = identifier(raw.get('idstr') or raw.get('id'))
        if not comment_id:
            raise ArchiveError('评论缺少有效 ID')
        declared_root = identifier(raw.get('rootidstr') or raw.get('rootid'))
        root = declared_root or root_id or comment_id
        parent, relation = None, 'root' if root == comment_id else 'thread_only'
        if root != comment_id:
            for field in ('reply_comment', 'reply_to_comment'):
                reference = raw.get(field)
                if isinstance(reference, dict):
                    parent = identifier(reference.get('idstr') or reference.get('id'))
                    if parent:
                        break
            if not parent:
                parent = identifier(raw.get('reply_id') or raw.get('reply_comment_id'))
            if parent:
                relation = 'explicit'
            elif nested_parent and not declared_root:
                parent, relation = nested_parent, 'nested'
        if parent == comment_id:
            parent, relation = None, 'thread_only'
        converted = dict(raw)
        if isinstance(raw.get('pic'), dict):
            converted['pics'] = [raw['pic']]
        # Mobile can return /large/&690 with pid=null, even labelled "video",
        # for a real comment photo. Desktop url_struct carries its real pic_infos.
        linked_pictures = []
        for link in raw.get('url_struct') or []:
            if not isinstance(link, dict):
                continue
            if link.get('url_type') != 39 and '/comment/compic_id/' not in str(link.get('long_url', '')):
                continue
            infos = link.get('pic_infos') or {}
            if not isinstance(infos, dict):
                continue
            for pid in link.get('pic_ids') or infos:
                if isinstance(infos.get(pid), dict):
                    linked_pictures.append({**infos[pid], 'pid': str(pid)})
        if linked_pictures:
            converted['pics'] = linked_pictures
        converted.pop('retweeted_status', None)
        node = normalize(converted, {**config, 'text': True})
        for item in node['media']:
            item['id'] = hashlib.sha256(('comment:' + item['id']).encode()).hexdigest()[:32]
        reference = raw.get('reply_comment') or raw.get('reply_to_comment') or {}
        reference_user = reference.get('user') if isinstance(reference, dict) else {}
        children = raw.get('comments') or []
        if isinstance(children, dict):
            children = children.get('data') or []
        if not isinstance(children, list):
            raise ArchiveError('内嵌回复格式异常')
        node.update(post_id=str(post_id), root_id=root, parent_id=parent,
                    parent_relation=relation, reply_count=reply_count(raw),
                    has_inline_replies=bool(children),
                    reply_to_author=str((reference_user or {}).get('screen_name') or ''))
        # Mobile previews can flatten multiple levels despite appearing in a
        # root's `comments` list. rootid identifies the thread, not its parent.
        quote = raw.get('reply_original_text')
        node['reply_original_text'] = html.unescape(plain_text(quote)) if isinstance(quote, str) else ''
        if not node['reply_to_author']:
            mention = re.match(r'^\s*回复\s*@([^:：\n]+)[:：]', node['text'])
            if mention:
                node['reply_to_author'] = mention.group(1)
        node.pop('repost', None)
        node.pop('comments_reported', None)
        node.pop('visibility', None)
        node.pop('visibility_check', None)
        result.append(node)
        # Desktop replies can carry a full parent object that is absent from the
        # mobile result. Preserve that real object, never synthesize its content.
        reference_id = identifier(reference.get('idstr') or reference.get('id')) if isinstance(reference, dict) else None
        if reference_id and reference_id != comment_id and any(key in reference for key in ('text', 'text_raw')):
            parent_records = normalize_comments([reference], post_id, config, root, depth=depth + 1)
            for parent_record in parent_records:
                parent_record['referenced_only'] = True
            result.extend(parent_records)
        if children:
            result.extend(normalize_comments(children, post_id, config, root, comment_id, depth + 1))
    return result


def parse_page(payload, kind):
    data = payload.get('data')
    if isinstance(data, list):
        items, source = data, payload
    elif isinstance(data, dict) and isinstance(data.get('data'), list):
        items, source = data['data'], data
    else:
        raise ArchiveError('评论分页缺少列表字段，检查点未推进')
    # hotFlowChild exposes the continuation at the top level.
    cursor_source = payload if kind == 'replies' and 'max_id' in payload else source
    if 'max_id' not in cursor_source:
        raise ArchiveError('评论分页缺少结束游标，未猜测抓取已完成')
    cursor = cursor_source['max_id']
    if not isinstance(cursor, (str, int)) or isinstance(cursor, bool) or not str(cursor).isascii() or not str(cursor).isdigit():
        raise ArchiveError('评论游标格式异常')
    total = cursor_source.get('total_number', source.get('total_number', payload.get('total_number')))
    try:
        total = max(0, int(total)) if total is not None else None
    except (TypeError, ValueError):
        total = None
    cursor_type = cursor_source.get('max_id_type', source.get('max_id_type', 0))
    return items, str(cursor), cursor_type, total


class CommentBackup:
    def __init__(self, store, transport, config, report, gate, export=None, owner_uid=None):
        self.store, self.transport, self.config = store, transport, config
        self.report, self.gate, self.export = report, gate, export
        self.prepared = set()
        self.order = []
        self.pages = 0
        self.owner_uid = str(owner_uid or (store.root.name if store.root.name.isdigit() else ''))

    def prepare(self, post):
        if post['id'] not in self.prepared:
            self.store.prepare_comments(post['id'])
            self.prepared.add(post['id'])
            self.order.append(post['id'])

    async def _pages(self, job):
        while not job.get('scan_complete'):
            await self.gate()
            self.report(comment_stage='fetching', comment_post=job['post_id'],
                        message=f'评论备份：微博 {job["post_id"]}，' +
                        ('一级评论' if job['kind'] == 'root' else '补齐父评论关联' if job['kind'] == 'parents' else '楼中楼及回复'))
            if job['kind'] == 'root':
                payload = await api.root_comments(self.transport, job['post_id'], job['cursor'], job['cursor_type'])
            elif job['kind'] == 'parents':
                if not self.owner_uid:
                    raise ArchiveError('缺少已验证账号标识，暂不能补齐桌面评论关联')
                payload = await api.reply_parents(self.transport, job['target_id'], self.owner_uid, job['cursor'])
            else:
                payload = await api.comment_replies(self.transport, job['target_id'], job['cursor'], job['cursor_type'])
            items, cursor, cursor_type, total = parse_page(payload, 'replies' if job['kind'] == 'parents' else job['kind'])
            history = job.get('cursor_history', [])
            if cursor != '0' and (cursor == str(job['cursor']) or cursor in history):
                raise ArchiveError('评论接口返回循环游标，未标记完成')
            if not items and cursor != '0':
                raise ArchiveError('评论接口返回空页但还有游标，保留断点等待重试')
            records = normalize_comments(items, job['post_id'], self.config,
                                         job.get('root_id') if job['kind'] != 'root' else None)
            job.update(cursor=cursor, cursor_type=cursor_type, scan_complete=cursor == '0',
                       cursor_history=(history + [str(job['cursor'])]), state='pending', error='')
            if total is not None:
                job['reported_total'] = total
            self.store.commit_comment_page(job, records)
            self.pages += 1
            self.report(**self.store.comment_counts(self.order), **self.store.counts())
            if self.export and self.pages % 5 == 0:
                self.export()

    async def _media(self, post_id):
        for comment in self.store.comments_for_post(post_id):
            for item in media_items(comment):
                if item['status'] not in ('pending', 'error', 'skipped_size'):
                    continue
                if item['kind'] in ('image', 'video') and not self.config['video' if item['kind'] == 'video' else 'images']:
                    continue
                await self.gate()
                try:
                    await download(self.transport, item, self.store.root, self.report,
                                   self.config.get('video_max_mb', 500) * 1024 ** 2)
                except (LoginRequired, Throttled):
                    raise
                except ArchiveError as error:
                    item.update(status='error', error=str(error))
                self.store.save_comment(comment)
                self.report(**self.store.counts())

    async def run(self):
        self.report(comment_stage='fetching', **self.store.comment_counts(self.order))
        for post_id in self.order:
            root = self.store.comment_job(post_id, 'root', post_id)
            if not root:
                continue
            try:
                if not root.get('scan_complete'):
                    await self._pages(root)
            except (LoginRequired, Throttled):
                raise
            except ArchiveError as error:
                root.update(state='error', error=str(error))
                self.store.save_comment_job(root)
            # New jobs can be discovered at any nesting level while fetching a thread.
            attempted = set()
            while True:
                jobs = [job for job in self.store.comment_jobs(post_id)
                        if job['kind'] != 'root' and not job.get('complete')
                        and (job['kind'], job['target_id']) not in attempted]
                if not jobs:
                    break
                job = jobs[0]
                attempted.add((job['kind'], job['target_id']))
                try:
                    await self._pages(job)
                    total = job.get('reported_total')
                    obtained = self.store.thread_count(post_id, job['target_id'])
                    if total is not None and obtained < total:
                        job.update(scan_complete=False, state='partial',
                                   error=f'接口已结束但仅保存 {obtained}/{total} 条回复，下次继续检查')
                        # A zero cursor is exhausted; a later retry must restart discovery.
                        job.update(cursor='0', cursor_history=[])
                    else:
                        job['complete'] = True
                        job['state'] = 'complete'
                    self.store.save_comment_job(job)
                except (LoginRequired, Throttled):
                    raise
                except ArchiveError as error:
                    job.update(state='error', error=str(error))
                    self.store.save_comment_job(job)
            # A desktop reply may carry the real parent object omitted by the
            # mobile page. Recheck mobile count gaps after those objects commit.
            for job in self.store.comment_jobs(post_id):
                if (job['kind'] == 'replies' and job.get('state') == 'partial'
                        and str(job.get('cursor')) == '0' and job.get('reported_total') is not None
                        and job.get('error', '').startswith('接口已结束但仅保存')
                        and self.store.thread_count(post_id, job['target_id']) >= job['reported_total']):
                    job.update(scan_complete=True, complete=True, state='complete', error='')
                    self.store.save_comment_job(job)
            await self._media(post_id)
            self.store.finish_comments(post_id)
            self.report(**self.store.comment_counts(self.order), **self.store.counts())
        self.report(comment_stage='finished')
