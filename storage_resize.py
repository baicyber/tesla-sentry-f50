"""Recreate an empty exFAT image after the caller confirms clearing USB contents."""
import errno
import os
from pathlib import Path
import subprocess
import sys
import time

from storage_allocation import GIB, sync_directory

# configfs 写入会因平台 USB 看门狗/控制器重新注册而短暂失败 (ENODEV/EBUSY/EAGAIN),
# 这些是瞬时错误, 重试几秒基本都能过; 其它错误立刻上报, 不要盖住真实原因。
TRANSIENT = (errno.ENODEV, errno.EBUSY, errno.EAGAIN, errno.ENOENT)
RETRY_DELAY = float(os.environ.get('TSF_RESIZE_DELAY', '0.4'))
BIND_TRIES = 6


class ResizeError(RuntimeError):
    def __init__(self, message, needs_recovery=False):
        super().__init__(message)
        self.needs_recovery = needs_recovery


def command(args, label=None, timeout=300):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout or '').strip()[-200:]
        raise ResizeError('%s 失败：%s' % (label or args[0], detail))
    return result.stdout.strip()


def pending_recovery(image):
    return any(Path(str(image) + suffix).exists() for suffix in ('.resize-state', '.resize-new', '.resize-backup'))


def repair(image, gadget, udc):
    """上次调整留下的恢复文件: 若导出状态已经一致 (镜像在位、UDC/LUN 都绑对), 说明只是残骸,
    清掉就好, 不要把人锁在「请检查恢复文件」里。返回说明文字, 不该动时返回空串。"""
    image, gadget = Path(image), Path(gadget)
    files = [Path(str(image) + suffix) for suffix in ('.resize-state', '.resize-new', '.resize-backup')]
    if not any(path.exists() for path in files):
        return ''
    if not udc or not image.is_file():
        return ''
    binding = gadget / 'UDC'
    lun = gadget / 'functions/mass_storage.0/lun.0/file'
    if read_attr(binding) != udc or read_attr(lun) != str(image):
        return ''
    for path in files:
        try:
            path.unlink()
        except OSError:
            return ''
    sync_directory(image.parent)
    return '已清理上次调整留下的恢复文件（USB 导出正常）'


def write_retry(target, value, attempts=8, delay=None):
    """写 configfs 属性, 瞬时错误等一会儿再试 (控制器重新注册时就是 ENODEV)。"""
    delay = RETRY_DELAY if delay is None else delay
    last = None
    for index in range(attempts):
        try:
            target.write_text(value, encoding='utf-8')
            return
        except OSError as exc:
            last = exc
            if exc.errno not in TRANSIENT:
                raise
            if index + 1 < attempts:
                time.sleep(delay)
    raise last


def read_attr(target):
    try:
        return target.read_text().strip()
    except OSError:
        return ''


def bind_controller(binding, udc):
    """A platform USB watchdog may already have rebound the same controller.

    写入 UDC 时若控制器正被平台摘掉会报 ENODEV, 这不是终局: 每次写完都回读一次,
    只要已经是目标值就算成功; 瞬时错误重试 BIND_TRIES 轮再放弃。"""
    last = None
    for attempt in range(BIND_TRIES):
        fatal = None
        try:
            write_retry(binding, udc + '\n', attempts=2)
        except OSError as exc:
            last = exc
            if exc.errno not in TRANSIENT:     # 不是瞬时错误, 重试也没用
                fatal = exc
        if read_attr(binding) == udc:          # 写完回读: 看门狗抢先绑好也算成功
            return
        if fatal is not None:
            break
        if attempt + 1 < BIND_TRIES:
            time.sleep(RETRY_DELAY)
    raise ResizeError('USB 控制器绑定失败：%s' % (last or 'UDC 未就绪'))


def attach(lun, image):
    write_retry(lun, str(image) + '\n')
    if read_attr(lun) != str(image):
        raise ResizeError('录像镜像绑定失败')


def detach(binding, lun):
    """断开车机导出: 先摘镜像, 再松控制器。"""
    write_retry(lun, '\n')
    if read_attr(lun):
        raise ResizeError('未能解除录像镜像绑定')
    write_retry(binding, '\n')
    if read_attr(binding):
        raise ResizeError('未能断开车机导出')


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
    if read_attr(binding) != udc or read_attr(lun) != str(image):
        raise ResizeError('USB 导出状态不匹配，未调整容量')
    disconnected = False
    created = False
    published = False
    success = False
    marked = False
    failure = None
    cleanup_error = None
    try:
        with marker.open('x', encoding='utf-8') as stream:
            marked = True
            stream.write('recreate\n')
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(image.parent)
        progress('准备调整容量', 0)
        detach(binding, lun)
        disconnected = True
        # Keep other USB functions connected while the replacement is created.
        # The F50 platform may automatically rebind an unbound controller.
        bind_controller(binding, udc)
        with temporary.open('xb'):
            created = True
        progress('创建空白 U 盘', 20)
        command([sys.executable, '-B', mkexfat, temporary, str(size_gib)], label='创建 exFAT 镜像')
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
        write_retry(binding, '\n')
        attach(lun, image)
        bind_controller(binding, udc)
        if read_attr(binding) != udc or read_attr(lun) != str(image):
            raise ResizeError('USB 导出恢复失败')
        disconnected = False
        backup.unlink()
        sync_directory(image.parent)
        success = True
        progress('容量调整完成', 100)
    except Exception as exc:            # 先记住真实原因, 清理完再一起报, 不要被清理错误盖掉
        failure = exc
    finally:
        try:
            if not success:
                if published and backup.exists():
                    write_retry(binding, '\n')
                    write_retry(lun, '\n')
                    os.replace(backup, image)
                    sync_directory(image.parent)
                    disconnected = True
                elif backup.exists():
                    backup.unlink()
                # 不管之前走到哪一步, 只要导出状态不是「绑定 + 原镜像」就修复回来。
                # 只看实际状态而不是流程标记: 解绑到一半失败也要把 U 盘还给车机。
                if (disconnected or read_attr(binding) != udc or read_attr(lun) != str(image)):
                    write_retry(binding, '\n')
                    attach(lun, image)
                    bind_controller(binding, udc)
                if read_attr(binding) != udc or read_attr(lun) != str(image):
                    raise ResizeError('车机导出恢复失败')
                if created and temporary.exists():
                    temporary.unlink()
        except Exception as exc:
            cleanup_error = exc
    # 只有「清理也失败」才需要人工介入并保留标记; 恢复成功的就清掉标记, 别把功能锁死
    if cleanup_error is None and marked and marker.exists():
        marker.unlink()
        sync_directory(image.parent)
    if failure is None and cleanup_error is None:
        return
    if cleanup_error is not None:
        detail = '调整失败：%s；恢复也失败：%s' % (failure or '未完成', cleanup_error)
        raise ResizeError('镜像已保留，请检查恢复文件：%s' % detail, needs_recovery=True) from cleanup_error
    if isinstance(failure, ResizeError):
        raise failure
    raise ResizeError('调整失败：%s' % failure) from failure
