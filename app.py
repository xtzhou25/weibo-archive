import argparse
import asyncio
import errno
import json
import secrets
import socket
import webbrowser
from pathlib import Path

from aiohttp import web
from archive.auth import Auth
from archive.engine import Engine
from archive.export import safe_media, export_archive
from archive.store import Store

STATIC = Path(__file__).parent / 'static'
AUTH_KEY = web.AppKey('auth', Auth)
ENGINE_KEY = web.AppKey('engine', Engine)


def create_app(root, auth=None, engine=None):
    auth = auth or Auth(root)
    engine = engine or Engine(root, auth)
    token = secrets.token_urlsafe(32)

    @web.middleware
    async def security(request, handler):
        expected = '127.0.0.1:' + str(request.transport.get_extra_info('sockname')[1])
        if (request.host != expected or request.headers.get('Origin', 'http://' + expected) != 'http://' + expected
                or request.headers.get('Sec-Fetch-Site') == 'cross-site'):
            return web.json_response({'error': '来源校验失败，请使用启动窗口中的本地地址'}, status=403)
        if request.method != 'GET' and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), token):
            return web.json_response({'error': '页面会话已更新，请刷新页面'}, status=403)
        try:
            response = await handler(request)
        except web.HTTPException as error:
            response = web.json_response({'error': error.reason}, status=error.status)
        except (ValueError, TypeError) as error:
            response = web.json_response({'error': str(error) or '无效操作'}, status=400)
        response.headers.update({'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store',
                                 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
                                 'Content-Security-Policy': "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"})
        return response

    app = web.Application(middlewares=[security], client_max_size=8192)
    app[AUTH_KEY], app[ENGINE_KEY] = auth, engine

    async def status(request):
        if auth.ready and not engine.active and (engine.store is None or engine.store.root.name != auth.uid):
            path = Path(root) / 'accounts' / auth.uid
            if (path / 'archive.sqlite').exists():
                engine.store = Store(path)
                engine.report(**engine.store.counts(), **engine.store.comment_counts())
        return web.json_response({'auth': auth.status(), 'backup': dict(engine.status), 'active': engine.active, 'csrf': token,
                'features': {'comments': True, 'video_size_limit': True, 'failed_media_retry': True,
                                               'visibility': True, 'archive_schema': 2}})

    async def qr(request):
        if not auth.qr:
            return web.json_response({'error': '二维码尚未就绪或已失效'}, status=404)
        return web.Response(body=auth.qr, content_type='image/png')

    async def command(request):
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise ValueError('请求数据格式错误') from None
        if not isinstance(body, dict):
            raise ValueError('请求必须为 JSON 对象')
        action = request.match_info['action']
        if action in ('login', 'logout') and engine.active:
            raise ValueError('请先停止备份再切换登录')
        if action == 'login':
            await auth.begin(body.get('persist', False), body.get('mode', 'qr'))
        elif action == 'logout':
            await auth.logout()
        elif action == 'start':
            await engine.start(body)
        elif action == 'pause':
            engine.pause()
        elif action == 'resume':
            engine.resume()
        elif action == 'stop':
            await engine.cancel()
        elif action == 'export':
            if not engine.store:
                raise ValueError('还没有可导出的备份')
            export_archive(engine.store, engine.store.root.name)
        else:
            raise web.HTTPNotFound()
        return web.json_response({'ok': True})

    async def static(request):
        name = request.match_info.get('name') or 'index.html'
        mime = {'index.html': 'text/html', 'app.js': 'application/javascript', 'style.css': 'text/css'}.get(name)
        if not mime:
            raise web.HTTPNotFound()
        return web.Response(body=(STATIC / name).read_bytes(), content_type=mime, charset='utf-8')

    async def archive_file(request):
        if not engine.store:
            raise web.HTTPNotFound(reason='尚未产生备份，请先运行备份')
        name = request.match_info['name']
        if name not in ('reader.html', 'archive.json'):
            raise web.HTTPNotFound()
        target = engine.store.root / name
        if not target.is_file():
            raise web.HTTPNotFound()
        return web.Response(body=target.read_bytes(), content_type='text/html' if name.endswith('.html') else 'application/json', charset='utf-8')

    async def media(request):
        if not engine.store:
            raise web.HTTPNotFound()
        target = safe_media(engine.store.root, 'media/' + request.match_info['name'])
        if not target.is_file():
            raise web.HTTPNotFound()
        response = web.FileResponse(target)
        suffix = target.suffix.lower()
        response.content_type = {'.mp4': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm',
                                 '.m4v': 'video/mp4', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                                 '.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif', '.avif': 'image/avif'}[suffix]
        return response

    app.router.add_get('/api/status', status)
    app.router.add_get('/api/qr', qr)
    app.router.add_post('/api/{action}', command)
    app.router.add_get('/', static)
    app.router.add_get('/static/{name}', static)
    app.router.add_get('/{name:reader.html|archive.json}', archive_file)
    app.router.add_get('/media/{name}', media)

    async def cleanup(application):
        await engine.cancel()
        await auth.stop()
    app.on_cleanup.append(cleanup)
    return app


def bind_port(port):
    sock = socket.socket()
    try:
        sock.bind(('127.0.0.1', port))
    except OSError as error:
        sock.close()
        if port == 0 or (error.errno not in (errno.EACCES, errno.EADDRINUSE) and getattr(error, 'winerror', None) not in (10013, 10048)):
            raise
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
    sock.setblocking(False)
    return sock


async def run(args):
    root = args.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    app = create_app(root)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    sock = bind_port(args.port)
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    url = f'http://127.0.0.1:{port}/'
    print(f'本地入口：{url}\n数据目录：{root}\n关闭程序会保留备份检查点。', flush=True)
    if not args.no_browser:
        await asyncio.to_thread(webbrowser.open, url)
    restore = asyncio.create_task(app[AUTH_KEY].restore())
    try:
        await asyncio.Event().wait()
    finally:
        restore.cancel()
        await asyncio.gather(restore, return_exceptions=True)
        await runner.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='全新实现的微博本地档案')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).parent / 'data')
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('端口必须在 0 到 65535 之间')
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
