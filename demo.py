"""Build a fictional offline example without login or network."""
import base64
from pathlib import Path
from archive.model import normalize, options
from archive.store import Store
from archive.export import export_archive
from archive.comments import normalize_comments


def build(root):
    store = Store(root)
    config = options({})
    samples = [
        {'id': '1001', 'created_at': '2026-10-27T09:00:00+08:00', 'user': {'id': 123, 'screen_name': '演示账号'},
         'text_raw': '这是一份本地档案。\n文字、照片和转发保存在同一条记录中。', 'visible': {'type': 6},
         'pics': [{'pid': 'example', 'url': 'https://wx1.sinaimg.cn/example.png'}],
         'retweeted_status': {'id': 1000, 'text_raw': '这条转发原文也保存在本地。', 'visible': {'type': 0}, 'user': {'screen_name': '原作者'}}},
        {'id': '1002', 'created_at': '2026-10-26T17:00:00+08:00', 'user': {'screen_name': '演示账号'},
         'text_raw': '演示中没有真实微博内容，也不会触发登录或网络请求。', 'visible': {'type': 1}},
    ]
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aHYQAAAAASUVORK5CYII=')
    for raw in samples:
        post = normalize(raw, config)
        for item in post['media']:
            relative = f'media/{item["id"]}.png'
            (store.root / 'media').mkdir(exist_ok=True)
            (store.root / relative).write_bytes(png)
            item.update(status='done', path=relative, error='')
        store.save_post(post)
    comments = normalize_comments([{'id': '2001', 'text_raw': '这是一级评论。', 'user': {'screen_name': '读者甲'},
        'comments': [{'id': '2002', 'text_raw': '这条回复关联一级评论。', 'user': {'screen_name': '读者乙'},
        'comments': [{'id': '2003', 'text_raw': '这是更深一层回复，父评论是 2002。',
                      'user': {'screen_name': '读者丙'}}]}]}], '1001', config)
    for comment in comments:
        store.save_comment(comment)
    store.prepare_comments('1001')
    job = store.comment_job('1001', 'root', '1001')
    job.update(scan_complete=True, complete=True, state='complete')
    store.save_comment_job(job)
    export_archive(store, 'demo')
    return store.root / 'reader.html'


if __name__ == '__main__':
    print(build(Path(__file__).parent / 'demo'))
