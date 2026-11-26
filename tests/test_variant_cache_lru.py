"""清晰度变体缓存必须按热度 (LRU) 淘汰, 而不是按生成时间 (FIFO)。

背景: 缓存盘 noatime 挂载, res_cleanup() 按变体文件 mtime 删最旧。以前命中
_variant_ready() 只是判断存在, 从不 touch —— 于是一个天天回看的老片段会因为
"生成得早"被优先删掉, 删了就得重新转码, 首帧从 0.3s (faststart) 退回 3~6s
(原片 moov 在尾部)。修复: 命中时按节流刷新变体 mtime, 保留热缓存。

同时锁定 RES_MAX = 8GB: 缓存实际落在 207G 虚拟盘, 旧 2GB 上限贴顶后频繁
误删热缓存, 命中率白白损失。
"""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch


def _os_is_windows():
    return os.name == "nt"


class VariantCacheLRUTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        viewer_path = Path(__file__).resolve().parents[1] / "sentry-viewer.py"
        spec = importlib.util.spec_from_file_location("lru_viewer", viewer_path)
        self.viewer = importlib.util.module_from_spec(spec)
        mods = {"fcntl": types.ModuleType("fcntl")} if _os_is_windows() else {}
        with patch.dict(sys.modules, mods):
            spec.loader.exec_module(self.viewer)
        self.viewer.RES_DIR = str(self.root / "res")
        Path(self.viewer.RES_DIR).mkdir(parents=True)
        self.viewer._VARIANT_TOUCH.clear()
        # 源文件: _variant_path 的 key 依赖 size/mtime, 固定住
        self.src = self.root / "clip-front.mp4"
        self.src.write_bytes(b"x" * 1024)
        os.utime(self.src, (1_700_000_000, 1_700_000_000))

    def tearDown(self):
        self.viewer._VARIANT_TOUCH.clear()

    def _variant_file(self, qv="480p"):
        p = Path(self.viewer._variant_path(str(self.src), qv))
        p.write_bytes(b"m" * 2048)
        # 造一个明显偏旧的生成时间
        old = time.time() - 10_000
        os.utime(p, (old, old))
        return p

    def test_res_max_is_8gb(self):
        self.assertEqual(self.viewer.RES_MAX, 8 * 1024 * 1024 * 1024,
                         "缓存上限应为 8GB, 避免贴着 2GB 频繁误删热缓存")

    def test_hit_refreshes_mtime(self):
        p = self._variant_file()
        before = p.stat().st_mtime
        self.assertTrue(self.viewer._variant_ready(str(self.src), "480p"))
        after = p.stat().st_mtime
        self.assertGreater(after, before + 9000,
                           "命中变体必须刷新 mtime, 否则 res_cleanup 仍按生成时间误删热缓存")

    def test_touch_is_throttled(self):
        """60s 内重复命中不应反复写盘: 第二次命中不改变 mtime。"""
        p = self._variant_file()
        self.viewer._variant_ready(str(self.src), "480p")
        first = p.stat().st_mtime
        # 直接用 patch 冻结时间线, 保证仍在 TTL 内 (不依赖真实时钟精度)
        with patch.object(self.viewer.time, "monotonic",
                          return_value=self.viewer._VARIANT_TOUCH.get(
                              self.viewer._variant_path(str(self.src), "480p"), 0) + 1):
            self.viewer._variant_ready(str(self.src), "480p")
        self.assertEqual(p.stat().st_mtime, first,
                         "TTL 内重复命中不应再次 touch (避免视频请求频繁写盘)")

    def test_ttl_expiry_touches_again(self):
        p = self._variant_file()
        self.viewer._variant_ready(str(self.src), "480p")
        first = p.stat().st_mtime
        key = self.viewer._variant_path(str(self.src), "480p")
        with patch.object(self.viewer.time, "monotonic",
                          return_value=self.viewer._VARIANT_TOUCH[key] + 61):
            self.viewer._variant_ready(str(self.src), "480p")
        self.assertGreater(p.stat().st_mtime, first)

    def test_missing_and_empty_still_false(self):
        self.assertFalse(self.viewer._variant_ready(str(self.src), "360p"), "不存在应 False")
        empty = Path(self.viewer._variant_path(str(self.src), "720p"))
        empty.write_bytes(b"")
        self.assertFalse(self.viewer._variant_ready(str(self.src), "720p"), "0 字节应 False")

    def test_res_cleanup_prefers_least_recently_used(self):
        """两个变体: A 生成早但刚命中, B 生成晚但没命中 -> 超限时先删 B。"""
        a = self._variant_file("480p")
        b = self._variant_file("360p")
        os.utime(a, (time.time() - 100_000, time.time() - 100_000))  # A 生成更早
        old_b = time.time() - 50_000
        os.utime(b, (old_b, old_b))
        # 命中 A (刷新其 mtime 到当前)
        self.viewer._variant_ready(str(self.src), "480p")
        self.viewer.RES_DIR = str(self.root / "res")
        # 把上限压到刚好只能留一个, 触发清理
        size = a.stat().st_size
        self.viewer.RES_MAX = size + 1
        self.viewer.res_cleanup()
        self.assertTrue(a.exists(), "刚命中过的热缓存 A 应保留")
        self.assertFalse(b.exists(), "未命中、相对更冷的 B 应被淘汰")


if __name__ == "__main__":
    unittest.main()
