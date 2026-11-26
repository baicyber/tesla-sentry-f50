"""导出行车水印的车速必须平滑跟随遥测帧, 不能整秒一个大台阶。

背景: 服务端 ASS 水印以前按整秒取样 (fr_at(ss+n)), 一段匀减速会被画成
36→21→2→0 的大跳变, 看着像车速数值乱跳; 底层 SEI 其实每帧差 <1 km/h。
本测试构造一条线性减速轨, 断言 ASS 里的车速变化幅度受控。
"""
import importlib.util
import math
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


class WatermarkSpeedTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        viewer_path = Path(__file__).resolve().parents[1] / "sentry-viewer.py"
        spec = importlib.util.spec_from_file_location("wm_viewer", viewer_path)
        self.viewer = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"fcntl": types.ModuleType("fcntl")} if os_is_windows() else {}):
            spec.loader.exec_module(self.viewer)
        self.viewer.RES_DIR = str(self.root / "res")
        Path(self.viewer.RES_DIR).mkdir(parents=True)

    def _fake_telemetry(self, fps=36.0, dur=20.0, start=36.0, end=0.0):
        """线性减速的遥测帧: 每秒降 (start-end)/dur km/h, 每帧只降 <1。"""
        frames = []
        n = int(fps * dur)
        for k in range(n + 1):
            t = k / fps
            spd = max(0.0, start + (end - start) * min(1.0, t / dur))
            frames.append([round(t, 2), "D", round(spd, 1), 0, 0.0, 0, 0, 0,
                           0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        return {"fps": fps, "count": n + 1, "duration": dur, "frames": frames,
                "max_speed": start, "avg_speed": (start + end) / 2, "brake_ratio": 0.0}

    def _speed_dialogues(self, ass_path):
        """从 ASS 里抽出车速 Dialogue (an6 右锚那行) 的 (start秒, 整数车速)。"""
        out = []
        for line in Path(ass_path).read_text(encoding="utf-8").splitlines():
            if not line.startswith("Dialogue:") or "\\an6" not in line:
                continue
            head = line.split(",", 9)
            start = self._ass_sec(head[1])
            tail = re.search(r"}([^{}]*)$", line.rstrip())
            if tail and tail.group(1).strip().lstrip("-").isdigit():
                out.append((start, int(tail.group(1))))
        return out

    @staticmethod
    def _ass_sec(s):
        h, m, sec = s.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)

    def test_speed_steps_are_linear_not_whole_second_jumps(self):
        # 实测场景: 2 秒从 36 km/h 急刹到 0, 之后停住。
        # 旧整秒取样会画出 36→18→0 的 18 km/h 大台阶; 0.1s 取样应逐步递减。
        tel = self._fake_telemetry(dur=2.0, start=36.0, end=0.0)
        # 延长静止段到 10 秒, 覆盖 "减速+停车" 完整观感
        tail_t = tel["frames"][-1][0]
        for k in range(1, int(8 * tel["fps"])):
            tel["frames"].append([round(tail_t + k / tel["fps"], 2), "P", 0.0, 0, 0.0, 0, 0, 0,
                                  0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        tel["duration"] = 10.0
        fp = str(self.root / "2026-11-23_00-40-24-front.mp4")
        Path(fp).write_bytes(b"")
        with patch.object(self.viewer, "telemetry_cached", return_value=tel), \
             patch.object(self.viewer, "_wm_font", return_value=str(self.root / "font.ttf")):
            ass = self.viewer._wm_ass(fp, 0, 10, {}, size=(1280, 720))
        self.assertTrue(ass, "必须生成 ASS 字幕")

        rows = self._speed_dialogues(ass)
        self.assertGreater(len(rows), 20, "2 秒急刹 + 静止应有多段车速变化, 不是整秒台阶")

        speeds = [v for _, v in rows]
        # 0.1s 采样: 每秒降 18 => 每步至多 2 km/h, 远小于整秒取样的 18
        jumps = [abs(b - a) for a, b in zip(speeds, speeds[1:])]
        self.assertLessEqual(max(jumps), 2, "车速出现大跨度跳变: %r" % jumps)
        self.assertEqual(max(speeds), 36)
        self.assertEqual(min(speeds), 0)

        # 采样间隔应是 0.1s 量级, 而不是整秒
        starts = [t for t, _ in rows]
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertLessEqual(min(gaps), 0.11, "水印时间粒度仍为整秒: %r" % gaps[:10])


def os_is_windows():
    import os
    return os.name == "nt"


if __name__ == "__main__":
    unittest.main()
