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
: "${TSF_IMG:=$TSF_DIR/teslacam.img}"
: "${TSF_ARCHIVE:=/archive}"
: "${TSF_MNT:=/tmp/teslamnt}"
: "${TSF_CACHE:=$TSF_DIR/cache}"

echo "==> 停止服务 (兼容 procd / systemd / rc.local 三种安装方式)"
# 1) OpenWrt procd 启动脚本 (含旧版 sentry-viewer 兼容名)
for svc in tesla-gadget tesla-viewer tesla-live sentry-viewer; do
    if [ -x "/etc/init.d/$svc" ]; then
        "/etc/init.d/$svc" stop 2>/dev/null || true
        "/etc/init.d/$svc" disable 2>/dev/null || true
    fi
done
# 2) systemd 单元 (tesla-live 以前漏停/漏删, 会残留后台同步进程)
if command -v systemctl >/dev/null 2>&1; then
    for svc in tesla-gadget sentry-viewer tesla-live; do
        systemctl disable --now "$svc.service" 2>/dev/null || true
    done
fi
# 3) 通用分支是 rc.local + setsid 直接拉起, 只能按进程名收掉
for pat in sentry-viewer.py tesla-live-sync.py tesla-sync.sh usb-selfheal.sh; do
    pkill -f "$pat" 2>/dev/null || true
done

echo "==> 恢复 USB 导出 (移除 mass_storage)"
# 停掉服务后手工收尾: 卸载残留挂载 + 删除函数 + 重新绑定
if [ -f "$TSF_DIR/tesla-gadget.sh" ]; then
    sh "$TSF_DIR/tesla-gadget.sh" off 2>/dev/null || true
fi
[ -n "$TSF_MNT" ] && umount "$TSF_MNT" 2>/dev/null || true

echo "==> 清理开机自启与定时任务"
rm -f /etc/init.d/tesla-gadget /etc/init.d/tesla-viewer /etc/init.d/tesla-live /etc/init.d/sentry-viewer
rm -f /etc/systemd/system/tesla-gadget.service /etc/systemd/system/sentry-viewer.service \
      /etc/systemd/system/tesla-live.service
rm -f /etc/cron.d/tesla-sentry
# procd 用 /etc/crontabs/root, 通用分支可能用 /etc/crontab
for f in /etc/crontabs/root /etc/crontab; do
    [ -f "$f" ] && sed -i '/tesla-sync.sh/d;/usb-selfheal.sh/d;/tsf-start.sh/d' "$f" 2>/dev/null || true
done
[ -f /etc/rc.local ] && sed -i '/tsf-start.sh/d' /etc/rc.local 2>/dev/null || true
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload 2>/dev/null || true
    systemctl restart cron 2>/dev/null || true
fi
[ -x /etc/init.d/cron ] && /etc/init.d/cron restart >/dev/null 2>&1 || true

echo "==> 删除程序文件与缓存"
# install.sh 实际落盘的全部程序文件 (以前只删 3 个, 会留下 live-sync / 自愈 / 字体等)
rm -f "$TSF_DIR/sentry-viewer.py" "$TSF_DIR/tesla-live-sync.py" \
      "$TSF_DIR/tesla-gadget.sh" "$TSF_DIR/tesla-sync.sh" \
      "$TSF_DIR/usb-selfheal.sh" "$TSF_DIR/tesla-img.sh" \
      "$TSF_DIR/tsf-check.sh" "$TSF_DIR/tsf-start.sh" \
      "$TSF_DIR/uninstall.sh" "$TSF_DIR/auth.conf" \
      "$TSF_DIR/sentry-viewer.bak.py" "$TSF_DIR"/*.log
rm -rf "$TSF_DIR/fonts" "$TSF_CACHE/thumbs" "$TSF_CACHE/tel-cache"
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
