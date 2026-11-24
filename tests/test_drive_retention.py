"""Quota rotation must retain new four-camera clips without recycling old trips."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


class DriveRetentionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.archive = self.root / "archive"
        self.enterContext(patch.dict(os.environ, TSF_ARCHIVE=str(self.archive),
                                     TSF_CACHE=str(self.root / "cache"),
                                     TSF_SETTINGS=str(self.root / "settings.json")))
        source = Path(__file__).resolve().parents[1] / "sentry-viewer.py"
        spec = importlib.util.spec_from_file_location("retention_viewer", source)
        self.viewer = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"fcntl": types.ModuleType("fcntl")} if os.name == "nt" else {}):
            spec.loader.exec_module(self.viewer)
        self.recent = self.archive / "RecentClips"
        self.retained = self.archive / "DriveClips"
        self.recent.mkdir(parents=True)
        self.retained.mkdir()
        self.enterContext(patch.object(self.viewer, "drive_clip_gears", return_value=(60, [[0, 1]])))
        self.enterContext(patch.object(self.viewer, "movie_duration", return_value=60))
        # Windows cannot open directories for fsync; Linux/device runs use real directory fds.
        if os.name == "nt":
            sentinel = self.root / "directory-fsync"
            sentinel.write_bytes(b"test")
            real_open = os.open

            def open_directory(path, flags, *args, **kwargs):
                if Path(path).is_dir():
                    return real_open(sentinel, os.O_RDWR)
                return real_open(path, flags, *args, **kwargs)

            self.enterContext(patch.object(os, "O_DIRECTORY", 0, create=True))
            self.enterContext(patch.object(os, "open", side_effect=open_directory))
        self.viewer.DRIVE_MAX_GB = 3 * 256 / (1 << 30)

    def clip(self, clock, *, retained=False, size=64):
        name = "2026-11-20_" + clock
        folder = (self.retained if retained else self.recent) / name
        folder.mkdir()
        for camera in self.viewer.DRIVE_CAMERAS:
            (folder / (name + "-" + camera + ".mp4")).write_bytes(b"v" * size)
        return name

    def names(self):
        return sorted(path.name for path in self.retained.iterdir())

    def test_full_archive_evicts_whole_oldest_trip(self):
        self.clip("08-00-00", retained=True)
        self.clip("08-01-00", retained=True)
        middle = self.clip("09-00-00", retained=True)
        newest = self.clip("10-00-00")
        protected = self.archive / "SavedClips" / "keep.mp4"
        protected.parent.mkdir()
        protected.write_bytes(b"original")
        self.viewer.DriveArchive().review()
        self.assertEqual(self.names(), [middle, newest])
        self.assertEqual(protected.read_bytes(), b"original")
        for source in (self.recent / newest).iterdir():
            self.assertTrue(os.path.samefile(source, self.retained / newest / source.name))

    def test_rotated_clips_are_not_reimported_after_restart(self):
        for clock in ("08-00-00", "08-01-00", "09-00-00", "10-00-00"):
            self.clip(clock)
        self.viewer.DriveArchive().review()
        expected = self.names()
        for _ in range(3):
            self.viewer.DriveArchive().review()
            self.assertEqual(self.names(), expected)
        self.assertEqual(expected, ["2026-11-20_09-00-00", "2026-11-20_10-00-00"])

    def test_manual_delete_skips_only_deleted_clip_and_refreshes_running_guard(self):
        earlier = self.clip('08-00-00')
        deleted = self.clip('09-00-00')
        later = self.clip('10-00-00')
        guard = self.viewer.DriveArchive()
        self.assertTrue(self.viewer._tombstone('DriveClips', deleted))
        guard.review()
        self.assertEqual(self.names(), [earlier, later])
        self.viewer.DriveArchive().review()
        self.assertEqual(self.names(), [earlier, later])

    def test_long_single_trip_keeps_latest_minutes(self):
        for minute in range(5):
            self.clip("08-%02d-00" % minute)
        self.viewer.DriveArchive().review()
        self.assertEqual(self.names(), ["2026-11-20_08-%02d-00" % minute for minute in (2, 3, 4)])

    def test_failed_link_leaves_old_archive_intact(self):
        old = self.clip("08-00-00", retained=True)
        self.viewer.DRIVE_MAX_GB = 256 / (1 << 30)
        self.clip("10-00-00")
        real_link = os.link
        calls = 0

        def failed_link(source, target):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("injected hard-link failure")
            return real_link(source, target)

        with patch.object(os, "link", side_effect=failed_link):
            with self.assertRaises(OSError):
                self.viewer.DriveArchive().review()
        self.assertEqual(self.names(), [old])
        self.assertFalse(list(self.archive.glob(".drive-link-*")))

    def test_oversized_single_clip_does_not_discard_old_archive(self):
        old = self.clip("08-00-00", retained=True)
        self.clip("10-00-00", size=256)
        self.viewer.DriveArchive().review()
        self.assertEqual(self.names(), [old])

    def test_source_changed_during_link_does_not_discard_old_archive(self):
        old = self.clip("08-00-00", retained=True)
        newest = self.clip("10-00-00")
        self.viewer.DRIVE_MAX_GB = 256 / (1 << 30)
        real_link = os.link

        def changing_link(source, target):
            real_link(source, target)
            if source.endswith("right_repeater.mp4"):
                (self.recent / newest / (newest + "-front.mp4")).write_bytes(b"changed")

        with patch.object(os, "link", side_effect=changing_link):
            with self.assertRaises(OSError):
                self.viewer.DriveArchive().review()
        self.assertEqual(self.names(), [old])

    def test_failed_delete_keeps_new_clip_and_retries(self):
        old = self.clip("08-00-00", retained=True)
        newest = self.clip("10-00-00")
        self.viewer.DRIVE_MAX_GB = 256 / (1 << 30)
        with patch.object(self.viewer.shutil, "rmtree", side_effect=OSError("injected delete failure")):
            with self.assertRaises(OSError):
                self.viewer.DriveArchive().review()
        self.assertEqual(self.names(), [old, newest])
        self.viewer.DriveArchive().review()
        self.assertEqual(self.names(), [newest])

    def test_incomplete_clip_does_not_trigger_rotation(self):
        old = self.clip("08-00-00", retained=True)
        newest = self.clip("10-00-00")
        (self.recent / newest / (newest + "-back.mp4")).unlink()
        self.viewer.DRIVE_MAX_GB = 256 / (1 << 30)
        self.viewer.DriveArchive().review()
        self.assertEqual(self.names(), [old])

    def test_exact_quota_does_not_delete_anything(self):
        old = self.clip("08-00-00", retained=True)
        middle = self.clip("09-00-00", retained=True)
        newest = self.clip("10-00-00")
        self.viewer.DriveArchive().review()
        self.assertEqual(self.names(), [old, middle, newest])

    def test_rotation_failure_retries_even_without_recent_clips(self):
        old = self.clip("08-00-00", retained=True)
        newest = self.clip("10-00-00")
        self.viewer.DRIVE_MAX_GB = 256 / (1 << 30)
        with patch.object(self.viewer, "_tombstone", return_value=False):
            with self.assertRaises(OSError):
                self.viewer.DriveArchive().review()
        self.assertEqual(self.names(), [old, newest])
        self.viewer.shutil.rmtree(self.recent / newest)
        self.viewer.DriveArchive().review()
        self.assertEqual(self.names(), [newest])

    def test_disabled_retention_keeps_existing_archive(self):
        old = self.clip("08-00-00", retained=True)
        self.clip("10-00-00")
        self.viewer.DRIVE_MAX_GB = 0
        self.viewer.DriveArchive().review()
        self.assertEqual(self.names(), [old])

    def test_six_camera_clip_keeps_pillar_streams(self):
        """HW4 车型会录 6 路; 归档只拷 4 路会把左右翼的片段悄悄丢掉。"""
        name = self.clip("11-00-00")
        folder = self.recent / name
        for camera in ("left_pillar", "right_pillar"):
            (folder / ("%s-%s.mp4" % (name, camera))).write_bytes(b"v" * 64)
        self.viewer.DriveArchive().review()
        kept = sorted(path.name for path in (self.retained / name).iterdir())
        self.assertEqual(len(kept), 6)
        self.assertIn(name + "-left_pillar.mp4", kept)
        self.assertIn(name + "-right_pillar.mp4", kept)

    def test_incomplete_minute_is_not_archived(self):
        name = "2026-11-20_12-00-00"
        folder = self.recent / name
        folder.mkdir()
        (folder / (name + "-front.mp4")).write_bytes(b"v" * 64)
        self.viewer.DriveArchive().review()
        self.assertFalse((self.retained / name).exists())

    def test_unlimited_archive_keeps_one_hour_roll_after_drive_review_failure(self):
        """归档总上限为 0 时「最近1小时监控」仍按窗口清理; 行车记录检查失败也不能拖累它。"""
        old = self.clip("08-00-00")
        edge = self.clip("09-30-00")
        newest = self.clip("10-31-00")
        drive = self.viewer.DriveArchive()
        with patch.object(self.viewer, "refresh_storage_settings"), \
             patch.object(self.viewer, "ARCHIVE_TOTAL_GB", 0), \
             patch.object(self.viewer, "roll_archive_total", return_value=0), \
             patch.object(drive, "review", side_effect=OSError("boom")):
            self.viewer.disk_guard_cycle(drive)
        self.assertFalse((self.recent / old).exists())
        self.assertFalse((self.recent / edge).exists())
        self.assertTrue((self.recent / newest).is_dir())

    def test_recent_size_cap_evicts_oldest_even_when_archive_unlimited(self):
        """归档总量不限(0)时, 「最近1小时监控」体积上限仍独立淘汰最旧片段,
        但保留最近必留窗口内的片段 (6 路 1 小时约 12GiB 的兜底)。"""
        old = self.clip("08-00-00")
        edge = self.clip("09-00-00")
        newest = self.clip("10-00-00")
        self.viewer.RECENT_KEEP_MIN = 60
        # 每个事件约 256B, 预算 300B 只够容纳 1 个
        self.viewer.RECENT_MAX_GB = 300 / (1 << 30)
        fake_now = self.viewer.time.time() + 3600   # 让目录 mtime 不再被视为"正在写入"
        with patch.object(self.viewer, "ARCHIVE_TOTAL_GB", 0), \
             patch.object(self.viewer.time, "time", return_value=fake_now):
            removed = self.viewer.roll_recent_size()
        self.assertEqual(removed, 1)
        self.assertFalse((self.recent / old).exists())          # 最旧且在必留窗口外 -> 删除
        self.assertTrue((self.recent / edge).is_dir())          # 必留窗口内 -> 保留
        self.assertTrue((self.recent / newest).is_dir())

    def test_prune_orphan_transcodes_removes_cache_without_source(self):
        """归档视频被删后, 对应的转码缓存自动清除; 有源文件的缓存保留。"""
        transcode = Path(self.viewer.RES_DIR)
        transcode.mkdir(parents=True, exist_ok=True)
        # 有源文件的缓存
        src = self.recent / "2026-11-20_10-00-00" / "clip-front.mp4"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"v" * 100)
        valid_name = os.path.basename(self.viewer._variant_path(str(src), "480p"))
        (transcode / valid_name).write_bytes(b"cached")
        # 孤儿缓存 (源文件不存在)
        orphan = "a" * 32 + ".mp4"
        (transcode / orphan).write_bytes(b"orphan")
        removed = self.viewer.prune_orphan_transcodes(force=True)
        self.assertEqual(removed, 1)
        self.assertFalse((transcode / orphan).exists())
        self.assertTrue((transcode / valid_name).exists())

    def test_cleanup_transcode_keeps_dir_removes_all_files(self):
        """cleanup_transcode 清空转码缓存下全部文件与子目录, 但保留 TRANSCODE_DIR 本身。"""
        transcode = Path(self.viewer.TRANSCODE_DIR)
        transcode.mkdir(parents=True, exist_ok=True)
        # 顶层文件
        (transcode / "a.mp4").write_bytes(b"x" * 100)
        (transcode / "b.mp4").write_bytes(b"y" * 200)
        # 子目录 + 文件
        sub = transcode / "push-20261120"
        sub.mkdir()
        (sub / "wm-1.ass").write_bytes(b"sub" * 50)
        result = self.viewer.cleanup_transcode()
        self.assertTrue(result["ok"])
        self.assertEqual(result["removed_files"], 3)
        self.assertEqual(result["freed_bytes"], 100 + 200 + 50 * 3)  # b"sub" * 50
        # 目录本身保留
        self.assertTrue(transcode.is_dir())
        # 所有文件都删干净了 (包括子目录里的)
        leftover = [p for p in transcode.rglob("*") if p.is_file()]
        self.assertEqual(leftover, [])
        # 再清一次也不该报错
        result2 = self.viewer.cleanup_transcode()
        self.assertTrue(result2["ok"])
        self.assertEqual(result2["removed_files"], 0)

    def test_cleanup_transcode_handles_missing_dir_gracefully(self):
        """TRANSCODE_DIR 不存在时返回 ok=True 不崩溃。"""
        missing = self.root / "never-existed-transcode"
        with patch.object(self.viewer, "TRANSCODE_DIR", str(missing)):
            result = self.viewer.cleanup_transcode()
        self.assertTrue(result["ok"])
        self.assertEqual(result["removed_files"], 0)

    def test_cleanup_archive_removes_only_cats_keeps_non_video_dirs(self):
        """cleanup_archive 只清 CATS (四个录像分类), 保留 files/ transcode_cache/ .skins/ 等非录像目录。"""
        archive = Path(self.viewer.ARCHIVE)
        # CATS 里放文件
        self.clip("08-00-00", retained=True)   # DriveClips
        self.clip("09-00-00")                  # RecentClips
        saved_cat = archive / "SavedClips"
        saved_cat.mkdir(parents=True, exist_ok=True)
        (saved_cat / "keep.mp4").write_bytes(b"saved" * 32)
        sentry_cat = archive / "SentryClips"
        sentry_cat.mkdir(parents=True, exist_ok=True)
        (sentry_cat / "sentry.mp4").write_bytes(b"sentry" * 32)
        # 非录像目录放文件 (应当保留)
        (archive / "files").mkdir()
        index = archive / "files" / "index.json"
        index.write_bytes(b"index data")
        (archive / ".skins").mkdir()
        skin = archive / ".skins" / "dark.css"
        skin.write_bytes(b"body{}")
        (archive / "boombox-queue").mkdir()
        queue = archive / "boombox-queue" / "q1.txt"
        queue.write_bytes(b"queue")
        # transcode_cache 也在 ARCHIVE 下, cleanup_archive 不碰它
        transcode = archive / "transcode_cache"
        transcode.mkdir()
        cached = transcode / "abc123.mp4"
        cached.write_bytes(b"cache")
        result = self.viewer.cleanup_archive()
        self.assertTrue(result["ok"])
        # 2 个 clip 事件, 每个 4 路 → 8 个文件; 加 SavedClips/SentryClips 各 1 个 → 10
        self.assertEqual(result["removed_files"], 10)
        expected_bytes = 8 * 64 + len(b"saved" * 32) + len(b"sentry" * 32)
        self.assertEqual(result["freed_bytes"], expected_bytes)
        # 非录像文件全部保留
        self.assertTrue(index.exists())
        self.assertTrue(skin.exists())
        self.assertTrue(queue.exists())
        self.assertTrue(cached.exists())
        self.assertEqual(index.read_bytes(), b"index data")
        self.assertEqual(cached.read_bytes(), b"cache")
        # CATS 目录仍存在 (只是空了)
        for cat in self.viewer.CATS:
            self.assertTrue((archive / cat).is_dir())
            files_in_cat = [p for p in (archive / cat).rglob("*") if p.is_file()]
            self.assertEqual(files_in_cat, [])

    def test_cleanup_archive_handles_missing_dir_gracefully(self):
        """ARCHIVE 目录不存在时返回 ok=True 不崩溃。"""
        missing = self.root / "never-existed-archive"
        with patch.object(self.viewer, "ARCHIVE", str(missing)):
            result = self.viewer.cleanup_archive()
        self.assertTrue(result["ok"])
        self.assertEqual(result["removed_files"], 0)


if __name__ == "__main__":
    unittest.main()
