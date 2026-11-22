#!/bin/sh
# Shared by the fresh-install boot and maintenance entry points.
tsf_storage_ready() {
    [ -n "${TSF_CARD_UUID:-}" ] && [ -n "${TSF_CARD_MNT:-}" ] || return 1
    tsf_dev=$(blkid -U "$TSF_CARD_UUID" 2>/dev/null) || return 1
    [ -b "$tsf_dev" ] || return 1
    tsf_source=$(awk -v m="$TSF_CARD_MNT" '$2 == m && $3 == "ext4" && $4 ~ /(^|,)rw(,|$)/ {print $1}' /proc/mounts)
    [ -n "$tsf_source" ] || return 1
    [ "$(readlink -f "$tsf_source")" = "$(readlink -f "$tsf_dev")" ]
}
