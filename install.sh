#!/bin/sh
# Tesla Sentry F50 - 一键安装脚本
#
# 用法 1 - 简易安装 (通用 Linux / OpenWrt):
#   sudo ./install.sh [选项]
#     --size GB            虚拟U盘容量, 默认 64
#     --port PORT          网页端口, 默认 8080
#     --video-root PATH    视频存储根目录 (固定结构: <根>/img/teslacam.img + <根>/archive/transcode_cache)
#                          默认检测 TF 卡 (/dev/mmcblk1p1), 挂载后放 <挂载点>/tesla-sentry;
#                          未检测到 TF 卡时回退 /archive
#     --dir DIR            程序安装目录, 默认 /opt/tesla-sentry
#     -h, --help           显示帮助
#
# 用法 2 - F50 完整安装 (调用 scripts/install-f50.py):
#   sudo ./install.sh f50 [install-f50.py 的参数...]
#   sudo ./install.sh f50-check          # 仅预检 (默认 --apply 关闭)
#   sudo ./install.sh f50-apply          # 预检 + 真正安装 (--apply)
#   sudo ./install.sh f50-install        # 预检 + 安装 + 服务启动 (--apply --start)
#   等价于: ./install.sh f50-install --data-mount /mnt/sdcard ...
#
# 旧 install-f50.sh 的逻辑已完全并入本脚本: 直接 exec 同一套 Python 安装器,
# 可执行离线 runtime (runtime/bin/python3) 或系统 python3; 独立入口文件已删除.

set -e

# ---- 公共工具函数 ----
say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

# ---- 子命令分发: f50 / f50-check / f50-apply / f50-install ----
# install-f50.py 的启动器; 原 install-f50.sh 逻辑已全部内嵌于此
_run_f50_installer() {
    local sub="$1"; shift
    local src py
    src=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
    py=""
    if [ -d "$src/runtime" ]; then
        case "$(uname -m)" in
            aarch64) py="$src/runtime/bin/python3" ;;
            *) die "离线运行环境仅支持 aarch64 架构的 F50" ;;
        esac
    fi
    if [ -z "$py" ]; then
        py=$(command -v python3 || true)
        [ -n "$py" ] || die "需要 Python 3.11+ (OpenWrt: opkg install python3 python3-pip)"
        "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
            || die "需要 Python 3.11 或更新版本"
    fi
    case "$sub" in
        f50-check)   : ;;                           # 仅预检
        f50-apply)   set -- --apply "$@" ;;
        f50-install) set -- --apply --start "$@" ;;
        f50)         : ;;                           # 用户自己传参数, 不追加
        *) die "未知子命令: $sub" ;;
    esac
    say "调用 scripts/install-f50.py (子命令: $sub)"
    exec "$py" -B "$src/scripts/install-f50.py" "$@"
}

case "${1:-}" in
    f50|f50-check|f50-apply|f50-install)
        sub_cmd="$1"; shift; _run_f50_installer "$sub_cmd" "$@" ;;
esac

# ---- 正常 install.sh 选项解析 (非子命令路径) ----
SIZE_GB=64
PORT=8080
VIDEO_ROOT=""
TSF_DIR=/opt/tesla-sentry

while [ $# -gt 0 ]; do
    case "$1" in
        --size)       SIZE_GB="$2"; shift 2 ;;
        --port)       PORT="$2"; shift 2 ;;
        --video-root) VIDEO_ROOT="$2"; shift 2 ;;
        --archive|--img) echo "[提示] $1 已废弃, 请改用 --video-root"; shift 2 ;;
        --dir)        TSF_DIR="$2"; shift 2 ;;
        -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
        *) echo "未知参数: $1 (用 --help 查看)"; exit 1 ;;
    esac
done

CONF=/etc/tesla-sentry.conf

# 视频存储根目录默认放到 TF 卡: F50 上 /dev/mmcblk1p1 一般挂载到 /mnt/sdcard
TF_MOUNT=""
if [ -r /proc/mounts ]; then
    TF_MOUNT=$(awk '$1 ~ /mmcblk1p1$/ {print $2; exit}' /proc/mounts 2>/dev/null || true)
fi
[ -z "$TF_MOUNT" ] && [ -d /mnt/sdcard ] && TF_MOUNT=/mnt/sdcard

