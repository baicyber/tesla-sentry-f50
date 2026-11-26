"""回归: tesla-sync.sh 在没有音频要写时不得弹出/恢复车机 U 盘。

历史 bug: 脚本由 cron 每 10 分钟执行, 无论 boombox-queue 是否为空, 都会
"停止导出 -> 挂载镜像 -> 恢复导出", 主机(车机/电脑)看到 U 盘被弹出又插回。
用户导出视频(耗时数分钟)撞上这个窗口就掉盘。修复把 has_audio 检查提前到
第一次触碰 UDC 之前, 无音频直接退出。
本测试用纯静态分析, 不需要真机 / root / chroot, 跨平台可跑。
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "tesla-sync.sh"


class TestUsbNoEmptyEject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding="utf-8")
        cls.lines = cls.text.splitlines()

    def _line(self, needle):
        for i, ln in enumerate(self.lines):
            if needle in ln:
                return i
        self.fail("脚本里找不到: %s" % needle)

    def test_no_audio_early_exit_before_first_udc_write(self):
        # 第一次真正把 UDC 写空(停止导出)的位置
        eject = self._line('echo "" > "$GADGET/UDC"')
        # 无音频早退的位置
        early = self._line("skip without touching USB")
        # 早退那一行后面必须紧跟 exit
        self.assertLess(early, eject,
                        "NO-AUDIO 早退必须在停止导出(UDC 写空)之前, 否则仍会空弹一次 U 盘")
        block = "\n".join(self.lines[early:early + 3])
        self.assertIn("exit 0", block, "NO-AUDIO 分支必须立即 exit 0")

    def test_has_audio_check_is_before_idle_log(self):
        # has_audio 探测必须在 "sync start" 日志之前, 避免误记一次伪同步
        detect = self._line("has_audio=0")
        log = self._line("=== sync start")
        self.assertLess(detect, log, "has_audio 检查应在 sync start 日志之前")

    def test_no_duplicate_has_audio_recheck_after_mount(self):
        # 挂载镜像之后不应再重复 has_audio 探测(已在早退阶段确认过)
        mount = self._line("mount -t exfat")
        occurrences = [i for i, ln in enumerate(self.lines) if "has_audio=0" in ln]
        self.assertEqual(len(occurrences), 1, "has_audio 只应初始化一次")
        self.assertLess(occurrences[0], mount)

    def test_restore_still_exists_for_audio_path(self):
        # 有音频时仍要恢复导出, 不能为了早退把恢复逻辑删掉
        self.assertIn("export restored", self.text)
        self.assertIn("restore", self.text)


if __name__ == "__main__":
    unittest.main()
