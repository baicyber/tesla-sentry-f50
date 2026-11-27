#!/bin/sh
# Shared by the fresh-install boot and maintenance entry points.
# 就绪判断以「挂载点是 ext4 且 rw 挂载」为准。
# 旧实现还要求挂载来源等于 TSF_CARD_UUID 对应的设备: 重新格式化或换卡后 UUID
# 变了但配置来不及更新, 开机就会判定数据卡未就绪, 卡不挂载、同步不启动,
# 表现为「重启后录像全消失」。
tsf_storage_ready() {
    [ -n "${TSF_CARD_MNT:-}" ] || return 1
    tsf_source=$(awk -v m="$TSF_CARD_MNT" '$2 == m && $3 == "ext4" && $4 ~ /(^|,)rw(,|$)/ {print $1}' /proc/mounts)
    [ -n "$tsf_source" ]
}
