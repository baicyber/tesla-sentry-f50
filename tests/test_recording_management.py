"""Recording actions must preserve protected footage and target exact trip members."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class RecordingManagementTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.archive = self.root / 'archive'
        self.enterContext(patch.dict(os.environ, TSF_ARCHIVE=str(self.archive),
            TSF_CACHE=str(self.root/'cache'), TSF_SETTINGS=str(self.root/'settings.json')))
        spec = importlib.util.spec_from_file_location('recording_viewer', ROOT/'sentry-viewer.py')
        self.viewer = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'fcntl': types.ModuleType('fcntl')} if os.name == 'nt' else {}):
            spec.loader.exec_module(self.viewer)
        self.enterContext(patch.object(self.viewer, 'movie_duration', return_value=60))
        self.enterContext(patch.object(self.viewer, '_acquire_usb_flock', return_value=True))
        self.release = self.enterContext(patch.object(self.viewer, '_release_usb_flock'))
        self.enterContext(patch.object(self.viewer, 'free_mb', return_value=99999))

    def clip(self, cat, clock):
        name = '2026-11-20_' + clock
        folder = self.archive/cat/name
        folder.mkdir(parents=True)
        (folder/(name+'-front.mp4')).write_bytes(b'video' * 100)
        os.utime(folder, (1, 1))
        return folder

    def body(self, cat, event, action='lock', locked=True):
        item = next(e for e in self.viewer.list_events(cat, trips=True)[cat] if e['name'] == event)
        return dict(cat=cat, event=event, clips=item.get('clips', [event]), action=action, locked=locked)

    def test_lock_persists_and_protects_against_all_cleanup_modes(self):
        for cat in ('SentryClips', 'SavedClips', 'DriveClips'):
            old = self.clip(cat, '08-00-00')
            self.clip(cat, '09-00-00')
            self.viewer.recording_action(self.body(cat, old.name))
            self.assertTrue(self.viewer.list_events(cat, trips=True)[cat][-1]['locked'])
        self.viewer.BOX_KEEP_DAYS = .001
        self.viewer.BOX_MAX_GB = .0000001
        self.viewer.ARCHIVE_TOTAL_GB = .0000001
        self.viewer.DRIVE_MAX_GB = .0000001
        self.viewer.roll_box()
        self.viewer.roll_archive_total()
        drive = self.archive/'DriveClips'
        self.viewer.DriveArchive().rotate(str(drive), self.viewer._dir_size(str(drive)))
        for cat in ('SentryClips', 'SavedClips', 'DriveClips'):
            self.assertTrue((self.archive/cat/'2026-11-20_08-00-00').is_dir())
        self.assertEqual(len(json.loads((self.archive/'.recording-locks.json').read_text(encoding='utf-8'))), 3)

    def test_trip_delete_requires_unlock_and_all_confirmed_members(self):
        first = self.clip('DriveClips', '08-00-00')
        second = self.clip('DriveClips', '08-01-00')
        other = self.clip('DriveClips', '10-00-00')
        body = self.body('DriveClips', first.name)
        self.viewer.recording_action(body)
        with self.assertRaisesRegex(ValueError, '解锁'):
            self.viewer.recording_action(dict(body, action='delete'))
        self.viewer.recording_action(dict(body, locked=False))
        with self.assertRaisesRegex(ValueError, '片段已变化'):
            self.viewer.recording_action(dict(body, action='delete', clips=[first.name]))
        result = self.viewer.recording_action(dict(body, action='delete'))
        self.assertEqual(result['done'], 2)
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertTrue(other.is_dir())
        self.assertIn('DriveClips/'+second.name, Path(self.viewer.PRUNED_FILE).read_text(encoding='utf-8'))
        self.release.assert_called_once()

    def test_untrusted_targets_and_busy_sync_cannot_remove_files(self):
        folder = self.clip('SavedClips', '08-00-00')
        body = self.body('SavedClips', folder.name, 'delete')
        for changes in (dict(cat='RecentClips'), dict(cat='../SavedClips'), dict(event='../'), dict(clips=['../']), dict(action='purge')):
            with self.assertRaises(ValueError):
                self.viewer.recording_action(dict(body, **changes))
        with patch.object(self.viewer, '_acquire_usb_flock', return_value=False):
            with self.assertRaisesRegex(ValueError, '正在同步'):
                self.viewer.recording_action(body)
        self.assertTrue(folder.exists())
        self.assertFalse(Path(self.viewer.PRUNED_FILE).exists())

    def test_lock_write_failure_and_corruption_fail_closed(self):
        folder = self.clip('SavedClips', '08-00-00')
        with patch.object(self.viewer.storage_allocation, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.viewer.recording_action(self.body('SavedClips', folder.name))
        (self.archive/'.recording-locks.json').write_text('{', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '锁定状态'):
            self.viewer.roll_box()
        event = self.viewer.list_events('SavedClips')['SavedClips'][0]
        self.assertTrue(event['lock_unknown'])
        self.assertTrue(event['videos'])
        self.assertTrue(folder.exists())

    def test_legacy_delete_upload_and_hardlink_alias_cannot_modify_locked_video(self):
        folder = self.clip('DriveClips', '08-00-00')
        source = next(folder.iterdir())
        original = source.read_bytes()
        self.viewer.recording_action(self.body('DriveClips', folder.name))
        for target in (source, self.archive/'.recording-locks.json'):
            with self.assertRaisesRegex(ValueError, '解锁'):
                self.viewer.archive_delete_file(str(target))
            with self.assertRaisesRegex(ValueError, '解锁'):
                self.viewer.archive_upload(str(target.parent), target.name, b'replacement')
        recent = self.archive/'RecentClips'/folder.name
        recent.mkdir(parents=True)
        os.link(source, recent/source.name)
        self.viewer.archive_upload(str(recent), source.name, b'replacement')
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual((recent/source.name).read_bytes(), b'replacement')

    def test_new_members_of_locked_drive_trip_inherit_protection(self):
        first = self.clip('DriveClips', '08-00-00')
        self.viewer.recording_action(self.body('DriveClips', first.name))
        second = self.clip('DriveClips', '08-01-00')
        self.clip('DriveClips', '10-00-00')
        self.viewer.DRIVE_MAX_GB = .0000001
        drive = self.archive/'DriveClips'
        self.viewer.DriveArchive().rotate(str(drive), self.viewer._dir_size(str(drive)))
        self.assertTrue(second.exists())
        self.assertIn('DriveClips/'+second.name, self.viewer.recording_locks())

    def test_file_manager_cannot_delete_locked_parent_or_child(self):
        folder = self.clip('SavedClips', '08-00-00')
        self.viewer.recording_action(self.body('SavedClips', folder.name))
        if os.name == 'nt':
            # The existing file manager resolves Linux archive paths. Exercise
            # its protection on Windows; Linux/device tests use the real resolver.
            self.enterContext(patch.object(self.viewer, '_fm_resolve',
                                          side_effect=lambda area, path, root: os.path.realpath(path)))
        for target in (folder, folder.parent, next(folder.iterdir())):
            ok, message, *_ = self.viewer.fm_action('archive', 'delete', [str(target)], '', '')
            self.assertFalse(ok)
            self.assertIn('解锁', message)
        self.assertTrue(folder.exists())

    def test_failed_delete_releases_lock_and_reports_partial_result(self):
        first = self.clip('DriveClips', '08-00-00')
        second = self.clip('DriveClips', '08-01-00')
        body = self.body('DriveClips', first.name, 'delete')
        delete = self.viewer.shutil.rmtree
        def partial(path):
            if Path(path) == second:
                raise OSError('injected failure')
            delete(path)
        with patch.object(self.viewer.shutil, 'rmtree', side_effect=partial):
            result = self.viewer.recording_action(body)
        self.assertFalse(result['ok'])
        self.assertEqual(result['done'], 1)
        self.assertTrue(second.exists())
        self.release.assert_called_once()


if __name__ == '__main__':
    unittest.main()
