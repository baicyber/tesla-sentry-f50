#!/usr/bin/env python3
"""Check the bundled ARM64 runtime without installing or touching services/USB."""
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def check(runtime):
    for name in ('ssl', 'fcntl', 'http.server', 'zipfile', 'threading', 'ctypes', 'sqlite3',
                 'bz2', 'lzma', 'zlib', 'multiprocessing', 'hashlib', 'socket'):
        importlib.import_module(name)
    expected = str(runtime / 'bin/python3')
    if sys.executable != expected:
        raise RuntimeError('Child Python path does not use the bundled launcher')
    child = subprocess.check_output([sys.executable, '-c', 'import sys; print(sys.executable)'], text=True, timeout=30)
    if child.strip() != expected:
        raise RuntimeError('Child Python cannot restart through the bundled loader')
    for name in ('blkid', 'flock'):
        subprocess.run([str(runtime / 'bin' / name), '--version'], check=True, capture_output=True)
    with tempfile.TemporaryDirectory(prefix='f50-runtime-check-') as directory:
        folder = Path(directory)
        (folder / 'test.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\n行车记录\n', encoding='utf-8')
        graph = ('[0:v]split=4[a][b][c][d];[a][b][c][d]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0,'
                 'subtitles=test.srt:fontsdir=' + str(runtime / 'fonts') + '[v]')
        subprocess.run([str(runtime / 'bin/ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                        'color=size=160x90:rate=5', '-filter_complex', graph, '-map', '[v]',
                        '-frames:v', '5', '-threads', '1', '-c:v', 'libx264', 'test.mp4'],
                       cwd=folder, check=True, capture_output=True, timeout=60)
        result = subprocess.check_output([str(runtime / 'bin/ffprobe'), '-v', 'error',
                  '-select_streams', 'v:0', '-show_entries', 'stream=width,height,codec_name,nb_frames',
                  '-of', 'json', str(folder / 'test.mp4')], text=True, timeout=20)
        stream = json.loads(result)['streams'][0]
        if (stream['width'], stream['height'], stream['codec_name'], stream['nb_frames']) != (320, 180, 'h264', '5'):
            raise RuntimeError('Unexpected encoded video: ' + result)
        subprocess.run([str(runtime / 'bin/ffmpeg'), '-v', 'error', '-i', str(folder / 'test.mp4'),
                        '-f', 'null', '-'], check=True, capture_output=True, timeout=30)
    print('离线运行环境检查通过：Python、子进程、磁盘工具、四路合成、中文水印和 H.264 编解码。')


if __name__ == '__main__':
    check(Path(__file__).resolve().parents[1] / 'runtime')
