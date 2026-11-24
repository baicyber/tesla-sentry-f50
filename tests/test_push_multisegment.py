"""一个事件同一路切成多段时, 推送片段与卡片路数的回归测试。

新车型 (HW4) 的 SentryClips 目录里, 一个事件同一路会有多个按秒命名的 mp4 段。
老逻辑按「一路一段」处理: 取排序第一段导致推送从触发前几十秒开始, 数文件导致
「6 路 44 段」被写成「44 路画面」。
"""
import datetime
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

CAMS = ('front', 'back', 'left_pillar', 'right_pillar', 'left_repeater', 'right_repeater')
EVENT = '2026-11-24_18-32-19'


class PushMultisegmentTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.archive = self.root / 'archive'
        self.enterContext(patch.dict(os.environ, TSF_ARCHIVE=str(self.archive),
                                     TSF_CACHE=str(self.root / 'cache')))
        source = Path(__file__).resolve().parents[1] / 'sentry-viewer.py'
        spec = importlib.util.spec_from_file_location('push_multisegment_viewer', source)
        self.viewer = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'fcntl': types.ModuleType('fcntl')} if os.name == 'nt' else {}):
            spec.loader.exec_module(self.viewer)
        self.event_dir = self.archive / 'SentryClips' / EVENT
        self.event_dir.mkdir(parents=True)

    def make_event(self, timestamp, camera=5, segments=6, cameras=CAMS):
        (self.event_dir / 'event.json').write_text(json.dumps({
            'timestamp': timestamp, 'camera': str(camera),
            'reason': 'sentry_aware_object_detection',
        }), encoding='utf-8')
        base = datetime.datetime.strptime(EVENT, '%Y-%m-%d_%H-%M-%S') - datetime.timedelta(seconds=50)
        for cam in cameras:
            for i in range(segments):
                at = base + datetime.timedelta(seconds=10 * i)
                (self.event_dir / ('%s-%s.mp4' % (at.strftime('%Y-%m-%d_%H-%M-%S'), cam))).touch()

    def card_field(self, key):
        card = self.viewer._feishu_card({}, 'SentryClips', EVENT)
        for el in card['elements']:
            for field in el.get('fields', []):
                text = field['text']['content']
                if text.startswith('**%s**' % key):
                    return text.split('\n', 1)[1]
        self.fail('卡片里没有字段: %s' % key)

    def test_pick_clip_uses_segment_nearest_trigger(self):
        # 触发点在 18:32:24, 左翼子板(#5 -> left_repeater)最后一段从 18:32:19 开始,
        # 触发偏移应为 5 秒 => 推送从触发前 5 秒起截; 旧逻辑会挑 18:31:29 那段(偏移 55s)。
        self.make_event('2026-11-24T18:32:24')
        fp, off, _meta = self.viewer._pick_clip('SentryClips', EVENT)
        self.assertEqual(Path(fp).name, '2026-11-24_18-32-19-left_repeater.mp4')
        self.assertEqual(off, 5.0)
        self.assertEqual(max(0.0, off - 5.0), 0.0)

    def test_card_counts_cameras_not_mp4_files(self):
        self.make_event('2026-11-24T18:32:24')
        self.assertEqual(len(self.viewer._ev_files(str(self.event_dir))), 36)
        self.assertEqual(self.card_field('片段'), '6 路画面')

    def test_single_segment_per_camera_unchanged(self):
        self.make_event('2026-11-24T18:32:24', segments=1)
        fp, off, _meta = self.viewer._pick_clip('SentryClips', EVENT)
        self.assertEqual(Path(fp).name, '2026-11-24_18-31-29-left_repeater.mp4')
        self.assertEqual(off, 55.0)
        self.assertEqual(self.card_field('片段'), '6 路画面')


if __name__ == '__main__':
    unittest.main()
