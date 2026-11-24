#!/bin/sh
# 兼容层: install-f50.sh 现在只是 ./install.sh f50 的别名.
# 实际 launcher 逻辑已内嵌进 install.sh (_run_f50_installer).
# 直接调 ./install.sh f50 [args] 或 ./install.sh f50-apply 更清晰.
set -eu
SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$SRC/install.sh" f50 "$@"
