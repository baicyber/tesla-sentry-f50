"""Real loop/exFAT checks; run only inside the disposable diskless test VM."""
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import storage_resize


def main():
    if not Path('/run/f50-isolated-test').is_file() or os.geteuid() != 0:
        raise RuntimeError('Run in the isolated test VM only')
    root = Path('/testdata')
    image = root / 'teslacam.img'
    gadget = root / 'gadget'
    lun = gadget / 'functions/mass_storage.0/lun.0/file'
    lun.parent.mkdir(parents=True)
    lun.write_text(str(image))
    binding = gadget / 'UDC'
    binding.write_text('controller')
    mount = root / 'verify'
    mount.mkdir()
    tool = REPO / 'scripts/mkexfat.py'
    subprocess.run([sys.executable, '-B', str(tool), str(image), '1'], check=True)
    command = storage_resize.command
    data = b'video-sei-test\0' * 50000
    relative = 'TeslaCam/SavedClips/测试文件.mp4'

    def check_contents(cleared=False):
        loop = command(['losetup', '-f'])
        command(['losetup', '-r', loop, image])
        try:
            command(['mount', '-t', 'exfat', '-o', 'ro', loop, mount])
            try:
                if cleared:
                    assert not [path for path in mount.rglob('*') if path.is_file()]
                    for category in ('RecentClips', 'SavedClips', 'SentryClips', 'EncryptedClips'):
                        assert (mount / 'TeslaCam' / category).is_dir()
                else:
                    assert (mount / relative).read_bytes() == data
                    assert abs((mount / relative).stat().st_mtime - 1700000000) < 2
                    assert (mount / 'TeslaCam/空目录').is_dir()
            finally:
                command(['umount', mount])
        finally:
            command(['losetup', '-d', loop])

    loop = command(['losetup', '-f'])
    command(['losetup', loop, image])
    command(['mount', '-t', 'exfat', loop, mount])
    (mount / relative).write_bytes(data)
    os.utime(mount / relative, (1700000000, 1700000000))
    (mount / 'TeslaCam/空目录').mkdir()
    command(['umount', mount])
    command(['losetup', '-d', loop])
    check_contents()
    storage_resize.resize(image, 2, gadget, 'controller', tool, lambda *args: None)
    assert image.stat().st_size == 2 << 30
    assert binding.read_text().strip() == 'controller'
    assert lun.read_text().strip() == str(image)
    assert not list(root.glob('*.resize-*'))
    check_contents(cleared=True)
    print('EXFAT_CLEAR_AND_EXPAND_VERIFIED', flush=True)

    # Populate the new disk so rollback checks real recordings, not only an empty disk.
    loop = command(['losetup', '-f'])
    command(['losetup', loop, image])
    command(['mount', '-t', 'exfat', loop, mount])
    (mount / relative).write_bytes(data)
    os.utime(mount / relative, (1700000000, 1700000000))
    (mount / 'TeslaCam/空目录').mkdir()
    command(['umount', mount])
    command(['losetup', '-d', loop])

    # Fail the first USB rebind after publication, then allow rollback to rebind.
    original_inode = image.stat().st_ino
    original_write = Path.write_text
    failed = False
    def reject_new_export(path, data, *args, **kwargs):
        nonlocal failed
        if path == binding and data.strip() == 'controller' and image.stat().st_size == 1 << 30 and not failed:
            failed = True
            raise OSError('injected USB rebind failure')
        return original_write(path, data, *args, **kwargs)
    try:
        with patch.object(Path, 'write_text', reject_new_export):
            storage_resize.resize(image, 1, gadget, 'controller', tool, lambda *args: None)
        raise AssertionError('failure was not injected')
    except OSError as exc:
        assert 'injected' in str(exc)
    assert failed and image.stat().st_ino == original_inode and image.stat().st_size == 2 << 30
    assert binding.read_text().strip() == 'controller'
    assert lun.read_text().strip() == str(image)
    assert not list(root.glob('*.resize-*'))
    check_contents()
    print('EXFAT_ROLLBACK_VERIFIED', flush=True)
    storage_resize.resize(image, 1, gadget, 'controller', tool, lambda *args: None)
    assert image.stat().st_size == 1 << 30
    check_contents(cleared=True)
    assert not list(root.glob('*.resize-*'))
    print('EXFAT_CLEAR_AND_SHRINK_VERIFIED', flush=True)


if __name__ == '__main__':
    main()
