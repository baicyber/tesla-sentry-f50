"""回归: 跨段缓冲时的占位最后一帧(canvas.hold)不能长期盖住新段画面。

历史 bug: showClip 跨段(continuous)时把上一段最后一帧画到 canvas.hold 盖在
新视频上做平滑过渡, 旧实现只监听 loadeddata/error 移除。新段首帧迟迟不来时
(原画大文件下载 / 低清后台转码十几秒), 旧帧一直盖着, 进度条已走到新段而画面
仍是上一段 -> 用户看到"缓冲中出现无关画面帧"。
真机 26.0.26 复现: 跨段后 4 秒内 hold 恒在、新视频 readyState=0, 截图显示
进度条 09:56:00 但画面是上一段 09:55:00 的车。
修复: 多重就绪事件(loadeddata/canplay/playing/timeupdate/seeked)移除 + 800ms
超时兜底 + 淡出动画。本测试纯静态分析, 跨平台可跑。
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VIEWER = REPO / "sentry-viewer.py"


class TestHoldFrameRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = VIEWER.read_text(encoding="utf-8")

    def hold_block(self):
        i = self.text.find("const still = document.createElement('canvas')")
        self.assertGreaterEqual(i, 0, "找不到 canvas.hold 创建代码")
        j = self.text.find("holdTimer = setTimeout", i)
        self.assertGreater(j, i, "找不到超时兜底")
        return self.text[i:j + 80]

    def test_hold_has_timeout_fallback(self):
        b = self.hold_block()
        m = re.search(r"setTimeout\(clearHold,\s*(\d+)\)", b)
        self.assertIsNotNone(m, "hold 帧必须有超时兜底移除")
        ms = int(m.group(1))
        self.assertLessEqual(ms, 1500, "超时上限应较短(<=1.5s), 不能长期盖旧帧")
        self.assertGreaterEqual(ms, 200, "超时不能太短以免闪屏")

    def test_hold_listens_multiple_ready_events(self):
        b = self.hold_block()
        for ev in ("loadeddata", "canplay", "playing", "timeupdate", "seeked"):
            self.assertIn("'%s'" % ev, b, "hold 应监听 %s 以在首帧到达时立即移除" % ev)

    def test_hold_error_removal(self):
        b = self.hold_block()
        self.assertRegex(b, r"addEventListener\('error',\s*clearHold", "错误时也要清掉占位帧")

    def test_clear_hold_guards_disconnected(self):
        b = self.hold_block()
        self.assertIn("still.isConnected", b, "重复移除要先判断节点是否仍在 DOM")

    def test_clear_hold_cancels_timer(self):
        b = self.hold_block()
        self.assertIn("clearTimeout(holdTimer)", b, "事件移除后应取消超时, 避免重复触发")

    def test_fade_css_present(self):
        self.assertIn("canvas.hold.fade", self.text, "占位帧淡出 CSS 应存在")
        self.assertIn("@keyframes hold-fade", self.text, "占位帧淡出动画应存在")

    def test_no_old_two_listener_only(self):
        # 旧实现: 只有 loadeddata/error 两条 once 监听, 没有超时
        old = ("incoming.addEventListener('loadeddata', () => still.remove(), {once:true});\n"
               "      incoming.addEventListener('error', () => still.remove(), {once:true});")
        self.assertNotIn(old, self.text, "旧的仅双事件移除实现不应回归")


if __name__ == "__main__":
    unittest.main(verbosity=2)
