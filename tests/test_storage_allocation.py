import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('storage_allocation', REPO / 'storage_allocation.py')
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.values = dict(archive_gib=34, drive_gib=12, free_min_gib=4, free_target_gib=10)

    def test_budget_counts_drive_inside_archive(self):
        self.assertEqual(storage.required_bytes(self.values, 64), 112 * storage.GIB)
        storage.check_budget(self.values, 64, {'budget_bytes': 112 * storage.GIB})
        with self.assertRaisesRegex(ValueError, '预算'):
            storage.check_budget(self.values, 64, {'budget_bytes': 111 * storage.GIB})

    def test_invalid_values_cannot_reach_storage(self):
        for value in (True, 0, -1, 1.5, float('nan'), float('inf'), '12'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                storage.validate_values(dict(self.values, drive_gib=value), 64)
        with self.assertRaisesRegex(ValueError, '归档总容量'):
            storage.validate_values(dict(self.values, drive_gib=35), 64)
        with self.assertRaisesRegex(ValueError, '最低空闲'):
            storage.validate_values(dict(self.values, free_min_gib=11), 64)

    def test_atomic_save_and_failure_keep_previous_settings(self):
        path = self.root / 'storage.json'
        storage.save(self.values, path)
        with patch.object(storage.os, 'replace', side_effect=OSError('full')):
            with self.assertRaises(OSError):
                storage.save(dict(self.values, drive_gib=15), path)
        self.assertEqual(storage.load(path), self.values)
        self.assertEqual(list(self.root.glob('.storage-*')), [])

    def test_hardlinks_are_not_counted_twice(self):
        (self.root / 'a').write_bytes(b'video' * 100)
        os.link(self.root / 'a', self.root / 'b')
        self.assertEqual(storage.tree_usage(self.root)[0], 500)

    def test_revision_changes_on_configuration_or_image_size(self):
        image = self.root / 'image'
        image.write_bytes(b'original')
        before = storage.revision(self.values, image)
        self.assertNotEqual(before, storage.revision(dict(self.values, drive_gib=13), image))
        image.write_bytes(b'replaced image')
        self.assertNotEqual(before, storage.revision(self.values, image))

    def test_malformed_saved_settings_are_not_silently_applied(self):
        path = self.root / 'storage.json'
        path.write_text(json.dumps(dict(self.values, drive_gib=0)), encoding='utf-8')
        with self.assertRaises(ValueError):
            storage.load(path)


if __name__ == '__main__':
    unittest.main()