if [ -z "$VIDEO_ROOT" ]; then
    if [ -n "$TF_MOUNT" ]; then
        VIDEO_ROOT="$TF_MOUNT/tesla-sentry"
        say "检测到 TF 卡 ($TF_MOUNT), 视频存储默认放在 $VIDEO_ROOT"
    else
        VIDEO_ROOT=/archive
        echo "  [提示] 未检测到 TF 卡 (/dev/mmcblk1p1)。建议把视频存储装到 TF 卡上;"
        echo "         F50 默认设备 /dev/mmcblk1p1, 挂载点 /mnt/sdcard。"
        echo "         可用 --video-root 指定根目录, 或挂载 TF 卡后重新运行。"
    fi
fi

# 从 VIDEO_ROOT 固定派生三个路径
IMG="$VIDEO_ROOT/img/teslacam.img"
ARCHIVE="$VIDEO_ROOT/archive"
TRANSCODE_DIR="$ARCHIVE/transcode_cache"

say "视频存储根目录: $VIDEO_ROOT"
say "  车机U盘镜像: $IMG"
say "  归档区:     $ARCHIVE"
say "  转码缓存:   $TRANSCODE_DIR"

# ---------------- 0. 权限与环境 ----------------
[ "$(id -u)" = "0" ] || die "请用 root 运行 (sudo ./install.sh)"
SRC_DIR=$(cd "$(dirname "$0")" && pwd)
[ -f "$SRC_DIR/sentry-viewer.py" ] || die "找不到 sentry-viewer.py, 请在项目根目录运行"

PYTHON=$(command -v python3 || true)
[ -n "$PYTHON" ] || die "需要 python3 (OpenWrt: opkg install python3-light)"

