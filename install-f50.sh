#!/bin/sh
# Run from a complete checkout; default invocation only checks prerequisites.
set -eu
TSF_SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -d "$TSF_SOURCE/runtime" ]; then
    case "$(uname -m)" in
        aarch64|arm64) ;;
        *) echo '离线运行环境仅支持 ARM64 F50。' >&2; exit 1 ;;
    esac
    exec "$TSF_SOURCE/runtime/bin/python3" -B "$TSF_SOURCE/scripts/install-f50.py" "$@"
fi
command -v python3 >/dev/null 2>&1 || {
    echo '需要 Python 3.11+；请先按 docs/INSTALL-F50.md 准备运行环境。' >&2
    exit 1
}
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
    echo '需要 Python 3.11 或更新版本。' >&2
    exit 1
}
exec python3 -B "$TSF_SOURCE/scripts/install-f50.py" "$@"
