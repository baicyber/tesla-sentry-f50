#!/bin/sh
# tsf-watchdog - 服务存活监控与自动恢复
#
# 规则:
#   1. 每 10 秒检查一次 tesla-viewer / tesla-live 是否在运行, 没在跑就 restart。
#   2. 连续 6 次恢复仍失败 -> 重启 OpenWrt 尝试恢复。
#   3. 重启 OpenWrt 2 次仍失败 -> 暂停自动重启, 把具体失败原因与处理建议写进系统日志。
#
# 不会跟"有意停止/暂停"打架:
#   - 测速产生的 .sync-pause 标志、或网页正在挂载车机U盘(/tmp/teslamnt)时,
#     tesla-live 属于被有意暂停, 不 restart、不计失败;
#   - tsf-mount stop(卸载数据卡/关机)会写 /tmp/tsf-watchdog.pause, 看门狗整体暂停;
#   - 数据卡未挂载时不 restart、不计失败、不重启系统, 只定期尝试 tsf-mount 挂载;
#   - 手动 /etc/init.d/<svc> stop|disable 的服务不自动拉起(disable 持久生效);
#   - tsf-mount 被 disable 时看门狗整体退出监控。
# 以上 /tmp 标记在断电重启后自动清空, 不会导致重启后监控失效。
#
# 重启次数持久化在 /root/tsf-watchdog/, 服务恢复健康后清零。
STATE_DIR=/root/tsf-watchdog
# 读取实际配置, 避免自定义挂载点/暂停标志路径时判断失准
[ -f /etc/tesla-sentry.conf ] && . /etc/tesla-sentry.conf
FAIL_FILE=$STATE_DIR/recover-fails
BOOT_FILE=$STATE_DIR/reboot-count
GIVEUP_FILE=$STATE_DIR/giveup
# 服务被有意停掉时(tsf-mount stop / 卸载数据卡 / 关机)看门狗必须让路
PAUSE_FILE=/tmp/tsf-watchdog.pause
# 手动 /etc/init.d/<svc> stop|disable 会在 /tmp 留下标记, disable 另在 STATE_DIR 留持久标记
STOPPED_PREFIX=/tmp/tsf-watchdog.stopped-
# tsf-mount 被 disable 说明用户不要这套服务自启, 看门狗整体退出监控
MOUNT_ENABLE_LINK=/etc/rc.d/S08tsf-mount
# 数据卡挂载点: 没挂载时重启服务毫无意义, 更不能因此重启系统
DATA_ROOT=${TSF_CARD_MNT:-/mnt/sdcard}
# 车机U盘暂停同步标志(测速等)与网页挂载车机U盘的挂载点
SYNC_PAUSE_FLAG=${TSF_SYNC_PAUSE:-/opt/tesla-sentry/.sync-pause}
USB_MNT=${TSF_MNT:-/tmp/teslamnt}
SERVICES="tesla-viewer tesla-live"
INTERVAL=10
MAX_FAILS=6
MAX_REBOOTS=2

mkdir -p "$STATE_DIR"
# 每次看门狗启动(含系统重启后)重新计数恢复失败, 但重启次数要跨重启保留
fails=0
storage_miss=0
tick=0
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
    tick=$((tick + 1))
    # 已暂停自动重启后仍保留恢复能力, 但降为每分钟一轮, 避免每 10 秒刷系统日志
    if [ -f "$GIVEUP_FILE" ] && [ $((tick % 6)) -ne 0 ]; then
        continue
    fi
    # tsf-mount 被禁用: 用户明确不要这套服务自启, 看门狗不做任何恢复
    [ -e "$MOUNT_ENABLE_LINK" ] || continue
    # tsf-mount stop / 卸载数据卡 / 关机期间服务是被有意停掉的, 不参与恢复与计数
    if [ -f "$PAUSE_FILE" ]; then
        continue
    fi
    # 数据卡没挂载(未安装/突然断电后没识别到): 直接 restart 服务只会反复失败,
    # 甚至把"没插卡"误判成服务故障去重启系统。这里改为尝试走 tsf-mount 挂载,
    # 挂不上就继续等, 不计失败、不重启。
    if ! grep -q " $DATA_ROOT " /proc/mounts 2>/dev/null; then
        fails=0
        echo 0 > "$FAIL_FILE"
        storage_miss=$((storage_miss + 1))
        if [ $((storage_miss % 6)) -eq 1 ] && [ ! -f /tmp/tsf-card-format ]; then
            log "数据卡未挂载($DATA_ROOT), 尝试 tsf-mount 挂载; 自动恢复与重启策略暂停"
            /etc/init.d/tsf-mount start >/tmp/tsf-watchdog-mount.log 2>&1
        fi
        # 网页本身不依赖数据卡: 卡缺失时仍要保证 tesla-viewer 在跑,
        # 否则用户连"格式化/换卡/排查"的入口都没有, 只能 SSH。
        # tesla-live 依赖数据卡, 卡没挂时不拉起、也不计失败。
        if [ -x /etc/init.d/tesla-viewer ] &&
           [ ! -f "$STOPPED_PREFIXtesla-viewer" ] && [ ! -f "$STATE_DIR/disabled-tesla-viewer" ] &&
           ! /etc/init.d/tesla-viewer running >/dev/null 2>&1; then
            log "数据卡未挂载, 但网页服务未运行, 尝试 start tesla-viewer"
            /etc/init.d/tesla-viewer start >/tmp/tsf-watchdog-restart.log 2>&1
        fi
        continue
    fi
    storage_miss=0
    healthy=1
    for svc in $SERVICES; do
        [ -x "/etc/init.d/$svc" ] || continue
        # 手动 stop(本次开机内有效) / disable(持久) 的服务不自动拉起
        if [ -f "$STOPPED_PREFIX$svc" ] || [ -f "$STATE_DIR/disabled-$svc" ]; then
            continue
        fi
        if [ "$svc" = "tesla-live" ] && { [ -f "$SYNC_PAUSE_FLAG" ] || grep -q " $USB_MNT " /proc/mounts 2>/dev/null; }; then
            # 车机U盘暂停同步(测速)或网页正在挂载车机U盘: 同步服务被有意暂停,
            # 不 restart、不计失败, 避免跟"暂停同步"打架
            continue
        fi
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
    # reboot 若因故没生效, 清零失败计数, 避免每 10 秒连续触发重启
    fails=0
    echo 0 > "$FAIL_FILE"
    log "连续 $MAX_FAILS 次恢复失败, 第 $boots 次重启 OpenWrt 尝试恢复"
    sync
    reboot
    # reboot 没立即生效时避免同一轮里反复触发
    sleep 60
done
