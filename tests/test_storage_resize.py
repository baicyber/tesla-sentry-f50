from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import storage_resize as resize


class ResizeTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.image = self.root / 'teslacam.img'
        self.image.write_bytes(b'original-image')
        self.gadget = self.root / 'gadget'
        self.lun = self.gadget / 'functions/mass_storage.0/lun.0/file'
        self.lun.parent.mkdir(parents=True)
        self.lun.write_text(str(self.image), encoding='utf-8')
        self.binding = self.gadget / 'UDC'
        self.binding.write_text('controller', encoding='ascii')
        self.commands = []
        # Small fake capacities avoid allocating GiB-sized files on Windows.
        self.enterContext(patch.object(resize, 'GIB', 1 << 20))
        self.enterContext(patch.object(resize, 'command', side_effect=self.command))

    def command(self, args):
        args = [str(arg) for arg in args]
        self.commands.append(args)
        if any(arg.endswith('mkexfat.py') for arg in args):
            with Path(args[-2]).open('wb') as stream:
                stream.write(b'\xebv\x90EXFAT   ')
                stream.truncate(int(args[-1]) * resize.GIB)
        else:
            raise AssertionError('Recreation must not mount or copy existing recordings: %s' % args)
        return ''

    def run_resize(self):
        resize.resize(self.image, 1, self.gadget, 'controller',
                      REPO / 'scripts/mkexfat.py', lambda *args: None)

    def test_recreation_clears_usb_and_rebinds_without_changing_archives(self):
        archive = self.root / 'archive/DriveClips/录像.mp4'
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b'archived-video')
        self.run_resize()
        self.assertNotIn(b'original-image', self.image.read_bytes())
        self.assertEqual(archive.read_bytes(), b'archived-video')
        self.assertEqual(self.image.stat().st_size, resize.GIB)
        self.assertEqual(self.binding.read_text().strip(), 'controller')
        self.assertEqual(self.lun.read_text().strip(), str(self.image))
        self.assertEqual(list(self.root.glob('*.resize-*')), [])

    def test_failed_creation_keeps_old_image_and_restores_usb(self):
        with patch.object(resize, 'command', side_effect=OSError('No space left on device')):
            with self.assertRaisesRegex(OSError, 'No space'):
                self.run_resize()
        self.assertEqual(self.image.read_bytes(), b'original-image')
        self.assertEqual(self.binding.read_text().strip(), 'controller')
        self.assertFalse(Path(str(self.image) + '.resize-new').exists())

    def test_creation_runs_with_empty_lun_and_connected_other_usb_functions(self):
        def check_and_create(args):
            self.assertEqual(self.binding.read_text().strip(), 'controller')
            self.assertEqual(self.lun.read_text().strip(), '')
            return self.command(args)
        with patch.object(resize, 'command', side_effect=check_and_create):
            self.run_resize()

    def test_already_bound_controller_is_accepted_only_when_it_matches(self):
        with patch.object(Path, 'write_text', side_effect=OSError('Device or resource busy')):
            resize.bind_controller(self.binding, 'controller')
            with self.assertRaises(OSError):
                resize.bind_controller(self.binding, 'foreign-controller')

    def test_failed_publish_keeps_old_image(self):
        with patch.object(resize.os, 'replace', side_effect=OSError('publish failed')):
            with self.assertRaisesRegex(OSError, 'publish failed'):
                self.run_resize()
        self.assertEqual(self.image.read_bytes(), b'original-image')
        self.assertEqual(self.binding.read_text().strip(), 'controller')
        self.assertFalse(Path(str(self.image) + '.resize-backup').exists())

    def test_old_contents_can_exceed_new_capacity_when_clearing(self):
        with self.image.open('wb') as stream:
            stream.write(b'original-image')
            stream.truncate(2 * resize.GIB)
        self.run_resize()
        self.assertEqual(self.image.stat().st_size, resize.GIB)

    def test_existing_recovery_file_and_foreign_lun_are_protected(self):
        backup = Path(str(self.image) + '.resize-backup')
        backup.write_bytes(b'previous recovery')
        with self.assertRaisesRegex(resize.ResizeError, '恢复文件'):
            self.run_resize()
        self.assertEqual(backup.read_bytes(), b'previous recovery')
        backup.unlink()
        self.lun.write_text('/other/image', encoding='utf-8')
        with self.assertRaisesRegex(resize.ResizeError, '状态不匹配'):
            self.run_resize()
        self.assertEqual(self.binding.read_text(), 'controller')

    def test_invalid_new_image_does_not_publish(self):
        with patch.object(resize, 'command', return_value=''):
            with self.assertRaisesRegex(resize.ResizeError, '校验失败'):
                self.run_resize()
        self.assertEqual(self.image.read_bytes(), b'original-image')

    def test_rebind_failure_restores_old_image_after_publish(self):
        original_write = Path.write_text
        failed = False
        def reject_new_export(path, data, *args, **kwargs):
            nonlocal failed
            if path == self.binding and data.strip() == 'controller' and not failed and Path(str(self.image) + '.resize-backup').exists():
                failed = True
                raise OSError('rebind failed')
            return original_write(path, data, *args, **kwargs)
        with patch.object(Path, 'write_text', reject_new_export):
            with self.assertRaisesRegex(OSError, 'rebind failed'):
                self.run_resize()
        self.assertEqual(self.image.read_bytes(), b'original-image')
        self.assertEqual(self.binding.read_text().strip(), 'controller')
        self.assertFalse(resize.pending_recovery(self.image))

    def test_failed_recovery_retains_durable_marker(self):
        original_write = Path.write_text
        def reject_exports(path, data, *args, **kwargs):
            if path == self.binding and data.strip() == 'controller':
                raise OSError('controller unavailable')
            return original_write(path, data, *args, **kwargs)
        with patch.object(Path, 'write_text', reject_exports):
            with self.assertRaises(resize.ResizeError) as raised:
                self.run_resize()
        self.assertTrue(raised.exception.needs_recovery)
        self.assertTrue(resize.pending_recovery(self.image))
        self.assertEqual(self.image.read_bytes(), b'original-image')
        self.assertEqual(self.binding.read_text().strip(), '')


if __name__ == '__main__':
    unittest.main()
