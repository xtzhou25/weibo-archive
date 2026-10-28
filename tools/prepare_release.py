"""Build a new source-only staging folder and ZIP; never copy runtime archives."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    '.gitignore', '.gitattributes', '.python-version', 'VERSION', 'LICENSE',
    'README.md', 'NOTICE.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'VERIFICATION.md',
    'NETWORK.md', 'READER_V2.md', 'FAILED_MEDIA_RETRY.md', 'LIVE_COMMENT_TEST.md',
    'LIVE_VISIBILITY_TEST.md', 'requirements.txt', 'requirements.lock.txt',
    'app.py', 'reader_v2.py', 'demo.py', 'weiboutil.py', 'start_windows.bat',
    'start_reader_v2.bat', 'PORTABLE.md',
)
SOURCE_TREES = {
    'archive': {'.py'}, 'static': {'.html', '.css', '.js'},
    'tests': {'.py'}, 'tools': {'.py'}, '.github': {'.yml'},
}


def source_files(root):
    paths = [root / name for name in ROOT_FILES]
    for name, suffixes in SOURCE_TREES.items():
        paths.extend(path for path in (root / name).rglob('*')
                     if path.is_file() and path.suffix in suffixes and '__pycache__' not in path.parts)
    for path in paths:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Invalid source entry: ' + path.name)
        yield path


def build(output=None):
    root = ROOT.resolve()
    version = (root / 'VERSION').read_text('utf-8').strip()
    if not version or any(char not in '0123456789abcdefghijklmnopqrstuvwxyz.-' for char in version):
        raise ValueError('Invalid version')
    output = Path(output).resolve() if output else root / 'dist'
    output.mkdir(parents=True, exist_ok=True)
    name = f'weibo-archive-{version}-{datetime.now():%Y%m%d-%H%M%S-%f}'
    destination = output / name
    destination.mkdir(exist_ok=False)
    for path in source_files(root):
        target = destination / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = path.read_bytes()
        if path.suffix == '.bat':
            content = content.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        target.write_bytes(content)

    # Rebuild fictional demo from source. Existing demo/ and data/ are never copied.
    sys.path.insert(0, str(root))
    from demo import build as build_demo
    from archive.reader_v2 import export_v2
    with tempfile.TemporaryDirectory() as temporary:
        demo = Path(temporary)
        build_demo(demo)
        export_v2(demo)
        for path in [demo / name for name in ('archive.json', 'reader.html', 'reader-v2.html')] + list((demo / 'media').glob('*.png')):
            target = destination / 'demo' / path.relative_to(demo)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)

    paths = sorted(path for path in destination.rglob('*') if path.is_file())
    manifest = {path.relative_to(destination).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in paths}
    (destination / 'SOURCE_MANIFEST.json').write_text(json.dumps(
        {'version': version, 'files': manifest}, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    archive = destination.parent / (destination.name + '.zip')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as output_zip:
        for path in sorted(destination.rglob('*')):
            if path.is_file():
                output_zip.write(path, name + '/' + path.relative_to(destination).as_posix())
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + '.sha256').write_text(checksum + '  ' + archive.name + '\n', 'utf-8')
    return {'directory': str(destination), 'zip': str(archive), 'files': len(manifest) + 1,
            'sha256': checksum, 'bytes': archive.stat().st_size}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False, indent=2))
