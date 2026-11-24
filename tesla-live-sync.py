#!/usr/bin/env python3
"""tesla-live-sync.py - 车机在线只读同步 (不挂载, 不打断录制)

原理
----
虚拟U盘由 gadget 独占导出给车机, Linux 侧无法同时 mount (exFAT 非日志, 双挂载会互毁)。
但内核导出时 backing 文件本身仍可被只读打开 —— 本程序直接按 exFAT 规范解析
镜像的引导扇区 / FAT / 目录项, 沿簇链把车机写好的片段拷到归档区。

车机完全无感知: 全程 os.pread 只读, 不 mount、不写镜像。

"已完成"判定
------------
exFAT 目录项里 DataLength 在文件关闭时才落最终值, 因此必须:
  1. DataLength > 0
  2. 连续两轮扫描 (DataLength, ValidDataLength, mtime) 完全一致
才认为片段写完, 避免把半截 mp4 拷出来 (缺 moov 头放不了)。

用法
----
  tesla-live-sync.py --dump            打印镜像内 TeslaCam 目录树 (调试)
  tesla-live-sync.py --once            扫一轮就退出
  tesla-live-sync.py                   常驻轮询 (默认 15 秒)
"""
import argparse
import datetime
import fcntl
import json
import os
import re
import socket
import struct
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import storage_allocation

# 配置兜底: procd 不把 init.d 的 export 传给服务进程, 这里自己读 conf
# (优先级: 显式环境变量 > /etc/tesla-sentry.conf > 代码默认值; 空值视为用默认)
def _load_conf(path="/etc/tesla-sentry.conf"):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k.startswith("TSF_") and v and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_conf()

