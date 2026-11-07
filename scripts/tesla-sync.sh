#!/bin/sh
# tesla-sync.sh - 把锁车音/音效写入车机U盘 (视频归档已交给 tesla-live-sync.py)
#
# 历史 (v2.7.0 之前): 本脚本按"归档目录不存在就 cp -r"复制车机 U 盘上的事件目录。
#   但 tesla-live-sync.py 与 sentry-viewer.py 有 /archive/.pruned 墓碑与 RecentClips
#   滚动窗口, 已清理的事件在车机 U 盘上仍然存在, 会被这里当成"新事件"重抄回来,
#   形成 删除→重抄 死循环 (实测 .pruned 里同一事件被登记两次, 归档日志反复写满磁盘)。
#   v2.7.0 起视频复制整个移除: 视频只由 tesla-live-sync.py 每 15 秒只读同步一次,
#   它带墓碑与窗口过滤, 且全程不挂载、车机无感。
#
# 本脚本只做: 空闲检测 → 短暂停止导出 → 挂载镜像 → 写 Boombox 音频 → 恢复导出
#
# 安全机制:
#  1. 跨进程互斥锁, 与网页 U盘管理 / usb-selfheal / tesla-live-sync 共用同一把锁
#  2. 镜像超过 TSF_IDLE 秒无写入才动手 (避免打断行车录制)
#  3. 网页"U盘管理"模式(镜像已被挂载)时自动跳过
#  4. 全程日志

[ -f /etc/tesla-sentry.conf ] && . /etc/tesla-sentry.conf

: "${TSF_DIR:=/opt/tesla-sentry}"
: "${TSF_IMG:=$TSF_DIR/teslacam.img}"
: "${TSF_ARCHIVE:=/archive}"
: "${TSF_MNT:=/tmp/teslamnt}"
: "${TSF_IDLE:=600}"
: "${TSF_LOG_DIR:=$TSF_DIR}"
: "${TSF_USB_LOCK:=/tmp/tsf-usb.lock}"

IMG="$TSF_IMG"
ARCHIVE="$TSF_ARCHIVE"
AUDIO_Q="$ARCHIVE/boombox-queue"
AUDIO_DONE="$ARCHIVE/boombox-done"
MNT="$TSF_MNT"
IDLE="$TSF_IDLE"
LOCK="$TSF_USB_LOCK"
LOG="$TSF_LOG_DIR/tesla-sync.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# --- 0. 跨进程互斥 ---
# 网页端用 fcntl.flock 持排他锁跨整个挂载周期, live-sync 持共享锁;
# 这里拿不到锁立刻退出, 不排队堆积 (cron 每 10 分钟会再试)。
exec 9>"$LOCK" 2>/dev/null || { echo "LOCK-OPEN-FAIL"; exit 1; }
flock -n 9 || {
    log "skip: usb lock held (viewer usb-manage / selfheal / another sync)"
    echo "SKIP-LOCKED"
    exit 0
}

# --- 自动探测 ---
find_gadget() {
    if [ -n "$TSF_GADGET" ] && [ -d "$TSF_GADGET" ]; then echo "$TSF_GADGET"; return 0; fi
    for d in /sys/kernel/config/usb_gadget/*/; do
        [ -d "$d/configs" ] && { echo "${d%/}"; return 0; }
    done
    return 1
}
GADGET=$(find_gadget) || { echo "ERROR: gadget not found"; exit 1; }
UDC=$(ls /sys/class/udc/ 2>/dev/null | head -1)

# losetup: 优先系统自带, 兼容 busybox 精简系统
if [ -n "$TSF_LOSETUP" ]; then BB="$TSF_LOSETUP"
elif command -v losetup >/dev/null 2>&1; then BB=$(command -v losetup)
elif [ -x /opt/mu300/busybox-bin/losetup ]; then BB=/opt/mu300/busybox-bin/losetup
else BB="busybox losetup"; fi

restore() {
    [ -n "$UDC" ] || return 0
    echo "$UDC" > "$GADGET/UDC"
    sleep 2
    log "export restored, state=$(cat /sys/class/udc/$UDC/state 2>/dev/null)"
}

# 1. 网页 U 盘管理模式占用中则跳过 (锁之外的第二道保险)
if grep -qs " $MNT " /proc/mounts; then
    log "skip: mirror mounted by viewer usb-manage"
    echo "SKIP-MANAGED"
    exit 0
fi

# 2. 空闲检测 (设备可能没有 stat, 用 ls --full-time 换算)
now=$(date +%s)
lsdate=$(ls -l --full-time "$IMG" 2>/dev/null | awk '{print $6, $7}')
mtime=$(date -d "$lsdate" +%s 2>/dev/null)
if [ -z "$mtime" ]; then
    log "WARN: cannot parse mtime ($lsdate), skip"
    echo "SKIP-MTIME"
    exit 0
fi
age=$((now - mtime))
if [ "$age" -lt "$IDLE" ]; then
    echo "busy (last write ${age}s ago), skip"
    exit 0
fi

log "=== sync start (idle ${age}s) ==="

# 3. 短暂停止导出
echo "" > "$GADGET/UDC"
sleep 1

# 4. 显式 losetup + 读写挂载 (音频要写)
mkdir -p "$MNT" "$AUDIO_Q" "$AUDIO_DONE"
LB=$($BB -f 2>/dev/null)
[ -z "$LB" ] && LB=/dev/loop3
if ! $BB "$LB" "$IMG" 2>>"$LOG"; then
    log "ERROR: losetup $LB failed"
    restore; exit 1
fi
if ! mount -t exfat "$LB" "$MNT" 2>>"$LOG"; then
    log "ERROR: mount image failed (镜像不是 exFAT?)"
    $BB -d "$LB" 2>/dev/null
    restore; exit 1
fi

# 5. 视频归档不再由本脚本负责, 只做一次存活提醒
ps w 2>/dev/null | grep -v grep | grep -q tesla-live-sync.py || \
    log "WARN: tesla-live-sync.py 未运行, 视频暂不归档 (请检查 /etc/init.d/tesla-live)"

# 6. 把待写入的音频直接写进车机U盘根目录
#    (上传的音频全部放根目录: LockChime.wav 即锁车音, 其它文件名原样放根目录)
pushed=0
has_audio=0
for f in "$AUDIO_Q"/*.wav "$AUDIO_Q"/*.mp3; do
    [ -f "$f" ] && has_audio=1 && break
done
if [ "$has_audio" = "1" ]; then
    for f in "$AUDIO_Q"/*.wav "$AUDIO_Q"/*.mp3; do
        [ -f "$f" ] || continue
        bn=$(basename "$f")
        dest="$MNT/$bn"
        if cp "$f" "$dest" 2>>"$LOG"; then
            mv "$f" "$AUDIO_DONE/" && pushed=$((pushed+1))
            log "audio -> ${dest#$MNT/}"
        else
            log "audio copy fail: $bn"
        fi
    done
    sync
    log "pushed $pushed audio -> USB root"
fi

# 7. 卸载并恢复导出
umount "$MNT" 2>>"$LOG"
$BB -d "$LB" 2>/dev/null
sleep 1
restore
echo "SYNC-DONE audio=$pushed"