say "检查 USB Gadget 支持..."
GADGET=""
for d in /sys/kernel/config/usb_gadget/*/; do
    [ -d "$d/configs" ] && { GADGET="${d%/}"; break; }
done
[ -n "$GADGET" ] || die "未发现 USB gadget (configfs 未挂载或设备不支持 Device 模式)"
[ -n "$(ls /sys/class/udc/ 2>/dev/null | head -1)" ] || die "未发现 UDC 控制器"

if ! grep -qw exfat /proc/filesystems 2>/dev/null; then
    modprobe exfat 2>/dev/null || true
    grep -qw exfat /proc/filesystems 2>/dev/null || die "内核不支持 exFAT (需要 CONFIG_EXFAT_FS)"
fi

if [ -r /proc/config.gz ]; then
    zcat /proc/config.gz 2>/dev/null | grep -q "CONFIG_USB_F_MASS_STORAGE=y" \
        || echo "  [警告] 内核配置未见 CONFIG_USB_F_MASS_STORAGE, 仍会尝试 (见 README 硬件要求)"
fi

FREE_MB=$(df -Pm "$(dirname "$TSF_DIR")" 2>/dev/null | awk 'NR==2{print $4}')
TOTAL_MB=$(df -Pm "$(dirname "$TSF_DIR")" 2>/dev/null | awk 'NR==2{print $2}')
[ -z "$FREE_MB" ] && FREE_MB="?"
say "存储空闲: ${FREE_MB}MB, 计划镜像 ${SIZE_GB}G (稀疏文件, 按实际写入占用)"

# 镜像最多占存储卡实际容量的一半: 归档区要与车机内容一致(车机录像会复制过来),
# 还要放转码缓存、铃声、涂装等; 超过一半时归档区很容易被挤爆。
if [ -n "$TOTAL_MB" ] && [ "$TOTAL_MB" != "?" ] && [ $((SIZE_GB * 1024)) -gt $((TOTAL_MB / 2)) ]; then
    echo "  [警告] 镜像 ${SIZE_GB}G 超过存储卡实际容量的一半 ($((TOTAL_MB / 2))MB)。"
    echo "         归档区要与车机内容保持一致，还要存放转码缓存、铃声、涂装等，建议:"
    echo "           sudo ./install.sh --size $((TOTAL_MB / 2048))"
fi

# 容量余量: 镜像是稀疏文件, 预置文件系统后初始只占几十 KB; 但车机持续录像会
# 逐步把它写满, 镜像 + 归档 + 系统必须留出余量, 否则磁盘写满后块设备返回
# I/O 错误, 车机会把 U 盘判为「读不出/需要格式化」。
# 经验值: 64G 镜像在哨兵 4 路约 8.4G/小时下可滚动录制 7 小时以上, 足够日常使用。
if [ "$FREE_MB" != "?" ]; then
    NEED_MB=$((SIZE_GB * 1024 + 20480))    # 镜像满容量 + 至少 20G 归档/系统余量
    if [ "$FREE_MB" -lt "$NEED_MB" ]; then
        echo "  [警告] 空闲 ${FREE_MB}MB 可能放不下 ${SIZE_GB}G 镜像+归档余量(约 ${NEED_MB}MB)"
        echo "         车机录像会逐步写满镜像; 空间紧张请降低容量, 例如:"
        echo "           sudo ./install.sh --size 32"
    fi
fi

# 程序缓存 (缩略图/遥测/合成临时文件): 放在安装目录 cache/ 下, 不占数据卡
CACHE_DIR="$TSF_DIR/cache"
mkdir -p "$CACHE_DIR/thumbs" "$CACHE_DIR/tel-cache" "$CACHE_DIR/stitch-tmp" 2>/dev/null || true

# ffmpeg 探测: 缩略图与部分导出功能依赖。F50 是 aarch64/OpenWrt, 推荐 apk add ffmpeg,
# 生成的配置固定写 /usr/bin, 不再优先使用来源不明的 aarch64 静态包。
if [ -x /usr/bin/ffmpeg ] && [ -x /usr/bin/ffprobe ]; then
    FFMPEG=/usr/bin/ffmpeg
    FFPROBE=/usr/bin/ffprobe
else
    FFMPEG="$(command -v ffmpeg 2>/dev/null || echo /usr/bin/ffmpeg)"
    FFPROBE="$(command -v ffprobe 2>/dev/null || echo /usr/bin/ffprobe)"
fi
if [ -x "$FFMPEG" ]; then
    say "ffmpeg: $FFMPEG"
    # 精简构建 (OpenWrt mini) 缺 libx264 和 HEVC 解码: 网页里表现为抽帧失败、
    # 转码报 Option not found。装完就提前提示, 不要等用户去"运行监测"里发现。
    if ! "$FFMPEG" -hide_banner -encoders 2>/dev/null | grep -q libx264 \
       || ! "$FFMPEG" -hide_banner -decoders 2>/dev/null | grep -q hevc; then
        echo "  [警告] 当前 ffmpeg 是精简构建：缺少 libx264 或 HEVC 解码, 缩略图/转码会失败。"
        echo "         修复: sh install.sh f50-apply --install-deps  (会自动补装完整版或官方静态构建)"
    fi
else echo "  [提示] 未找到 ffmpeg, 列表缩略图会缺失 (F50 执行 apk add ffmpeg 即可)"; fi

# ---------------- 1. 目录与文件 ----------------
say "安装到 $TSF_DIR ..."
mkdir -p "$TSF_DIR"
cp "$SRC_DIR/sentry-viewer.py"      "$TSF_DIR/sentry-viewer.py"
cp "$SRC_DIR/tesla-live-sync.py"    "$TSF_DIR/tesla-live-sync.py"
cp "$SRC_DIR/storage_allocation.py" "$TSF_DIR/storage_allocation.py"
cp "$SRC_DIR/storage_resize.py"     "$TSF_DIR/storage_resize.py"
cp "$SRC_DIR/scripts/tesla-gadget.sh" "$TSF_DIR/tesla-gadget.sh"
cp "$SRC_DIR/scripts/tesla-usb-start.sh" "$TSF_DIR/tesla-usb-start.sh"
cp "$SRC_DIR/scripts/tesla-sync.sh"   "$TSF_DIR/tesla-sync.sh"
cp "$SRC_DIR/scripts/usb-selfheal.sh" "$TSF_DIR/usb-selfheal.sh"
cp "$SRC_DIR/scripts/tesla-img.sh"    "$TSF_DIR/tesla-img.sh"
cp "$SRC_DIR/scripts/tsf-check.sh"    "$TSF_DIR/tsf-check.sh"
cp "$SRC_DIR/scripts/tsf-storage.sh"  "$TSF_DIR/tsf-storage.sh"
cp "$SRC_DIR/scripts/tsf-maintenance.sh" "$TSF_DIR/tsf-maintenance.sh"
cp "$SRC_DIR/scripts/mkexfat.py"     "$TSF_DIR/mkexfat.py"
chmod +x "$TSF_DIR"/*.sh "$TSF_DIR"/*.py

mkdir -p "$VIDEO_ROOT"/img "$TRANSCODE_DIR" \
         "$ARCHIVE"/SentryClips "$ARCHIVE"/SavedClips "$ARCHIVE"/RecentClips "$ARCHIVE"/DriveClips \
         "$ARCHIVE"/.skins "$ARCHIVE"/boombox-queue "$ARCHIVE"/boombox-done "$ARCHIVE"/files

# ---------------- 2. 镜像 ----------------
# 已经在跑的设备: 复用当前正在导出的镜像, 避免重装后车机看到一块空盘
CUR_EXPORT=""
for f in "$GADGET"/functions/mass_storage*/lun.0/file; do
    [ -f "$f" ] || continue
    CUR_EXPORT=$(cat "$f" 2>/dev/null)
    [ -n "$CUR_EXPORT" ] && break
