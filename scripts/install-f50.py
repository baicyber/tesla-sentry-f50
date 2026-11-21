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
import tempfile
import time
import urllib.error
import urllib.request
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import storage_allocation

APP = '/opt/tesla-sentry'
CONF = '/etc/tesla-sentry.conf'
SERVICES = ('tsf-mount', 'tesla-gadget', 'tesla-live', 'tesla-viewer')
SCRIPTS = ('mkexfat.py', 'tesla-gadget.sh', 'tesla-img.sh', 'tesla-sync.sh',
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
    parser.add_argument('--data-mount', default='/mnt/mmc')
    parser.add_argument('--size-gb', type=int, default=64)
    parser.add_argument('--archive-gb', type=int, default=34)
    parser.add_argument('--drive-gb', type=int, default=12)
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--ffmpeg')
    parser.add_argument('--ffprobe')
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
            raise InstallError('行车记录配额须大于 0 且不超过总归档配额')
        storage_allocation.validate_values(dict(archive_gib=self.args.archive_gb, drive_gib=self.args.drive_gb,
                                                free_min_gib=4, free_target_gib=10), self.args.size_gb)
        if not 1 <= self.args.port <= 65535:
            raise InstallError('端口须在 1～65535 范围内')
        if self.args.start and not self.args.apply:
            raise InstallError('--start 需要同时指定 --apply')

    def required_bytes(self):
        # Cache budget plus free-space target are reserved even for sparse images.
        return storage_allocation.required_bytes(dict(archive_gib=self.args.archive_gb, free_target_gib=10), self.args.size_gb)

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
                image = self.path(self.data_home) / 'teslacam.img'
                matches = matches and image.stat().st_size == self.args.size_gb * GIB
            except (OSError, ValueError, KeyError):
                matches = False
            if matches:
                return True
            raise InstallError('已有安装与当前代码或参数不同；本脚本仅用于全新安装，不覆盖现有安装')
        targets = [APP, CONF, self.data_home] + ['/etc/init.d/' + name for name in SERVICES]
        for target in targets:
            if os.path.lexists(self.path(target)):
                raise InstallError('发现已有路径 %s；停止安装，保留原有内容' % target)
        return False

    def options_record(self):
        options = {key: getattr(self.args, key) for key in
                   ('data_mount', 'size_gb', 'archive_gb', 'drive_gb', 'port', 'ffmpeg', 'ffprobe')}
        if self.runtime:
            options['runtime'] = hashlib.sha256((self.runtime / 'manifest.json').read_bytes()).hexdigest()
        return options

    def configuration(self):
        settings = dict(TSF_DIR=APP, TSF_CARD_UUID=self.device['uuid'],
                        TSF_CARD_MNT=self.args.data_mount,
                        TSF_IMG=self.data_home + '/teslacam.img',
                        TSF_ARCHIVE=self.data_home + '/archive',
                        TSF_CACHE=self.data_home + '/cache',
                        TSF_LOG_DIR=self.data_home + '/logs',
                        TSF_LIVE_LOG=self.data_home + '/logs/tesla-live.log',
                        TSF_LIVE_STATE=self.data_home + '/live-state.json',
                        TSF_MNT='/tmp/teslamnt', TSF_LOOP=self.device['loop'],
                        TSF_PORT=self.args.port, TSF_IDLE=600, TSF_USB_TIMEOUT=300,
                        TSF_LOSETUP=self.device['losetup'], TSF_GADGET=self.device['gadget'],
                        TSF_GADGET_CONFIG=self.device['gadget_config'], TSF_UDC=self.device['udc'],
                        TSF_FFMPEG=self.device['ffmpeg'], TSF_FFPROBE=self.device['ffprobe'],
                        TSF_USB_LOCK='/tmp/tsf-usb.lock', TSF_RECENT_ROLL_MIN=60,
                        TSF_RECENT_KEEP_MIN=60, TSF_BOX_KEEP_DAYS=14, TSF_BOX_MAX_GB=12,
                        TSF_DRIVE_MAX_GB=self.args.drive_gb, TSF_ARCHIVE_TOTAL_GB=self.args.archive_gb,
                        TSF_DISK_GUARD_INTERVAL=60, TSF_FREE_MIN_MB=4096, TSF_FREE_TARGET_MB=10240,
                        TSF_LIVE_INTERVAL=15, TSF_SYNC_FAIL_RETRY_SEC=300,
                        TSF_SYNC_FAIL_RETRY_MAX_SEC=1800)
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

    def preflight(self):
        self.validate_options()
        if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
            raise InstallError('安装目标须为 ARM64 Linux F50；本机测试请运行 tests/test_install_f50.py')
        if os.geteuid() != 0:
            raise InstallError('请用 root 执行')
        if not self.path('/etc/rc.common').is_file() or not self.path('/etc/init.d/cron').is_file():
            raise InstallError('需要 OpenWrt procd 与 cron；本安装器不安装系统固件')
        runtime_files = self.runtime_files()
        if self.existing_install():
            return False
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
            raise InstallError('数据盘须预先以 ext4 读写挂载到 ' + self.args.data_mount)
        if self.path(self.args.data_mount).is_symlink():
            raise InstallError('数据盘挂载路径不能是符号链接')
        uuid = run([self.tool('blkid'), '-o', 'value', '-s', 'UUID', mounted[0]]).strip()
        if not re.fullmatch(r'[A-Za-z0-9-]+', uuid):
            raise InstallError('无法读取数据盘 UUID')
        if shutil.disk_usage(self.path(self.args.data_mount)).free < self.required_bytes():
            raise InstallError('数据盘至少需要 %d GiB 空闲空间（镜像、归档、缓存、余量）' %
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
        if any(gadget.glob('functions/mass_storage*')):
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
            binary = getattr(self.args, name) or self.tool(name)
            if not binary and self.path('/opt/mu300/ffmpeg/' + name).is_file():
                binary = '/opt/mu300/ffmpeg/' + name
            if not binary or not re.fullmatch(r'/[A-Za-z0-9_./-]+', binary):
                raise InstallError('缺少 %s；可用 --%s 指定绝对路径' % (name, name))
            run([binary, '-version'])
            self.device[name] = binary
        filters = run([self.device['ffmpeg'], '-hide_banner', '-filters'])
        for required in ('scale', 'pad', 'fps', 'xstack', 'setsar', 'subtitles'):
            if not re.search(r'\s' + required + r'\s', filters):
                raise InstallError('FFmpeg 缺少滤镜：' + required)
        run([self.device['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i',
             'color=size=64x64:rate=1', '-frames:v', '1', '-c:v', 'libx264',
             '-f', 'null', '-'])
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
            print('相同版本已安装，未改动配置、凭据、录像或服务。')
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
            if self.existing_install():
                return False
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
            for name in ('archive', 'cache', 'logs'):
                (data_stage / name).mkdir()
            run([self.python(), '-B', str(self.source / 'scripts/mkexfat.py'),
                 str(data_stage / 'teslacam.img'), str(self.args.size_gb)])
            image = data_stage / 'teslacam.img'
            with image.open('rb') as stream:
                header = stream.read(11)
            if header[3:11] != b'EXFAT   ' or image.stat().st_size != self.args.size_gb * GIB:
                raise InstallError('新镜像校验失败')
            env = os.environ.copy()
            env.update(TSF_ARCHIVE=str(data_stage / 'archive'), TSF_CACHE=str(data_stage / 'cache'),
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
            write(target, self.configuration(), created=created)
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

    def start(self):
        # Installation remains intact if startup fails; no blind reinstall or USB rollback.
        run(['/etc/init.d/tsf-mount', 'start'], timeout=90)
        run(['/etc/init.d/tesla-live', 'running'])
        run(['/etc/init.d/tesla-viewer', 'running'])
        expected = self.data_home + '/teslacam.img'
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
            if installed and installer.args.start:
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
