#!/usr/bin/env python3
"""Install into a fresh F50/OpenWrt system with an already mounted ext4 data card."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import storage_allocation

APP = '/opt/tesla-sentry'
CONF = '/etc/tesla-sentry.conf'
SERVICES = ('tsf-mount', 'tesla-gadget', 'tesla-live', 'tesla-viewer')
SCRIPTS = ('mkexfat.py', 'tesla-gadget.sh', 'tesla-usb-start.sh', 'tesla-img.sh', 'tesla-sync.sh',
           'usb-selfheal.sh', 'tsf-check.sh', 'tsf-storage.sh', 'tsf-maintenance.sh')
GIB = 1 << 30
MARKER = 'installation.json'


class InstallError(RuntimeError):
    pass


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='只检查（默认）')
    mode.add_argument('--apply', action='store_true', help='安装到全新设备')
    parser.add_argument('--start', action='store_true', help='安装成功后启动；仅与 --apply 一起使用')
    parser.add_argument('--data-mount', default='/mnt/sdcard',
                        help='视频存储（镜像+归档）所在盘的挂载点，建议 TF 卡；F50 默认 /dev/mmcblk1p1 挂载到 /mnt/sdcard')
    parser.add_argument('--size-gb', type=int, default=64)
    parser.add_argument('--archive-gb', type=int, default=34)
    parser.add_argument('--drive-gb', type=int, default=12)
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--ffmpeg')
    parser.add_argument('--ffprobe')
    parser.add_argument('--install-deps', action='store_true',
                        help='缺少 ffmpeg/ffprobe 时用固件的包管理器安装，并写回配置（可单独用于修复已安装设备）')
    return parser.parse_args(argv)


def run(command, **kwargs):
    timeout = kwargs.pop('timeout', 60)
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, **kwargs)
    if result.returncode:
        raise InstallError('%s 失败：%s' % (command[0], result.stderr.strip()[-500:]))
    return result.stdout


def write(path, content, mode=0o644, created=None):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        if created is not None:
            created.append(path)
        os.chmod(path, mode)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


class Installer:
    def __init__(self, source, args, root=Path('/')):
        self.source = Path(source)
        self.args = args
        self.root = Path(root)
        self.device = {}
        self.repaired = False
        self.upgrade = False
        self.runtime = self.source / 'runtime' if (self.source / 'runtime').is_dir() else None

    def python(self):
        return str(self.runtime / 'bin/python3') if self.runtime else sys.executable

    def tool(self, name):
        if self.runtime and (self.runtime / 'bin' / name).is_file():
            return str(self.runtime / 'bin' / name)
        return shutil.which(name)

    def runtime_files(self):
        if not self.runtime:
            return {}
        manifest = json.loads((self.runtime / 'manifest.json').read_text(encoding='utf-8'))
        files = manifest['files']
        required = {'bin/python3', 'bin/ffmpeg', 'bin/ffprobe', 'bin/losetup', 'bin/blkid',
                    'bin/flock', 'bin/mount', 'bin/umount', 'bin/readlink', 'bin/setsid',
                    'fonts/NotoSansSC.ttf', 'certs/cacert.pem', 'lib/ld-musl-aarch64.so.1'}
        if manifest.get('platform') != 'aarch64-linux-musl' or not required.issubset(files):
            raise InstallError('离线运行环境不完整或平台不匹配')
        for name, info in files.items():
            relative = PurePosixPath(name)
            if (relative.is_absolute() or '..' in relative.parts or '\\' in name or
                    str(relative) != name):
                raise InstallError('离线运行环境含非法路径')
            path = self.runtime / name
            if (not path.is_file() or path.is_symlink() or path.stat().st_size != info['size'] or
                    not path.resolve().is_relative_to(self.runtime.resolve()) or
                    info.get('mode') not in (0o644, 0o755) or
                    hashlib.sha256(path.read_bytes()).hexdigest() != info['sha256']):
                raise InstallError('离线运行环境校验失败：' + name)
        return files

    def path(self, absolute):
        return self.root / absolute.lstrip('/')

    @property
    def data_home(self):
        return self.args.data_mount.rstrip('/') + '/tesla-sentry'

    def validate_options(self):
        mount = self.args.data_mount
        if (not re.fullmatch(r'/[A-Za-z0-9_./-]+', mount) or
                '..' in PurePosixPath(mount).parts or str(PurePosixPath(mount)) != mount or
                mount in ('/', '/etc', '/opt', '/tmp', '/var', '/usr', '/root')):
            raise InstallError('数据盘路径必须是独立挂载点，且不含空格或 shell 字符')
        if not 1 <= self.args.size_gb <= 128:
            raise InstallError('镜像容量须在 1～128 GiB 范围内')
        if not 1 <= self.args.drive_gb <= self.args.archive_gb:
            raise InstallError('驾驶记录配额须大于 0 且不超过总归档配额')
        storage_allocation.validate_values(dict(archive_gib=self.args.archive_gb, drive_gib=self.args.drive_gb,
                                                free_min_gib=4, free_target_gib=10), self.args.size_gb)
        if not 1 <= self.args.port <= 65535:
            raise InstallError('端口须在 1～65535 范围内')
        if self.args.start and not self.args.apply:
            raise InstallError('--start 需要同时指定 --apply')

    def required_bytes(self):
        # 缓存不再单独预留预算: 转码缓存按实际文件占用计入磁盘用量, 安装估算只算镜像+归档+安全预留。
        return storage_allocation.required_bytes(dict(archive_gib=self.args.archive_gb, free_target_gib=10),
                                                 self.args.size_gb)

    def payload(self):
        result = {APP + '/' + name: self.source / name
                  for name in ('sentry-viewer.py', 'tesla-live-sync.py', 'storage_allocation.py', 'storage_resize.py')}
        result.update({APP + '/' + name: self.source / 'scripts' / name for name in SCRIPTS})
        result.update({'/etc/init.d/' + name: self.source / 'openwrt/init.d' / name
                       for name in SERVICES})
        return result

    def source_hashes(self):
        return {dest: hashlib.sha256(src.read_text(encoding='utf-8').encode()).hexdigest()
                for dest, src in self.payload().items()}

    def existing_install(self):
        """返回 new / same / upgrade。

        same: marker 与当前代码、参数完全一致；
        upgrade: 已有安装目录但代码或参数不同（含老版本没有 marker 的情况），
                 走原地升级，保留镜像、录像、账号与设置；
        new: 什么都没有，按全新安装。
        """
        marker = self.path(APP) / MARKER
        if marker.is_file():
            try:
                saved = json.loads(marker.read_text(encoding='utf-8'))
                matches = (saved['files'] == self.source_hashes() and
                           saved['options'] == self.options_record())
                for name, info in self.runtime_files().items():
                    installed = self.path(APP) / 'runtime' / name
                    matches = matches and hashlib.sha256(installed.read_bytes()).hexdigest() == info['sha256']
                for name, digest in saved['files'].items():
                    matches = matches and hashlib.sha256(self.path(name).read_bytes()).hexdigest() == digest
                matches = matches and self.path(CONF).is_file()
                matches = matches and (self.path(APP) / 'auth.conf').is_file()
                matches = matches and (self.path(APP) / 'settings.json').is_file()
                image = self.path(self.data_home) / 'img' / 'teslacam.img'
                matches = matches and image.stat().st_size == self.args.size_gb * GIB
            except (OSError, ValueError, KeyError):
                matches = False
            return 'same' if matches else 'upgrade'
        targets = [APP, CONF, self.data_home] + ['/etc/init.d/' + name for name in SERVICES]
        for target in targets:
            if os.path.lexists(self.path(target)):
                return 'upgrade'
        return 'new'

    def options_record(self):
        options = {key: getattr(self.args, key) for key in
                   ('data_mount', 'size_gb', 'archive_gb', 'drive_gb', 'port', 'ffmpeg', 'ffprobe')}
        if self.runtime:
            options['runtime'] = hashlib.sha256((self.runtime / 'manifest.json').read_bytes()).hexdigest()
        return options

    @staticmethod
    def _parse_old_conf(text):
        """把 /etc/tesla-sentry.conf 的文本解析成 dict (TSF_* → value), 跳过注释和空行。"""
        out = {}
        for raw in (text or '').splitlines():
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            k, _, v = line.partition('=')
            k, v = k.strip(), v.strip()
            if k.startswith('TSF_'):
                out[k] = v
        return out

    def configuration(self, old_conf_text=None, upgrade=False):
        """生成 conf 字符串。

        old_conf_text: 如果传入旧 conf 内容, 用于升级时继承用户修改过的键值。
        迁移规则:
          - TSF_TRANSCODE_DIR 旧形态 /.transcode → /transcode_cache
          - 缺 TSF_VIDEO_ROOT 但有 TSF_ARCHIVE → 反推 VIDEO_ROOT
          - 视频路径四键 (VIDEO_ROOT/IMG/ARCHIVE/TRANSCODE) 优先取新派生值,
            其他参数 (PORT/IDLE/GADGET/FFMPEG 等) 旧 conf 有就继承
        """
        # F50 推荐 apk add ffmpeg；未显式指定时把配置固定到 /usr/bin，避免再去找旧 aarch64 包。
        ffmpeg = self.args.ffmpeg or ('/usr/bin/ffmpeg' if self.path('/usr/bin/ffmpeg').is_file()
                                      else self.device['ffmpeg'])
        ffprobe = self.args.ffprobe or ('/usr/bin/ffprobe' if self.path('/usr/bin/ffprobe').is_file()
                                        else self.device['ffprobe'])
        settings = dict(TSF_DIR=APP, TSF_CARD_UUID=self.device['uuid'],
                        TSF_CARD_MNT=self.args.data_mount,
                        TSF_VIDEO_ROOT=self.data_home,
                        TSF_IMG=self.data_home + '/img/teslacam.img',
                        TSF_ARCHIVE=self.data_home + '/archive',
                        TSF_CACHE=APP + '/cache',
                        TSF_TRANSCODE_DIR=self.data_home + '/archive/transcode_cache',
                        TSF_LOG_DIR=self.data_home + '/logs',
                        TSF_LIVE_LOG=self.data_home + '/logs/tesla-live.log',
                        TSF_LIVE_STATE=self.data_home + '/live-state.json',
                        TSF_MNT='/tmp/teslamnt', TSF_LOOP=self.device['loop'],
                        TSF_PORT=self.args.port, TSF_IDLE=600, TSF_USB_TIMEOUT=300,
                        TSF_LOSETUP=self.device['losetup'], TSF_GADGET=self.device['gadget'],
                        TSF_GADGET_CONFIG=self.device['gadget_config'], TSF_UDC=self.device['udc'],
                        TSF_FFMPEG=ffmpeg, TSF_FFPROBE=ffprobe,
                        TSF_USB_LOCK='/tmp/tsf-usb.lock', TSF_RECENT_ROLL_MIN=60,
                        TSF_RECENT_KEEP_MIN=60, TSF_RECENT_MAX_GB=12,
                        TSF_BOX_KEEP_DAYS=14,
                        TSF_DRIVE_MAX_GB=self.args.drive_gb, TSF_ARCHIVE_TOTAL_GB=self.args.archive_gb,
                        TSF_DISK_GUARD_INTERVAL=60, TSF_FREE_MIN_MB=4096, TSF_FREE_TARGET_MB=10240,
                        TSF_LIVE_INTERVAL=15, TSF_SYNC_FAIL_RETRY_SEC=300,
                        TSF_SYNC_FAIL_RETRY_MAX_SEC=1800)

        # ---- 升级迁移: 合并旧 conf ----
        if old_conf_text:
            old = self._parse_old_conf(old_conf_text)
            # 1) 旧版 .transcode → transcode_cache
            old_tc = old.get('TSF_TRANSCODE_DIR', '')
            if old_tc.endswith('/.transcode'):
                old['TSF_TRANSCODE_DIR'] = old_tc[:-len('/.transcode')] + '/transcode_cache'
                print('迁移 TSF_TRANSCODE_DIR: %s -> %s' % (old_tc, old['TSF_TRANSCODE_DIR']))
            # 2) 缺 VIDEO_ROOT 但有 ARCHIVE → 反推
            if not old.get('TSF_VIDEO_ROOT') and old.get('TSF_ARCHIVE'):
                old['TSF_VIDEO_ROOT'] = str(Path(old['TSF_ARCHIVE']).parent)
                print('从旧 TSF_ARCHIVE 推导 TSF_VIDEO_ROOT = %s' % old['TSF_VIDEO_ROOT'])
            # 3) 其他键继承 (用户改过的 PORT / IDLE / GADGET / FFMPEG 等不被覆盖)
            #    排除视频路径四键 —— 让 install 自己的派生逻辑保持确定性
            path_keys = {'TSF_VIDEO_ROOT', 'TSF_IMG', 'TSF_ARCHIVE', 'TSF_TRANSCODE_DIR'}
            for k, v in old.items():
                if k in path_keys:
                    continue
                if k in settings:
                    settings[k] = v
            # 原地升级: 视频与日志路径沿用旧值, 录像留在原来的挂载点, 不因参数默认值变化被改走
            if upgrade:
                for key in ('TSF_VIDEO_ROOT', 'TSF_IMG', 'TSF_ARCHIVE', 'TSF_TRANSCODE_DIR',
                            'TSF_LOG_DIR', 'TSF_LIVE_LOG', 'TSF_LIVE_STATE',
                            'TSF_CARD_MNT', 'TSF_CARD_UUID'):
                    if old.get(key):
                        settings[key] = old[key]
                settings['TSF_CACHE'] = APP + '/cache'
                # FFmpeg 用本次探测/安装好的路径, 避免继续引用已失效的旧路径
                for key, name in (('TSF_FFMPEG', 'ffmpeg'), ('TSF_FFPROBE', 'ffprobe')):
                    if self.device.get(name):
                        settings[key] = self.device[name]

        if self.runtime:
            runtime = APP + '/runtime'
            settings.update(TSF_PYTHON=runtime + '/bin/python3', TSF_FONTS=runtime + '/fonts')
            for key in ('TSF_FFMPEG', 'TSF_FFPROBE', 'TSF_LOSETUP'):
                name = Path(settings[key]).name
                if str(settings[key]) == str(self.runtime / 'bin' / name):
                    settings[key] = runtime + '/bin/' + name
            settings['PATH'] = runtime + '/bin:/usr/sbin:/usr/bin:/sbin:/bin'
        return ''.join('%s=%s\n' % item for item in settings.items())

    def cron_text(self, original):
        start, end = '# BEGIN F50 DEVICE SOURCE', '# END F50 DEVICE SOURCE'
        if start in original or end in original:
            raise_if = original.count(start) != 1 or original.count(end) != 1
            if raise_if or original.index(start) > original.index(end):
                raise InstallError('F50 定时任务区块不完整')
            original = re.sub(re.escape(start) + r'.*?' + re.escape(end) + r'\n?',
                              '', original, flags=re.S)
        prefix = '/etc/init.d/tesla-viewer running >/dev/null 2>&1 && /bin/sh ' + APP + '/'
        return (original.rstrip('\n') + '\n' + start + '\n' +
                '*/2 * * * * ' + prefix + 'tsf-maintenance.sh usb >/dev/null 2>&1\n' +
                '*/10 * * * * ' + prefix + 'tsf-maintenance.sh audio >/dev/null 2>&1\n' +
                end + '\n')

    def resolve_binary(self, name):
        """按 --ffmpeg/--ffprobe -> /usr/bin (apk 安装) -> PATH -> /opt/mu300 的顺序找可执行文件。
        F50 是 aarch64/OpenWrt，推荐用 `apk add ffmpeg`；/usr/bin 的包与系统 ABI 一致，
        不要优先使用来源不明的 aarch64 静态包。"""
        binary = getattr(self.args, name)
        if not binary:
            standard = '/usr/bin/' + name
            if self.path(standard).is_file() and os.access(self.path(standard), os.X_OK):
                binary = standard
        if not binary:
            binary = self.tool(name)
        if not binary and self.path('/opt/mu300/ffmpeg/' + name).is_file():
            binary = '/opt/mu300/ffmpeg/' + name
        if not binary or not re.fullmatch(r'/[A-Za-z0-9_./-]+', binary):
            return None
        return binary

    def package_install(self):
        """缺依赖时按固件自带的包管理器装; 装不上的原因原样告诉用户。"""
        tried = []
        managers = []
        if shutil.which('apk'):
            managers.append((['apk', 'add', '--no-cache', 'ffmpeg'], None))
            # 有的固件把 ffmpeg 拆成 cli + full 库, 精简版解不了 HEVC
            managers.append((['apk', 'add', '--no-cache', 'libffmpeg-full'], None))
        if shutil.which('opkg'):
            managers.append((['opkg', 'install', 'ffmpeg'], ['opkg', 'update']))
            managers.append((['opkg', 'install', 'libffmpeg-full'], None))
        if not managers:
            raise InstallError('缺少 ffmpeg/ffprobe，且系统没有 apk/opkg；'
                               '请先执行 `apk add ffmpeg`，'
                               '或用 --ffmpeg/--ffprobe 指定绝对路径 (F50 推荐 /usr/bin/ffmpeg)')
        for command, prepare in managers:
            if prepare:
                try:
                    run(prepare, timeout=180)
                except InstallError as exc:
                    print('软件源刷新失败（继续尝试安装）：%s' % exc, flush=True)
            print('正在安装 FFmpeg：%s' % ' '.join(command), flush=True)
            try:
                run(command, timeout=600)
            except InstallError as exc:
                tried.append(str(exc))
                continue
            if self.resolve_binary('ffmpeg') and self.resolve_binary('ffprobe'):
                return
            tried.append('%s 安装后仍找不到 ffmpeg/ffprobe' % command[0])
        raise InstallError('自动安装 FFmpeg 失败：%s；请手动执行 `apk add ffmpeg`，'
                           '或用 --ffmpeg /usr/bin/ffmpeg 指定' % '；'.join(tried))

    def ffmpeg_caps(self, binary):
        """真实能力探测: 编码器列表很长, 必须整段搜索, 不能截断。"""
        def dump(flag):
            try:
                p = subprocess.run([binary, '-hide_banner', flag], stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, timeout=60, errors='replace')
                return (p.stdout or '') + (p.stderr or '')
            except Exception:
                return ''
        enc, dec = dump('-encoders'), dump('-decoders')
        filters, muxers = dump('-filters'), dump('-muxers')
        row = r'^\s*\S{6}\s+%s\s'
        return {'x264': bool(re.search(row % 'libx264', enc, re.M)),
                'v4l2': bool(re.search(row % 'h264_v4l2m2m', enc, re.M)),
                'hevc': bool(re.search(row % 'hevc', dec, re.M)),
                'scale': bool(re.search(r'^\s*\S+\s+scale\s', filters, re.M)),
                'subtitles': bool(re.search(r'^\s*\S+\s+subtitles\s', filters, re.M)),
                'mp4': bool(re.search(r'^\s*\S+\s+mp4\s', muxers, re.M))}

    def ffmpeg_ok(self, binary):
        """完整构建的标准: 能解 HEVC(Tesla 原片)、能编 H.264、有 scale 滤镜和 mp4 封装。"""
        caps = self.ffmpeg_caps(binary)
        return (caps['hevc'] and (caps['x264'] or caps['v4l2'])
                and caps['scale'] and caps['mp4']), caps

    def install_static_ffmpeg(self):
        """固件的 ffmpeg 是精简构建时, 下载官方 arm64(aarch64) 静态包
        (含 HEVC 解码 + libx264), 直接装到 /usr/bin/ffmpeg、/usr/bin/ffprobe;
        该目录已有同名文件会被原子替换。只从固定官方地址取, 解压按文件名白名单提取。"""
        if platform.machine() not in ('aarch64', 'arm64'):
            raise InstallError('静态 FFmpeg 仅提供 aarch64 构建，当前架构为 %s' % platform.machine())
        target = self.path('/usr/bin')
        binary = target / 'ffmpeg'
        probe = target / 'ffprobe'
        if binary.is_file() and os.access(binary, os.X_OK):
            ok, _caps = self.ffmpeg_ok(str(binary))
            if ok:
                return {'ffmpeg': str(binary), 'ffprobe': str(probe)}
        url = 'https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-arm64-static.tar.xz'
        print('固件自带的 FFmpeg 不完整（缺 HEVC 解码或 H.264 编码），'
              '正在下载官方 aarch64 静态构建并安装到 /usr/bin：%s' % url, flush=True)
        target.mkdir(parents=True, exist_ok=True)
        archive = Path(tempfile.mkdtemp(prefix='tsf-ff-', dir='/tmp')) / 'ffmpeg.tar.xz'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'tsf-install/1.0'})
            with urllib.request.urlopen(req, timeout=300) as stream, open(archive, 'wb') as out:
                shutil.copyfileobj(stream, out)
            with tarfile.open(archive, 'r:xz') as tar:
                for member in tar.getmembers():
                    name = PurePosixPath(member.name).name
                    if not member.isfile() or name not in ('ffmpeg', 'ffprobe'):
                        continue
                    source = tar.extractfile(member)
                    if source is None:
                        continue
                    temporary = target / ('.' + name + '.part')
                    with open(temporary, 'wb') as out:
                        shutil.copyfileobj(source, out)
                    os.chmod(temporary, 0o755)
                    temporary.replace(target / name)
        finally:
            archive.unlink(missing_ok=True)
            archive.parent.rmdir()
        ok, caps = self.ffmpeg_ok(str(binary))
        if not ok:
            raise InstallError('静态 FFmpeg 校验失败（HEVC=%s libx264=%s scale=%s mp4=%s）；'
                               '请手动放置 ffmpeg/ffprobe 到 /usr/bin 后用 --ffmpeg /usr/bin/ffmpeg 指定'
                               % (caps['hevc'], caps['x264'], caps['scale'], caps['mp4']))
        return {'ffmpeg': str(binary), 'ffprobe': str(probe)}

    def provision_ffmpeg(self):
        """--install-deps: 先补齐依赖, 再按 HEVC/libx264/滤镜/实际编码一帧做完整校验。
        校验比"文件存在"更重要 —— OpenWrt 自带的精简 ffmpeg 通常没有 libx264 和 HEVC 解码,
        表现就是网页里抽帧失败、转码报 Option not found。"""
        if not (self.resolve_binary('ffmpeg') and self.resolve_binary('ffprobe')):
            self.package_install()
        resolved = {}
        for name in ('ffmpeg', 'ffprobe'):
            binary = self.resolve_binary(name)
            if not binary:
                raise InstallError('缺少 %s：请执行 `apk add ffmpeg`，或用 --%s /usr/bin/%s 指定'
                                   % (name, name, name))
            run([binary, '-version'], timeout=30)
            resolved[name] = binary
        ok, caps = self.ffmpeg_ok(resolved['ffmpeg'])
        if not ok and not self.args.ffmpeg:
            # 包管理器只装到精简版: 再试一次 full 库, 最后退回官方静态构建
            try:
                self.package_install()
            except InstallError as exc:
                print('补充安装完整 FFmpeg 失败：%s' % exc, flush=True)
            resolved['ffmpeg'] = self.resolve_binary('ffmpeg') or resolved['ffmpeg']
            resolved['ffprobe'] = self.resolve_binary('ffprobe') or resolved['ffprobe']
            ok, caps = self.ffmpeg_ok(resolved['ffmpeg'])
        if not ok:
            print('FFmpeg 能力不足（HEVC=%s libx264=%s scale=%s mp4=%s），改用静态构建'
                  % (caps['hevc'], caps['x264'], caps['scale'], caps['mp4']), flush=True)
            resolved = self.install_static_ffmpeg()
        filters = run([resolved['ffmpeg'], '-hide_banner', '-filters'], timeout=60)
        for required in ('scale', 'pad', 'fps', 'xstack', 'setsar', 'subtitles'):
            if not re.search(r'\s' + required + r'\s', filters):
                raise InstallError('FFmpeg 缺少滤镜：%s。请用 `apk add ffmpeg` 安装完整构建，'
                                   '或用 --ffmpeg /usr/bin/ffmpeg 指定' % required)
        enc = self.ffmpeg_caps(resolved['ffmpeg'])
        encoder = 'libx264' if enc['x264'] else 'h264_v4l2m2m'
        run([resolved['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i',
             'color=size=64x64:rate=1', '-frames:v', '1', '-c:v', encoder,
             '-f', 'null', '-'], timeout=120)
        return resolved

    def update_dependency_paths(self, resolved):
        """已安装设备只修依赖: 把 TSF_FFMPEG/TSF_FFPROBE 写回 conf, 不动录像和其他配置。"""
        conf = self.path(CONF)
        try:
            original = conf.read_text(encoding='utf-8')
        except OSError as exc:
            raise InstallError('读取 %s 失败：%s' % (CONF, exc)) from exc
        lines, seen = [], set()
        for line in original.splitlines():
            key = line.split('=', 1)[0].strip()
            if key in ('TSF_FFMPEG', 'TSF_FFPROBE'):
                seen.add(key)
                lines.append('%s=%s' % (key, resolved['ffmpeg' if key == 'TSF_FFMPEG' else 'ffprobe']))
            else:
                lines.append(line)
        for key, name in (('TSF_FFMPEG', 'ffmpeg'), ('TSF_FFPROBE', 'ffprobe')):
            if key not in seen:
                lines.append('%s=%s' % (key, resolved[name]))
        backup = conf.with_name(conf.name + '.bak-' + secrets.token_hex(3))
        shutil.copy2(conf, backup)
        temporary = conf.with_name('.tsf-conf-' + secrets.token_hex(6))
        write(temporary, '\n'.join(lines) + '\n', 0o644)
        temporary.replace(conf)
        print('已更新 %s（备份 %s）' % (CONF, backup))

    def preflight(self):
        self.validate_options()
        if platform.system() != 'Linux' or platform.machine() != 'aarch64':
            raise InstallError('安装目标须为 aarch64 Linux F50')
        if os.geteuid() != 0:
            raise InstallError('请用 root 执行')
        if not self.path('/etc/rc.common').is_file() or not self.path('/etc/init.d/cron').is_file():
            raise InstallError('需要 OpenWrt procd 与 cron；本安装器不安装系统固件')
        runtime_files = self.runtime_files()
        resolved = None
        repair = self.args.install_deps and self.args.apply
        if self.args.install_deps and not self.args.apply:
            print('提示：--install-deps 只在 --apply 时真正安装；检查模式不会改动系统', flush=True)
        if repair:
            # 先把 FFmpeg 配好再谈安装: 这不是"用到时才发现"的可选项
            resolved = self.provision_ffmpeg()
            print('FFmpeg 就绪：%s / %s' % (resolved['ffmpeg'], resolved['ffprobe']), flush=True)
        state = self.existing_install()
        self.upgrade = state == 'upgrade'
        if state == 'same':
            if resolved:
                self.update_dependency_paths(resolved)
                try:
                    run([str(self.path('/etc/init.d/tesla-viewer')), 'restart'], timeout=90)
                    print('已重启 tesla-viewer，缩略图与转码恢复')
                except InstallError as exc:
                    print('重启服务失败，请手动执行 /etc/init.d/tesla-viewer restart：%s' % exc)
                self.repaired = True
            return False
        if self.upgrade:
            print('检测到已有安装：按升级方式替换程序，镜像、录像、账号与设置保留', flush=True)
        for name in ('blkid', 'flock', 'mount', 'umount', 'readlink', 'setsid'):
            if not self.tool(name):
                raise InstallError('缺少工具：' + name)
        run([self.python() if self.runtime else '/usr/bin/python3', '-c',
             'import sys,ssl,fcntl,http.server,zipfile,threading; assert sys.version_info >= (3,11)'])
        if self.runtime:
            run([self.python(), '-B', str(self.source / 'scripts/check-runtime.py')], timeout=90)
        for dest, src in self.payload().items():
            if not src.is_file():
                raise InstallError('安装包缺少：' + str(src))
            if dest.endswith('.py'):
                compile(src.read_text(encoding='utf-8'), str(src), 'exec')
        mounts = [line.split() for line in self.path('/proc/mounts').read_text().splitlines()]
        mounted = next((row for row in mounts if row[1] == self.args.data_mount), None)
        if not mounted or mounted[2] != 'ext4' or 'rw' not in mounted[3].split(','):
            raise InstallError('数据盘须预先以 ext4 读写挂载到 ' + self.args.data_mount
                               + '（F50 默认 TF 卡 /dev/mmcblk1p1，建议视频存储装到 TF 卡）')
        if self.path(self.args.data_mount).is_symlink():
            raise InstallError('数据盘挂载路径不能是符号链接')
        uuid = run([self.tool('blkid'), '-o', 'value', '-s', 'UUID', mounted[0]]).strip()
        if not re.fullmatch(r'[A-Za-z0-9-]+', uuid):
            raise InstallError('无法读取数据盘 UUID')
        usage = shutil.disk_usage(self.path(self.args.data_mount))
        # 升级不新建镜像, 也不重新分配空间, 只做只读校验
        if not self.upgrade and self.args.size_gb > usage.total / GIB / 2:
            raise InstallError('镜像容量不能超过数据盘实际容量的一半（%.1f GiB）：归档区要与车机内容一致，'
                               '还要存放转码缓存、铃声、涂装等文件' % (usage.total / GIB / 2))
        if not self.upgrade and usage.free < self.required_bytes():
            raise InstallError('数据盘至少需要 %d GiB 空闲空间（镜像、归档、安全预留）' %
                               (self.required_bytes() // GIB))
        app_bytes = sum(info['size'] for info in runtime_files.values()) + 128 * 1024 * 1024
        if shutil.disk_usage(self.path('/opt')).free < app_bytes:
            raise InstallError('/opt 至少需要 %d MiB 空闲空间' % ((app_bytes + (1 << 20) - 1) >> 20))
        gadgets = list(self.path('/sys/kernel/config/usb_gadget').glob('*/configs'))
        if len(gadgets) != 1:
            raise InstallError('需要固件预先建立唯一 USB Gadget；请先完成固件 USB 配置')
        gadget = gadgets[0].parent
        configs = [p.name for p in gadgets[0].iterdir() if p.is_dir()]
        controllers = [p.name for p in self.path('/sys/class/udc').iterdir()]
        bound = (gadget / 'UDC').read_text().strip()
        if len(configs) != 1 or not controllers or (not bound and len(controllers) != 1):
            raise InstallError('USB 配置或控制器不唯一；请先在固件中选择正确的 USB Device 控制器')
        udc = bound or controllers[0]
        if udc not in controllers:
            raise InstallError('USB 控制器不可用')
        if not self.upgrade and any(gadget.glob('functions/mass_storage*')):
            raise InstallError('USB 已存在 mass_storage 功能；保留现有导出，停止安装')
        # Do not load modules or mount configfs during a check.
        supported = self.path('/proc/filesystems').read_text().split()
        if 'exfat' not in supported or 'configfs' not in supported:
            raise InstallError('内核须预先启用 exFAT 和 configfs')
        mass_storage = self.path('/sys/module/usb_f_mass_storage').exists()
        if not mass_storage:
            import gzip
            config = self.path('/proc/config.gz')
            if config.is_file():
                with gzip.open(config, 'rt') as stream:
                    mass_storage = 'CONFIG_USB_F_MASS_STORAGE=y' in stream.read()
        if not mass_storage:
            raise InstallError('未确认 USB mass storage 支持；请先加载匹配内核的 usb_f_mass_storage')
        losetup = self.tool('losetup')
        if not losetup and self.path('/opt/mu300/busybox-bin/losetup').is_file():
            losetup = '/opt/mu300/busybox-bin/losetup'
        if not losetup:
            raise InstallError('缺少可执行的 losetup')
        loop = run([losetup, '-f']).strip()
        if not re.fullmatch(r'/dev/loop[0-9]+', loop):
            raise InstallError('没有可用 loop 设备')
        self.device = dict(uuid=uuid, gadget='/' + gadget.relative_to(self.root).as_posix(),
                           gadget_config=configs[0], udc=udc, losetup=losetup, loop=loop)
        for name in ('ffmpeg', 'ffprobe'):
            binary = (resolved or {}).get(name) or self.resolve_binary(name)
            if not binary:
                raise InstallError('缺少 %s；请执行 `apk add ffmpeg`，或用 --%s /usr/bin/%s 指定，'
                                   '或加 --install-deps 让安装器尝试安装' % (name, name, name))
            run([binary, '-version'])
            self.device[name] = binary
        filters = run([self.device['ffmpeg'], '-hide_banner', '-filters'])
        for required in ('scale', 'pad', 'fps', 'xstack', 'setsar', 'subtitles'):
            if not re.search(r'\s' + required + r'\s', filters):
                raise InstallError('FFmpeg 缺少滤镜：' + required)
        run([self.device['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i',
             'color=size=64x64:rate=1', '-frames:v', '1', '-c:v',
             'libx264' if self.ffmpeg_caps(self.device['ffmpeg'])['x264'] else 'h264_v4l2m2m',
             '-f', 'null', '-'])
        if not self.upgrade:
            with socket.socket() as probe:
                try:
                    probe.bind(('0.0.0.0', self.args.port))
                except OSError as exc:
                    raise InstallError('端口不可用：%s' % self.args.port) from exc
        for value in self.device.values():
            if not re.fullmatch(r'[A-Za-z0-9_./-]+', value):
                raise InstallError('设备路径或标识包含不支持的字符')
        return True

    def install(self):
        if not self.preflight():
            print('依赖已修复并重启服务，录像、凭据与容量设置未改动。' if self.repaired
                  else '相同版本已安装，未改动配置、凭据、录像或服务。')
            return False
        lock = self.path('/tmp/tsf-install.lock')
        try:
            lock.mkdir()
        except FileExistsError as exc:
            raise InstallError('另一安装任务正在执行，或上次中断留下 /tmp/tsf-install.lock') from exc
        created = []
        stages = []
        cron_path = self.path('/etc/crontabs/root')
        old_cron = None
        old_cron_mode = 0o600
        cron_changed = False
        try:
            old_cron = cron_path.read_bytes() if cron_path.exists() else None
            old_cron_mode = cron_path.stat().st_mode & 0o777 if cron_path.exists() else 0o600
            # Recheck under the install lock before publishing any files.
            state = self.existing_install()
            if state == 'same':
                return False
            if state == 'upgrade':
                self.upgrade_in_place(old_cron, old_cron_mode)
                return True
            app_stage = Path(tempfile.mkdtemp(prefix='.tsf-app-', dir=self.path('/opt')))
            stages.append(app_stage)
            data_stage = Path(tempfile.mkdtemp(prefix='.tsf-data-', dir=self.path(self.args.data_mount)))
            stages.append(data_stage)
            os.chmod(app_stage, 0o755)
            os.chmod(data_stage, 0o755)
            if self.runtime:
                for name, info in self.runtime_files().items():
                    target = app_stage / 'runtime' / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(self.runtime / name, target)
                    os.chmod(target, info['mode'])
                shutil.copyfile(self.runtime / 'manifest.json', app_stage / 'runtime/manifest.json')
            for dest, src in self.payload().items():
                if dest.startswith(APP + '/'):
                    write(app_stage / Path(dest).name, src.read_text(encoding='utf-8'), 0o755)
            password = secrets.token_urlsafe(18)
            write(app_stage / 'auth.conf', 'user=admin\npass=%s\nsecret=%s\nttl=2592000\n' %
                  (password, secrets.token_hex(32)), 0o600)
            write(app_stage / 'settings.json', '{"auto_update": false}\n', 0o600)
            for name in ('archive', 'logs'):
                (data_stage / name).mkdir()
            (data_stage / 'img').mkdir()
            (data_stage / 'archive' / 'transcode_cache').mkdir()
            (app_stage / 'cache').mkdir()
            run([self.python(), '-B', str(self.source / 'scripts/mkexfat.py'),
                 str(data_stage / 'img' / 'teslacam.img'), str(self.args.size_gb)])
            image = data_stage / 'img' / 'teslacam.img'
            with image.open('rb') as stream:
                header = stream.read(11)
            if header[3:11] != b'EXFAT   ' or image.stat().st_size != self.args.size_gb * GIB:
                raise InstallError('新镜像校验失败')
            env = os.environ.copy()
            env.update(TSF_ARCHIVE=str(data_stage / 'archive'), TSF_CACHE=str(app_stage / 'cache'),
                       TSF_AUTH_FILE=str(app_stage / 'auth.conf'),
                       TSF_SETTINGS=str(app_stage / 'settings.json'))
            run([self.python(), '-B', str(app_stage / 'sentry-viewer.py'), '--ota-check'], env=env)
            write(app_stage / MARKER, json.dumps({'files': self.source_hashes(),
                  'options': self.options_record()}, indent=2) + '\n')
            data_stage.rename(self.path(self.data_home))
            created.append(self.path(self.data_home))
            app_stage.rename(self.path(APP))
            created.append(self.path(APP))
            # Exclusive creates protect against unexpected files appearing after preflight.
            for dest, src in self.payload().items():
                if dest.startswith('/etc/init.d/'):
                    target = self.path(dest)
                    write(target, src.read_text(encoding='utf-8'), 0o755, created)
            target = self.path(CONF)
            # 升级场景: 旧 conf 存在时先备份一份用于回滚, 再读旧值合并到新配置
            old_conf_text = None
            if target.exists():
                backup = target.with_name(target.name + '.bak-' + time.strftime('%Y%m%d-%H%M%S'))
                shutil.copy2(target, backup)
                print('旧配置已备份 -> %s  (回滚: cp %s %s)' % (backup, backup, target))
                old_conf_text = target.read_text(encoding='utf-8')
            write(target, self.configuration(old_conf_text=old_conf_text), created=created)
            # Only the mount gate starts at boot. It starts the dependent services in order.
            for name in ('S08tsf-mount', 'K99tsf-mount'):
                target = self.path('/etc/rc.d/' + name)
                target.symlink_to('../init.d/tsf-mount')
                created.append(target)
            cron_new = self.cron_text((old_cron or b'').decode('utf-8'))
            if (cron_path.read_bytes() if cron_path.exists() else None) != old_cron:
                raise InstallError('安装期间定时任务被其他程序修改，停止发布')
            # Replace atomically so cron never reads half a block.
            temporary = cron_path.with_name('.tsf-cron-' + secrets.token_hex(6))
            write(temporary, cron_new, old_cron_mode, created)
            temporary.replace(cron_path)
            cron_changed = True
            # OpenWrt cron observes the crontab directory without a restart.
        except BaseException:
            if cron_changed:
                if old_cron is None:
                    cron_path.unlink()
                else:
                    cron_path.write_bytes(old_cron)
                    os.chmod(cron_path, old_cron_mode)
            for path in reversed(created):
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
            raise
        finally:
            for path in stages:
                if path.exists():
                    shutil.rmtree(path)
            lock.rmdir()
        print('安装完成。账号 admin；密码保存在 /opt/tesla-sentry/auth.conf。')
        print('启动命令：/etc/init.d/tsf-mount start；网页端口：%s' % self.args.port)
        return True

    @staticmethod
    def _replace_file(path, content, mode=0o644):
        """原子替换已有文件：同目录写临时文件再 rename，服务不会读到半截内容。"""
        temporary = path.with_name('.' + path.name + '.tsf-' + secrets.token_hex(6))
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            os.chmod(temporary, mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def upgrade_in_place(self, old_cron, old_cron_mode):
        """老版本原地升级：只替换程序、离线运行环境与启动脚本。

        镜像、归档录像、auth.conf、settings.json、通知配置、品牌/字体等目录全部保留，
        视频路径沿用旧 conf，不会因为参数默认值变化把归档改到别的挂载点。
        """
        app = self.path(APP)
        conf_path = self.path(CONF)
        cron_path = self.path('/etc/crontabs/root')
        stamp = time.strftime('%Y%m%d-%H%M%S')
        backup = app.with_name(app.name + '.bak-' + stamp)
        conf_backup = None
        cron_changed = False
        swapped = False
        staging = Path(tempfile.mkdtemp(prefix='.tsf-upgrade-', dir=app.parent))
        old_conf_text = conf_path.read_text(encoding='utf-8') if conf_path.is_file() else None
        old = self._parse_old_conf(old_conf_text)
        video_root = old.get('TSF_VIDEO_ROOT') or ''
        if not video_root and old.get('TSF_ARCHIVE'):
            video_root = str(Path(old['TSF_ARCHIVE']).parent)
        if not video_root:
            video_root = self.data_home
        image = old.get('TSF_IMG') or video_root + '/img/teslacam.img'
        archive = old.get('TSF_ARCHIVE') or video_root + '/archive'
        if not self.path(image).is_file():
            raise InstallError('旧安装找不到车机镜像 %s；请确认 TF 卡已挂载后重试，或全新安装' % image)
        try:
            # 以现有目录为底复制(排除可再生的 cache 与 runtime)，保留账号、设置与上传素材
            shutil.copytree(app, staging, symlinks=True, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('cache', 'runtime'))
            (staging / 'cache').mkdir(exist_ok=True)
            os.chmod(staging, 0o755)
            if self.runtime:
                for name, info in self.runtime_files().items():
                    target = staging / 'runtime' / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(self.runtime / name, target)
                    os.chmod(target, info['mode'])
                shutil.copyfile(self.runtime / 'manifest.json', staging / 'runtime/manifest.json')
            for dest, src in self.payload().items():
                if dest.startswith(APP + '/'):
                    target = staging / Path(dest).name
                    target.write_text(src.read_text(encoding='utf-8'), encoding='utf-8', newline='\n')
                    os.chmod(target, 0o755)
            if not (staging / 'auth.conf').is_file():
                password = secrets.token_urlsafe(18)
                (staging / 'auth.conf').write_text(
                    'user=admin\npass=%s\nsecret=%s\nttl=2592000\n' % (password, secrets.token_hex(32)),
                    encoding='utf-8', newline='\n')
                os.chmod(staging / 'auth.conf', 0o600)
            if not (staging / 'settings.json').is_file():
                (staging / 'settings.json').write_text('{"auto_update": false}\n',
                                                       encoding='utf-8', newline='\n')
                os.chmod(staging / 'settings.json', 0o600)
            (staging / MARKER).write_text(json.dumps({'files': self.source_hashes(),
                                                      'options': self.options_record()}, indent=2) + '\n',
                                          encoding='utf-8', newline='\n')
            env = os.environ.copy()
            env.update(TSF_ARCHIVE=archive, TSF_CACHE=str(staging / 'cache'),
                       TSF_AUTH_FILE=str(staging / 'auth.conf'),
                       TSF_SETTINGS=str(staging / 'settings.json'))
            run([self.python(), '-B', str(staging / 'sentry-viewer.py'), '--ota-check'], env=env)
            app.rename(backup)
            try:
                staging.rename(app)
            except BaseException:
                backup.rename(app)
                raise
            swapped = True
            for dest, src in self.payload().items():
                if dest.startswith('/etc/init.d/'):
                    self._replace_file(self.path(dest), src.read_text(encoding='utf-8'), 0o755)
            for name in ('S08tsf-mount', 'K99tsf-mount'):
                target = self.path('/etc/rc.d/' + name)
                if not os.path.lexists(target):
                    target.symlink_to('../init.d/tsf-mount')
            if conf_path.exists():
                conf_backup = conf_path.with_name(conf_path.name + '.bak-' + stamp)
                shutil.copy2(conf_path, conf_backup)
            self._replace_file(conf_path, self.configuration(old_conf_text=old_conf_text, upgrade=True))
            cron_new = self.cron_text((old_cron or b'').decode('utf-8'))
            if (cron_path.read_bytes() if cron_path.exists() else None) != old_cron:
                raise InstallError('安装期间定时任务被其他程序修改，停止发布')
            self._replace_file(cron_path, cron_new, old_cron_mode)
            cron_changed = True
            # 备份只留程序，旧缓存目录随备份释放，不在卡上多占一份
            shutil.rmtree(backup / 'cache', ignore_errors=True)
        except BaseException:
            if swapped:
                if app.exists():
                    shutil.rmtree(app, ignore_errors=True)
                if backup.exists():
                    backup.rename(app)
            if conf_backup and conf_backup.exists():
                shutil.copy2(conf_backup, conf_path)
            if cron_changed:
                if old_cron is None:
                    if cron_path.exists():
                        cron_path.unlink()
                else:
                    cron_path.write_bytes(old_cron)
                    os.chmod(cron_path, old_cron_mode)
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        print('升级完成，旧程序已备份到 %s' % backup)
        if conf_backup:
            print('旧配置已备份到 %s' % conf_backup)
        for service in ('tesla-live', 'tesla-viewer'):
            script = str(self.path('/etc/init.d/' + service))
            try:
                run([script, 'running'], timeout=15)
                run([script, 'restart'], timeout=90)
                print('已重启 %s' % service)
            except InstallError as exc:
                print('提示：%s 未运行或重启失败（%s），可稍后手动启动' % (service, exc))

    def start(self):
        # Installation remains intact if startup fails; no blind reinstall or USB rollback.
        run(['/etc/init.d/tsf-mount', 'start'], timeout=90)
        run(['/etc/init.d/tesla-live', 'running'])
        run(['/etc/init.d/tesla-viewer', 'running'])
        expected = self.data_home + '/img/teslacam.img'
        lun = self.path(self.device['gadget']) / 'functions/mass_storage.0/lun.0/file'
        if lun.read_text().strip() != expected:
            raise InstallError('启动后 USB 镜像绑定校验失败；安装文件已保留')
        for attempt in range(20):
            try:
                with urllib.request.urlopen('http://127.0.0.1:%s/login' % self.args.port, timeout=2) as response:
                    if response.status == 200:
                        print('查看器、同步服务与 USB 镜像绑定检查通过。')
                        return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(1)
        raise InstallError('查看器启动检查超时；请查看 logread 与 tesla-gadget.log，安装文件已保留')


def main(argv=None):
    installer = Installer(Path(__file__).resolve().parents[1], arguments(argv))
    try:
        if installer.args.apply:
            installed = installer.install()
            # 原地升级已重启服务, 不再跑全新安装的启动校验(镜像路径可能沿用旧配置)
            if installed and installer.args.start and not installer.upgrade:
                installer.start()
        elif installer.preflight():
            print('检查通过。需要 %d GiB 数据盘空闲空间；尚未写入文件或启动服务。' %
                  (installer.required_bytes() // GIB))
        else:
            print('相同版本已安装，未改动设备。')
        return 0
    except (InstallError, OSError, UnicodeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print('安装未完成：%s' % exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