done
if [ -n "$CUR_EXPORT" ] && [ -f "$CUR_EXPORT" ]; then
    IMG="$CUR_EXPORT"
    say "检测到已在导出的镜像, 沿用它: $IMG"
fi

# 新部署: 直接生成完整的 exFAT 文件系统 + TeslaCam 目录, 车机插上就能
# 识别容量并开始录制, 不再弹「格式化 USB 设备」。
# 旧版只建了全零空盘, 这里检测到没有 exFAT 签名也会补写一次 (安全: 镜像里
# 已有任何数据时 mkexfat.py 会拒绝覆盖)。
if [ -f "$IMG" ]; then
    MAGIC=$(dd if="$IMG" bs=1 skip=3 count=5 2>/dev/null)
    if [ "$MAGIC" = "EXFAT" ]; then
        say "镜像已存在且有 exFAT, 跳过: $IMG"
    else
        say "镜像没有文件系统 (旧版空盘), 补写 exFAT + TeslaCam: $IMG"
        "$PYTHON" "$SRC_DIR/scripts/mkexfat.py" "$IMG" "$SIZE_GB"
        sync
    fi
else
    say "创建 ${SIZE_GB}G 稀疏镜像并预置 exFAT + TeslaCam 目录 ..."
    "$PYTHON" "$SRC_DIR/scripts/mkexfat.py" "$IMG" "$SIZE_GB"
    sync
fi
ls -lh "$IMG"

# ---------------- 3. 配置 ----------------
say "写入配置 $CONF ..."

# ---- 3.1 升级场景: 备份旧 conf + 迁移旧值 ----
# 如果旧 conf 存在, 先备份一份用于回滚 (F50 安装器和其他升级路径也这么做)
# 只保留一份备份 ${CONF}.bak, 历史带时间戳的备份顺手清掉, 避免占满 /etc
CONF_BAK=""
if [ -f "$CONF" ]; then
    CONF_BAK="${CONF}.bak"
    cp "$CONF" "$CONF_BAK"
    for _old_bak in "${CONF}".bak.* "${CONF}".bak-????????-??????; do
        [ -f "$_old_bak" ] && rm -f "$_old_bak"
    done
    say "旧配置已备份 -> $CONF_BAK  (可直接 cp 回去回滚)"
    # 解析旧 conf 的 TSF_* 值到当前 shell
    set -a
    . "$CONF" 2>/dev/null || true
    set +a
fi

# 旧版迁移: TSF_TRANSCODE_DIR 旧形态是 /.transcode, 2.8.48 起固定 transcode_cache
if [ -n "$TSF_TRANSCODE_DIR" ]; then
    case "$TSF_TRANSCODE_DIR" in
        */.transcode)
            NEW_TC="${TSF_TRANSCODE_DIR%/.transcode}/transcode_cache"
            say "迁移 TSF_TRANSCODE_DIR: $TSF_TRANSCODE_DIR -> $NEW_TC"
            TSF_TRANSCODE_DIR="$NEW_TC"
            ;;
    esac
fi

# 旧版迁移: TSF_CACHE 早期默认指向 /opt/mu300, 该目录已废弃; 清空后改用安装目录下的 cache
case "${TSF_CACHE:-}" in
    /opt/mu300|/opt/mu300/|/opt/mu300/cache)
        say "迁移 TSF_CACHE: $TSF_CACHE -> $CACHE_DIR"
        TSF_CACHE=""
        ;;
esac
# 尝试从旧分散键 TSF_ARCHIVE 反推一个合理的 VIDEO_ROOT
if [ -z "$VIDEO_ROOT" ] && [ -z "$TSF_VIDEO_ROOT" ] && [ -n "$TSF_ARCHIVE" ]; then
    VIDEO_ROOT=$(dirname "$TSF_ARCHIVE")
    say "从旧 TSF_ARCHIVE 推导 VIDEO_ROOT = $VIDEO_ROOT"
fi
[ -z "$VIDEO_ROOT" ] && VIDEO_ROOT="$TSF_VIDEO_ROOT"

