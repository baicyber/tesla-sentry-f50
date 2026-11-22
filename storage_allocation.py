"""Shared storage budgets and durable settings; sizes are integer GiB."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

GIB = 1 << 30
CACHE_GIB = 4
FIELDS = ('archive_gib', 'drive_gib', 'free_min_gib', 'free_target_gib')


def settings_path():
    return Path(os.environ.get('TSF_STORAGE_SETTINGS') or
                Path(__file__).resolve().with_name('storage.json'))


def defaults():
    return dict(archive_gib=int(os.environ.get('TSF_ARCHIVE_TOTAL_GB', '6')),
                drive_gib=int(os.environ.get('TSF_DRIVE_MAX_GB', '12')),
                free_min_gib=int(os.environ.get('TSF_FREE_MIN_MB', '4096')) / 1024,
                free_target_gib=int(os.environ.get('TSF_FREE_TARGET_MB', '10240')) / 1024)


def validate_values(values, usb_gib):
    if set(values) != set(FIELDS):
        raise ValueError('存储参数不完整')
    for value in (*values.values(), usb_gib):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 4096 or int(value) != value:
            raise ValueError('容量须为大于 0 的整数 GiB')
    if usb_gib > 128:
        raise ValueError('U 盘容量须在 1～128 GiB 范围内')
    if values['drive_gib'] > values['archive_gib']:
        raise ValueError('行车记录容量不能超过归档总容量')
    if values['free_min_gib'] > values['free_target_gib']:
        raise ValueError('最低空闲空间不能超过安全预留空间')
    return {name: int(values[name]) for name in FIELDS}


def required_bytes(values, usb_gib, cache_gib=CACHE_GIB):
    return (usb_gib + values['archive_gib'] + cache_gib + values['free_target_gib']) * GIB


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


def tree_usage(path, seen=None):
    """Count hardlinked files once; exclude symlinks and use allocated blocks for budget."""
    seen = seen if seen is not None else set()
    logical = allocated = 0
    if not Path(path).exists():
        return logical, allocated
    for root, dirs, files in os.walk(path):
        dirs[:] = [name for name in dirs if not Path(root, name).is_symlink()]
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
    archive_used, archive_allocated = tree_usage(archive, seen)
    cache_parent = cache
    while not cache_parent.exists() and cache_parent != cache_parent.parent:
        cache_parent = cache_parent.parent
    cache_on_card = cache_parent.stat().st_dev == info.st_dev
    cache_allocated = 0
    if cache_on_card:
        for name in ('res-cache', 'thumbs', 'stitch-tmp', 'tel-cache'):
            cache_allocated += tree_usage(cache / name, seen)[1]
    free = filesystem.f_bavail * filesystem.f_frsize
    return dict(total_bytes=filesystem.f_blocks * filesystem.f_frsize, free_bytes=free,
                budget_bytes=free + image_allocated + archive_allocated + cache_allocated,
                usb_bytes=info.st_size, archive_bytes=archive_used,
                drive_bytes=tree_usage(archive / 'DriveClips')[0], cache_gib=CACHE_GIB if cache_on_card else 0)


def check_budget(values, usb_gib, disk):
    if required_bytes(values, usb_gib, disk.get('cache_gib', CACHE_GIB)) > disk['budget_bytes']:
        raise ValueError('容量分配超过可用预算（包含缓存预算和安全预留空间）')
