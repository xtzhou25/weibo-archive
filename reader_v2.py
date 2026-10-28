"""Serve the new reader alongside an active backup without restarting it."""
import argparse
import asyncio
from pathlib import Path
import webbrowser

from aiohttp import web
from app import bind_port
from archive.reader_v2 import create_reader_app, export_v2


async def run(args):
    account = args.account_dir
    if account is None:
        accounts = [path for path in (Path(__file__).parent / 'data' / 'accounts').glob('*') if (path / 'archive.json').is_file()]
        if len(accounts) != 1:
            raise ValueError('请用 --account-dir 指定已备份账号目录；该目录应包含 archive.json 和 media 文件夹')
        account = accounts[0]
    account = account.resolve()
    export_v2(account)
    runner = web.AppRunner(create_reader_app(account), access_log=None)
    await runner.setup()
    sock = bind_port(args.port)
    await web.SockSite(runner, sock).start()
    url = f'http://127.0.0.1:{sock.getsockname()[1]}/'
    print(f'九宫格阅读器 v2：{url}\n仅生成 reader-v2.html，原版阅读器及备份任务保留。', flush=True)
    if not args.no_browser:
        await asyncio.to_thread(webbrowser.open, url)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='微博离线阅读器 · 九宫格版 v2')
    parser.add_argument('--account-dir', type=Path)
    parser.add_argument('--port', type=int, default=8788)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('端口必须在 0 到 65535 之间')
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')
