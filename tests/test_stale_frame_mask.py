"""回归: MSE seek/分片切换/跨事件切换时不能闪出解码器残留的旧镜头帧 (stale 遮罩)。

历史 bug (26.0.27/26.0.28): 单路播放中, MSE 480p 视频在分片边界 seek /
seekTo 等待新数据时, 浏览器 <video> 保留解码器里上一刻的旧帧。真机铁证:
三个不同事件(12:06/12:08/12:22)、不同镜头(前视/左B柱)的截图逐像素相同
(平均差 0.79/255), 即同一帧被跨事件/跨镜头复用显示。

26.0.28 的修复不彻底: staleOffIfCurrent 仅靠 readyState>=2 + timeupdate 撤
遮罩, 而旧帧的 readyState 同样 >=2, timeupdate 一到就误撤, 700ms 兜底又
无条件撤, 于是旧帧照样露出。

26.0.29 修复: 新视频挂载即盖不透明遮罩; 撤除完全交给 requestVideoFrameCallback,
且必须确认呈现帧的 mediaTime 已到达 seek 目标(才是真新帧); 关闭播放器彻底
releaseVideo 释放解码器。本测试纯静态分析, 跨平台可跑。
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VIEWER = REPO / "sentry-viewer.py"


class TestStaleFrameMask(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = VIEWER.read_text(encoding="utf-8")

    def test_stale_mask_css_exists(self):
        self.assertIn(".player .vcell .stale", self.text, "缺少 .stale 遮罩 CSS")
        self.assertIn(".player .vcell .stale.on", self.text, "缺少 .stale.on 显示态")
        m = re.search(r"\.player \.vcell \.stale \{[^}]*inset:0[^}]*z-index:1", self.text)
        self.assertIsNotNone(m, ".stale 必须 inset:0 全屏且 z-index:1")
        self.assertIn(".player .cload.buffering { z-index:2", self.text,
                      "缓冲中提示必须在遮罩之上可见")

    def test_stale_on_off_helpers_exist(self):
        self.assertIn("function staleOn(v, target)", self.text, "缺少 staleOn(v, target)")
        self.assertIn("function staleOffIfCurrent(v)", self.text, "缺少 staleOffIfCurrent")
        i = self.text.find("function staleOn(v, target)")
        j = self.text.find("function staleOffIfCurrent(v)")
        block = self.text[i:j]
        self.assertIn("createElement('div')", block)
        self.assertIn("className = 'stale'", block)
        self.assertIn("classList.add('on')", block)

    def test_stale_off_only_via_rvfc_media_time(self):
        """撤遮罩必须依赖呈现帧 mediaTime, 不能靠 readyState 误撤。"""
        i = self.text.find("function staleOffIfCurrent(v)")
        j = self.text.find("function ", i + 10)
        block = self.text[i:j]
        self.assertIn("requestVideoFrameCallback", block, "撤除应交给 rvfc")
        # 支持 rvfc 时直接 return, 不让 timeupdate 靠 readyState 误撤
        self.assertIn("if (typeof v.requestVideoFrameCallback === 'function') return", block,
                      "旧帧 readyState 也>=2, 必须禁止 timeupdate 误撤")

    def test_stale_on_checks_presented_frame_media_time(self):
        """rvfc 回调必须校验 mediaTime 到达 seek 目标才算新帧。"""
        i = self.text.find("function staleOn(v, target)")
        j = self.text.find("function staleOffIfCurrent(v)")
        block = self.text[i:j]
        self.assertIn("v._staleTarget", block, "须记录 seek 目标时间")
        self.assertIn("meta && meta.mediaTime", block, "须读呈现帧 mediaTime")
        self.assertIn("mt >= v._staleTarget", block, "须确认呈现帧到达目标")
        self.assertIn("setTimeout(waitFrame, 6000)", block, "缺超时兜底")
        # 兜底撤罩前必须确认视频已解码出帧, 否则慢速网络会提前露旧帧
        self.assertIn("v.readyState >= 2 && v.videoWidth > 0", block,
                      "兜底撤罩前须确认已有真实帧")
        self.assertIn("addEventListener('error', finish", block, "加载失败须撤罩露重试")

    def test_stale_on_invalidate_pending(self):
        i = self.text.find("function staleOn(v, target)")
        j = self.text.find("function staleOffIfCurrent(v)")
        block = self.text[i:j]
        self.assertIn("v._staleDone = false", block, "新一轮等待须作废上轮撤遮罩回调")
        self.assertIn("cancelVideoFrameCallback", block, "须取消上一轮 rvfc")

    def test_new_video_mounted_covered(self):
        """新挂载的视频默认盖遮罩 —— 解决跨事件旧帧串台的关键。"""
        needle = "if (!reused) staleOn(v, offset);"
        self.assertIn(needle, self.text, "新视频挂载时必须先盖遮罩")

    def test_close_player_releases_videos(self):
        """关闭播放器必须彻底释放解码器, 否则跨事件旧帧残留。"""
        i = self.text.find("function closePlayer()")
        j = self.text.find("function ", i + 10)
        block = self.text[i:j]
        self.assertIn("activeVideos().forEach", block, "closePlayer 须遍历释放 video")
        self.assertIn("releaseVideo(v)", block, "closePlayer 须 releaseVideo")

    def test_mse_waiting_covers_stale(self):
        i = self.text.find("v.onwaiting = () =>")
        j = self.text.find("v.onended =", i)
        block = self.text[i:j]
        self.assertIn("staleOn(v)", block, "onwaiting 的 MSE 分支未盖遮罩")

    def test_part_boundary_seek_covers_stale(self):
        i = self.text.find("if (playable > target && !video.dataset.positioning)")
        j = self.text.find("finalPartEnd =", i)
        block = self.text[i:j]
        self.assertIn("staleOn(video)", block, "分片边界 seek 未盖遮罩")

    def test_new_frame_events_clear_stale(self):
        i = self.text.find("v.onloadeddata = v.oncanplay = v.onplaying")
        j = self.text.find("v.onended =", i)
        block = self.text[i:j]
        self.assertIn("staleOffIfCurrent(v)", block)
        self.assertIn("'timeupdate'", block)
        self.assertIn("'seeked'", block)


if __name__ == "__main__":
    unittest.main()