# ---- 3.2 合并策略 ----
# 视频路径 (VIDEO_ROOT / IMG / ARCHIVE / TRANSCODE_DIR):
#   - 如果 install 命令行显式传了 --video-root → 用新派生值
#   - 否则用旧 conf 里的值 (保留用户已有存储位置)
# 其他参数 (PORT / IDLE / GADGET / FFMPEG 等):
#   - 旧 conf 里已有 → 继承 (用户改过的值不被覆盖)
#   - 没有 → 用 install 默认值
[ -z "$VIDEO_ROOT" ]        && VIDEO_ROOT="${TSF_VIDEO_ROOT:-}"
[ -z "$IMG" ]               && IMG="${TSF_IMG:-}"
[ -z "$ARCHIVE" ]           && ARCHIVE="${TSF_ARCHIVE:-}"
[ -z "$TRANSCODE_DIR" ]     && TRANSCODE_DIR="${TSF_TRANSCODE_DIR:-}"
# 如果只拿到了部分旧键, 补全:
[ -z "$VIDEO_ROOT" ] && [ -n "$ARCHIVE" ] && VIDEO_ROOT=$(dirname "$ARCHIVE")
[ -z "$IMG" ]        && [ -n "$VIDEO_ROOT" ] && IMG="$VIDEO_ROOT/img/teslacam.img"
[ -z "$ARCHIVE" ]    && [ -n "$VIDEO_ROOT" ] && ARCHIVE="$VIDEO_ROOT/archive"
[ -z "$TRANSCODE_DIR" ] && [ -n "$ARCHIVE" ] && TRANSCODE_DIR="$ARCHIVE/transcode_cache"

[ -z "$TSF_VIDEO_ROOT" ]    && TSF_VIDEO_ROOT="$VIDEO_ROOT"
[ -z "$TSF_IMG" ]           && TSF_IMG="$IMG"
[ -z "$TSF_ARCHIVE" ]       && TSF_ARCHIVE="$ARCHIVE"
[ -z "$TSF_TRANSCODE_DIR" ] && TSF_TRANSCODE_DIR="$TRANSCODE_DIR"

# 其他参数: 旧 conf 有值就继承, 没有就用 install 默认
[ -z "$TSF_PORT" ]          && TSF_PORT="$PORT"
[ -z "$TSF_DIR" ]           && TSF_DIR="$TSF_DIR"
[ -z "$TSF_IDLE" ]          && TSF_IDLE=600
[ -z "$TSF_USB_TIMEOUT" ]   && TSF_USB_TIMEOUT=300
[ -z "$TSF_LOOP" ]          && TSF_LOOP=/dev/loop3
[ -z "$TSF_MNT" ]           && TSF_MNT=/tmp/teslamnt
[ -z "$TSF_LOSETUP" ]       && TSF_LOSETUP=
[ -z "$TSF_GADGET" ]        && TSF_GADGET="$GADGET"
[ -z "$TSF_CACHE" ]         && TSF_CACHE="$CACHE_DIR"
[ -z "$TSF_FFMPEG" ]        && TSF_FFMPEG="$FFMPEG"
[ -z "$TSF_FFPROBE" ]       && TSF_FFPROBE="$FFPROBE"

# ---- 3.3 原子写入 ----
TMP_CONF="${CONF}.tmp.$$"
cat > "$TMP_CONF" <<EOF
# Tesla Sentry F50 配置 (由 install.sh 生成)
# $(date -Iseconds)
$( [ -n "$CONF_BAK" ] && echo "# 升级自: $CONF_BAK" || true )
TSF_DIR=$TSF_DIR
TSF_VIDEO_ROOT=$VIDEO_ROOT
TSF_IMG=$IMG
TSF_ARCHIVE=$ARCHIVE
TSF_MNT=${TSF_MNT:-/tmp/teslamnt}
TSF_LOOP=${TSF_LOOP:-/dev/loop3}
TSF_PORT=${TSF_PORT:-8080}
TSF_IDLE=${TSF_IDLE:-600}
TSF_USB_TIMEOUT=${TSF_USB_TIMEOUT:-300}
TSF_LOSETUP=${TSF_LOSETUP:-}
TSF_GADGET=${TSF_GADGET:-$GADGET}
TSF_CACHE=${TSF_CACHE:-$CACHE_DIR}
TSF_TRANSCODE_DIR=${TSF_TRANSCODE_DIR:-$TRANSCODE_DIR}
TSF_FFMPEG=${TSF_FFMPEG:-$FFMPEG}
TSF_FFPROBE=${TSF_FFPROBE:-$FFPROBE}
TSF_USB_LOCK=${TSF_USB_LOCK:-/tmp/tsf-usb.lock}

