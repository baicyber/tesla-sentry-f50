#!/usr/bin/env python3
"""Build a versioned offline tarball from a clean checkout and audited ARM64 runtime."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile


PYTHON_WRAPPER = '''#!/bin/sh
set -eu
TSF_RUNTIME=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export PYTHONHOME="$TSF_RUNTIME/python"
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export TSF_PYTHON_EXECUTABLE="$TSF_RUNTIME/bin/python3"
export SSL_CERT_FILE="$TSF_RUNTIME/certs/cacert.pem"
export PATH="$TSF_RUNTIME/bin:/usr/sbin:/usr/bin:/sbin:/bin"
exec "$TSF_RUNTIME/lib/ld-musl-aarch64.so.1" --library-path "$TSF_RUNTIME/lib" \
    "$TSF_RUNTIME/python/bin/python3.13" "$@"
'''

SITE_CUSTOMIZE = '''# Keep child interpreters and exec-based restarts on the bundled loader.
import os
import sys
if os.environ.get('TSF_PYTHON_EXECUTABLE'):
    sys.executable = os.environ['TSF_PYTHON_EXECUTABLE']
    sys._base_executable = sys.executable
'''


def wrappers():
    result = {'bin/python3': PYTHON_WRAPPER,
              'python/lib/python3.13/sitecustomize.py': SITE_CUSTOMIZE}
    for name in ('blkid', 'flock'):
        result['bin/' + name] = '''#!/bin/sh
set -eu
TSF_RUNTIME=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$TSF_RUNTIME/lib/ld-musl-aarch64.so.1" --library-path "$TSF_RUNTIME/lib" \
    "$TSF_RUNTIME/native/''' + name + '''" "$@"
'''
    for name in ('losetup', 'mount', 'umount', 'readlink', 'setsid', 'sha256sum'):
        result['bin/' + name] = '''#!/bin/sh
set -eu
TSF_RUNTIME=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$TSF_RUNTIME/bin/busybox" ''' + name + ''' "$@"
'''
    return {name: content.encode('utf-8') for name, content in result.items()}


def build(source, runtime, output):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args])
    if git('status', '--porcelain').strip():
        raise RuntimeError('Commit the reviewed source before packaging')
    commit = git('rev-parse', 'HEAD').decode().strip()
    exported = json.loads((runtime / 'manifest.json').read_text(encoding='utf-8'))
    files = {}
    for name, info in exported['files'].items():
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or str(path) != name:
            raise RuntimeError('Unsafe runtime path: ' + name)
        data = (runtime / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != info['sha256'] or len(data) != info['size']:
            raise RuntimeError('Runtime checksum mismatch: ' + name)
        files[name] = data
    # Supplementary files are public CA roots and upstream license/provenance texts.
    for directory in ('certs', 'licenses'):
        for path in (runtime / directory).rglob('*'):
            if path.is_file():
                files[path.relative_to(runtime).as_posix()] = path.read_bytes()
    if 'certs/cacert.pem' not in files or 'licenses/NOTICE.md' not in files:
        raise RuntimeError('Missing CA roots or third-party notices')
    files.update(wrappers())
    manifest = {key: exported[key] for key in ('platform', 'python', 'ffmpeg', 'dependencies')}
    manifest['files'] = {}
    for name, data in sorted(files.items()):
        executable = name.startswith(('bin/', 'native/', 'lib/', 'python/bin/'))
        manifest['files'][name] = dict(sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                                      mode=0o755 if executable else 0o644)
    files['manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, 'x:gz', compresslevel=6) as archive:
        def add(name, data, mode):
            info = tarfile.TarInfo('tesla-sentry-f50/' + name)
            info.size, info.mode, info.mtime = len(data), mode, 0
            archive.addfile(info, io.BytesIO(data))
        for entry in git('ls-tree', '-r', '-z', 'HEAD').split(b'\0'):
            if not entry:
                continue
            meta, raw_name = entry.split(b'\t', 1)
            mode, kind, obj = meta.split()
            if kind != b'blob':
                raise RuntimeError('Unsupported source entry')
            add(raw_name.decode('utf-8'), git('cat-file', 'blob', obj.decode()), int(mode, 8) & 0o777)
        for name, data in sorted(files.items()):
            add('runtime/' + name, data, manifest['files'].get(name, {}).get('mode', 0o644))
        add('PACKAGE.json', (json.dumps({'commit': commit, 'runtime': manifest['platform'],
             'runtime_manifest_sha256': hashlib.sha256(files['manifest.json']).hexdigest()}, indent=2) + '\n').encode(), 0o644)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + '.sha256').write_text(digest + '  ' + output.name + '\n', encoding='utf-8')
    print(json.dumps(dict(package=str(output), bytes=output.stat().st_size, sha256=digest,
                         commit=commit, runtime_files=len(manifest['files'])), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    build(Path(__file__).resolve().parents[1], args.runtime, args.output)