VIDEO_ROOT = os.environ.get("TSF_VIDEO_ROOT", "/mnt/sdcard/tesla-sentry")
IMG = os.environ.get("TSF_IMG", VIDEO_ROOT + "/img/teslacam.img")
ARCHIVE = os.environ.get("TSF_ARCHIVE", VIDEO_ROOT + "/archive")
MNT = os.environ.get("TSF_MNT", "/tmp/teslamnt")
LOG = os.environ.get("TSF_LIVE_LOG", VIDEO_ROOT + "/logs/tesla-live.log")
STATE = os.environ.get("TSF_LIVE_STATE", VIDEO_ROOT + "/live-state.json")
INTERVAL = int(os.environ.get("TSF_LIVE_INTERVAL", "15"))
RECENT_INTERVAL = 5
RECENT_SEGMENT_SECONDS = 60
RECENT_FAST_BEFORE = 5
RECENT_FAST_AFTER = 8
RECENT_STABLE_SECONDS = 3
RECENT_CAMERAS = ("front", "back", "left_repeater", "right_repeater")
LIVE_READY_SOCKET = "/root/.tesla-live-ready.sock"
# 与网页 U盘管理 / 定时归档 / U盘自愈共用的跨进程锁 (那边持排他, 这里持共享)
USB_LOCK = os.environ.get("TSF_USB_LOCK", "/tmp/tsf-usb.lock")
# 归档滚动窗口(分钟), 与 sentry-viewer.py 的 TSF_RECENT_ROLL_MIN 保持一致;
# 窗口外的 RecentClips 根本不再抄进归档 —— 否则清掉的片段会被当成"新片段"重抄一遍,
# 形成 删除→重抄 的死循环, 既拖慢新片段落地又白写 eMMC
ROLL_MIN = int(os.environ.get("TSF_RECENT_ROLL_MIN", "60"))
FREE_MIN_MB = int(os.environ.get("TSF_FREE_MIN_MB", "4096"))
SETTINGS_FILE = os.environ.get("TSF_SETTINGS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "settings.json")


def refresh_storage_settings():
    global FREE_MIN_MB, ROLL_MIN
    try:
        FREE_MIN_MB = int(storage_allocation.load()['free_min_gib'] * 1024)
    except (OSError, ValueError, TypeError):
        pass
    # 网页里设置的「实时滚动记录」窗口优先于静态 conf, 与 sentry-viewer.py 保持一致
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as stream:
            saved = json.load(stream)
        value = int(saved.get("recent_roll_min"))
        if 0 <= value <= 1440:
            ROLL_MIN = value
    except (OSError, ValueError, TypeError):
        pass


refresh_storage_settings()
FREE_LOG_INTERVAL = int(os.environ.get("TSF_SYNC_FREE_LOG_INTERVAL", "300"))
FAIL_RETRY_BASE = int(os.environ.get("TSF_SYNC_FAIL_RETRY_SEC", "300"))
FAIL_RETRY_MAX = int(os.environ.get("TSF_SYNC_FAIL_RETRY_MAX_SEC", "1800"))
CATS = ("SentryClips", "SavedClips", "RecentClips")
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-")


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def archive_free_mb():
    try:
        st = os.statvfs(ARCHIVE)
        return st.f_bavail * st.f_frsize // 1048576
    except OSError:
        return -1


def usb_shared_lock():
    """拿共享锁; 别人(网页挂载/归档/自愈)持排他锁时返回 None, 本轮直接跳过"""
    fd = None
    try:
        fd = os.open(USB_LOCK, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        if any(os.path.exists(IMG + suffix) for suffix in ('.resize-state', '.resize-new', '.resize-backup')):
            os.close(fd)
            return None
        return fd
    except OSError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        return None


def usb_unlock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def dos_time(raw):
    """exFAT 32bit 时间 → epoch 近似值"""
    if not raw:
        return 0
    sec = (raw & 0x1F) * 2
    minute = (raw >> 5) & 0x3F
    hour = (raw >> 11) & 0x1F
    day = (raw >> 16) & 0x1F
    month = (raw >> 21) & 0x0F
    year = ((raw >> 25) & 0x7F) + 1980
    try:
        return int(time.mktime((year, month, day, hour, minute, sec, 0, 0, -1)))
    except Exception:
        return 0


class Entry:
    __slots__ = ("name", "is_dir", "cluster", "size", "vdl", "mtime", "no_fat_chain", "valid")

    def __init__(self, name, is_dir, cluster, size, vdl, mtime, no_fat_chain, valid):
        self.name = name
        self.is_dir = is_dir
        self.cluster = cluster
        self.size = size
        self.vdl = vdl
        self.mtime = mtime
        self.no_fat_chain = no_fat_chain
        self.valid = valid

    def __repr__(self):
        return "<%s %s size=%d vdl=%d>" % ("DIR" if self.is_dir else "FILE", self.name, self.size, self.vdl)


class ExFat:
    """最小 exFAT 只读解析器 (只依赖标准库)"""

    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY)
        self.fd_ino = os.fstat(self.fd).st_ino
        vbr = os.pread(self.fd, 512, 0)
        if len(vbr) < 512 or vbr[3:11] != b"EXFAT   ":
            raise ValueError("不是 exFAT 卷: %s" % path)
        self.part_offset = struct.unpack_from("<Q", vbr, 64)[0]
        self.vol_len = struct.unpack_from("<Q", vbr, 72)[0]
        self.fat_offset = struct.unpack_from("<I", vbr, 80)[0]
        self.fat_len = struct.unpack_from("<I", vbr, 84)[0]
        self.heap_offset = struct.unpack_from("<I", vbr, 88)[0]
        self.cluster_count = struct.unpack_from("<I", vbr, 92)[0]
        self.root_cluster = struct.unpack_from("<I", vbr, 96)[0]
        self.vol_flags = struct.unpack_from("<H", vbr, 106)[0]
        self.bytes_per_sector = 1 << vbr[108]
        self.cluster_size = self.bytes_per_sector << vbr[109]
        self.num_fats = vbr[110]
        # 多 FAT 时由 VolumeFlags bit0 指定活动 FAT
        self.active_fat = self.fat_offset
        if self.num_fats > 1 and (self.vol_flags & 0x1):
            self.active_fat += self.fat_len
        self.heap_byte = self.heap_offset * self.bytes_per_sector

    # ---------- 底层读取 ----------
    def pread(self, n, off):
        out = os.pread(self.fd, n, off)
        return out

    def cluster_off(self, cluster):
        return self.heap_byte + (cluster - 2) * self.cluster_size

    def read_cluster(self, cluster):
        return self.pread(self.cluster_size, self.cluster_off(cluster))

    def fat_next(self, cluster):
        ent = self.pread(4, self.active_fat * self.bytes_per_sector + cluster * 4)
        if len(ent) < 4:
            return 0xFFFFFFFF
        return struct.unpack("<I", ent)[0]

    def chain(self, first, max_clusters=None):
        """遍历簇链, 返回簇号列表"""
        if first < 2:
            return []
        limit = max_clusters or self.cluster_count
        out = []
        c = first
        while 2 <= c < (self.cluster_count + 2) and len(out) < limit:
            out.append(c)
            nxt = self.fat_next(c)
            if nxt >= 0xFFFFFFF7:  # EOC 区间
                break
            if nxt < 2 or nxt == c:
                break
            c = nxt
        return out

    def clusters_for(self, entry):
        """文件占用簇列表: NoFatChain 位表示连续分配"""
        n = (entry.size + self.cluster_size - 1) // self.cluster_size
        if n <= 0:
            return []
        if entry.no_fat_chain:
            return list(range(entry.cluster, entry.cluster + n))
        return self.chain(entry.cluster, n)

    def read_file(self, entry, limit=None):
        """按簇链读取文件内容 (可分块迭代)"""
        remain = entry.size if limit is None else min(entry.size, limit)
        for c in self.clusters_for(entry):
            if remain <= 0:
                break
            data = self.read_cluster(c)
            if not data:
                break
            chunk = data[:remain]
            remain -= len(chunk)
            yield chunk

    # ---------- 目录解析 ----------
    @staticmethod
    def _set_checksum(buf):
        cs = 0
        for i, b in enumerate(buf):
            if i in (2, 3):
                continue
            cs = ((cs << 15) | (cs >> 1)) & 0xFFFF
            cs = (cs + b) & 0xFFFF
        return cs

    def list_dir(self, cluster, size=0):
        """解析目录簇链, 返回 Entry 列表"""
        if size > 0:
            clist = self.clusters_for(Entry("", True, cluster, size, size, 0, False, True))
        else:
            clist = self.chain(cluster, 64)
        data = b"".join(self.read_cluster(c) for c in clist)
        entries = []
        i = 0
        total = len(data)
        while i + 32 <= total:
            etype = data[i]
            if etype == 0x00:  # 目录结束
                break
            if etype != 0x85:
                i += 32
                continue
            sec_count = data[i + 1]
            set_end = i + 32 * (1 + sec_count)
            if sec_count < 2 or set_end > total:
                break
            raw = data[i:set_end]
            ok = self._set_checksum(raw) == struct.unpack_from("<H", raw, 2)[0]
            attrs = struct.unpack_from("<H", raw, 4)[0]
            mtime = dos_time(struct.unpack_from("<I", raw, 12)[0])
            name = ""
            first = size_ = vdl = 0
            no_chain = False
            for j in range(sec_count):
                sec = raw[32 * (1 + j):32 * (2 + j)]
                st = sec[0]
                if st == 0xC0:
                    flags = sec[1]
                    vdl = struct.unpack_from("<Q", sec, 8)[0]
                    first = struct.unpack_from("<I", sec, 20)[0]
                    size_ = struct.unpack_from("<Q", sec, 24)[0]
                    no_chain = bool(flags & 0x02)
                elif st == 0xC1:
                    name += sec[2:32].decode("utf-16-le", "ignore")
            # 文件名在 0xC1 项里以 NUL 填充到末尾: 取第一个 NUL 之前最可靠
            # (NameLength 字段在部分实现里是字符数而非字节数, 不可全信)
            name = name.split("\x00")[0]
            if not name or name in (".", ".."):
                i = set_end
                continue
            entries.append(Entry(name, bool(attrs & 0x10), first, size_, vdl, mtime, no_chain, ok))
            i = set_end
        return entries

    def find(self, entries, name):
        low = name.lower()
        for e in entries:
            if e.name.lower() == low:
                return e
        return None

    def signature_ok(self):
        """车机格式化/重建镜像期间引导扇区会被清空或改写，用它判断是否需要重开。
        只读 11 字节，每个轮询周期做一次也不影响性能。"""
        try:
            vbr = os.pread(self.fd, 11, 0)
        except OSError:
            return False
        return len(vbr) >= 11 and vbr[3:11] == b"EXFAT   "

    def close(self):
        try:
            os.close(self.fd)
        except Exception:
            pass


# ---------------- 同步逻辑 ----------------
def safe_dest(*parts):
    p = os.path.join(ARCHIVE, *parts)
    rp = os.path.realpath(p)
    if not rp.startswith(os.path.realpath(ARCHIVE) + os.sep):
        raise ValueError("目标越界: %s" % rp)
    return rp


def same_file(path, size):
    try:
        return os.path.getsize(path) == size
    except OSError:
        return False


class ClipNotReady(Exception):
    pass


def mp4_has_index(read_at, size):
    """检查 MP4 的媒体数据与索引箱已写齐，兼容索引放在文件头或尾。"""
    media = index = False
    offset = 0
    for _ in range(128):
        if offset + 8 > size:
            return False
        head = read_at(offset, 8)
        if len(head) != 8:
            return False
        length, kind = struct.unpack(">I4s", head)
        header_size = 8
        if length == 1:
            extended = read_at(offset + 8, 8)
            if len(extended) != 8:
                return False
            length = struct.unpack(">Q", extended)[0]
            header_size = 16
        elif length == 0:
            length = size - offset
        if length < header_size or offset + length > size:
            return False
        if kind == b"moov" and length >= header_size + 8:
            index = True
        if kind == b"mdat" and length > header_size:
            media = True
        if index and media:
            return True
        offset += length
    return False


def source_has_mp4_index(exfat, entry):
    clusters = exfat.clusters_for(entry)

    def read_at(offset, count):
        data = bytearray()
        while len(data) < count:
            block, within = divmod(offset + len(data), exfat.cluster_size)
            if block >= len(clusters):
                break
            length = min(count - len(data), exfat.cluster_size - within)
            chunk = exfat.pread(length, exfat.cluster_off(clusters[block]) + within)
            data.extend(chunk)
            if len(chunk) != length:
                break
        return data

    return mp4_has_index(read_at, entry.size)


def copy_entry(exfat, entry, dest_path, require_index=False):
    """把镜像里的文件写到归档区, 返回写入字节数"""
    if require_index and not source_has_mp4_index(exfat, entry):
        raise ClipNotReady
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    tmp = dest_path + ".part"
    written = 0
    try:
        with open(tmp, "w+b") as f:
            for chunk in exfat.read_file(entry):
                f.write(chunk)
                written += len(chunk)
            if written != entry.size:
                raise IOError("读取不完整 %d/%d" % (written, entry.size))
            f.flush()
            if require_index:
                def read_at(pos, count):
                    f.seek(pos)
                    return f.read(count)
                if not mp4_has_index(read_at, written):
                    raise ClipNotReady
            os.fsync(f.fileno())
        os.replace(tmp, dest_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return written


def iter_media(exfat, dir_entry, prefix=()):
    """遍历目录, 产出 (相对路径元组, 文件 Entry)"""
    if not dir_entry or not dir_entry.is_dir:
        return
    try:
        entries = exfat.list_dir(dir_entry.cluster, dir_entry.size)
    except Exception as e:
        log("读取目录失败 %s: %s" % (dir_entry.name, e))
        return
    for e in entries:
        if not e.valid:
            continue
        if e.is_dir:
            yield from iter_media(exfat, e, prefix + (e.name,))
        elif (e.name.lower().endswith(".mp4") or e.name.lower() == "event.json") and e.size > 0:
            yield prefix, e


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    try:
        with open(STATE + ".tmp", "w") as f:
            json.dump(st, f)
        os.replace(STATE + ".tmp", STATE)
    except Exception:
        pass


def scan(exfat, cats=CATS):
    """返回 {(分类, 事件名, 文件名): Entry}"""
    found = {}
    try:
        root = exfat.list_dir(exfat.root_cluster)
    except Exception as e:
        log("读根目录失败: %s" % e)
        return found
    cam = exfat.find(root, "TeslaCam")
    if not cam or not cam.is_dir:
        return found
    cat_list = exfat.list_dir(cam.cluster, cam.size)
    for cat in cats:
        catdir = exfat.find(cat_list, cat)
        if not catdir or not catdir.is_dir:
            continue
        if cat == "RecentClips":
            # 平铺文件: 按文件名时间戳分组, 让网页能按事件展示
            try:
                items = exfat.list_dir(catdir.cluster, catdir.size)
            except Exception:
                continue
            for e in items:
                if e.is_dir or e.size <= 0 or not e.valid:
                    continue
                if e.name.lower() == "event.json":
                    # 平铺目录里的 event.json: 复制时跟随每个分钟分组各存一份
                    found[(cat, "_event", "event.json")] = e
                    continue
                if not e.name.lower().endswith(".mp4"):
                    continue
                m = TS_RE.match(e.name)
                group = m.group(1) if m else "_misc"
                found[(cat, group, e.name)] = e
        else:
            for prefix, e in iter_media(exfat, catdir):
                if not prefix:
                    continue
                found[(cat, prefix[0], e.name)] = e
    return found


def ev_dt(ev):
    """事件目录名即片段起始时间 (2026-11-05_16-30-39)"""
    try:
        return datetime.datetime.strptime(ev[:19], "%Y-%m-%d_%H-%M-%S")
    except (ValueError, TypeError):
        return None


# 墓碑: sentry-viewer.py 清理盒子侧哨兵/手动保存归档时把 "分类/事件名" 记进 /archive/.pruned,
# 这里读到就跳过 —— 车机 U 盘上还留着的旧事件若不跳过, 会被当成新片段重抄回来,
# 形成 删除→重抄 的死循环 (和 RecentClips 滚动窗口同理)
def load_pruned():
    try:
        with open(os.path.join(ARCHIVE, ".pruned"), encoding="utf-8", errors="ignore") as f:
            return set(x.strip() for x in f if "/" in x)
    except OSError:
        return set()


class RecentSchedule:
    """从新一组车机源文件首次扫齐的时刻预测下一次检查窗口。"""

    def __init__(self):
        self.group = ""
        self.anchor = None
        self.candidate = None
        self.phase = "waiting"
        self.pending_since = {}
        self.published_group = ""

    def publish_archived(self, cur, group):
        if group == "_misc" or group <= self.published_group:
            return
        for camera in RECENT_CAMERAS:
            name = "%s-%s.mp4" % (group, camera)
            entry = cur.get(("RecentClips", group, name))
            if entry is None or entry.vdl < entry.size:
                return
            try:
                dest = safe_dest("RecentClips", group, name)
            except ValueError:
                return
            if not same_file(dest, entry.size):
                return
        self.published_group = group
        body = json.dumps({"clip": group, "ready_at": time.time()}).encode("ascii")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.setblocking(False)
                sock.sendto(body, LIVE_READY_SOCKET)
        except OSError:
            pass  # 网页的定时检查可以补上丢失的通知。

    def window(self):
        if self.anchor is None:
            return None
        predicted = self.anchor + RECENT_SEGMENT_SECONDS
        return predicted - RECENT_FAST_BEFORE, predicted + RECENT_FAST_AFTER

    def next_check(self, now):
        fallback = now + RECENT_INTERVAL
        window = self.window()
        if window is None or now >= window[1]:
            return fallback
        start, end = window
        if now < start:
            return min(fallback, start)
        return min(now + 1, end)

    def observe(self, exfat, scanned, now):
        window = self.window()
        if window and self.phase == "waiting" and window[0] <= now < window[1]:
            self.phase = "fast"
            log("行车加速检查: %s 后的窗口，每秒一次" % self.group)
        if window and self.phase != "expired" and now >= window[1]:
            self.phase = "expired"
            log("行车窗口未命中: %s 后恢复每 %d 秒检查" % (self.group, RECENT_INTERVAL))

        groups = {}
        for (cat, group, name), entry in scanned.items():
            if cat != "RecentClips" or group == "_misc" or group <= self.group or entry.vdl < entry.size:
                continue
            camera = name[len(group) + 1:-4].lower()
            if camera in RECENT_CAMERAS:
                groups.setdefault(group, {})[camera] = entry
        group = max((name for name, cameras in groups.items() if len(cameras) == len(RECENT_CAMERAS)), default="")
        if not group:
            self.candidate = None
            return
        entries = [groups[group][c] for c in RECENT_CAMERAS]
        signatures = tuple((e.cluster, e.size, e.vdl, e.mtime) for e in entries)
        key = (group, signatures)
        if self.candidate is None or self.candidate[0] != key:
            self.candidate = (key, now) if all(source_has_mp4_index(exfat, e) for e in entries) else None
            return
        first_seen = self.candidate[1]
        if now - first_seen < RECENT_STABLE_SECONDS:
            return
        if not all(source_has_mp4_index(exfat, e) for e in entries):
            self.candidate = None
            return
        self.group, self.anchor = group, first_seen
        self.candidate = None
        self.phase = "waiting"
        wall = time.time() - (time.monotonic() - first_seen)
        def clock(ts):
            return time.strftime("%H:%M:%S", time.localtime(ts))
        log("行车同步校准: %s 四路首次扫齐 %s，下次加速 %s–%s" % (
            group, clock(wall),
            clock(wall + RECENT_SEGMENT_SECONDS - RECENT_FAST_BEFORE),
            clock(wall + RECENT_SEGMENT_SECONDS + RECENT_FAST_AFTER)))


def do_sync(exfat, state, quiet=False, cats=CATS, recent=None):
    """跑一轮; 返回 (新增文件数, 字节数)"""
    cur = scan(exfat, cats)
    recent_check = recent is not None and "RecentClips" in cats
    if recent_check:
        observed = time.monotonic()
        recent.observe(exfat, cur, observed)
    pruned = load_pruned()
    # 只收滚动窗口内的 RecentClips: 以车机 U 盘上最新的片段为锚点
    cut = None
    if ROLL_MIN > 0:
        newest = None
        for (cat, ev, name) in cur:
            if cat == "RecentClips":
                d = ev_dt(ev)
                if d and (newest is None or d > newest):
                    newest = d
        if newest:
            cut = newest - datetime.timedelta(minutes=ROLL_MIN)
    pending_key = "__pending__"
    failures_key = "__failures__"
    pending = state.get(pending_key, {})
    failures = state.get(failures_key, {})
    new_pending = {k: v for k, v in pending.items() if k.split("/", 1)[0] not in cats}
    new_failures = {k: v for k, v in failures.items() if k.split("/", 1)[0] not in cats}
    copied = 0
    bytes_ = 0
    for (cat, ev, name), e in cur.items():
        if ev == "_event":
            continue            # 平铺目录的 event.json 由片段复制时顺带写入
        if "%s/%s" % (cat, ev) in pruned:
            continue            # 已被盒子侧定期清理, 不再重抄
        if cut is not None and cat == "RecentClips":
            d = ev_dt(ev)
            if d and d < cut:
                continue          # 超出归档窗口, 留在车机里, 不再抄
        key = "%s/%s/%s" % (cat, ev, name)
        sig = [e.size, e.vdl, e.mtime]
        dest = safe_dest(cat, ev, name)
        if same_file(dest, e.size):
            continue
        if cat == "RecentClips" and e.vdl < e.size:
            continue
        old_fail = failures.get(key)
        if (old_fail and old_fail.get("sig") == sig and
                time.time() < old_fail.get("next", 0)):
            new_pending[key] = sig
            new_failures[key] = old_fail
            continue
        # 保留两轮确认；高频检查时也要经过实际的稳定时间。
        matches = pending.get(key) == sig
        stable = matches
        if recent_check and cat == "RecentClips":
            cluster, since = recent.pending_since.get(key, (None, observed))
            if not matches or cluster != e.cluster:
                since = observed
            recent.pending_since[key] = (e.cluster, since)
            stable = matches and observed - since >= RECENT_STABLE_SECONDS
        if not stable:
            new_pending[key] = sig
            if not quiet and not matches:
                log("发现新片段(待确认): %s (%d bytes)" % (key, e.size))
            continue
        try:
            n = copy_entry(exfat, e, dest, require_index=cat == "RecentClips")
            copied += 1
            bytes_ += n
            if recent_check and cat == "RecentClips":
                recent.publish_archived(cur, ev)
                ev_entry = cur.get((cat, "_event", "event.json"))
                if ev_entry is not None:
                    try:
                        copy_entry(exfat, ev_entry, safe_dest(cat, ev, "event.json"))
                    except Exception:
                        pass
            log("已同步 %s (%d KB)" % (key, n // 1024))
        except ClipNotReady:
            new_pending[key] = sig
        except Exception as ex:
            prev_count = int(old_fail.get("fail_count", 0)) if old_fail and old_fail.get("sig") == sig else 0
            fail_count = prev_count + 1
            delay = min(FAIL_RETRY_MAX, FAIL_RETRY_BASE * (1 << min(fail_count - 1, 8)))
            log("同步失败 %s: %s；%d 秒后重试" % (key, ex, delay))
            new_pending[key] = sig
            new_failures[key] = {"sig": sig, "fail_count": fail_count, "next": time.time() + delay}
    if recent_check:
        recent.pending_since = {k: v for k, v in recent.pending_since.items() if k in new_pending}
    if pending != new_pending or failures != new_failures:
        state[pending_key] = new_pending
        state[failures_key] = new_failures
        save_state(state)
    return copied, bytes_


def dump(exfat):
    root = exfat.list_dir(exfat.root_cluster)
    print("根目录 (%d 项):" % len(root))
    for e in root:
        print("  %s %-30s size=%-10d %s" % ("DIR " if e.is_dir else "FILE", e.name, e.size,
                                            "" if e.valid else "[checksum bad]"))
    cam = exfat.find(root, "TeslaCam")
    if not cam:
        print("! 没有 TeslaCam 目录")
        return
    for cat in CATS:
        c = exfat.find(exfat.list_dir(cam.cluster, cam.size), cat)
        if not c:
            print("%s: 不存在" % cat)
            continue
        items = exfat.list_dir(c.cluster, c.size)
        print("%s: %d 项" % (cat, len(items)))
        for it in items[:8]:
            print("   %s %-40s size=%d" % ("DIR " if it.is_dir else "FILE", it.name, it.size))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=INTERVAL)
    args = ap.parse_args()
    if args.interval < 1:
        ap.error("--interval must be at least 1 second")

    if not os.path.exists(IMG):
        log("镜像不存在: %s" % IMG)
        return 1

    def open_exfat(verbose=True):
        """车机格式化时引导扇区会短暂无效；不要一次失败就退出服务。"""
        try:
            return ExFat(IMG)
        except Exception as e:
            if verbose:
                log("解析镜像失败: %s（若车机正在格式化，稍后会自动重试）" % e)
            return None

    exfat = open_exfat()

    if args.dump:
        if exfat is None:
            return 1
        dump(exfat)
        exfat.close()
        return 0

    if args.once:
        if exfat is None:
            return 1
        state = load_state()
        n, b = do_sync(exfat, state)
        log("单轮完成: %d 个新片段, %.1f MB" % (n, b / 1048576))
        exfat.close()
        return 0

    log("在线同步启动: 行车 %d 秒（窗口内 1 秒），事件 %d 秒" % (RECENT_INTERVAL, args.interval))
    state = load_state()
    lock_miss = 0
    free_log_at = 0
    open_log_at = 0.0
    intervals = {"RecentClips": RECENT_INTERVAL, "SentryClips": args.interval, "SavedClips": args.interval}
    next_scan = dict.fromkeys(intervals, 0)
    recent = RecentSchedule()
    while True:
        delay = min(next_scan.values()) - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        now = time.monotonic()
        cats = tuple(cat for cat, tick in next_scan.items() if tick <= now)
        if not cats:
            continue
        for cat in cats:
            next_scan[cat] = recent.next_check(now) if cat == "RecentClips" else now + intervals[cat]
        refresh_storage_settings()
        free_mb = archive_free_mb()
        if free_mb < FREE_MIN_MB:
            now = time.time()
            if now >= free_log_at:
                log("归档空间不足 (%s MB)，暂停本轮同步" % free_mb)
                free_log_at = now + FREE_LOG_INTERVAL
            continue
        lk = usb_shared_lock()
        if lk is None:
            # 网页正在管理U盘 / 定时归档正在挂载: 跳过本轮。
            # 节流日志: 万一锁文件不可写导致长期停摆, 仍要留下线索。
            lock_miss += 1
            if lock_miss in (1, 20, 80):
                log("跳过本轮: U盘被网页管理/归档占用, 或锁文件不可写 (%s), 连续 %d 轮" % (USB_LOCK, lock_miss))
            continue
        lock_miss = 0
        try:
            # 网页正在管理U盘(已挂载)时跳过, 避免与挂载视图不一致
            if os.path.ismount(MNT):
                continue
            # 镜像被重建/替换时 fd 会指向旧 inode; 车机格式化会改写引导扇区。
            # 两种情况都把旧句柄关掉重开，且失败不退出常驻循环。
            try:
                st = os.stat(IMG)
                if exfat is not None and (st.st_ino != exfat.fd_ino
                                          or st.st_size != exfat.vol_len * exfat.bytes_per_sector):
                    log("镜像大小或 inode 变化, 重新打开")
                    exfat.close()
                    exfat = None
                    recent = RecentSchedule()
                if exfat is not None and not exfat.signature_ok():
                    log("镜像引导扇区无效 (可能车机正在格式化), 稍后重试")
                    exfat.close()
                    exfat = None
                    recent = RecentSchedule()
            except OSError as ex:
                log("镜像不可读: %s" % ex)
                continue
            if exfat is None:
                exfat = open_exfat(verbose=False)
                if exfat is None:
                    now_s = time.time()
                    if now_s - open_log_at >= 60:
                        open_log_at = now_s
                        log("镜像暂不可解析，等待车机格式化完成")
                    continue
                log("镜像已恢复，继续在线同步")
            do_sync(exfat, state, cats=cats, recent=recent)
            if "RecentClips" in cats:
                next_scan["RecentClips"] = recent.next_check(now)
        except Exception as e:
            log("轮询异常: %s" % e)
        finally:
            usb_unlock(lk)


if __name__ == "__main__":
    sys.exit(main() or 0)
