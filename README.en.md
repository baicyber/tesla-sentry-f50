# Tesla Sentry Drive · 特斯拉哨兵云盘

[中文](README.md) | **English**

**A virtual Sentry USB drive · multi-camera synchronized playback · SEI driving telemetry · watermarked export — running on any USB Gadget device.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.7+](https://img.shields.io/badge/python-3.7%2B-3776AB.svg)](https://www.python.org/)
[![Zero third-party deps](https://img.shields.io/badge/deps-zero-brightgreen.svg)](#features)

Plug a small Linux box (travel router / dev board / OpenWrt router) into the glovebox USB port instead of the factory USB drive: the car keeps recording as usual, and you open a web page over the box's Wi-Fi to review 4 / 6 synchronized camera angles, read the SEI telemetry embedded in the video bitstream frame by frame (gear / speed / accelerator / brake / steering angle / turn signals / Autopilot / GPS), and export clips with a driving-info watermark burned in.

No USB Device mode? That works too — most x86 boxes, NAS and software routers can run it: copy `TeslaCam/` onto the machine and start `TSF_ARCHIVE=/path python3 sentry-viewer.py` for a pure web viewer. Capabilities fall into three tiers — **A full deployment / B semi-automatic archiving / C viewer only** — see [Supported devices](#supported-devices).

The repository name comes from the reference device, the **ZTE F50 storage edition** (fully tested end to end). The project itself is not tied to that model · [deployment overview](docs/overview.png)

---

## Features

| Capability | Details |
|---|---|
| 🗂 Virtual USB drive | A 64G sparse exFAT image exported through USB Gadget `mass_storage`. **The installer pre-creates a complete exFAT filesystem plus the `TeslaCam` directory**, so the car recognizes it, shows the capacity and starts recording immediately — no "Format USB device" prompt to click through. Coexists with the network interface and serial console. |
| ⚡ Live view | `tesla-live-sync.py` parses exFAT **read-only** every 15 seconds (no mount, no USB disconnect), so the car writes while the web page reads; new clips usually show up within 15–30 seconds. The `*/10` cron job only writes Boombox audio. |
| 📦 Archive & rolling window | Video lands in a separate ext4 archive, never fighting the car for the image. `RecentClips` follows the car's rolling buffer and keeps only the last 60 minutes; the box-side copies of `SentryClips` / `SavedClips` are pruned by age/size (default 14 days / 12GB) while the drive-side originals stay untouched. `.pruned` tombstones prevent the "delete → re-copy" loop. |
| 🎬 Playback | **Single camera by default**: opening an event plays the camera that triggered it (a switcher bar below the player offers front / rear / left B-pillar / right B-pillar / grid — only the selected angle loads, and switching keeps your position). Grid view auto-fits 4 angles as 2×2 and 6 as 3×2, with 250ms sync correction, one shared progress bar and 0.5–4x speed. **Playback lands on the minute containing the trigger and starts 5 seconds before it** (marked with a red dot in the clip list). |
| 📊 Telemetry & metadata | Reads the SEI protobuf embedded in the bitstream: gear / speed / accelerator / brake / steering angle / turn signals / Autopilot state / GPS / 3-axis acceleration, updating frame by frame with playback. `event.json` supplies time / city / street / trigger reason / trigger frame. |
| 🔍 Filtering & preview | Trigger-reason and date chips combine as filters. Deep links via `?ev=` `?date=` `?cat=` `?cam=` `?exp=1`. 📡 The "Last hour" tab now combines the recent-clip list and the near-live preview entry in one page, with Original / 720p / 480p / 360p quality choice. Storage bars (the virtual drive counts actual written bytes inside the volume, not the sparse image's apparent size; the bar is colour-coded by usage: <30% green / 30–85% blue / >85% red) plus a live sync status. |
| 📶 Quality | Playback offers original / 720p / 480p / 360p (server-side transcode cache, 2GB cap). **480p is the default** — the same resolution used as the default for Feishu push and export; a single 480p stream starts in 0.08s over the public internet in testing. Manual choices are remembered and preferred next time. When a new Sentry / manually saved event lands, the background transcodes the triggering camera's trigger segment ± adjacent segments to 480p (3 files per event) so it plays instantly — only when nobody is watching. **Dashcam and near-live preview are never pre-transcoded**: those clips are added every minute and scroll away quickly, so the cache would be wasted, and constant transcoding would keep a fanless box pegged and hot. They are transcoded on demand when you actually open them. **A missing cache never makes you wait**: the player starts with any existing lower-quality variant (falling back to the original only if none exists) and switches to the target quality on the next segment or next open, while the transcode runs in the background. |
| 🎞 Export | Single-clip download, zip bundle, or multi-camera composite (2×2, or the third angle filling the bottom row when there are 3). Quality: original / 720p / 480p / 360p. **Composites take 4:3 cells from the source with no letterboxing** (single-clip original exports at 1280×960 with the source frame rate). Watermarked exports are always rendered on-device by ffmpeg to H.264 mp4 (+faststart, so WeChat and the photo library accept them directly). The overlay matches the web player (time / location on the left; speed / turn signal / gear / brake / accelerator on the right, plus an **Autopilot steering-wheel icon** that is blue when engaged and grey when not). Clips without driving telemetry (parked Sentry recordings) degrade to time + location only rather than silently dropping the watermark. |
| 📂 File management | Browse, upload and download on the drive or archive; multi-select batch delete / move / copy / rename (realpath-guarded). One click clears every recording under `TeslaCam/` while keeping the directory structure and leaving Boombox / Wraps alone. Drive management mode reverts to export automatically after 5 minutes idle. |
| 🚗 Driving recordings | A separate `DriveClips` store keeps **full-quality originals** for every camera, grouped into trips (detected from gear position and parking duration) and shown as continuous runs. When the quota fills, the oldest trips are pruned in order so recording can continue; protected clips are never pruned. |
| 🔒 Protection & storage budget | The archive player can lock / unlock / delete, and driving recordings are handled per whole trip. Lock state is honoured by sync, automatic pruning and file management alike, and a deleted recording is not re-synced. Settings shows the relationship between the USB drive, archive, driving-recording quota, cache and free-space reserve; changing the USB capacity requires confirming that the drive will be cleared, then rebuilds the exFAT image (recovery files are kept on failure). |
| 🔔 Feishu push | Direct message (self only) or group bot webhook: cover card of the trigger frame, or a short clip starting 5 seconds before the trigger, optionally with the driving watermark burned in. Deduplicated, 20-second delay, 3 retries on failure. The export sheet can push too. |
| 🔐 Login protection | `install.sh` generates a random 10-character password (user `admin`) by default. HMAC-signed cookie valid for 30 days plus HTTP Basic. 8 wrong attempts from one IP within 10 minutes returns 429. Settings → Account lets you change the password or sign out. |
| 🔄 Online update | Multiple sources raced in parallel with a GitHub API fallback; the main file and its local modules are downloaded, self-checked and installed together → backup → atomic swap → restart → 150-second health probe that automatically reverts to the previous version if the new one does not come up, plus one-click rollback. Settings → Update has an **auto-update** toggle (on by default: upgrades when a new version appears and nobody is watching). |
| 🎨 Personalization | Custom title icon and name (defaults to 「哨兵一剪梅」), dark / light / auto theme, 4 UI styles (Default / Apple / Claude / Pixel), 10 UI languages, background skins, lock chime (`LockChime.wav`), wraps `Wraps/`. |
| 🛡 Robustness | Journaled ext4 archive, boot-time self-check, service auto-restart, automatic LUN re-attach via `*/2` cron after eject, recovery from power loss. |

> 📢 **Changelog**: highlights of the major releases after 2.3 (instant playback / thermals / no-format first setup / export watermark / security & OTA) are in [docs/RELEASE-NOTES.md](docs/RELEASE-NOTES.md).

---

## Supported devices

| Tier | Requirements | What you get | Typical hardware |
|---|---|---|---|
| **A · Full** | USB Device (Gadget) + kernel exFAT + root | Virtual USB drive + transparent live sync + every web feature | ZTE F50, travel routers, dev boards |
| **B · Semi-automatic** | Any Linux + storage | Copy `TeslaCam/` into the archive yourself; web features work as usual | NAS, software routers, old laptops |
| **C · Viewer only** | Python 3.7+ | Point it at an existing archive (NFS / SMB is fine) and use it as a front end | Any machine, x86 included |

**Verified**: ZTE F50 storage edition (Unisoc T760 / aarch64 / 1.4G RAM / 256G eMMC, OpenWrt 25.12.5, kernel 7.2.9, `25100000.dwc3`) runs the full stack, with a 2022 Model Y (4 cameras) as the head unit. OTG boards such as the Raspberry Pi Zero 2 W are expected to work but need a self-check; x86 / NAS can only run tiers B and C.

**Tier A hard requirements**: `/sys/class/udc/` non-empty, configfs + `USB_F_MASS_STORAGE`, kernel exFAT, loop / losetup, root, and a car USB port that can supply power. Recommend ≥32G storage (image plus archive), Python 3.7+ (the viewer has zero third-party dependencies). ffmpeg only affects thumbnails and transcoding — missing it does not block installation.

Check before installing (read-only, changes nothing):

```sh
sh scripts/tsf-check.sh          # item-by-item UDC / configfs / exFAT / Python / init check, colour-coded verdict
sh scripts/tsf-check.sh --json   # machine-readable, for scripts
```

Full device list, per-OS compatibility and the feature-degradation matrix are in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

---

## Installation

**1. Deploy** (over SSH on the device)

```sh
git clone https://github.com/baicyber/tesla-sentry-f50.git
cd tesla-sentry-f50
sudo ./install.sh                                             # 64G image + /archive + port 8080
sudo ./install.sh --size 120 --port 8080 --archive /archive    # custom options (image ≤128G)
```

The installer will: check kernel gadget / exFAT / Python → create the sparse image and **pre-populate exFAT + the `TeslaCam` directory** → install into `/opt/tesla-sentry/` and write `/etc/tesla-sentry.conf` → register the `tesla-gadget` / `tesla-viewer` / `tesla-live` services (OpenWrt procd, systemd or rc.local) plus two cron jobs → generate a random login password → start everything and print status. Missing ffmpeg, fonts or optional kernel features produce warnings, not aborts.

> A fresh aarch64 OpenWrt device can use the dedicated `./install.sh f50` entry point instead (it only checks prerequisites by default; add `--apply` to actually install, Python 3.11+ required). Use either it or the generic install, not both — see [docs/INSTALL-F50.md](docs/INSTALL-F50.md).

**2. Plug it into the car**: insert into the glovebox USB port — the head unit recognizes it, shows the capacity and starts recording. The image already contains `TeslaCam/{RecentClips,SavedClips,SentryClips,EncryptedClips}`, so **there is no "Format USB device" step**. If the car still asks, confirming is harmless — it just rebuilds the same structure.

**3. Open the web UI**: `http://<device-ip>:8080` (join the box's hotspot, connect through a USB network interface, or add Tailscale / frp yourself). The first login is `admin` with the password printed by the installer or stored in `/opt/tesla-sentry/auth.conf`. Video comes from the 15-second live sync, so you don't wait for scheduled archiving.

**Uninstall**

```sh
sudo ./uninstall.sh              # stop services, remove program and cache, keep image and archived footage
sudo ./uninstall.sh --purge      # also delete image and archive (irreversible)
```

---

## Configuration

Main config: `/etc/tesla-sentry.conf` (restart services after editing):

| Variable | Default | Purpose |
|---|---|---|
| `TSF_IMG` / `TSF_ARCHIVE` | `/opt/tesla-sentry/teslacam.img` / `/archive` | Virtual drive image / archive root |
| `TSF_PORT` | `8080` | Web port |
| `TSF_LIVE_INTERVAL` | `15` | Live read-only sync poll interval, seconds |
| `TSF_IDLE` | `600` | Image idle seconds; only used by the cron job that mounts to write Boombox audio |
| `TSF_RECENT_ROLL_MIN` | `60` | `RecentClips` rolling window in minutes, 0 = no rolling |
| `TSF_BOX_KEEP_DAYS` | `14` | Box-side retention for Sentry / saved clips, by age; 0 = unlimited |
| `TSF_FREE_MIN_MB` / `TSF_FREE_TARGET_MB` | `4096` / `10240` | Free-space fallback cleanup: trigger threshold and target |
| `TSF_CACHE` | automatic | Thumbnail / telemetry / transcode cache directory |

OTA, Feishu push and authentication settings are **not** written to this file — they use environment variables (such as `TSF_OTA_SOURCES`, `TSF_OTA_REPO`) or the web UI. The full variable table is in section 8 of `docs/COMPATIBILITY.md`.

The web **⚙ Personalization** panel has 10 sub-tabs: Storage, Car USB files, Lock chime, Wraps, Push, Account, Language, Appearance, Brand, Update.

- **Brand**: Settings → Brand. Replace the title-bar icon (png/jpg/webp, ≤3MB) and the name (≤24 characters). Changes apply instantly and propagate to the login page; the built-in 「哨兵一剪梅」 icon and name are the defaults and can be restored with one click.
- **Online update**: Settings → Update compares against the repository's `version.json` and offers one-click upgrade / rollback. Configuration and recordings are never part of an update.
- **Feishu push**: Settings → Push. Use a self-built app for direct messages (self only) or a group bot webhook; supports cover cards and short clips from the triggering camera. Hit "Send test" first.
- **Authentication**: credentials live in `/opt/tesla-sentry/auth.conf` (mode 600). Deleting that file and restarting disables auth (only advisable on a trusted LAN).
- **Drive management mode**: "Connect car USB drive" temporarily stops exporting and mounts the image; it reverts after 5 minutes idle. Avoid leaving it mounted while the car is driving or Sentry is active.
- **Lock chime**: WAV, 44.1kHz, 16-bit, <1MB, 1–5 seconds, named `LockChime.wav` in the root of the drive.
- **API**: dozens of endpoints for events, media, telemetry, uploads, file management, OTA and push — `do_GET` / `do_POST` / `do_DELETE` in `sentry-viewer.py` are the reference.

Restart services after changing configuration:

```sh
/etc/init.d/tesla-viewer restart
/etc/init.d/tesla-live restart
/etc/init.d/tesla-gadget restart
```

---

## FAQ

- **The car doesn't see the drive / asks to format it**: confirm `/sys/class/udc/` has a controller and `grep exfat /proc/filesystems` returns something, then run `/opt/tesla-sentry/tesla-gadget.sh status`. New deployments ship a pre-built exFAT filesystem, so this should not happen; all-zero images left by older installs are patched automatically during reinstall. If `--size` exceeds 128G (the addressing limit of the car-compatible layout), it falls back to a blank image that the car must format once; to write the filesystem manually run `python3 /opt/tesla-sentry/mkexfat.py <image-path> <size-GB>` (it refuses to overwrite an image that already holds data).
- **Black video**: the clips are H.265 / HEVC. Use Edge / Chrome on Windows (with an HEVC decoder) or Safari on iPhone; some Android browsers cannot play them.
- **No events in the web UI**: check `tesla-live.log` and `tesla-sync.log` under `/opt/tesla-sentry/`. Confirm the sync-status chip at the top is online, then verify the archive and image paths.
- **Thumbnails never appear / playback buffers for a long time**: look for a red "FFmpeg missing" bar at the top of the page, or run `grep TSF_FFMPEG /etc/tesla-sentry.conf` and execute that path with `-version`. Without a working `ffmpeg`/`ffprobe` every thumbnail request fails and the quality transcode silently falls back to the original file (which looks like endless buffering over a public link). On the F50 run `apk add ffmpeg` first, then `sh install.sh f50 --apply --install-deps --ffmpeg /usr/bin/ffmpeg` — this also repairs an existing install (install dependencies → rewrite the config → restart the service). `TSF_FFMPEG`/`TSF_FFPROBE` should point at `/usr/bin/ffmpeg` and `/usr/bin/ffprobe`; avoid unknown aarch64 static builds.
- **Entering 0 in storage**: an archive cap of `0` means unlimited (no total-size pruning; the oldest clips are only removed when free space drops below the reserve line). The DriveClips cap must stay ≥1 and cannot exceed the archive cap.
- **Copying only the main program still works**: `sentry-viewer.py` uses `storage_allocation.py` and `storage_resize.py` from the same directory, but since 2.8.43 the main file embeds gzip copies of both and falls back to them when the files are missing, so a lone `sentry-viewer.py` still boots (`--ota-check` reports `modules=embedded`). Web OTA now downloads, self-checks and installs the main program, local modules and `tesla-live-sync.py` together and **refuses the upgrade, keeping the old version** if a module is missing or broken. For manual upgrades copying the related `.py` files together (or re-running `install.sh`) is still recommended, since it lets you hot-fix a module on its own.

### Update check fails (GitHub unreachable)

On mainland networks `raw.githubusercontent.com` and the jsDelivr endpoints are frequently reset or hit by TLS interference, and third-party mirrors come and go, so "Check for updates" may occasionally report **all download sources unavailable**. Since 2.8.13 the behaviour is:

- Seven built-in sources (`ghfast.top`, `gcore.jsdelivr.net`, `ghproxy.net`, `gh-proxy.org`, `gh-proxy.com`, raw, jsDelivr) are **raced in parallel** — any one of them working is enough, instead of waiting out each timeout in order;
- Errors list every failed source with its reason (timeout / connection blocked / certificate tampered / DNS failure), so you can tell a firewall problem from a dead mirror at a glance;
- Auto-update is on by default: after the network recovers it retries every 5 minutes and upgrades automatically, no manual clicking.

To add your own source, put a working mirror in the config (comma-separated, in the form `https://domain/<repo-path>/main`):

```bash
echo 'TSF_OTA_SOURCES=https://ghfast.top/https://raw.githubusercontent.com/baicyber/tesla-sentry-f50/main,https://gcore.jsdelivr.net/gh/baicyber/tesla-sentry-f50@main' >> /etc/tesla-sentry.conf
/etc/init.d/tesla-viewer restart
```

**On old versions (2.8.12 and earlier) where even the check fails and the update page is unreachable**, overwrite the program manually once on the device; automatic updates resume afterwards:

```bash
wget -O /tmp/sv.py https://ghfast.top/https://raw.githubusercontent.com/baicyber/tesla-sentry-f50/main/sentry-viewer.py \
  || wget -O /tmp/sv.py https://gcore.jsdelivr.net/gh/baicyber/tesla-sentry-f50@main/sentry-viewer.py
python3 -m py_compile /tmp/sv.py && cp /tmp/sv.py /opt/tesla-sentry/sentry-viewer.py \
  && chmod 755 /opt/tesla-sentry/sentry-viewer.py && /etc/init.d/tesla-viewer restart
```

### Watermark checked but missing from the exported video

Burning the overlay on-device (`ffmpeg` + libass) requires a **CJK font**. Versions 2.8.18 and earlier silently skipped the whole subtitle chain when the font was missing — the export succeeded, with no watermark at all. Since 2.8.19:

- The export panel shows a "watermark font" status; when missing it offers **Install font** (one click, downloads from the official `google/fonts` repository, ~17MB, retried across mirrors) and **Upload** (local ttf/otf);
- With no font available the export is blocked with an explicit message rather than handing you a watermark-free video;
- Font detection also falls back to system font directories outside `/opt/tesla-sentry/fonts`, so boxes that already had a font installed work immediately after upgrading.

Installing the font manually (copying it from elsewhere works fine offline):

```bash
mkdir -p /opt/tesla-sentry/fonts
wget -O /opt/tesla-sentry/fonts/NotoSansSC.ttf \
  "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf" \
  || wget -O /opt/tesla-sentry/fonts/NotoSansSC.ttf \
  "https://ghfast.top/https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf"
ls -l /opt/tesla-sentry/fonts/     # should be about 17MB
/etc/init.d/tesla-viewer restart
```

More troubleshooting in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md); design details (read-only exFAT parsing, dual-path mutual exclusion, SEI decoding, drive-management state machine) in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Security notes

- `install.sh` enables site-wide login protection by default. **Running `python3 sentry-viewer.py` directly without an `auth.conf` leaves authentication off** — only do that on a trusted LAN. Deleting `auth.conf` is still the "disable auth" switch, but if the file exists and the password cannot be read, the service **refuses to start** instead of running unprotected.
- The login page and HTTP Basic share one rate limit: 8 failures from an IP within 10 minutes locks it for 5 minutes, and Basic cannot bypass it.
- Uploads are capped at 100MB, plain JSON endpoints at 1MB per request; per-connection read/write timeout is 30 seconds with up to 48 threads. Paths are confined to the archive / image mount points and cannot escape into system directories.
- Authentication and cookies travel over plain HTTP. Use HTTPS or a VPN for internet exposure; behind a reverse proxy send `X-Forwarded-Proto: https` (or set `TSF_COOKIE_SECURE=1`) and the login cookie gains `Secure` automatically.

## Disclaimer

This is a personal hobby project, not affiliated with Tesla. Changing how your car's storage behaves carries risk (data loss, interrupted recording). Evaluate it yourself and test with a small image first.

## Credits

- [teslausb](https://github.com/marcone/teslausb) — the pioneering Raspberry Pi USB-drive impersonator; the idea this project grew from
- [SentryBox](https://github.com/weitanism/sentrybox) — productized practice for an always-on in-car device
- [tesla-sentry-viewer](https://github.com/denysvitali/tesla-sentry-viewer) — reference for the web-viewer form factor

## ☕ Sponsor

> This project is completely free and open source. If it has helped you, you are welcome to buy the author some tokens to keep it going.

<table>
  <tr>
    <td align="center"><img src="docs/sponsor/web_alipay.jpg" width="240" alt="Alipay QR code"><br><sub><b>Alipay</b></sub></td>
    <td align="center"><img src="docs/sponsor/web_wechatpay.jpg" width="240" alt="WeChat Pay QR code"><br><sub><b>WeChat Pay</b></sub></td>
  </tr>
</table>

## 💬 Community

> Questions, ideas, or just want to compare setups — scan and join the group. Content is mostly in Chinese.

<table>
  <tr>
    <td align="center"><img src="docs/sponsor/web_qqgroup.jpg" width="240" alt="QQ group QR code"><br><sub><b>QQ group: 831426822</b></sub></td>
    <td align="center"><img src="docs/sponsor/web_wechatgroup.jpg" width="240" alt="WeChat group QR code"><br><sub><b>WeChat group</b> (QR code expires; join the QQ group or contact the author if it has)</sub></td>
  </tr>
</table>

**To join**: if a QR code has expired, or you would rather talk directly, follow the author on Douyin `@耶基大学懂事长` (ID `yejiuni`) or scan the WeChat code to add the author as a friend and get an invite.

<table>
  <tr>
    <td align="center"><img src="docs/sponsor/web_douyin.jpg" width="240" alt="Douyin QR code"><br><sub><b>Douyin · @耶基大学懂事长</b></sub></td>
    <td align="center"><img src="docs/sponsor/web_wechatauthor.jpg" width="240" alt="Author WeChat QR code"><br><sub><b>WeChat · contact the author</b></sub></td>
  </tr>
</table>

## License

[MIT](LICENSE)
