#!/bin/sh
# tsf-watchdog - 服务存活监控与自动恢复
#
# 规则:
#   1. 每 10 秒检查一次 tesla-viewer / tesla-live 是否在运行, 没在跑就 restart。
#   2. 连续 6 次恢复仍失败 -> 重启 OpenWrt 尝试恢复。
#   3. 重启 OpenWrt 2 次仍失败 -> 暂停自动重启, 把具体失败原因与处理建议写进系统日志。
#
# 重启次数持久化在 /root/tsf-watchdog/, 服务恢复健康后清零。
STATE_DIR=/root/tsf-watchdog
FAIL_FILE=$STATE_DIR/recover-fails
BOOT_FILE=$STATE_DIR/reboot-count
GIVEUP_FILE=$STATE_DIR/giveup
SERVICES="tesla-viewer tesla-live"
INTERVAL=10
MAX_FAILS=6
MAX_REBOOTS=2

mkdir -p "$STATE_DIR"
# 每次看门狗启动(含系统重启后)重新计数恢复失败, 但重启次数要跨重启保留
fails=0
echo 0 > "$FAIL_FILE"
boots=0
[ -f "$BOOT_FILE" ] && boots=$(cat "$BOOT_FILE" 2>/dev/null)
case "$boots" in ''|*[!0-9]*) boots=0 ;; esac

log() {
    logger -t tsf-watchdog -- "$*"
    echo "[tsf-watchdog] $*"
}

last_error() {
    logread 2>/dev/null | grep -Ei 'tesla-viewer|tesla-live|sentry-viewer|Traceback|SyntaxError|Error' | tail -n 5
}

while true; do
    sleep "$INTERVAL"
    healthy=1
    for svc in $SERVICES; do
        [ -x "/etc/init.d/$svc" ] || continue
        if ! /etc/init.d/"$svc" running >/dev/null 2>&1; then
            healthy=0
            log "$svc 未运行, 尝试 restart 恢复"
            /etc/init.d/"$svc" restart >/tmp/tsf-watchdog-restart.log 2>&1
        fi
    done

    if [ "$healthy" = 1 ]; then
        if [ "$fails" != 0 ] || [ "$boots" != 0 ] || [ -f "$GIVEUP_FILE" ]; then
            log "服务已恢复健康, 清零失败计数"
        fi
        fails=0
        boots=0
        echo 0 > "$FAIL_FILE"
        echo 0 > "$BOOT_FILE"
        rm -f "$GIVEUP_FILE"
        continue
    fi

    fails=$((fails + 1))
    echo "$fails" > "$FAIL_FILE"
    log "自动恢复第 $fails 次后服务仍未正常运行"
    [ "$fails" -lt "$MAX_FAILS" ] && continue

    if [ -f "$GIVEUP_FILE" ]; then
        # 已暂停自动重启, 只继续尝试恢复服务
        fails=0
        echo 0 > "$FAIL_FILE"
        continue
    fi

    if [ "$boots" -ge "$MAX_REBOOTS" ]; then
        reason=$(last_error | tr '\n' ' ' | cut -c1-500)
        log "连续 $MAX_FAILS 次恢复失败, 且已重启 OpenWrt $boots 次仍未恢复, 暂停自动重启"
        log "最近错误: ${reason:-未在系统日志中找到具体报错}"
        log "建议: 1) SSH 执行 logread | grep -Ei 'tesla|sentry' 查看详细报错;"
        log "      2) 检查数据卡是否以 ext4 读写挂载、端口是否被其它程序占用;"
        log "      3) 升级后出问题可执行 /opt/tesla-sentry/install.sh rollback 回滚上一版;"
        log "      4) 仍不行则重新运行 install.sh install, 或恢复出厂后重刷。"
        : > "$GIVEUP_FILE"
        fails=0
        echo 0 > "$FAIL_FILE"
        continue
    fi

    boots=$((boots + 1))
    echo "$boots" > "$BOOT_FILE"
    log "连续 $MAX_FAILS 次恢复失败, 第 $boots 次重启 OpenWrt 尝试恢复"
    sync
    reboot
    # reboot 没立即生效时避免同一轮里反复触发
    sleep 60
done