# ---- 归档清理 / 在线同步参数 (改完重启服务生效; 0 = 关闭对应策略) ----
# 这些参数以前没写进 conf, OpenWrt 启动脚本又只导出固定变量,
# 导致在 F50 上照 README 改清理窗口/保留天数完全不生效。
TSF_RECENT_ROLL_MIN=${TSF_RECENT_ROLL_MIN:-60}
TSF_RECENT_KEEP_MIN=${TSF_RECENT_KEEP_MIN:-60}
TSF_RECENT_MAX_GB=${TSF_RECENT_MAX_GB:-12}
TSF_BOX_KEEP_DAYS=${TSF_BOX_KEEP_DAYS:-14}
TSF_FREE_MIN_MB=${TSF_FREE_MIN_MB:-4096}
TSF_FREE_TARGET_MB=${TSF_FREE_TARGET_MB:-10240}
TSF_LIVE_INTERVAL=${TSF_LIVE_INTERVAL:-15}
TSF_PREWARM_SENTRY=${TSF_PREWARM_SENTRY:-3}
TSF_PREWARM_SAVED=${TSF_PREWARM_SAVED:-3}
EOF
mv "$TMP_CONF" "$CONF"
chmod 644 "$CONF"
[ -n "$CONF_BAK" ] && say "升级完成. 如遇问题可直接回滚: cp $CONF_BAK $CONF"

# ---------------- 3.5 访问认证 ----------------
AUTH_CONF="$TSF_DIR/auth.conf"
if [ -f "$AUTH_CONF" ]; then
    say "认证配置已存在, 保持不动: $AUTH_CONF"
else
    AUTH_PASS=$(head -c 96 /dev/urandom | tr -dc 'a-z2-9' | head -c 10)
    cat > "$AUTH_CONF" <<EOF
# 网页登录凭据 (改完执行 /etc/init.d/tesla-viewer restart 生效)
# 删掉本文件 = 关闭认证, 只有内网使用时才建议这么干
user=admin
pass=$AUTH_PASS
ttl=2592000
EOF
    chmod 600 "$AUTH_CONF"
    say "已生成网页登录账号 -> admin / $AUTH_PASS   (已写入 $AUTH_CONF, 请记好)"
fi

# ---------------- 4. 服务 ----------------
if [ -f /etc/rc.common ]; then
    say "安装 OpenWrt procd 服务 ..."
    cat > /etc/init.d/tesla-gadget <<'EOF'
#!/bin/sh /etc/rc.common
START=99
USE_PROCD=1
start_service() {
    set -a; [ -f /etc/tesla-sentry.conf ] && . /etc/tesla-sentry.conf; set +a
    if [ -f /etc/mu300/usb-storage-boot ] && [ -x "$TSF_DIR/tesla-usb-start.sh" ]; then
        setsid /bin/sh "$TSF_DIR/tesla-usb-start.sh" </dev/null >/dev/null 2>&1 &
    else
        setsid /bin/sh "$TSF_DIR/tesla-gadget.sh" on </dev/null >/dev/null 2>&1 &
    fi
}
stop_service() {
    set -a; [ -f /etc/tesla-sentry.conf ] && . /etc/tesla-sentry.conf; set +a
    setsid /bin/sh "$TSF_DIR/tesla-gadget.sh" off </dev/null >/dev/null 2>&1 &
}
EOF
    cat > /etc/init.d/tesla-viewer <<EOF
#!/bin/sh /etc/rc.common
START=99
USE_PROCD=1
start_service() {
    # set -a 兼容会继承 shell 环境的 init; procd 不继承 export,
    # 程序启动时会自己读 conf 补齐, 因此自定义清理/同步参数在 F50 上也能生效。
    set -a; [ -f $CONF ] && . $CONF; set +a
    procd_open_instance
    procd_set_param command $PYTHON "\$TSF_DIR/sentry-viewer.py"
    procd_set_param respawn 3600 5 0
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}
EOF
    # 旧版服务名统一成 tesla-viewer, 避免两个 procd 实例抢 8080
    if [ -f /etc/init.d/sentry-viewer ]; then
        /etc/init.d/sentry-viewer disable 2>/dev/null || true
        rm -f /etc/init.d/sentry-viewer
    fi
    chmod +x /etc/init.d/tesla-gadget /etc/init.d/tesla-viewer
    /etc/init.d/tesla-gadget enable
    /etc/init.d/tesla-viewer enable

    # 车机在线只读同步 (不挂载镜像, 车机无感, 新片段秒级进网页)
    cat > /etc/init.d/tesla-live <<EOF
