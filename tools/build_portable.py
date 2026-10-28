"""Build a minimal, browser-free Windows x64 distribution in a NEW directory."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = '3.13.17'
EMBED_URL = f'https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip'
EMBED_SHA256 = '9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prune(site):
    """Omit developer-only wheel files; retain all code, binaries and licenses."""
    omitted = []
    for path in sorted(site.rglob('*'), reverse=True):
        if not path.is_file():
            continue
        parts = path.relative_to(site).parts
        if ('__pycache__' in parts or 'tests' in parts or parts[0] in ('bin', 'Scripts')
                or path.suffix in ('.pyi', '.pyc', '.pyo', '.h') or path.name == 'py.typed'):
            assert path.resolve().is_relative_to(site.resolve())
            omitted.append(path.relative_to(site).as_posix())
            path.unlink()
    for path in sorted(site.rglob('*'), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    # RECORD must describe only retained wheel files, including relocatable paths.
    for record in site.glob('*.dist-info/RECORD'):
        rows = list(csv.reader(io.StringIO(record.read_text('utf-8'))))
        rows = [row for row in rows if '..' not in Path(row[0]).parts and (site / row[0]).is_file()]
        stream = io.StringIO(newline='')
        csv.writer(stream, lineterminator='\n').writerows(rows)
        record.write_text(stream.getvalue(), encoding='utf-8', newline='')
    return omitted


def build(output, embed_zip=None):
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 13):
        raise RuntimeError('Build with Windows x64 Python 3.13; the target runtime is Python 3.13')
    version = (ROOT / 'VERSION').read_text('utf-8').strip()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'weibo-archive-v{version}-windows-x64-portable.zip'
    if archive.exists():
        raise FileExistsError('Existing release is preserved: ' + str(archive))
    stage = output / f'portable-build-{datetime.now():%Y%m%d-%H%M%S-%f}'
    root = stage / 'weibo-archive-portable'
    runtime = root / 'runtime'
    runtime.mkdir(parents=True)
    if embed_zip is None:
        embed_zip = stage / 'python-embed.zip'
        with urllib.request.urlopen(EMBED_URL, timeout=90) as response, embed_zip.open('wb') as target:
            shutil.copyfileobj(response, target)
    if sha256(embed_zip) != EMBED_SHA256:
        raise ValueError('Official Python embedded archive SHA-256 mismatch')
    with zipfile.ZipFile(embed_zip) as package:
        for name in package.namelist():
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('Unsafe embedded runtime entry')
        package.extractall(runtime)
    (runtime / 'python313._pth').write_text('python313.zip\n.\nLib/site-packages\n..\n', encoding='ascii')
    site = runtime / 'Lib/site-packages'
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
                    '--only-binary=:all:', '--no-compile', '--target', str(site),
                    '-r', str(ROOT / 'requirements.txt')], check=True)
    omitted = prune(site)
    dependencies = {d.metadata['Name'].lower().replace('_', '-'): d.version
                    for d in importlib.metadata.distributions(path=[str(site)])}
    expected = dict(line.strip().split('==') for line in (ROOT / 'requirements.lock.txt').read_text().splitlines()
                    if line.strip() and not line.startswith('#'))
    expected = {name.lower().replace('_', '-'): version for name, version in expected.items()}
    if dependencies != expected:
        raise ValueError('Runtime dependencies differ from the lock file')

    # Explicit allowlist: no personal data, virtual environments, tests or tools.
    for name in ('app.py', 'reader_v2.py', 'VERSION', 'LICENSE', 'README.md', 'NOTICE.md',
                 'NETWORK.md', 'READER_V2.md', 'FAILED_MEDIA_RETRY.md', 'CHANGELOG.md',
                 'requirements.txt', 'requirements.lock.txt', 'PORTABLE.md',
                 'CONTRIBUTING.md', 'VERIFICATION.md', 'LIVE_COMMENT_TEST.md', 'LIVE_VISIBILITY_TEST.md'):
        shutil.copyfile(ROOT / name, root / name)
    for folder, extensions in (('archive', {'.py'}), ('static', {'.js', '.css', '.html'})):
        for path in (ROOT / folder).rglob('*'):
            if path.is_file() and path.suffix in extensions and '__pycache__' not in path.parts:
                target = root / path.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
    sys.path.insert(0, str(ROOT))
    from demo import build as demo_build
    from archive.reader_v2 import export_v2
    with tempfile.TemporaryDirectory() as temporary:
        demo = Path(temporary)
        demo_build(demo)
        export_v2(demo)
        for path in [demo / name for name in ('archive.json', 'reader.html', 'reader-v2.html')] + list((demo / 'media').glob('*.png')):
            target = root / 'demo' / path.relative_to(demo)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)

    header = '''@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 goto failed
set "PYTHONHOME="
set "PYTHONPATH="
set "PLAYWRIGHT_NODEJS_PATH="
set "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1"
if not exist "%~dp0runtime\\python.exe" (
  echo 未找到自带运行时。请完整解压 ZIP 后再双击启动。
  goto failed
)
'''
    footer = '''
if errorlevel 1 goto failed
exit /b 0
:failed
echo 启动失败，请保留错误信息。已有备份不会被删除。
pause
exit /b 1
'''
    for name, command in {
        'start_windows.bat': '"%~dp0runtime\\python.exe" -B -X utf8 "%~dp0app.py" %*',
        'start_reader_v2.bat': '"%~dp0runtime\\python.exe" -B -X utf8 "%~dp0reader_v2.py" %*',
        'start_demo.bat': '"%~dp0runtime\\python.exe" -B -X utf8 "%~dp0reader_v2.py" --account-dir "%~dp0demo" %*',
    }.items():
        (root / name).write_bytes((header + command + footer).replace('\n', '\r\n').encode('utf-8'))
    (root / '使用说明-先读我.txt').write_text((ROOT / 'PORTABLE.md').read_text('utf-8'), encoding='utf-8-sig')
    metadata = {'version': version, 'built_at': datetime.now(timezone.utc).isoformat(),
                'python': PYTHON_VERSION, 'python_archive_sha256': EMBED_SHA256,
                'dependencies': dependencies, 'bundled_browsers': [],
                'login_browser': 'Installed Microsoft Edge, then Google Chrome; isolated temporary profile',
                'control_transport': 'Playwright private pipe; no debugging TCP port',
                'omitted': ['Browser binaries and cache', 'pip/setuptools/wheel', 'Project tests and build tools'],
                'omitted_wheel_files': sorted(omitted)}
    (root / 'PORTABLE_BUILD.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    files = {}
    total = 0
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if path.suffix.lower() == '.exe' and path.name.lower() not in ('python.exe', 'pythonw.exe', 'node.exe'):
            raise ValueError('Unexpected executable in package: ' + relative.as_posix())
        if set(relative.parts) & {'data', 'private', 'accounts', '.git', '.venv', '__pycache__', 'tests'}:
            raise ValueError('Unexpected private/developer data: ' + relative.as_posix())
        payload = path.read_bytes()
        private_paths = (str(Path.home()), Path.home().as_posix(), str(ROOT), ROOT.as_posix())
        if any(marker.encode('utf-8') in payload for marker in private_paths):
            raise ValueError('Private build marker: ' + relative.as_posix())
        files[relative.as_posix()] = hashlib.sha256(payload).hexdigest()
        total += len(payload)
    (root / 'PORTABLE_MANIFEST.json').write_text(json.dumps({'version': version, 'files': files}, indent=2) + '\n', 'utf-8')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for path in sorted(root.rglob('*')):
            if path.is_file():
                package.write(path, (Path(root.name) / path.relative_to(root)).as_posix())
    digest = sha256(archive)
    archive.with_name(archive.name + '.sha256').write_text(digest + '  ' + archive.name + '\n', 'ascii')
    result = {'zip': str(archive), 'root': str(root), 'sha256': digest, 'bytes': archive.stat().st_size,
              'uncompressed_bytes': total, 'files': len(files) + 1, 'dependencies': len(dependencies)}
    (stage / 'build-result.json').write_text(json.dumps(result, indent=2), 'utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--embed-zip', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.embed_zip), ensure_ascii=False, indent=2))
