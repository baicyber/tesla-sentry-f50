"""USB startup regressions using repository scripts and a disposable filesystem.

Run on Linux: python3 -B -m unittest discover -s tests -p test_usb_startup.py -v
Only copies of shell scripts run; all configuration/sysfs paths are redirected.
No root access, USB hardware, mounted image or firmware source is required.
"""
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform.startswith('linux'), 'Requires Linux /bin/sh and flock')
class UsbStartupTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='tsf-usb-test-')))
        self.path('/run').mkdir()
        self.path('/tmp').mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.trace = self.root / 'commands.log'
        self.exports = self.root / 'exports.log'
        self.gadget = self.path('/sys/kernel/config/usb_gadget/linux')
        self.udc = self.gadget / 'UDC'
        self.lun = self.gadget / 'functions/mass_storage.0/lun.0/file'
        self.link = self.gadget / 'configs/c.1/massstorage'
        self.adb_link = self.gadget / 'configs/c.1/f3'
        self.ep1 = self.path('/dev/usb-ffs/adb/ep1')
        self.shared_lock = self.path('/run/mu300-usb-rebind.lock')
        self.storage_lock = self.path('/tmp/tsf-usb.lock')
        self.manage_mount = self.path('/tmp/teslamnt')
        self.image = self.path('/mnt/test-data/teslacam.img')
        self.app = self.path('/opt/tesla-sentry')
        (self.gadget / 'configs/c.1').mkdir(parents=True)
        self.write(self.udc, 'test.udc\n')
        self.write(self.lun, '')
        self.write(self.image, 'fixture image; never mounted\n')
        self.write(self.path('/etc/mu300/usb-storage-boot'), '1\n')
        self.write(self.path('/sys/class/udc/test.udc/state'), 'configured\n')
        self.write(self.path('/sys/class/usb_role/25100000.dwc3-role-switch/role'), 'device\n')
        self.write(self.path('/etc/mu300/adb'), 'on\n')
        self.write(self.path('/opt/mu300/bin/adbd'), '#!/bin/sh\nexit 0\n', executable=True)
        self.write(self.path('/proc/mounts'), '')
        self.write(self.path('/etc/tesla-sentry.conf'), '\n'.join([
            'TSF_DIR=' + str(self.app), 'TSF_IMG=' + str(self.image),
            'TSF_GADGET=' + str(self.gadget), 'TSF_GADGET_CONFIG=c.1', 'TSF_UDC=test.udc',
            'TSF_LOG_DIR=' + str(self.app), 'TSF_USB_BOOT_WAIT=60',
            'TSF_USB_LOCK=' + str(self.storage_lock), 'TSF_MNT=' + str(self.manage_mount),
            'TSF_USB_BOOT_LOCK=' + str(self.path('/run/tsf-usb-start.lock')), '',
        ]))
        # Deliberately not idempotent: every invocation counts as an export.
        # This ensures the coordinator, not the fixture, prevents duplicate work.
        self.write(self.app / 'tesla-gadget.sh', '''#!/bin/sh
set -eu
[ "${TSF_USB_START_COORDINATED:-}" = 1 ] || exit 2
held=no
[ -s "$TEST_LOCK/pid" ] && held=yes
printf 'export lock=%s\\n' "$held" >> "$TEST_EXPORTS"
sleep 3
printf '%s\\n' "$TEST_IMG" > "$TEST_LUN"
ln -s "$TEST_FUNCTION" "$TEST_LINK"
printf 'test.udc\\n' > "$TEST_UDC"
''', executable=True)
        for command in ('logger', 'mount', 'umount', 'modprobe'):
            self.write(self.bin / command, '#!/bin/sh\nprintf "%s %s\\n" "' + command + '" "$*" >> "$TEST_TRACE"\n', executable=True)
        self.write(self.bin / 'setsid', '#!/bin/sh\nexec "$@"\n', executable=True)
        self.write(self.bin / 'sleep', '#!' + sys.executable + '\nimport sys,time\ntime.sleep(min(float(sys.argv[1]) * 0.05, 0.2))\n', executable=True)
        self.env = dict(os.environ)
        self.env.update({
            'PATH': str(self.bin) + ':' + os.environ.get('PATH', '/bin:/usr/bin'),
            'TEST_TRACE': str(self.trace), 'TEST_EXPORTS': str(self.exports),
            'TEST_IMG': str(self.image), 'TEST_LUN': str(self.lun), 'TEST_UDC': str(self.udc),
            'TEST_LINK': str(self.link), 'TEST_FUNCTION': str(self.lun.parent.parent),
            'TEST_LOCK': str(self.shared_lock),
            'TSF_CONF': str(self.path('/etc/tesla-sentry.conf')),
            'MU300_USB_LOCK': str(self.shared_lock),
            'MU300_ADB_CONF': str(self.path('/etc/mu300/adb')),
            'MU300_ADBD_BIN': str(self.path('/opt/mu300/bin/adbd')),
            'MU300_FFS': str(self.path('/dev/usb-ffs/adb')),
            'MU300_USB_ROLE': str(self.path('/sys/class/usb_role/25100000.dwc3-role-switch/role')),
        })
        self.processes = []
        self.prepared = {}
        self.addCleanup(self.stop_owned_processes)
        self.coordinator = self.prepare_script('scripts/tesla-usb-start.sh', '/opt/tesla-sentry/tesla-usb-start.sh')

    def path(self, absolute):
        return self.root / absolute.lstrip('/')

    def write(self, path, text, executable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        if executable:
            path.chmod(0o755)

    def prepare_script(self, relative, absolute):
        if relative in self.prepared:
            return self.prepared[relative]
        text = (REPO / relative).read_text(encoding='utf-8')
        # One pass avoids rewriting the temporary path's own /tmp prefix.
        text = re.sub(r'/(?:etc|sys|run|tmp|opt|var)(?=/|[\s\"\'}])',
                      lambda match: str(self.path(match.group())), text)
        text = text.replace('/dev/usb-ffs', str(self.path('/dev/usb-ffs')))
        text = text.replace('/proc/mounts', str(self.path('/proc/mounts')))
        target = self.path(absolute)
        self.write(target, text, executable=True)
        self.prepared[relative] = target
        return target

    def launch(self, target=None, *args):
        process = subprocess.Popen(['/bin/sh', str(target or self.coordinator), *args], env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        return process

    def finish(self, process, expected=0):
        stdout, stderr = process.communicate(timeout=12)
        self.assertEqual(process.returncode, expected, stdout + stderr)
        return stdout + stderr

    def stop_owned_processes(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()

    def ready_adb(self):
        self.write(self.ep1, 'descriptor-ready\n')
        target = self.gadget / 'functions/ffs.adb'
        target.mkdir(parents=True, exist_ok=True)
        self.adb_link.symlink_to(target)

    def export_lines(self):
        return self.exports.read_text(encoding='utf-8').splitlines() if self.exports.exists() else []

    def short_timeout(self):
        conf = self.path('/etc/tesla-sentry.conf')
        self.write(conf, conf.read_text(encoding='utf-8').replace('TSF_USB_BOOT_WAIT=60', 'TSF_USB_BOOT_WAIT=1'))

    def assert_prerequisite_timeout(self):
        self.short_timeout()
        self.finish(self.launch(), expected=1)
        self.assertIn('logger -t tsf-usb USB startup prerequisites timed out', self.trace.read_text(encoding='utf-8'))
        self.assertEqual(self.export_lines(), [])
        self.assertFalse(self.shared_lock.exists())

    def test_waits_for_adb_before_exporting(self):
        process = self.launch()
        time.sleep(0.2)
        self.assertEqual(self.export_lines(), [])
        self.assertIsNone(process.poll(), 'Coordinator exited instead of waiting for ADB')
        self.ready_adb()
        self.finish(process)
        self.assertEqual(self.export_lines(), ['export lock=yes'])

    def test_three_startup_entries_export_once(self):
        self.ready_adb()
        mount = self.prepare_script('openwrt/init.d/tsf-mount', '/etc/init.d/tsf-mount')
        service = self.prepare_script('openwrt/init.d/tesla-gadget', '/etc/init.d/tesla-gadget')
        self.write(self.app / 'tsf-storage.sh', 'tsf_storage_ready() { return 0; }\n')
        for name in ('tesla-live', 'tesla-viewer'):
            self.write(self.path('/etc/init.d/' + name), '#!/bin/sh\nexit 0\n', executable=True)
        mount_harness = self.root / 'mount-entry.sh'
        self.write(mount_harness, '. ' + shlex.quote(str(mount)) + '\nstart_apps\n')
        service_harness = self.root / 'gadget-entry.sh'
        self.write(service_harness, '. ' + shlex.quote(str(service)) + '\nstart_service\nwait\n')
        processes = [self.launch(), self.launch(mount_harness), self.launch(service_harness)]
        for process in processes:
            self.finish(process)
        self.assertEqual(self.export_lines(), ['export lock=yes'])

    def test_existing_export_is_unchanged(self):
        self.write(self.lun, str(self.image) + '\n')
        self.link.symlink_to(self.lun.parent.parent)
        before = self.udc.stat().st_mtime_ns
        self.finish(self.launch())
        self.assertEqual(self.export_lines(), [])
        self.assertEqual(self.udc.stat().st_mtime_ns, before)

    def test_adb_off_does_not_block_storage(self):
        self.write(self.path('/etc/mu300/adb'), 'off\n')
        self.finish(self.launch())
        self.assertEqual(self.export_lines(), ['export lock=yes'])

    def test_finished_failed_adb_without_ffs_link_allows_storage(self):
        self.write(self.path('/run/mu300-adb-start-finished'), '')
        self.finish(self.launch())
        self.assertEqual(self.export_lines(), ['export lock=yes'])

    def test_finished_adb_with_stale_ffs_link_does_not_export(self):
        self.ready_adb()
        self.ep1.unlink()
        self.write(self.path('/run/mu300-adb-start-finished'), '')
        self.assert_prerequisite_timeout()

    def test_unready_adb_times_out_without_export(self):
        self.assert_prerequisite_timeout()

    def test_missing_image_times_out_without_export(self):
        self.ready_adb()
        self.image.unlink()
        self.assert_prerequisite_timeout()

    def test_live_platform_lock_is_respected_and_preserved(self):
        self.ready_adb()
        self.write(self.shared_lock / 'pid', str(os.getpid()) + '\n')
        self.short_timeout()
        self.finish(self.launch(), expected=1)
        self.assertIn('USB rebind lock timed out', self.trace.read_text(encoding='utf-8'))
        self.assertEqual(self.export_lines(), [])
        self.assertEqual((self.shared_lock / 'pid').read_text(encoding='utf-8').strip(), str(os.getpid()))

    def mark_management_mounted(self):
        self.write(self.path('/proc/mounts'), '/dev/loop99 ' + str(self.manage_mount) + ' exfat rw 0 0\n')

    def test_management_mount_is_not_exported(self):
        self.ready_adb()
        self.mark_management_mounted()
        before = self.udc.stat().st_mtime_ns
        self.finish(self.launch())
        self.assertEqual(self.export_lines(), [])
        self.assertEqual(self.udc.stat().st_mtime_ns, before)

    def test_management_lock_and_mount_are_rechecked_after_waiting_for_adb(self):
        import fcntl
        process = self.launch()
        time.sleep(0.15)
        self.assertIsNone(process.poll())
        with self.storage_lock.open('w', encoding='utf-8') as management_lock:
            fcntl.flock(management_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.mark_management_mounted()
            self.ready_adb()
            time.sleep(0.2)
            self.assertIsNone(process.poll(), 'Coordinator did not wait for the viewer USB lock')
            self.assertEqual(self.export_lines(), [])
            fcntl.flock(management_lock, fcntl.LOCK_UN)
        self.finish(process)
        self.assertEqual(self.export_lines(), [], 'Mount state was not rechecked after acquiring the lock')

    def test_exporter_does_not_rebind_complete_export(self):
        self.env['TSF_USB_START_COORDINATED'] = '1'
        exporter = self.prepare_script('scripts/tesla-gadget.sh', '/opt/tesla-sentry/tesla-gadget.sh')
        self.write(self.lun, str(self.image) + '\n')
        self.link.symlink_to(self.lun.parent.parent)
        before = self.udc.stat().st_mtime_ns
        output = self.finish(self.launch(exporter, 'on'))
        self.assertIn('ALREADY-ON', output)
        self.assertEqual(self.udc.stat().st_mtime_ns, before)
        self.assertFalse((self.app / 'tesla-gadget.log').exists())

    def test_exporter_repairs_missing_link_with_image_already_assigned(self):
        self.env['TSF_USB_START_COORDINATED'] = '1'
        exporter = self.prepare_script('scripts/tesla-gadget.sh', '/opt/tesla-sentry/tesla-gadget.sh')
        self.write(self.lun, str(self.image) + '\n')
        output = self.finish(self.launch(exporter, 'on'))
        self.assertNotIn('ALREADY-ON', output)
        self.assertIn('TESLA-GADGET-ON', output)
        self.assertTrue(self.link.is_symlink())
        self.assertEqual(self.udc.read_text(encoding='utf-8').strip(), 'test.udc')

    def test_exporter_repairs_unbound_udc_with_image_already_assigned(self):
        self.env['TSF_USB_START_COORDINATED'] = '1'
        exporter = self.prepare_script('scripts/tesla-gadget.sh', '/opt/tesla-sentry/tesla-gadget.sh')
        self.write(self.lun, str(self.image) + '\n')
        self.link.symlink_to(self.lun.parent.parent)
        self.write(self.udc, '')
        output = self.finish(self.launch(exporter, 'on'))
        self.assertNotIn('ALREADY-ON', output)
        self.assertIn('TESLA-GADGET-ON', output)
        self.assertEqual(self.udc.read_text(encoding='utf-8').strip(), 'test.udc')

    def test_ordinary_exporter_preserves_management_unbind(self):
        self.env.pop('TSF_USB_START_COORDINATED', None)
        exporter = self.prepare_script('scripts/tesla-gadget.sh', '/opt/tesla-sentry/tesla-gadget.sh')
        self.write(self.lun, str(self.image) + '\n')
        self.link.symlink_to(self.lun.parent.parent)
        self.write(self.udc, '')
        before = self.udc.stat().st_mtime_ns
        output = self.finish(self.launch(exporter, 'on'))
        self.assertIn('ALREADY-ON', output)
        self.assertEqual(self.udc.read_text(encoding='utf-8'), '')
        self.assertEqual(self.udc.stat().st_mtime_ns, before)
        self.assertFalse((self.app / 'tesla-gadget.log').exists())


if __name__ == '__main__':
    unittest.main()
