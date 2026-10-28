import json
import os
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / 'static'


def atomic_text(path, content):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(content, 'utf-8')
    os.replace(temporary, path)


def export_archive(store, uid):
    posts = store.posts()
    statuses = store.comment_statuses()
    for post in posts:
        post['comment_backup'] = statuses.get(post['id'], {'state': 'not_started', 'count': 0,
                                                         'coverage': 'incomplete', 'errors': []})
    payload = {'schema_version': 2, 'account_uid': str(uid), 'posts': posts, 'comments': store.all_comments()}
    raw = json.dumps(payload, ensure_ascii=False)
    atomic_text(store.root / 'archive.json', raw)
    safe = raw.replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    template = (STATIC / 'reader.html').read_text('utf-8')
    html = template.replace('/* READER_STYLE */', (STATIC / 'reader.css').read_text('utf-8'))
    html = html.replace('/* ARCHIVE_DATA */', 'const ARCHIVE = ' + safe + ';')
    html = html.replace('/* READER_SCRIPT */', (STATIC / 'reader.js').read_text('utf-8'))
    atomic_text(store.root / 'reader.html', html)


def safe_media(root, path):
    import re
    if not re.fullmatch(r'media/[a-f0-9]{32}\.(jpg|jpeg|png|gif|webp|avif|mp4|mov|webm|m4v)', path):
        raise ValueError('无效媒体路径')
    result = (Path(root) / path).resolve()
    if not result.is_relative_to((Path(root) / 'media').resolve()):
        raise ValueError('无效媒体路径')
    return result
