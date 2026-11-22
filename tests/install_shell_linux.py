"""Run OpenWrt entry points inside a disposable chroot with fake hardware commands.

Linux CI only: sudo python3 tests/install_shell_linux.py /bin/busybox
The supplied BusyBox must be static. No actual mount, service or USB operation occurs.
"""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
BUSYBOX = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else Path('/bin/busybox')


class BootTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in ('bin', 'etc/init.d', 'dev', 'proc', 'tmp', 'mnt/mmc/tesla-sentry',
                     'opt/tesla-sentry', 'sys/class/udc/controller',
                     'sys/kernel/config/usb_gadget/linux/configs/c.1'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BUSYBOX, self.root / 'bin/busybox')
        os.chmod(self.root / 'bin/busybox', 0o755)
        for name in ('sh', 'awk', 'grep', 'readlink', 'mkdir', 'cat', 'sleep'):
            (self.root / 'bin' / name).symlink_to('busybox')
        os.mknod(self.root / 'dev/card', stat.S_IFBLK | 0o600, os.makedev(7, 123))
        os.mknod(self.root / 'dev/null', stat.S_IFCHR | 0o666, os.makedev(1, 3))
        self.put('proc/mounts', '/dev/card /mnt/mmc ext4 rw,noatime 0 0\n')
        self.put('etc/tesla-sentry.conf',
                 'TSF_DIR=/opt/tesla-sentry\nTSF_CARD_UUID=test-card\nTSF_CARD_MNT=/mnt/mmc\n'
                 'TSF_ARCHIVE=/mnt/mmc/tesla-sentry/archive\nTSF_UDC=controller\n'
                 'TSF_GADGET=/sys/kernel/config/usb_gadget/linux\nTSF_GADGET_CONFIG=c.1\n')
        for name in ('tsf-storage.sh', 'tsf-maintenance.sh'):
            shutil.copyfile(REPO / 'scripts' / name, self.root / 'opt/tesla-sentry' / name)
        for name in ('tesla-gadget', 'tesla-live', 'tesla-viewer', 'tsf-mount'):
            shutil.copyfile(REPO / 'openwrt/init.d' / name, self.root / 'etc/init.d' / name)
            (self.root / 'etc/init.d' / name).chmod(0o755)
        self.put('bin/blkid', '#!/bin/sh\ncase "$1" in -U) echo /dev/card;; *) echo ext4;; esac\n', True)
        self.put('bin/logger', '#!/bin/sh\nexit 0\n', True)
        self.put('bin/mount', '#!/bin/sh\necho "mount-called" >>/tmp/calls\nexit 1\n', True)
        self.put('bin/umount', '#!/bin/sh\necho "umount-called" >>/tmp/calls\nexit 1\n', True)
        self.put('bin/setsid', '#!/bin/sh\nexec "$@"\n', True)
        self.put('opt/tesla-sentry/tesla-gadget.sh', '#!/bin/sh\necho gadget-$1 >>/tmp/calls\n', True)
        self.put('opt/tesla-sentry/tesla-sync.sh', '#!/bin/sh\necho audio >>/tmp/calls\n', True)
        self.put('opt/tesla-sentry/usb-selfheal.sh', '#!/bin/sh\necho usb >>/tmp/calls\n', True)
        self.put('etc/rc.common', '''#!/bin/sh
script=$1
action=$2
procd_open_instance() { :; }
procd_set_param() {
  if [ "$1" = command ]; then
    echo "$3" >>/tmp/calls
    echo "$2" >>/tmp/interpreters
  fi
}
procd_close_instance() { :; }
. "$script"
case "$action" in
  start) start_service;;
  running) exit 0;;
  stop) exit 0;;
esac
''', True)

    def put(self, name, text, executable=False):
        target = self.root / name
        target.write_text(text, encoding='utf-8')
        if executable:
            target.chmod(0o755)

    def shell(self, code):
        return subprocess.run([shutil.which('chroot'), str(self.root), '/bin/sh', '-c', code],
                              capture_output=True, text=True, timeout=10,
                              env={'PATH': '/bin'})

    def test_all_shell_sources_parse_with_busybox_ash(self):
        paths = [REPO / 'install.sh', REPO / 'install-f50.sh', *list((REPO / 'scripts').glob('*.sh')),
                 *list((REPO / 'openwrt/init.d').iterdir())]
        for source in paths:
            with self.subTest(file=source.name):
                result = subprocess.run([str(BUSYBOX), 'ash', '-n', str(source)], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_correct_card_starts_gadget_before_sync_and_viewer(self):
        result = self.shell('. /etc/init.d/tsf-mount; start')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'tmp/calls').read_text().splitlines(),
                         ['gadget-on', '/opt/tesla-sentry/tesla-live-sync.py',
                          '/opt/tesla-sentry/sentry-viewer.py'])

    def test_offline_services_use_bundled_python(self):
        conf = self.root / 'etc/tesla-sentry.conf'
        with conf.open('a', encoding='utf-8') as stream:
            stream.write('TSF_PYTHON=/opt/tesla-sentry/runtime/bin/python3\n')
        result = self.shell('. /etc/init.d/tsf-mount; start')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'tmp/interpreters').read_text().splitlines(),
                         ['/opt/tesla-sentry/runtime/bin/python3'] * 2)

    def test_wrong_card_blocks_each_service_and_mount_does_not_replace_it(self):
        self.put('proc/mounts', '/dev/other /mnt/mmc ext4 rw 0 0\n')
        for service in ('tesla-viewer', 'tesla-live', 'tesla-gadget'):
            result = self.shell('. /etc/init.d/%s; start_service' % service)
            self.assertNotEqual(result.returncode, 0)
        result = self.shell('. /etc/init.d/tsf-mount; mount_card')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'tmp/calls').exists())

    def test_absent_card_does_not_start_services(self):
        (self.root / 'dev/card').unlink()
        result = self.shell('. /etc/init.d/tesla-live; start_service')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'tmp/calls').exists())

    def test_empty_audio_queue_never_runs_usb_sync(self):
        result = self.shell('sh /opt/tesla-sentry/tsf-maintenance.sh audio')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'tmp/calls').exists())
        queue = self.root / 'mnt/mmc/tesla-sentry/archive/boombox-queue'
        queue.mkdir(parents=True)
        (queue / 'test.wav').write_bytes(b'audio')
        result = self.shell('sh /opt/tesla-sentry/tsf-maintenance.sh audio')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'tmp/calls').read_text(), 'audio\n')

    def test_unfinished_resize_blocks_gadget_rebinding(self):
        shutil.copyfile(REPO / 'scripts/tesla-gadget.sh', self.root / 'opt/tesla-sentry/tesla-gadget.sh')
        self.put('opt/tesla-sentry/teslacam.img.resize-state', '/tmp/unfinished-migration\n')
        for action in ('on', 'restore', 'off'):
            result = self.shell('sh /opt/tesla-sentry/tesla-gadget.sh ' + action)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('容量调整未完成', result.stdout)
        self.assertFalse((self.root / 'tmp/calls').exists())


if __name__ == '__main__':
    unittest.main()
