#!/bin/sh
# Serialize boot entry points and add storage after USB's ADB descriptors are ready.
set -a
[ ! -f "${TSF_CONF:-/etc/tesla-sentry.conf}" ] || . "${TSF_CONF:-/etc/tesla-sentry.conf}"
set +a
TSF_DIR=${TSF_DIR:-/opt/tesla-sentry}
IMG=${TSF_IMG:-$TSF_DIR/teslacam.img}
MNT=${TSF_MNT:-/tmp/teslamnt}
G=${TSF_GADGET:-/sys/kernel/config/usb_gadget/linux}
CONFIG=${TSF_GADGET_CONFIG:-c.1}
UDC=${TSF_UDC:-$(ls /sys/class/udc 2>/dev/null | head -n 1)}
WAIT=${TSF_USB_BOOT_WAIT:-90}
LOCK=${MU300_USB_LOCK:-/run/mu300-usb-rebind.lock}
ADB_CONF=${MU300_ADB_CONF:-/etc/mu300/adb}
ADBD=${MU300_ADBD_BIN:-/opt/mu300/bin/adbd}
FFS=${MU300_FFS:-/dev/usb-ffs/adb}
LUN=$G/functions/mass_storage.0/lun.0/file
LINK=$G/configs/$CONFIG/massstorage
case "$WAIT" in ''|*[!0-9]*) exit 2 ;; esac
[ -n "$UDC" ] || exit 1
[ "$(cat "${MU300_USB_ROLE:-/sys/class/usb_role/25100000.dwc3-role-switch/role}" 2>/dev/null)" != host ] || exit 1

exec 8>"${TSF_USB_BOOT_LOCK:-/run/tsf-usb-start.lock}" || exit 1
flock -x -w "$((WAIT + 10))" 8 || exit 1

export_ready() {
    [ -f "$IMG" ] && [ -L "$LINK" ] &&
        [ "$(cat "$LUN" 2>/dev/null)" = "$IMG" ] &&
        [ "$(cat "$G/UDC" 2>/dev/null)" = "$UDC" ]
}
usb_ready() {
    [ -f "$IMG" ] && [ -d "$G/configs/$CONFIG" ] || return 1
    if [ -x "$ADBD" ] && { [ ! -e "$ADB_CONF" ] || [ "$(cat "$ADB_CONF" 2>/dev/null)" = on ]; }; then
        if [ -e "$FFS/ep1" ] && [ -e "$G/configs/$CONFIG/f3" ]; then
            :
        elif [ -f /run/mu300-adb-start-finished ] && [ ! -L "$G/configs/$CONFIG/f3" ]; then
            : # ADB has exited; its service does not automatically respawn.
        else
            return 1
        fi
    fi
    [ -n "$(cat "$G/UDC" 2>/dev/null)" ]
}

export_ready && exit 0
elapsed=0
while ! usb_ready; do
    [ "$elapsed" -lt "$WAIT" ] || { logger -t tsf-usb 'USB startup prerequisites timed out'; exit 1; }
    sleep 1
    elapsed=$((elapsed + 1))
done

# Keep local image management and export mutually exclusive. Recheck after
# waiting because the web UI may have mounted the image while ADB started.
exec 9>"${TSF_USB_LOCK:-/tmp/tsf-usb.lock}" || exit 1
flock -x -w "$WAIT" 9 || exit 1
grep -q " $MNT " /proc/mounts && exit 0

# Share the platform ADB/LAN lock, so descriptors cannot change during export.
held=0
cleanup() {
    if [ "$held" = 1 ]; then
        rm -f "$LOCK/pid"
        rmdir "$LOCK" 2>/dev/null || :
    fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
while ! mkdir "$LOCK" 2>/dev/null; do
    [ "$elapsed" -lt "$WAIT" ] || { logger -t tsf-usb 'USB rebind lock timed out'; exit 1; }
    sleep 1
    elapsed=$((elapsed + 1))
done
held=1
echo $$ > "$LOCK/pid" || exit 1
export_ready && exit 0
usb_ready || exit 1
TSF_USB_START_COORDINATED=1 sh "$TSF_DIR/tesla-gadget.sh" on
