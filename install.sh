#!/bin/sh
# Tesla Sentry F50 - 中兴 F50 专用安装入口
#
#   sudo ./install.sh                    # 预检 + 安装 + 启动服务
#   sudo ./install.sh check              # 仅预检
#   sudo ./install.sh apply              # 预检 + 安装
#   sudo ./install.sh install            # 预检 + 安装 + 启动服务
#   sudo ./install.sh --help             # 查看参数
#
# 其他设备与其他 Linux 发行版不再支持。

set -e

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

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
    [ -n "$py" ] || die "需要 Python 3.11+ (OpenWrt: opkg install python3 python3-pip)"
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