#!/bin/sh /etc/rc.common
START=99
USE_PROCD=1
start_service() {
    set -a; [ -f $CONF ] && . $CONF; set +a
    procd_open_instance
    procd_set_param command $PYTHON "\$TSF_DIR/tesla-live-sync.py"
    procd_set_param respawn 3600 5 0
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}
EOF
    chmod +x /etc/init.d/tesla-live
    /etc/init.d/tesla-live enable

    # 归档定时任务 + U盘自愈(主机 eject 后自动重新导出)
    mkdir -p /etc/crontabs
    touch /etc/crontabs/root
    sed -i '/tesla-sync.sh/d;/usb-selfheal.sh/d' /etc/crontabs/root 2>/dev/null || true
    echo "*/10 * * * * /bin/sh $TSF_DIR/tesla-sync.sh >/dev/null 2>&1" >> /etc/crontabs/root
    echo "*/2 * * * * /bin/sh $TSF_DIR/usb-selfheal.sh >/dev/null 2>&1" >> /etc/crontabs/root
    /etc/init.d/cron enable 2>/dev/null || true
    /etc/init.d/cron restart 2>/dev/null || true

elif [ -d /etc/systemd/system ]; then
    say "安装 systemd 服务 ..."
    cat > /etc/systemd/system/tesla-gadget.service <<EOF
