"""多文件 OTA: 依赖发现与内容校验 (不联网, 不起服务)。

加载主程序需要 fcntl (Windows 没有), 这里注入一个空壳让它在非 Linux 上也能跑逻辑单测。
"""
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

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


class StartupSidecarTest(unittest.TestCase):
    def test_preinstalled_coordinator_is_updated_by_ota(self):
        payload = (REPO / 'scripts/tesla-usb-start.sh').read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'tesla-usb-start.sh'
            target.write_bytes(b'#!/bin/sh\n# previous coordinator\n')
            with patch.dict(os.environ, {'TSF_DIR': directory}), \
                    patch.object(viewer, '_http_get', return_value=payload) as fetch:
                updated, _ = viewer._ota_sidecars('https://example.invalid/repository')
            self.assertEqual(updated, ['tesla-usb-start.sh'])
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(fetch.call_count, 1)
            self.assertIn('/scripts/tesla-usb-start.sh?', fetch.call_args.args[0])
            if os.name == 'posix':
                self.assertEqual(target.stat().st_mode & 0o777, 0o755)

    def test_ota_preserves_existing_only_sidecar_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'TSF_DIR': directory}), \
                    patch.object(viewer, '_http_get') as fetch:
                updated, _ = viewer._ota_sidecars('https://example.invalid/repository')
            self.assertEqual(updated, [])
            fetch.assert_not_called()
            self.assertFalse((Path(directory) / 'tesla-usb-start.sh').exists())


if __name__ == '__main__':
    unittest.main()
