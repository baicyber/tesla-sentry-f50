#!/bin/sh
# Tesla Sentry F50 - 中兴 F50 专用安装入口 (仓库唯一的安装入口)
#
#   sudo ./install.sh                    # 预检 + 安装 + 启动服务
#   sudo ./install.sh check              # 仅预检 (只读): Python / ffmpeg / libx264 / 运行库 / USB Gadget / exFAT
#   sudo ./install.sh apply              # 预检 + 安装
#   sudo ./install.sh install            # 预检 + 安装 + 启动服务
#   sudo ./install.sh --help             # 查看参数
#
# 安装前检查已全部并入本入口与 scripts/install.py, 不再需要单独的 tsf-check.sh。
# 其他设备与其他 Linux 发行版不再支持。

set -e

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

# OpenWrt 25.12 起改用 apk, 24.10 及更早仍是 opkg; 只用于提示, 不在这里改系统
ow_ver=$(sed -n "s/^DISTRIB_RELEASE=['\"]*\([0-9][0-9.]*\).*/\1/p" /etc/openwrt_release 2>/dev/null | head -n1)
ow_major=${ow_ver%%.*}
pkg_cmd() {
    if [ -n "$ow_major" ] && [ "$ow_major" -ge 25 ] 2>/dev/null && command -v apk >/dev/null 2>&1; then
        printf 'apk add %s' "$*"
    elif [ -n "$ow_major" ] && [ "$ow_major" -lt 25 ] 2>/dev/null && command -v opkg >/dev/null 2>&1; then
        printf 'opkg install %s' "$*"
    elif command -v opkg >/dev/null 2>&1 && ! command -v apk >/dev/null 2>&1; then
        printf 'opkg install %s' "$*"
    elif command -v apk >/dev/null 2>&1; then
        printf 'apk add %s' "$*"
    else
        printf 'apk add %s（25.12+）或 opkg install %s（24.10 及更早）' "$*" "$*"
    fi
}

sub=install
if [ $# -gt 0 ]; then
    case "$1" in
        check|apply|install) sub="$1"; shift ;;
        -h|--help) sub=check ;;
        -*) : ;;
        *) die "仅支持 check、apply 或 install 子命令" ;;
    esac
fi

src=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
py=""
if [ -d "$src/runtime" ]; then
    case "$(uname -m)" in
        aarch64) py="$src/runtime/bin/python3" ;;
        *) die "离线运行环境仅支持 aarch64 架构的中兴 F50" ;;
    esac
fi
if [ -z "$py" ]; then
    py=$(command -v python3 || true)
    [ -n "$py" ] || die "需要 Python 3.11+ (OpenWrt: $(pkg_cmd python3 python3-pip))"
    "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
        || die "需要 Python 3.11 或更新版本"
fi

case "$sub" in
    check)   : ;;
    apply)   set -- --apply "$@" ;;
    install) set -- --apply --start "$@" ;;
esac

say "调用安装器 ($sub)"
exec "$py" -B "$src/scripts/install.py" "$@"
