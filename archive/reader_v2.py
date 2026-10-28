"""Independent reader version: consumes exported data, never changes the archive DB."""
import json
import re
from pathlib import Path

from aiohttp import web

from .export import STATIC, atomic_text, safe_media

SNAPSHOT = re.compile(r'reader-v1-\d{8}-\d{6}\.html')
MIME = {'.mp4': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm',
        '.m4v': 'video/mp4', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif', '.avif': 'image/avif'}


def render_v2(payload, snapshot=''):
    safe = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    template = (STATIC / 'reader-v2.html').read_text('utf-8')
    template = template.replace('/* READER_STYLE */', (STATIC / 'reader-v2.css').read_text('utf-8'))
    template = template.replace('/* ARCHIVE_DATA */', 'const ARCHIVE = ' + safe + ';\nconst LEGACY_SNAPSHOT = ' + json.dumps(snapshot if SNAPSHOT.fullmatch(snapshot) else '') + ';')
    return template.replace('/* READER_SCRIPT */', (STATIC / 'reader-v2.js').read_text('utf-8'))


def export_v2(account):
    account = Path(account)
    payload = json.loads((account / 'archive.json').read_text('utf-8'))
    if not isinstance(payload, dict) or not isinstance(payload.get('posts'), list):
        raise ValueError('档案 JSON 格式无效')
    snapshots = sorted(path.name for path in account.glob('reader-v1-*.html') if SNAPSHOT.fullmatch(path.name))
    html = render_v2(payload, snapshots[-1] if snapshots else '')
    atomic_text(account / 'reader-v2.html', html)
    return html


def create_reader_app(account):
    account = Path(account).resolve()

    @web.middleware
    async def local_only(request, handler):
        expected = '127.0.0.1:' + str(request.transport.get_extra_info('sockname')[1])
        if (request.host != expected or request.headers.get('Origin', 'http://' + expected) != 'http://' + expected
                or request.headers.get('Sec-Fetch-Site') == 'cross-site'):
            return web.Response(status=403, text='请使用本机阅读器地址')
        if request.method not in ('GET', 'HEAD'):
            return web.Response(status=405, text='阅读器仅支持读取')
        try:
            response = await handler(request)
        except (OSError, ValueError):
            return web.Response(status=503, text='档案正在更新或暂不可用，请稍后刷新')
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'none'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"})
        return response

    app = web.Application(middlewares=[local_only])

    async def reader(request):
        return web.Response(text=export_v2(account), content_type='text/html', charset='utf-8')

    async def legacy(request):
        name = request.match_info.get('name', 'reader.html')
        if name != 'reader.html' and not SNAPSHOT.fullmatch(name):
            raise web.HTTPNotFound()
        target = (account / name).resolve()
        if target.parent != account or not target.is_file():
            raise web.HTTPNotFound()
        return web.Response(body=target.read_bytes(), content_type='text/html', charset='utf-8')

    async def media(request):
        try:
            target = safe_media(account, 'media/' + request.match_info['name'])
        except ValueError:
            raise web.HTTPNotFound() from None
        if not target.is_file():
            raise web.HTTPNotFound()
        response = web.FileResponse(target)
        response.content_type = MIME[target.suffix.lower()]
        return response

    app.router.add_get('/', reader)
    app.router.add_get('/reader-v2.html', reader)
    app.router.add_get('/reader.html', legacy)
    app.router.add_get(r'/{name:reader-v1-[0-9]{8}-[0-9]{6}\.html}', legacy)
    app.router.add_get('/media/{name}', media)
    return app
