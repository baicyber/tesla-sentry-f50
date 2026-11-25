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


def remove_retry(path, attempts=12, delay=0.5):
    """删除旧镜像/残骸; 刚解绑时可能还被旧句柄短暂占用, EBUSY 时多等一会儿。"""
    path = Path(path)
    last = None
    busy_errors = (errno.EBUSY, errno.EPERM, errno.EACCES, getattr(errno, 'ETXTBSY', 26))
    for index in range(attempts):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            last = exc
            if exc.errno not in busy_errors or index + 1 >= attempts:
                raise
            time.sleep(delay)
    raise last


def resize(image, size_gib, gadget, udc, mkexfat, progress, action='调整容量'):
    """断开车机导出, 直接删除镜像并按目标容量重建一个空白 exFAT。

    不做备份、不保留旧 inode: 调整容量和格式化都会清空车机 U 盘内容。
    删除前先摘 UDC 再清 LUN, 可以中断车机/loop 对镜像的读写; 重建后重新绑定,
    车机侧会看到一张新的空白 U 盘, 无需再在车机上格式化。
    `action` only changes user-facing wording (调整容量 / 格式化 U 盘).
    """
    image, gadget = Path(image), Path(gadget)
    binding = gadget / 'UDC'
    lun = gadget / 'functions/mass_storage.0/lun.0/file'
    if not Path(mkexfat).is_file():
        raise ResizeError('创建工具不可用')
    progress('断开车机 U 盘', 0)
    # 先摘控制器让主机侧看到拔出, 再清 LUN; 状态不匹配时也尽量断开, 不要拒绝执行。
    try:
        write_retry(binding, '\n')
    except OSError as exc:
        if read_attr(binding):
            raise ResizeError('未能断开车机导出：%s' % exc)
    time.sleep(RETRY_DELAY)
    for attempt in range(4):
        try:
            write_retry(lun, '\n', attempts=1)
            break
        except OSError as exc:
            if exc.errno == errno.EBUSY and attempt < 3:
                time.sleep(1.0)
                continue
            raise ResizeError('未能解除车机 U 盘占用：%s' % exc)
    if read_attr(binding) or read_attr(lun):
        if image.is_file():
            try:
                attach(lun, image)
                bind_controller(binding, udc)
            except Exception:
                pass
        raise ResizeError('车机 U 盘仍被占用，未能开始%s' % action)
    try:
        # 旧镜像、旧版的临时/备份文件一并删掉, 不再保留任何恢复文件。
        progress('删除旧镜像', 10)
        valid = False
        for suffix in ('', '.resize-new', '.resize-backup', '.resize-state'):
            remove_retry(str(image) + suffix)
        image.parent.mkdir(parents=True, exist_ok=True)
        sync_directory(image.parent)
        progress('重建空白 U 盘', 20)
        command([sys.executable, '-B', mkexfat, image, str(size_gib)], label='创建 exFAT 镜像')
        with image.open('rb') as stream:
            header = stream.read(11)
        if header[3:11] != b'EXFAT   ' or image.stat().st_size != size_gib * GIB:
            raise ResizeError('新镜像校验失败')
        valid = True
        with image.open('r+b') as stream:
            os.fsync(stream.fileno())
        sync_directory(image.parent)
        progress('恢复车机导出', 90)
        attach(lun, image)
        bind_controller(binding, udc)
        if read_attr(binding) != udc or read_attr(lun) != str(image):
            raise ResizeError('USB 导出恢复失败')
        progress('%s完成' % action, 100)
    except Exception as exc:
        # 创建失败时删掉半成品, 避免把坏镜像重新交给车机; 旧镜像已按确认清空。
        if not valid:
            try:
                remove_retry(image)
            except OSError:
                pass
        if image.is_file():
            try:
                attach(lun, image)
                bind_controller(binding, udc)
            except Exception:
                pass
        if isinstance(exc, ResizeError):
            raise
        raise ResizeError('%s失败：%s' % (action, exc)) from exc
