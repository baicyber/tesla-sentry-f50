import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


class StorageIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, TSF_IMG=str(self.root / 'image'),
            TSF_ARCHIVE=str(self.root / 'archive'), TSF_CACHE=str(self.root / 'cache'),
            TSF_STORAGE_SETTINGS=str(self.root / 'storage.json'), TSF_SETTINGS=str(self.root / 'settings.json'),
            TSF_ARCHIVE_TOTAL_GB='34', TSF_DRIVE_MAX_GB='12', TSF_FREE_MIN_MB='4096', TSF_FREE_TARGET_MB='10240'))
        (self.root / 'image').write_bytes(b'image')
        spec = importlib.util.spec_from_file_location('storage_viewer', REPO / 'sentry-viewer.py')
        self.viewer = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'fcntl': types.ModuleType('fcntl')} if os.name == 'nt' else {}):
            spec.loader.exec_module(self.viewer)
        self.disk = dict(total_bytes=128 << 30, free_bytes=50 << 30, budget_bytes=120 << 30,
                         usb_bytes=64 << 30, archive_bytes=40 << 30, drive_bytes=18 << 30, cache_gib=4)
        self.enterContext(patch.object(self.viewer.storage_allocation, 'snapshot', return_value=self.disk))

    def body(self, **changes):
        current = self.viewer.storage_status()
        return dict(values=dict(current['values'], **changes), revision=current['revision'])

    def test_save_updates_limits_and_sync_reads_the_same_file(self):
        body = self.body(archive_gib=36, drive_gib=14, free_min_gib=3)
        result = self.viewer.storage_apply(body)
        self.assertTrue(result['ok'])
        self.assertEqual(self.viewer.ARCHIVE_TOTAL_GB, 36)
        self.assertEqual(self.viewer.DRIVE_MAX_GB, 14)
        spec = importlib.util.spec_from_file_location('storage_sync', REPO / 'tesla-live-sync.py')
        sync = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'fcntl': types.ModuleType('fcntl')} if os.name == 'nt' else {}):
            spec.loader.exec_module(sync)
        sync.refresh_storage_settings()
        self.assertEqual(sync.FREE_MIN_MB, 3072)

    def test_storage_usage_uses_usb_filesystem_and_keeps_unknown_usage_unknown(self):
        for amount in (3 << 30, None):
            with patch.object(self.viewer, '_exfat_used', return_value=amount):
                self.assertEqual(self.viewer.storage_status()['disk']['usb_used_bytes'], amount)

    def test_explicit_archive_maintenance_preserves_recordings_without_changing_limits(self):
        drive = unittest.mock.Mock()
        with patch.dict(os.environ, TSF_ARCHIVE_MAINTENANCE='1'), \
             patch.object(self.viewer, 'roll_box') as cleanup:
            self.viewer.disk_guard_cycle(drive)
            drive.review.assert_not_called()
            cleanup.assert_not_called()
        self.assertEqual(self.viewer.storage_status()['values']['archive_gib'], 34)

    def test_reduction_requires_confirmation_before_any_write(self):
        body = self.body(archive_gib=30, drive_gib=10)
        result = self.viewer.storage_apply(body)
        self.assertTrue(result['confirm'])
        self.assertEqual(result['archive_over_gib'], 10)
        self.assertFalse((self.root / 'storage.json').exists())
        self.assertTrue(self.viewer.storage_apply(dict(body, confirmed=True))['ok'])

    def test_stale_revision_cannot_overwrite_other_clients(self):
        body = self.body(archive_gib=35)
        self.assertTrue(self.viewer.storage_apply(body)['ok'])
        self.assertTrue(self.viewer.storage_apply(body)['conflict'])

    def test_over_budget_and_invalid_drive_limit_leave_settings_unchanged(self):
        for changes in (dict(archive_gib=60), dict(drive_gib=35)):
            with self.assertRaises(ValueError):
                self.viewer.storage_apply(self.body(**changes))
        self.assertFalse((self.root / 'storage.json').exists())

    def test_maintenance_blocks_other_changes_and_background_cleanup(self):
        with self.viewer._storage_job_lock:
            self.viewer._storage_job['state'] = 'running'
        with self.assertRaisesRegex(ValueError, '正在调整'):
            self.viewer.storage_apply(self.body(archive_gib=36))
        drive = unittest.mock.Mock()
        self.viewer.disk_guard_cycle(drive)
        drive.review.assert_not_called()
        self.assertFalse(self.viewer.usb_attach()[0])
        self.assertFalse(self.viewer.usb_detach()[0])

    def test_queued_usb_requests_recheck_maintenance_after_acquiring_lock(self):
        for operation in (self.viewer.usb_attach, self.viewer.usb_detach):
            with patch.object(self.viewer, 'storage_busy', side_effect=[False, True]), \
                 patch.object(self.viewer.subprocess, 'run') as command:
                self.assertFalse(operation()[0])
                command.assert_not_called()

    def test_resize_requires_explicit_clear_consent_even_from_old_clients(self):
        current = self.viewer.storage_status()
        body = dict(usb_gib=65, revision=current['revision'], confirmed=True)
        for consent in (None, False, 'true', 1):
            with patch.object(self.viewer.threading, 'Thread') as worker:
                with self.assertRaisesRegex(ValueError, '清空'):
                    self.viewer.storage_start_resize(dict(body, clear_confirmed=consent))
                worker.assert_not_called()
        self.assertEqual((self.root / 'image').read_bytes(), b'image')
        self.assertEqual(self.viewer.storage_job()['state'], 'idle')


if __name__ == '__main__':
    unittest.main()
