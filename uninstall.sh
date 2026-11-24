#!/bin/sh
# Tesla Sentry F50 - 卸载脚本
# 用法: sudo ./uninstall.sh [--purge]
#   默认   停服务 / 恢复 USB 导出 / 删除程序与缓存 / 保留镜像和归档录像数据
#   --purge 连稀疏镜像、归档区一起删除 (不可恢复!)
set -e

PURGE=0
[ "$1" = "--purge" ] && PURGE=1

[ "$(id -u)" = "0" ] || { echo "请用 root 运行"; exit 1; }
. /etc/tesla-sentry.conf 2>/dev/null || true
: "${TSF_DIR:=/opt/tesla-sentry}"
# 2.8.48+: 优先 VIDEO_ROOT, 否则从 IMG/ARCHIVE 反推
if [ -z "$TSF_VIDEO_ROOT" ]; then
    if [ -n "$TSF_ARCHIVE" ]; then
        TSF_VIDEO_ROOT=$(dirname "$TSF_ARCHIVE")
    elif [ -n "$TSF_IMG" ]; then
        TSF_VIDEO_ROOT=$(dirname "$(dirname "$TSF_IMG")")
    fi
fi
: "${TSF_VIDEO_ROOT:=/mnt/sdcard/tesla-sentry}"
: "${TSF_IMG:=$TSF_VIDEO_ROOT/img/teslacam.img}"
: "${TSF_ARCHIVE:=$TSF_VIDEO_ROOT/archive}"
: "${TSF_MNT:=/tmp/teslamnt}"
: "${TSF_CACHE:=$TSF_DIR/cache}"

echo "==> 停止 F50 OpenWrt procd 服务"
# 含旧版 sentry-viewer 兼容名
for svc in tesla-gadget tesla-viewer tesla-live sentry-viewer; do
    if [ -x "/etc/init.d/$svc" ]; then
        "/etc/init.d/$svc" stop 2>/dev/null || true
        "/etc/init.d/$svc" disable 2>/dev/null || true
    fi
done

echo "==> 恢复 USB 导出 (移除 mass_storage)"
# 停掉服务后手工收尾: 卸载残留挂载 + 删除函数 + 重新绑定
if [ -f "$TSF_DIR/tesla-gadget.sh" ]; then
    sh "$TSF_DIR/tesla-gadget.sh" off 2>/dev/null || true
fi
[ -n "$TSF_MNT" ] && umount "$TSF_MNT" 2>/dev/null || true

echo "==> 清理开机自启与定时任务"
rm -f /etc/init.d/tesla-gadget /etc/init.d/tesla-viewer /etc/init.d/tesla-live /etc/init.d/sentry-viewer
for f in /etc/crontabs/root /etc/crontab; do
    [ -f "$f" ] && sed -i '/tesla-sync.sh/d;/usb-selfheal.sh/d' "$f" 2>/dev/null || true
done
[ -x /etc/init.d/cron ] && /etc/init.d/cron restart >/dev/null 2>&1 || true

echo "==> 删除程序文件与缓存"
# install.sh 实际落盘的全部程序文件 (以前只删 3 个, 会留下 live-sync / 自愈 / 字体等)
rm -f "$TSF_DIR/sentry-viewer.py" "$TSF_DIR/tesla-live-sync.py" \
      "$TSF_DIR/tesla-gadget.sh" "$TSF_DIR/tesla-sync.sh" \
      "$TSF_DIR/usb-selfheal.sh" "$TSF_DIR/tesla-img.sh" \
      "$TSF_DIR/tsf-check.sh" "$TSF_DIR/tsf-start.sh" \
      "$TSF_DIR/uninstall.sh" "$TSF_DIR/auth.conf" \
      "$TSF_DIR/sentry-viewer.bak.py" "$TSF_DIR"/*.log
rm -rf "$TSF_DIR/fonts" "$TSF_CACHE/thumbs" "$TSF_CACHE/tel-cache" \
       "$TSF_CACHE/stitch-tmp"
rm -f /etc/tesla-sentry.conf

if [ "$PURGE" = "1" ]; then
    echo "==> [--purge] 删除镜像与归档录像 (不可恢复)"
    # 防呆: 配置缺失或被改坏时不要误删根目录
    case "$TSF_IMG" in ""|"/") echo "    镜像路径异常, 跳过";; *) rm -f "$TSF_IMG" || true;; esac
    case "$TSF_ARCHIVE" in ""|"/") echo "    归档路径异常, 跳过";; *) rm -rf "$TSF_ARCHIVE" || true;; esac
    rmdir "$TSF_DIR" 2>/dev/null || true
    # 共享缓存目录(如 /opt/mu300)只清本程序建的两个子目录, 不动目录本身
    case "$TSF_CACHE" in "$TSF_DIR"/*) rmdir "$TSF_CACHE" 2>/dev/null || true;; esac
else
    echo "==> 保留录像数据: $TSF_IMG , $TSF_ARCHIVE"
    echo "    需要彻底清除请加 --purge"
    echo "    注意: 网页登录凭据 auth.conf 已删除, 重装会生成新密码"
fi

echo "==> 卸载完成"
