"""Recreate an empty exFAT image after the caller confirms clearing USB contents."""
import os
from pathlib import Path
import subprocess
import sys

from storage_allocation import GIB, sync_directory


class ResizeError(RuntimeError):
    def __init__(self, message, needs_recovery=False):
        super().__init__(message)
        self.needs_recovery = needs_recovery


def command(args):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ResizeError('%s 失败：%s' % (args[0], result.stderr.strip()[-200:]))
    return result.stdout.strip()


def pending_recovery(image):
    return any(Path(str(image) + suffix).exists() for suffix in ('.resize-state', '.resize-new', '.resize-backup'))


def bind_controller(binding, udc):
    """A platform USB watchdog may already have rebound the same controller."""
    try:
        binding.write_text(udc + '\n', encoding='ascii')
    except OSError:
        if binding.read_text().strip() != udc:
            raise
    if binding.read_text().strip() != udc:
        raise ResizeError('USB 控制器绑定失败')


def resize(image, size_gib, gadget, udc, mkexfat, progress):
    """Replace USB contents with an empty disk, keeping the old inode for rollback.

    Interrupted operations leave named recovery files rather than overwriting them.
    The caller must confirm clearing contents and hold both USB locks.
    No video files are copied and archives outside the image are untouched.
    """
    image, gadget = Path(image), Path(gadget)
    temporary = image.with_name(image.name + '.resize-new')
    backup = image.with_name(image.name + '.resize-backup')
    marker = image.with_name(image.name + '.resize-state')
    binding = gadget / 'UDC'
    lun = gadget / 'functions/mass_storage.0/lun.0/file'
    if pending_recovery(image):
        raise ResizeError('发现上次调整留下的镜像，请先检查恢复文件')
    if image.is_symlink() or not image.is_file() or not Path(mkexfat).is_file():
        raise ResizeError('镜像或创建工具不可用')
    if binding.read_text().strip() != udc or lun.read_text().strip() != str(image):
        raise ResizeError('USB 导出状态不匹配，未调整容量')
    disconnected = False
    created = False
    published = False
    success = False
    marked = False
    try:
        with marker.open('x', encoding='utf-8') as stream:
            marked = True
            stream.write('recreate\n')
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(image.parent)
        progress('准备调整容量', 0)
        binding.write_text('\n', encoding='ascii')
        disconnected = True
        if binding.read_text().strip():
            raise ResizeError('未能断开车机导出')
        lun.write_text('\n', encoding='utf-8')
        if lun.read_text().strip():
            raise ResizeError('未能解除录像镜像绑定')
        # Keep other USB functions connected while the replacement is created.
        # The F50 platform may automatically rebind an unbound controller.
        bind_controller(binding, udc)
        with temporary.open('xb'):
            created = True
        progress('创建空白 U 盘', 20)
        command([sys.executable, '-B', mkexfat, temporary, str(size_gib)])
        with temporary.open('rb') as stream:
            header = stream.read(11)
        if header[3:11] != b'EXFAT   ' or temporary.stat().st_size != size_gib * GIB:
            raise ResizeError('新镜像校验失败')
        progress('完成校验', 90)
        with temporary.open('r+b') as stream:
            os.fsync(stream.fileno())
        # A hard link retains the old inode without copying its video data.
        os.link(image, backup)
        sync_directory(image.parent)
        os.replace(temporary, image)
        published = True
        sync_directory(image.parent)
        progress('恢复车机导出', 98)
        binding.write_text('\n', encoding='ascii')
        lun.write_text(str(image) + '\n', encoding='utf-8')
        bind_controller(binding, udc)
        if binding.read_text().strip() != udc or lun.read_text().strip() != str(image):
            raise ResizeError('USB 导出恢复失败')
        disconnected = False
        backup.unlink()
        sync_directory(image.parent)
        success = True
        progress('容量调整完成', 100)
    finally:
        try:
            if not success:
                if published and backup.exists():
                    binding.write_text('\n', encoding='ascii')
                    lun.write_text('\n', encoding='utf-8')
                    os.replace(backup, image)
                    sync_directory(image.parent)
                    disconnected = True
                elif backup.exists():
                    backup.unlink()
                if disconnected:
                    binding.write_text('\n', encoding='ascii')
                    lun.write_text(str(image) + '\n', encoding='utf-8')
                    bind_controller(binding, udc)
                    if binding.read_text().strip() != udc or lun.read_text().strip() != str(image):
                        raise ResizeError('车机导出恢复失败')
                if created and temporary.exists():
                    temporary.unlink()
        except Exception as exc:
            raise ResizeError('镜像已保留，请检查恢复文件：%s' % exc, needs_recovery=True) from exc
        if marked:
            marker.unlink()
            sync_directory(image.parent)
