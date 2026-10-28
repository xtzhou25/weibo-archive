import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .model import media_items, merge


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'archive.sqlite'
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS seen(job TEXT, id TEXT, PRIMARY KEY(job,id));
                CREATE TABLE IF NOT EXISTS totals(id INTEGER PRIMARY KEY, posts INTEGER, media INTEGER, incomplete INTEGER);
                INSERT OR IGNORE INTO totals(id,posts,media,incomplete) VALUES(1,0,0,0);
                CREATE TABLE IF NOT EXISTS comments(
                    post_id TEXT NOT NULL,id TEXT NOT NULL,root_id TEXT NOT NULL,
                    parent_id TEXT,data TEXT NOT NULL,PRIMARY KEY(post_id,id));
                CREATE INDEX IF NOT EXISTS comments_root ON comments(post_id,root_id);
                CREATE INDEX IF NOT EXISTS comments_parent ON comments(post_id,parent_id);
                CREATE TABLE IF NOT EXISTS comment_jobs(
                    post_id TEXT,kind TEXT,target_id TEXT,data TEXT NOT NULL,
                    PRIMARY KEY(post_id,kind,target_id));
                CREATE TABLE IF NOT EXISTS comment_totals(
                    id INTEGER PRIMARY KEY,comments INTEGER,media INTEGER,incomplete INTEGER);
                INSERT OR IGNORE INTO comment_totals(id,comments,media,incomplete) VALUES(1,0,0,0);
                PRAGMA user_version=2;
            ''')
            for table in ('totals', 'comment_totals'):
                if 'skipped' not in {row[1] for row in db.execute(f'PRAGMA table_info({table})')}:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0')
            if 'parent_relation' not in {row[1] for row in db.execute('PRAGMA table_info(comments)')}:
                db.execute('ALTER TABLE comments ADD COLUMN parent_relation TEXT')
                db.execute("UPDATE comments SET parent_relation=json_extract(data,'$.parent_relation')")
            if 'complete' not in {row[1] for row in db.execute('PRAGMA table_info(comment_jobs)')}:
                db.execute('ALTER TABLE comment_jobs ADD COLUMN complete INTEGER NOT NULL DEFAULT 0')
                db.execute("UPDATE comment_jobs SET complete=COALESCE(json_extract(data,'$.complete'),0)")
            db.execute('CREATE INDEX IF NOT EXISTS comments_relation ON comments(parent_relation,post_id)')
            db.execute('CREATE INDEX IF NOT EXISTS comment_jobs_complete ON comment_jobs(complete,post_id)')

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            with connection:
                yield connection
        finally:
            connection.close()

    def post(self, post_id):
        with self.connection() as db:
            row = db.execute('SELECT data FROM posts WHERE id=?', (post_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_post(self, post):
        with self.connection() as db:
            row = db.execute('SELECT data FROM posts WHERE id=?', (post['id'],)).fetchone()
            old_media = list(media_items(json.loads(row[0]))) if row else []
            new_media = list(media_items(post))
            db.execute('INSERT OR REPLACE INTO posts VALUES (?,?)', (post['id'], json.dumps(post, ensure_ascii=False)))
            done_delta = sum(item['status'] == 'done' for item in new_media) - sum(item['status'] == 'done' for item in old_media)
            pending_delta = sum(item['status'] not in ('done', 'skipped_size') for item in new_media) - sum(item['status'] not in ('done', 'skipped_size') for item in old_media)
            skipped_delta = sum(item['status'] == 'skipped_size' for item in new_media) - sum(item['status'] == 'skipped_size' for item in old_media)
            db.execute('UPDATE totals SET posts=posts+?,media=media+?,incomplete=incomplete+?,skipped=skipped+? WHERE id=1',
                       (int(row is None), done_delta, pending_delta, skipped_delta))

    def posts(self):
        with self.connection() as db:
            rows = db.execute('SELECT data FROM posts').fetchall()
        return sorted((json.loads(row[0]) for row in rows), key=lambda post: post.get('created_at', ''), reverse=True)

    def known(self, job, post_id):
        with self.connection() as db:
            return bool(db.execute('SELECT 1 FROM seen WHERE job=? AND id=?', (job, post_id)).fetchone())

    def mark(self, job, post_id):
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO seen VALUES (?,?)', (job, post_id))

    def job(self, key):
        with self.connection() as db:
            row = db.execute('SELECT data FROM jobs WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_job(self, key, value):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?)', (key, json.dumps(value)))

    def counts(self):
        with self.connection() as db:
            row = db.execute('SELECT posts,media,incomplete,skipped FROM totals WHERE id=1').fetchone()
            comments = db.execute('SELECT media,incomplete,skipped FROM comment_totals WHERE id=1').fetchone()
        return {'posts': row[0], 'media': row[1] + comments[0], 'incomplete': row[2] + comments[1],
                'skipped': row[3] + comments[2]}

    def _write_comment(self, db, record):
        previous = db.execute('SELECT data FROM comments WHERE post_id=? AND id=?',
                              (record['post_id'], record['id'])).fetchone()
        old = json.loads(previous[0]) if previous else None
        old_media = list(media_items(old)) if old else []
        if old:
            record = merge(dict(record), old)
            if record['parent_relation'] == 'thread_only' and old.get('parent_relation') in ('explicit', 'nested', 'root'):
                for key in ('parent_id', 'parent_relation', 'root_id'):
                    record[key] = old[key]
        new_media = list(media_items(record))
        db.execute('INSERT OR REPLACE INTO comments(post_id,id,root_id,parent_id,data,parent_relation) VALUES(?,?,?,?,?,?)',
                   (record['post_id'], record['id'], record['root_id'], record.get('parent_id'),
                    json.dumps(record, ensure_ascii=False), record['parent_relation']))
        db.execute('''UPDATE comment_totals SET comments=comments+?,media=media+?,
                      incomplete=incomplete+?,skipped=skipped+? WHERE id=1''',
                   (int(previous is None),
                    sum(item['status'] == 'done' for item in new_media) - sum(item['status'] == 'done' for item in old_media),
                    sum(item['status'] not in ('done', 'skipped_size') for item in new_media) - sum(item['status'] not in ('done', 'skipped_size') for item in old_media),
                    sum(item['status'] == 'skipped_size' for item in new_media) - sum(item['status'] == 'skipped_size' for item in old_media)))

    def save_comment(self, record):
        with self.connection() as db:
            self._write_comment(db, record)

    def comments_for_post(self, post_id):
        with self.connection() as db:
            rows = db.execute('SELECT data FROM comments WHERE post_id=?', (post_id,)).fetchall()
        return sorted((json.loads(row[0]) for row in rows), key=lambda item: (item.get('created_at', ''), item['id']))

    def all_comments(self):
        with self.connection() as db:
            rows = db.execute('SELECT data FROM comments ORDER BY post_id,id').fetchall()
        return [json.loads(row[0]) for row in rows]

    def comment_job(self, post_id, kind, target_id):
        with self.connection() as db:
            row = db.execute('SELECT data FROM comment_jobs WHERE post_id=? AND kind=? AND target_id=?',
                             (post_id, kind, target_id)).fetchone()
        return json.loads(row[0]) if row else None

    def comment_jobs(self, post_id):
        with self.connection() as db:
            rows = db.execute('SELECT data FROM comment_jobs WHERE post_id=? ORDER BY kind,target_id', (post_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _write_comment_job(self, db, job):
        db.execute('INSERT OR REPLACE INTO comment_jobs(post_id,kind,target_id,data,complete) VALUES(?,?,?,?,?)',
                   (job['post_id'], job['kind'], job['target_id'], json.dumps(job, ensure_ascii=False), int(bool(job.get('complete')))))

    def save_comment_job(self, job):
        with self.connection() as db:
            self._write_comment_job(db, job)

    @staticmethod
    def new_comment_job(post_id, kind, target_id, cycle, root_id=None):
        return {'post_id': post_id, 'kind': kind, 'target_id': target_id, 'root_id': root_id,
                'cycle': cycle, 'cursor': '0', 'cursor_type': 0, 'cursor_history': [],
                'scan_complete': False, 'complete': False, 'state': 'pending', 'error': ''}

    def prepare_comments(self, post_id):
        job = self.comment_job(post_id, 'root', post_id)
        if job is None or job.get('complete'):
            job = self.new_comment_job(post_id, 'root', post_id, (job['cycle'] + 1) if job else 1)
        elif job.get('state') in ('error', 'partial'):
            job.update(state='pending', error='')
        self.save_comment_job(job)

    def commit_comment_page(self, job, records):
        # Comment rows, discovered reply queues and the next cursor commit together.
        with self.connection() as db:
            for record in records:
                self._write_comment(db, record)
                if record['parent_relation'] == 'thread_only' and job['kind'] != 'parents':
                    target = record['root_id']
                    row = db.execute('SELECT data FROM comment_jobs WHERE post_id=? AND kind=? AND target_id=?',
                                     (job['post_id'], 'parents', target)).fetchone()
                    lookup = json.loads(row[0]) if row else None
                    if lookup is None or (lookup.get('complete') and lookup['cycle'] != job['cycle']):
                        lookup = self.new_comment_job(job['post_id'], 'parents', target, job['cycle'], target)
                    self._write_comment_job(db, lookup)
                is_root = record['id'] == record['root_id']
                has_replies = record.get('has_inline_replies') or (record.get('reply_count') or 0) > 0
                if not has_replies and not (is_root and record.get('reply_count') is None):
                    continue
                target = record['id']
                row = db.execute('SELECT data FROM comment_jobs WHERE post_id=? AND kind=? AND target_id=?',
                                 (job['post_id'], 'replies', target)).fetchone()
                child = json.loads(row[0]) if row else None
                if child is None or (child.get('complete') and child['cycle'] != job['cycle']):
                    child = self.new_comment_job(job['post_id'], 'replies', target, job['cycle'], record['root_id'])
                self._write_comment_job(db, child)
            self._write_comment_job(db, job)

    def thread_count(self, post_id, target_id):
        with self.connection() as db:
            row = db.execute('''WITH RECURSIVE descendants(id) AS (
                SELECT id FROM comments WHERE post_id=? AND (root_id=? OR parent_id=?) AND id!=?
                UNION SELECT c.id FROM comments c JOIN descendants d ON c.parent_id=d.id WHERE c.post_id=?
                ) SELECT COUNT(*) FROM descendants''',
                             (post_id, target_id, target_id, target_id, post_id)).fetchone()
        return row[0]

    def finish_comments(self, post_id):
        root = self.comment_job(post_id, 'root', post_id)
        if not root:
            return
        children = [job for job in self.comment_jobs(post_id) if job['kind'] != 'root']
        if root.get('scan_complete') and all(job.get('complete') for job in children):
            root.update(complete=True, state='complete', error='')
        elif root.get('state') != 'error':
            root.update(complete=False, state='partial', error='仍有未完成的评论分页或回复队列')
        self.save_comment_job(root)

    def comment_counts(self, post_ids=None):
        with self.connection() as db:
            total = db.execute('SELECT comments FROM comment_totals WHERE id=1').fetchone()[0]
            if post_ids is None:
                pending = db.execute('SELECT COUNT(*) FROM comment_jobs WHERE complete=0').fetchone()[0]
                unresolved = db.execute("SELECT COUNT(*) FROM comments WHERE parent_relation='thread_only'").fetchone()[0]
            else:
                selected = set(post_ids)
                pending = sum(row[1] for row in db.execute('SELECT post_id,COUNT(*) FROM comment_jobs WHERE complete=0 GROUP BY post_id') if row[0] in selected)
                unresolved = sum(row[1] for row in db.execute("SELECT post_id,COUNT(*) FROM comments WHERE parent_relation='thread_only' GROUP BY post_id") if row[0] in selected)
        return {'comments': total, 'comment_jobs_pending': pending, 'comment_parent_unresolved': unresolved}

    def comment_status(self, post_id):
        jobs = self.comment_jobs(post_id)
        root = next((job for job in jobs if job['kind'] == 'root'), None)
        with self.connection() as db:
            count = db.execute('SELECT COUNT(*) FROM comments WHERE post_id=?', (post_id,)).fetchone()[0]
            unresolved = db.execute("SELECT COUNT(*) FROM comments WHERE post_id=? AND parent_relation='thread_only'", (post_id,)).fetchone()[0]
        return {'state': root['state'] if root else 'not_started', 'count': count,
                'coverage': 'api_exhausted' if root and root.get('complete') else 'incomplete',
                'parent_links_unresolved': unresolved,
                'errors': [job['error'] for job in jobs if job.get('error')][:10]}

    def comment_statuses(self):
        with self.connection() as db:
            rows = db.execute('''SELECT post_id,COUNT(*),
                SUM(CASE WHEN parent_relation='thread_only' THEN 1 ELSE 0 END)
                FROM comments GROUP BY post_id''').fetchall()
            jobs = [json.loads(row[0]) for row in db.execute('SELECT data FROM comment_jobs')]
        result = {row[0]: {'state': 'not_started', 'count': row[1], 'parent_links_unresolved': row[2],
                           'coverage': 'incomplete', 'errors': []} for row in rows}
        for job in jobs:
            info = result.setdefault(job['post_id'], {'state': 'not_started', 'count': 0,
                                     'parent_links_unresolved': 0, 'coverage': 'incomplete', 'errors': []})
            if job['kind'] == 'root':
                info['state'] = job['state']
                info['coverage'] = 'api_exhausted' if job.get('complete') else 'incomplete'
            if job.get('error') and len(info['errors']) < 10:
                info['errors'].append(job['error'])
        return result
