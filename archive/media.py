import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from .transport import ArchiveError, allowed


def exceeds_video_limit(item, limit):
    return bool(limit and item['kind'] == 'video' and item.get('size_bytes', 0) > limit)


async def download(transport, item, root, report, video_max_bytes=0):
    if item['status'] == 'unsupported' or not item.get('url'):
        return
    address = urlsplit(item['url'])
    secure = address._replace(scheme='https').geturl()
    if address.scheme == 'http' and allowed(secure, media=True):
        item['url'] = secure
    suffix = Path(urlsplit(item['url']).path).suffix.lower()
    if suffix not in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif', '.mp4', '.mov', '.webm', '.m4v'):
        suffix = '.mp4' if item['kind'] == 'video' else '.jpg'
    relative = f'media/{item["id"]}{suffix}'
    target = Path(root) / relative
    target.parent.mkdir(exist_ok=True)
    part, metadata = target.with_suffix(suffix + '.part'), target.with_suffix(suffix + '.part.json')
    if target.exists() and target.stat().st_size:
        item.update(status='done', path=relative, error='', size_bytes=target.stat().st_size, size_is_lower_bound=False)
        return
    limit = video_max_bytes if item['kind'] == 'video' else 0

    def skip(total, lower_bound=False):
        part.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)
        measured = f'{"至少 " if lower_bound else ""}{total / 1024 ** 2:.1f} MB'
        reason = f'视频大小 {measured}，超过设置上限 {limit / 1024 ** 2:g} MB，已跳过本地下载'
        item.update(status='skipped_size', path='', error='', skip_reason=reason,
                    size_bytes=total, size_is_lower_bound=lower_bound, size_limit_bytes=limit)
        report(message=reason, current_file='', file_downloaded=0, file_total=0)

    if item['status'] == 'skipped_size' and exceeds_video_limit(item, limit):
        skip(item['size_bytes'], item.get('size_is_lower_bound', False))
        return
    for attempt in range(4):
        size = part.stat().st_size if part.exists() else 0
        if limit and size > limit:
            skip(size, True)
            return
        try:
            previous = json.loads(metadata.read_text('utf-8'))
        except (OSError, ValueError):
            previous = {}
        headers = {'Accept-Encoding': 'identity'}
        if size and previous.get('validator'):
            headers.update({'Range': f'bytes={size}-', 'If-Range': previous['validator']})
        elif size:
            part.unlink()
            size = 0
        response = await transport.request(item['url'], headers=headers, media=True, stream=True)
        try:
            if response.status_code == 416:
                part.unlink(missing_ok=True)
                metadata.unlink(missing_ok=True)
                continue
            if response.status_code not in (200, 206):
                raise ArchiveError(f'媒体不可用（HTTP {response.status_code}），下次可重试')
            mime = response.headers.get('content-type', '').lower()
            if any(value in mime for value in ('text/', 'html', 'json', 'mpegurl')):
                raise ArchiveError('媒体响应不是可下载的图片或普通视频')
            validator = response.headers.get('etag') or response.headers.get('last-modified') or ''
            if response.status_code == 206:
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('content-range', ''))
                if not match or int(match[1]) != size:
                    raise ArchiveError('分段下载位置不一致，未追加数据')
                if previous.get('validator') and validator and previous['validator'] != validator:
                    part.unlink(missing_ok=True)
                    metadata.unlink(missing_ok=True)
                    continue
                total, mode = int(match[3]), 'ab'
            else:
                total, size, mode = int(response.headers.get('content-length') or 0), 0, 'wb'
            if limit and total > limit:
                skip(total)
                return
            metadata.write_text(json.dumps({'validator': validator}), 'utf-8')
            report(file_downloaded=size, file_total=total, current_file=relative)
            exceeded = False
            with part.open(mode) as output:
                async for chunk in response.aiter_bytes(128 * 1024):
                    if transport.gate:
                        await transport.gate()
                    if limit and size + len(chunk) > limit:
                        # The response may omit or understate its size. Do not
                        # write beyond the cap; close the file before cleanup on Windows.
                        report(bytes_delta=len(chunk))
                        exceeded = True
                        break
                    output.write(chunk)
                    size += len(chunk)
                    report(bytes_delta=len(chunk), file_downloaded=size, file_total=total, current_file=relative)
                output.flush()
                os.fsync(output.fileno())
            if exceeded:
                skip(size + len(chunk), True)
                return
            if not size or (total and total != size):
                raise httpx.ReadError('Incomplete media')
            os.replace(part, target)
            metadata.unlink(missing_ok=True)
            item.update(status='done', path=relative, error='', size_bytes=size, size_is_lower_bound=False)
            item.pop('skip_reason', None)
            item.pop('size_limit_bytes', None)
            return
        except httpx.TransportError:
            if attempt == 3:
                raise ArchiveError('媒体传输中断；未完成文件已保留，可续传') from None
            await transport.wait(2 ** (attempt + 1))
        finally:
            await response.aclose()
    raise ArchiveError('媒体续传失败；保留未完成记录')
