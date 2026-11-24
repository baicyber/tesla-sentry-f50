"""Shared storage budgets and durable settings; sizes are integer GiB."""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

GIB = 1 << 30
FIELDS = ('archive_gib', 'drive_gib', 'free_min_gib', 'free_target_gib')
# 0 = 不设上限: 归档/驾驶记录只受磁盘空闲保护线约束, 不再按总量清理
UNLIMITED = ('archive_gib', 'drive_gib')


def settings_path():
    return Path(os.environ.get('TSF_STORAGE_SETTINGS') or
                Path(__file__).resolve().with_name('storage.json'))


def defaults():
    return dict(archive_gib=int(os.environ.get('TSF_ARCHIVE_TOTAL_GB', '30')),
                drive_gib=int(os.environ.get('TSF_DRIVE_MAX_GB', '12')),
                free_min_gib=int(os.environ.get('TSF_FREE_MIN_MB', '4096')) / 1024,
                free_target_gib=int(os.environ.get('TSF_FREE_TARGET_MB', '10240')) / 1024)


def validate_values(values, usb_gib):
    if set(values) != set(FIELDS):
        raise ValueError('存储参数不完整')
    for value in (*values.values(), usb_gib):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('容量须为整数 GiB')
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('容量须为整数 GiB')
        if int(value) != value:
            raise ValueError('容量须为整数 GiB')
    for name in FIELDS:
        low = 0 if name in UNLIMITED else 1
        if not low <= values[name] <= 4096:
            raise ValueError('归档与驾驶记录可填 0 表示无限制；其他容量须为 1～4096 GiB')
    if usb_gib > 128:
        raise ValueError('U 盘容量须在 1～128 GiB 范围内')
    if values['archive_gib'] and values['drive_gib'] > values['archive_gib']:
        raise ValueError('驾驶记录容量不能超过归档总容量')
    if values['free_min_gib'] > values['free_target_gib']:
        raise ValueError('最低空闲空间不能超过安全预留空间')
    return {name: int(values[name]) for name in FIELDS}


def required_bytes(values, usb_gib, cache_gib=0):
    # 归档设成 0 表示不设上限, 空间由空闲保护线兜底, 不再计入预留预算。
    # 缓存不再单独预留: 转码缓存按实际文件占用计入磁盘用量, 取消固定缓存预算。
    return (usb_gib + values['archive_gib'] + values['free_target_gib']) * GIB


def load(path=None):
    values = defaults()
    try:
        saved = json.loads(Path(path or settings_path()).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return values
    return validate_values(saved, 1)


def revision(values, image):
    info = Path(image).stat()
    body = json.dumps([values, info.st_dev, info.st_ino, info.st_size], sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


def sync_directory(path):
    if os.name == 'nt':
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save(values, path=None):
    destination = Path(path or settings_path())
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.storage-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(values, stream, ensure_ascii=False, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        sync_directory(destination.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def tree_usage(path, seen=None, skip=None):
    """Count hardlinked files once; exclude symlinks; skip dirs whose realpath is in skip.

    skip 用于把落在归档区内的转码缓存排除在归档用量之外 (它单独计入缓存预算)。
    """
    seen = seen if seen is not None else set()
    skip = skip or set()
    logical = allocated = 0
    if not Path(path).exists():
        return logical, allocated
    for root, dirs, files in os.walk(path):
        kept = []
        for name in dirs:
            sub = Path(root, name)
            if sub.is_symlink():
                continue
            if skip:
                try:
                    if os.path.realpath(sub) in skip:
                        continue
                except OSError:
                    pass
            kept.append(name)
        dirs[:] = kept
        for name in files:
            target = Path(root, name)
            try:
                info = target.lstat()
            except FileNotFoundError:
                continue
            if target.is_symlink() or not target.is_file() or (info.st_dev, info.st_ino) in seen:
                continue
            seen.add((info.st_dev, info.st_ino))
            logical += info.st_size
            allocated += getattr(info, 'st_blocks', (info.st_size + 511) // 512) * 512
    return logical, allocated


def snapshot(image, archive, cache):
    image, archive, cache = Path(image), Path(archive), Path(cache)
    info = image.stat()
    if archive.stat().st_dev != info.st_dev:
        raise ValueError('镜像和归档须位于同一数据盘')
    filesystem = os.statvfs(image.parent)
    image_allocated = getattr(info, 'st_blocks', (info.st_size + 511) // 512) * 512
    seen = {(info.st_dev, info.st_ino)}
    # 转码缓存落在归档区内: 统计归档用量时排除它, 再单独计入缓存预算
    skip = set()
    try:
        skip.add(os.path.realpath(cache))
    except OSError:
        pass
    archive_used, archive_allocated = tree_usage(archive, seen, skip)
    cache_parent = cache
    while not cache_parent.exists() and cache_parent != cache_parent.parent:
        cache_parent = cache_parent.parent
    try:
        cache_on_card = cache_parent.stat().st_dev == info.st_dev
    except OSError:
        cache_on_card = False
    cache_allocated = 0
    if cache_on_card:
        cache_allocated = tree_usage(cache, seen)[1]
    free = filesystem.f_bavail * filesystem.f_frsize
    return dict(total_bytes=filesystem.f_blocks * filesystem.f_frsize, free_bytes=free,
                budget_bytes=free + image_allocated + archive_allocated + cache_allocated,
                usb_bytes=info.st_size, archive_bytes=archive_used,
                drive_bytes=tree_usage(archive / 'DriveClips')[0])


def check_budget(values, usb_gib, disk):
    need = required_bytes(values, usb_gib)
    if need > disk['budget_bytes']:
        raise ValueError(
            '容量分配超过归档所在盘的可用容量 %.1f GiB：需要 %.1f（镜像 %d + 归档上限 %d + 安全预留 %d），'
            '可用 %.1f。规则：归档区上限 + 镜像容量（连同安全预留）不得超过归档区所在盘；'
            '可把归档总上限、镜像容量或安全预留调小，或将视频存储改到更大的盘。'
            % ((need - disk['budget_bytes']) / GIB, need / GIB, usb_gib,
               values['archive_gib'], values['free_target_gib'], disk['budget_bytes'] / GIB))


def check_resize_budget(values, usb_gib, disk):
    """调整镜像容量时按「归档实际占用」评估，而不是归档上限。

    归档上限是长期清理策略，随时可以下调；把上限全额预留会让「调大 U 盘」
    在还有很多未分配空间时也被误判为超出安全容量。安全预留仍然全额预留。
    """
    archive_actual = max(0, disk.get('archive_bytes', 0))
    if values['archive_gib']:
        archive_need = min(values['archive_gib'] * GIB, archive_actual)
    else:
        archive_need = archive_actual
    need = usb_gib * GIB + archive_need + values['free_target_gib'] * GIB
    if need > disk['budget_bytes']:
        raise ValueError(
            '容量调整超出可用空间 %.1f GiB：需要 %.1f GiB（U 盘 %d + 归档实际 %.1f + 安全预留 %d），'
            '可用 %.1f GiB。请减小 U 盘容量，或先清理归档/降低安全预留后重试。'
            % ((need - disk['budget_bytes']) / GIB, need / GIB, usb_gib, archive_need / GIB,
               values['free_target_gib'], disk['budget_bytes'] / GIB))
