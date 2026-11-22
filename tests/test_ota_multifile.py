"""多文件 OTA: 依赖发现与内容校验 (不联网, 不起服务)。

加载主程序需要 fcntl (Windows 没有), 这里注入一个空壳让它在非 Linux 上也能跑逻辑单测。
"""
import importlib.util
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

if 'fcntl' not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ImportError:
        sys.modules['fcntl'] = types.ModuleType('fcntl')

_spec = importlib.util.spec_from_file_location('sentry_viewer', REPO / 'sentry-viewer.py')
viewer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(viewer)


class NeedModulesTest(unittest.TestCase):
    def test_manifest_files_win(self):
        got = viewer._ota_need_modules({'files': ['storage_allocation.py', 'storage_resize.py']}, b'')
        self.assertEqual(got, ['storage_allocation.py', 'storage_resize.py'])

    def test_manifest_rejects_paths_and_main(self):
        got = viewer._ota_need_modules(
            {'files': ['../evil.py', 'sentry-viewer.py', 'scripts/x.sh', 'a.py']}, b'')
        self.assertEqual(got, ['a.py'])

    def test_source_scan_finds_local_imports(self):
        src = b'import os\nimport storage_allocation\nfrom storage_resize import x\nimport json\n'
        got = viewer._ota_need_modules({}, src)
        self.assertIn('storage_allocation.py', got)
        self.assertIn('storage_resize.py', got)
        self.assertNotIn('os.py', got)
        self.assertNotIn('json.py', got)

    def test_known_modules_are_always_included(self):
        got = viewer._ota_need_modules({}, b'')
        self.assertEqual(sorted(got), sorted(viewer.OTA_KNOWN_MODULES))


class CheckPyTest(unittest.TestCase):
    def test_good_module(self):
        self.assertEqual(viewer._ota_check_py('m.py', b'X = 1\n' * 40), '')

    def test_syntax_error_is_reported(self):
        msg = viewer._ota_check_py('m.py', b'def broken(\n' * 40)
        self.assertIn('语法错误', msg)

    def test_too_short_is_rejected(self):
        self.assertIn('过短', viewer._ota_check_py('m.py', b'x = 1\n'))

    def test_html_error_page_is_rejected(self):
        """镜像返回 200 + 错误页时不能当成模块装上去"""
        junk = b'<html><body>404 not found</body></html>' * 10
        self.assertNotEqual(viewer._ota_check_py('m.py', junk), '')


class PathsTest(unittest.TestCase):
    def test_module_dirs_cover_every_program_copy(self):
        dirs = viewer._ota_dirs()
        self.assertTrue(dirs)
        for p in viewer._self_paths():
            self.assertIn(str(Path(p).resolve().parent), [str(Path(d).resolve()) for d in dirs])


if __name__ == '__main__':
    unittest.main()
