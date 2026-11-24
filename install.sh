#!/bin/sh
# Tesla Sentry F50 - 中兴 F50 专用安装入口
#
#   sudo ./install.sh                    # 预检 + 安装 + 启动服务
#   sudo ./install.sh f50-check          # 仅预检
#   sudo ./install.sh f50-apply          # 预检 + 安装
#   sudo ./install.sh f50-install        # 预检 + 安装 + 启动服务
#   sudo ./install.sh f50 [参数...]      # 直接调用安装器，默认仅预检
#
# 其他设备与其他 Linux 发行版不再支持。

set -e

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

sub=f50-install
if [ $# -gt 0 ]; then
    case "$1" in
        f50|f50-check|f50-apply|f50-install) sub="$1"; shift ;;
        -h|--help) sub=f50 ;;
        *) die "仅支持中兴 F50；请使用 f50-check、f50-apply 或 f50-install" ;;
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
    [ -n "$py" ] || die "需要 Python 3.11+ (OpenWrt: opkg install python3 python3-pip)"
    "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
        || die "需要 Python 3.11 或更新版本"
fi

case "$sub" in
    f50-check)   : ;;
    f50-apply)   set -- --apply "$@" ;;
    f50-install) set -- --apply --start "$@" ;;
    f50)         : ;;
esac

say "调用中兴 F50 专用安装器 ($sub)"
exec "$py" -B "$src/scripts/install-f50.py" "$@"
