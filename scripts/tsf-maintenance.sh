#!/bin/sh
# Installed cron jobs run only while playback services and their storage are ready.
set -a
. /etc/tesla-sentry.conf || exit 1
set +a
. "$TSF_DIR/tsf-storage.sh" || exit 1
tsf_storage_ready || exit 1
/etc/init.d/tesla-viewer running >/dev/null 2>&1 || exit 0
case "$1" in
    usb) exec /bin/sh "$TSF_DIR/usb-selfheal.sh" ;;
    audio)
        for tsf_audio in "$TSF_ARCHIVE"/boombox-queue/*.wav "$TSF_ARCHIVE"/boombox-queue/*.mp3; do
            [ -f "$tsf_audio" ] && exec /bin/sh "$TSF_DIR/tesla-sync.sh"
        done
        exit 0
        ;;
    *) exit 2 ;;
esac
