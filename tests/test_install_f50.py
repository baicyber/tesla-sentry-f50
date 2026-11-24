"""Fresh installs use a temporary filesystem; never touch host services or USB."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import types
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]


class InstallTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('install_f50', REPO / 'scripts/install-f50.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.args = self.module.arguments(['--size-gb', '1', '--archive-gb', '2', '--drive-gb', '1'])
        self.installer = self.module.Installer(REPO, self.args, root=self.root)

    def test_default_is_check_only(self):
        args = self.module.arguments([])
        self.assertFalse(args.apply)
        self.assertFalse(args.start)

    def test_shell_metacharacters_in_paths_are_rejected(self):
        self.args.data_mount = '/mnt/card;reboot'
        with self.assertRaisesRegex(self.module.InstallError, '路径'):
            self.installer.validate_options()

    def test_disk_budget_includes_full_image_archive_and_reserve(self):
        # 转码缓存放在归档区内(数据卡), 需计入缓存预算: 1G 镜像 + 2G 归档 + 4G 缓存 + 10G 安全预留
        self.assertEqual(self.installer.required_bytes(), 17 * (1 << 30))

    def test_existing_unmanaged_install_is_never_overwritten(self):
        app = self.installer.path('/opt/tesla-sentry')
        app.mkdir(parents=True)
        old = app / 'sentry-viewer.py'
        old.write_text('existing installation', encoding='utf-8')
        with self.assertRaisesRegex(self.module.InstallError, '已有'):
            self.installer.existing_install()
        self.assertEqual(old.read_text(encoding='utf-8'), 'existing installation')

    def test_existing_empty_directory_is_also_protected(self):
        self.installer.path('/mnt/mmc/tesla-sentry').mkdir(parents=True)
        with self.assertRaisesRegex(self.module.InstallError, '已有'):
            self.installer.existing_install()

    def test_generated_config_has_device_uuid_and_private_update_defaults(self):
        self.installer.device = {'uuid': 'new-card-uuid', 'gadget': '/sys/kernel/config/usb_gadget/linux',
                                 'udc': '25100000.dwc3', 'gadget_config': 'c.1',
                                 'ffmpeg': '/usr/bin/ffmpeg', 'ffprobe': '/usr/bin/ffprobe',
                                 'losetup': '/sbin/losetup', 'loop': '/dev/loop3'}
        conf = self.installer.configuration()
        self.assertIn('TSF_CARD_UUID=new-card-uuid\n', conf)
        self.assertIn('TSF_CACHE=/opt/tesla-sentry/cache\n', conf)
        self.assertIn('TSF_TRANSCODE_DIR=/mnt/mmc/tesla-sentry/archive/transcode_cache\n', conf)
        self.assertIn('TSF_VIDEO_ROOT=/mnt/mmc/tesla-sentry\n', conf)
        self.assertIn('TSF_IMG=/mnt/mmc/tesla-sentry/img/teslacam.img\n', conf)
        self.assertNotIn('.transcode', conf)
        self.assertNotIn('/opt/mu300', conf)
        self.assertIn('TSF_DRIVE_MAX_GB=1\n', conf)
        self.assertIn('TSF_RECENT_MAX_GB=12\n', conf)

    def test_cron_merge_preserves_other_jobs_and_is_idempotent(self):
        original = '# local tasks\n0 * * * * /usr/bin/backup\n'
        merged = self.installer.cron_text(original)
        self.assertTrue(merged.startswith(original))
        self.assertEqual(self.installer.cron_text(merged), merged)
        self.assertIn('tesla-viewer running', merged)

    def prepare_device(self):
        for directory in ('/opt', '/tmp', '/etc/init.d', '/etc/rc.d', '/etc/crontabs', '/mnt/mmc',
                          '/sys/kernel/config/usb_gadget/linux/configs/c.1',
                          '/sys/kernel/config/usb_gadget/linux/functions',
                          '/sys/class/udc/25100000.dwc3', '/sys/module/usb_f_mass_storage', '/proc'):
            self.installer.path(directory).mkdir(parents=True, exist_ok=True)
        for name, value in {
            '/etc/rc.common': '# fixture', '/etc/init.d/cron': '# fixture',
            '/etc/crontabs/root': '# other job\n0 * * * * /usr/bin/backup\n',
            '/proc/mounts': '/dev/mmcblk1p1 /mnt/mmc ext4 rw,noatime 0 0\n',
            '/proc/filesystems': 'ext4\nexfat\nnodev configfs\n',
            '/sys/kernel/config/usb_gadget/linux/UDC': '25100000.dwc3\n',
        }.items():
            self.installer.path(name).write_text(value, encoding='utf-8')
        self.enterContext(patch.object(self.module.platform, 'system', return_value='Linux'))
        self.enterContext(patch.object(self.module.platform, 'machine', return_value='aarch64'))
        self.enterContext(patch.object(self.module.os, 'geteuid', return_value=0, create=True))
        self.enterContext(patch.object(self.module.shutil, 'which', side_effect=lambda n: '/usr/bin/' + n))
        self.enterContext(patch.object(self.module.shutil, 'disk_usage',
                                      return_value=types.SimpleNamespace(free=120 * (1 << 30))))
        # Keep preflight independent of services already listening on the host.
        sockets = self.enterContext(patch.object(self.module, 'socket'))
        self.port_probe = sockets.socket.return_value.__enter__.return_value
        self.commands = []
        self.enterContext(patch.object(self.module, 'run', side_effect=self.fake_run))
        if self.module.os.name == 'nt':
            # Windows users may lack symlink privilege. CI checks real Linux links.
            self.enterContext(patch.object(Path, 'symlink_to',
                lambda link, target: link.write_text('symlink:' + target, encoding='utf-8')))

    def fake_run(self, command, **kwargs):
        self.commands.append(command)
        if Path(command[0]).name == 'blkid':
            return 'new-card-uuid\n'
        if command[-1] == '-f':
            return '/dev/loop3\n'
        if command[-1] == '-filters':
            return ' scale V->V\n pad V->V\n fps V->V\n xstack N->V\n setsar V->V\n subtitles V->V\n'
        if any(str(part).endswith('mkexfat.py') for part in command):
            # Real image creation in a temporary directory, never a mounted drive.
            import subprocess
            subprocess.run(command, check=True, capture_output=True)
        return ''

    def test_preflight_never_creates_installation_or_changes_cron(self):
        self.prepare_device()
        original = self.installer.path('/etc/crontabs/root').read_bytes()
        self.assertTrue(self.installer.preflight())
        self.port_probe.bind.assert_called_once_with(('0.0.0.0', self.args.port))
        self.assertFalse(self.installer.path(self.module.APP).exists())
        self.assertEqual(self.installer.path('/etc/crontabs/root').read_bytes(), original)
        self.assertFalse(any('/etc/init.d/' in str(c) for c in self.commands))

    def test_unavailable_port_fails_before_installation(self):
        self.prepare_device()
        self.port_probe.bind.side_effect = OSError('fixture address already in use')
        with self.assertRaisesRegex(self.module.InstallError, '端口不可用'):
            self.installer.preflight()
        self.assertFalse(self.installer.path(self.module.APP).exists())

    def test_wrong_filesystem_and_missing_card_fail_before_any_install(self):
        self.prepare_device()
        for mounts in ('', '/dev/mmcblk1p1 /mnt/mmc exfat rw 0 0\n',
                       '/dev/mmcblk1p1 /mnt/mmc ext4 ro 0 0\n'):
            self.installer.path('/proc/mounts').write_text(mounts, encoding='utf-8')
            with self.assertRaisesRegex(self.module.InstallError, 'ext4'):
                self.installer.preflight()
        self.assertFalse(self.installer.path(self.module.APP).exists())

    def test_preflight_rejects_insufficient_disk_and_existing_usb_export(self):
        self.prepare_device()
        with patch.object(self.module.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=4 << 30)):
            with self.assertRaisesRegex(self.module.InstallError, '空闲'):
                self.installer.preflight()
        self.installer.path('/sys/kernel/config/usb_gadget/linux/functions/mass_storage.0').mkdir()
        with self.assertRaisesRegex(self.module.InstallError, 'mass_storage'):
            self.installer.preflight()

    def test_encoder_failure_is_not_ignored(self):
        self.prepare_device()
        def reject_encoder(command, **kwargs):
            if 'libx264' in command:
                raise self.module.InstallError('Unknown encoder libx264')
            return self.fake_run(command, **kwargs)
        with patch.object(self.module, 'run', side_effect=reject_encoder):
            with self.assertRaisesRegex(self.module.InstallError, 'libx264'):
                self.installer.preflight()
        self.assertFalse(self.installer.path(self.module.APP).exists())

    def test_fresh_install_and_repeat_preserve_credentials_settings_and_video(self):
        self.prepare_device()
        self.assertTrue(self.installer.install())
        app = self.installer.path(self.module.APP)
        coordinator = app / 'tesla-usb-start.sh'
        self.assertEqual(coordinator.read_text(encoding='utf-8'),
                         (REPO / 'scripts/tesla-usb-start.sh').read_text(encoding='utf-8'))
        auth = (app / 'auth.conf').read_bytes()
        self.assertIn(b'user=admin', auth)
        self.assertFalse(json.loads((app / 'settings.json').read_text())['auto_update'])
        video = self.installer.path(self.installer.data_home) / 'archive/keep.mp4'
        video.write_bytes(b'original video')
        self.assertFalse(self.installer.install())
        self.assertEqual((app / 'auth.conf').read_bytes(), auth)
        self.assertEqual(video.read_bytes(), b'original video')
        link = self.installer.path('/etc/rc.d/S08tsf-mount')
        if self.module.os.name == 'nt':
            self.assertEqual(link.read_text(), 'symlink:../init.d/tsf-mount')
        else:
            self.assertTrue(link.is_symlink())
            self.assertEqual(coordinator.stat().st_mode & 0o777, 0o755)
            self.assertEqual((app / 'auth.conf').stat().st_mode & 0o777, 0o600)
            self.assertEqual((app / 'settings.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.installer.path('/etc/init.d/tsf-mount').stat().st_mode & 0o777, 0o755)
        self.assertFalse(self.installer.path('/etc/rc.d/S99tesla-viewer').exists())
        self.assertIn('/usr/bin/backup', self.installer.path('/etc/crontabs/root').read_text())

    def test_failed_publish_removes_only_new_files(self):
        self.prepare_device()
        cron = self.installer.path('/etc/crontabs/root').read_bytes()
        real_write = self.module.write
        def fail_conf(path, *args, **kwargs):
            if path == self.installer.path(self.module.CONF):
                raise OSError('simulated full filesystem')
            return real_write(path, *args, **kwargs)
        with patch.object(self.module, 'write', side_effect=fail_conf):
            with self.assertRaisesRegex(OSError, 'full filesystem'):
                self.installer.install()
        self.assertFalse(self.installer.path(self.module.APP).exists())
        self.assertFalse(self.installer.path(self.installer.data_home).exists())
        self.assertFalse(self.installer.path('/etc/init.d/tesla-viewer').exists())
        self.assertEqual(self.installer.path('/etc/crontabs/root').read_bytes(), cron)
        self.assertTrue(self.installer.path('/etc/init.d/cron').exists())

    def test_racing_configuration_file_is_preserved(self):
        self.prepare_device()
        real_write = self.module.write
        def add_conf(path, *args, **kwargs):
            if path == self.installer.path(self.module.CONF):
                path.write_text('foreign config', encoding='utf-8')
            return real_write(path, *args, **kwargs)
        with patch.object(self.module, 'write', side_effect=add_conf):
            with self.assertRaises(FileExistsError):
                self.installer.install()
        self.assertEqual(self.installer.path(self.module.CONF).read_text(), 'foreign config')

    def prepare_runtime(self):
        runtime = self.root / 'package/runtime'
        runtime.mkdir(parents=True)
        files = {}
        for name in ('bin/python3', 'bin/ffmpeg', 'bin/ffprobe', 'bin/losetup', 'bin/blkid',
                     'bin/flock', 'bin/mount', 'bin/umount', 'bin/readlink', 'bin/setsid',
                     'fonts/NotoSansSC.ttf', 'certs/cacert.pem', 'lib/ld-musl-aarch64.so.1'):
            target = runtime / name
            target.parent.mkdir(parents=True, exist_ok=True)
            data = ('fixture:' + name).encode()
            target.write_bytes(data)
            files[name] = dict(size=len(data), sha256=hashlib.sha256(data).hexdigest(), mode=0o755)
        (runtime / 'manifest.json').write_text(json.dumps(dict(platform='aarch64-linux-musl', files=files)), encoding='utf-8')
        self.installer.runtime = runtime
        return runtime

    def test_corrupted_runtime_is_rejected_before_install(self):
        self.prepare_device()
        runtime = self.prepare_runtime()
        (runtime / 'bin/ffmpeg').write_bytes(b'truncated')
        with self.assertRaisesRegex(self.module.InstallError, '校验失败'):
            self.installer.install()
        self.assertFalse(self.installer.path(self.module.APP).exists())

    def test_runtime_paths_cannot_escape_package(self):
        runtime = self.prepare_runtime()
        manifest = json.loads((runtime / 'manifest.json').read_text())
        manifest['files']['../outside'] = {}
        (runtime / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(self.module.InstallError, '非法路径'):
            self.installer.runtime_files()

    def test_offline_install_moves_runtime_and_uses_final_paths(self):
        self.prepare_device()
        self.installer.preflight()
        runtime = self.prepare_runtime()
        self.installer.device.update({name: str(runtime / 'bin' / name)
                                      for name in ('ffmpeg', 'ffprobe', 'losetup')})
        # Native ARM64 execution is covered separately; this test verifies publication.
        with patch.object(self.installer, 'preflight', return_value=True), \
                patch.object(self.installer, 'python', return_value=self.module.sys.executable):
            self.assertTrue(self.installer.install())
        conf = self.installer.path(self.module.CONF).read_text()
        self.assertNotIn(str(runtime), conf)
        self.assertIn('TSF_PYTHON=/opt/tesla-sentry/runtime/bin/python3', conf)
        self.assertIn('TSF_FFMPEG=/opt/tesla-sentry/runtime/bin/ffmpeg', conf)
        self.assertIn('PATH=/opt/tesla-sentry/runtime/bin:', conf)
        self.assertTrue(self.installer.existing_install())
        (self.installer.path(self.module.APP) / 'runtime/bin/ffmpeg').write_bytes(b'broken')
        with self.assertRaisesRegex(self.module.InstallError, '已有安装'):
            self.installer.existing_install()


if __name__ == '__main__':
    unittest.main()