[Unit]
Description=Tesla Sentry virtual USB drive
After=local-fs.target
[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile=-$CONF
ExecStart=/bin/sh $TSF_DIR/tesla-gadget.sh on
ExecStop=/bin/sh $TSF_DIR/tesla-gadget.sh off
[Install]
WantedBy=multi-user.target
EOF
    cat > /etc/systemd/system/sentry-viewer.service <<EOF
[Unit]
Description=Tesla Sentry web viewer
After=network.target tesla-gadget.service
[Service]
Type=simple
EnvironmentFile=-$CONF
ExecStart=$PYTHON $TSF_DIR/sentry-viewer.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
    cat > /etc/systemd/system/tesla-live.service <<EOF
[Unit]
Description=Tesla Sentry live read-only sync (no mount)
After=tesla-gadget.service
[Service]
Type=simple
EnvironmentFile=-$CONF
ExecStart=$PYTHON $TSF_DIR/tesla-live-sync.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
    mkdir -p /etc/cron.d
    cat > /etc/cron.d/tesla-sentry <<EOF
*/10 * * * * root /bin/sh $TSF_DIR/tesla-sync.sh >/dev/null 2>&1
*/2 * * * * root /bin/sh $TSF_DIR/usb-selfheal.sh >/dev/null 2>&1
EOF
    systemctl daemon-reload
    systemctl enable tesla-gadget.service sentry-viewer.service tesla-live.service >/dev/null 2>&1
else
    # 通用分支: OpenRC / BusyBox init / runit / s6 等, 用 rc.local 拉起 + crontab 定时
    say "未识别的 init 系统, 安装为通用启动脚本 (rc.local + crontab) ..."
    cat > "$TSF_DIR/tsf-start.sh" <<EOF
#!/bin/sh
# 通用启动入口 (rc.local / 手动 / supervisor 均可调用)
set -a; [ -f $CONF ] && . $CONF; set +a
sh "\$TSF_DIR/tesla-gadget.sh" on >/dev/null 2>&1
pgrep -f tesla-live-sync.py >/dev/null 2>&1 || setsid $PYTHON "\$TSF_DIR/tesla-live-sync.py" </dev/null >>"\$TSF_DIR/tesla-live.log" 2>&1 &
sleep 1
pgrep -f sentry-viewer.py >/dev/null 2>&1 || setsid $PYTHON "\$TSF_DIR/sentry-viewer.py" </dev/null >>"\$TSF_DIR/sentry-viewer.log" 2>&1 &
EOF
    chmod +x "$TSF_DIR/tsf-start.sh"

    # rc.local
    if [ -f /etc/rc.local ]; then
        grep -q 'tsf-start.sh' /etc/rc.local || \
            sed -i "/^exit 0/i $TSF_DIR/tsf-start.sh" /etc/rc.local
    else
        cat > /etc/rc.local <<EOF
#!/bin/sh -e
$TSF_DIR/tsf-start.sh
exit 0
EOF
        chmod +x /etc/rc.local
    fi

    # 定时任务: BusyBox crontab (/etc/crontabs/root) 优先, 否则系统 /etc/crontab
    if [ -d /etc/crontabs ]; then
        CRONF=/etc/crontabs/root
        touch "$CRONF"
        sed -i '/tesla-sync.sh/d;/usb-selfheal.sh/d' "$CRONF" 2>/dev/null || true
        echo "*/10 * * * * /bin/sh $TSF_DIR/tesla-sync.sh >/dev/null 2>&1" >> "$CRONF"
        echo "*/2 * * * * /bin/sh $TSF_DIR/usb-selfheal.sh >/dev/null 2>&1" >> "$CRONF"
        (rc-service cron restart || /etc/init.d/cron restart) >/dev/null 2>&1 || true
    elif [ -f /etc/crontab ]; then
        sed -i '/tesla-sync.sh/d;/usb-selfheal.sh/d' /etc/crontab 2>/dev/null || true
        echo "*/10 * * * * root /bin/sh $TSF_DIR/tesla-sync.sh >/dev/null 2>&1" >> /etc/crontab
        echo "*/2 * * * * root /bin/sh $TSF_DIR/usb-selfheal.sh >/dev/null 2>&1" >> /etc/crontab
        (rc-service crond restart || service cron restart) >/dev/null 2>&1 || true
    else
        echo "  [提示] 没有找到 cron, 请自行把下面两行加到定时任务:"
        echo "    */10 * * * * /bin/sh $TSF_DIR/tesla-sync.sh"
        echo "    */2  * * * * /bin/sh $TSF_DIR/usb-selfheal.sh"
    fi
    echo "  [提示] 已写入 /etc/rc.local 与 crontab; 若系统用 OpenRC/runit," \
         "可把 $TSF_DIR/tsf-start.sh 包一层 service 以获得开机自启与守护"
fi

# ---------------- 5. 启动 ----------------
say "启动虚拟U盘 ..."
if [ -f /etc/rc.common ] && [ -f /etc/mu300/usb-storage-boot ] && [ -x "$TSF_DIR/tesla-usb-start.sh" ]; then
    sh "$TSF_DIR/tesla-usb-start.sh" || echo "  [警告] 虚拟U盘启动失败, 见 $TSF_DIR/tesla-gadget.log"
else
    sh "$TSF_DIR/tesla-gadget.sh" on || echo "  [警告] 虚拟U盘启动失败, 见 $TSF_DIR/tesla-gadget.log"
fi

# 中文字体: 设备端烧录行车信息水印时由 libass 使用。
# 没有字体时导出会明确提示"缺少中文字体"(也可以在导出面板点「安装字体」补), 不影响其它功能。
if ! ls "$TSF_DIR"/fonts/*.ttf "$TSF_DIR"/fonts/*.otf >/dev/null 2>&1; then
    say "下载中文字体 (水印用, 约 17MB) ..."
    mkdir -p "$TSF_DIR/fonts"
    FT="$TSF_DIR/fonts/NotoSansSC.ttf"
    for U in \
        "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf" \
        "https://gcore.jsdelivr.net/gh/google/fonts@main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf" \
        "https://ghfast.top/https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf" \
        "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf"
    do
        curl -sL --max-time 600 -o "$FT.part" "$U" 2>/dev/null || continue
        SZ=$(wc -c < "$FT.part" 2>/dev/null || echo 0)
        if [ "$SZ" -gt 2000000 ]; then
            mv -f "$FT.part" "$FT"
            echo "  [OK] 字体已安装 ($((SZ/1048576))MB)"
            break
        fi
        rm -f "$FT.part"
    done
    [ -f "$FT" ] || echo "  [提示] 字体下载失败, 不影响其它功能; 进网页 → 导出面板 → 点「安装字体」即可补上"
fi

say "启动查看器 ..."
if [ -f /etc/rc.common ]; then
    /etc/init.d/tesla-viewer restart >/dev/null 2>&1 || true
elif [ -d /etc/systemd/system ]; then
    systemctl restart sentry-viewer.service || true
fi

say "启动实时同步 (车机无感读取) ..."
if [ -f /etc/rc.common ]; then
    /etc/init.d/tesla-live restart >/dev/null 2>&1 || true
elif [ -d /etc/systemd/system ]; then
    systemctl restart tesla-live.service || true
else
    sh "$TSF_DIR/tsf-start.sh"    # 通用分支: 一次拉起全部
fi
sleep 2

IP=$(ip route 2>/dev/null | awk '/default/{print $3; exit}')
say "完成!"
echo
echo "  虚拟U盘镜像 : $IMG ($(du -sh "$IMG" 2>/dev/null | cut -f1) 实际占用)"
echo "  归档区      : $ARCHIVE"
echo "  查看器      : http://<设备IP>:$PORT"
[ -n "$IP" ] && echo "  当前网关    : $IP"
echo
echo "  下一步: 把设备插到车上 -> 车机提示格式化USB -> 确认 -> 哨兵正常录制"
echo "  状态检查: sh $TSF_DIR/tesla-gadget.sh status"
echo "  归档日志: $TSF_DIR/tesla-sync.log"
