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
        for value in (True, -1, 1.5, float('nan'), float('inf'), '12'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                storage.validate_values(dict(self.values, drive_gib=value), 64)
        for zero_field in ('free_min_gib', 'free_target_gib'):
            with self.subTest(field=zero_field), self.assertRaises(ValueError):
                storage.validate_values(dict(self.values, **{zero_field: 0}), 64)
        with self.assertRaisesRegex(ValueError, '归档总容量'):
            storage.validate_values(dict(self.values, drive_gib=35), 64)
        with self.assertRaisesRegex(ValueError, '最低空闲'):
            storage.validate_values(dict(self.values, free_min_gib=11), 64)

    def test_zero_archive_means_unlimited(self):
        unlimited = dict(self.values, archive_gib=0)
        self.assertEqual(storage.validate_values(unlimited, 64)['archive_gib'], 0)
        # 归档无上限时不参与预留预算, 空间交给空闲保护线兜底
        self.assertEqual(storage.required_bytes(unlimited, 64), 78 * storage.GIB)
        # 归档无上限时行车记录上限不再受它约束
        self.assertEqual(storage.validate_values(dict(unlimited, drive_gib=12), 64)['drive_gib'], 12)
        storage.check_budget(dict(unlimited, drive_gib=12), 64, {'budget_bytes': 78 * storage.GIB})

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
        path.write_text(json.dumps(dict(self.values, free_min_gib=0)), encoding='utf-8')
        with self.assertRaises(ValueError):
            storage.load(path)


if __name__ == '__main__':
    unittest.main()
