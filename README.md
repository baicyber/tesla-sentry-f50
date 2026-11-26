# 特斯拉哨兵云盘 · Tesla Sentry Drive

**中文** | [English](README.en.md)

**虚拟哨兵 U 盘 · 多路同步回放 · SEI 行车遥测 · 水印导出 —— 专为中兴 F50 优化。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![零第三方依赖](https://img.shields.io/badge/deps-%E9%9B%B6%E7%AC%AC%E4%B8%89%E6%96%B9%E5%BA%93-brightgreen.svg)](#功能)

把**中兴 F50 存储版**顶替原车 U 盘插进手套箱：车机照常录制，手机连 F50 WiFi 打开网页就能回看 4 / 6 路同步画面、逐帧读视频码流里的 SEI 遥测（挡位 / 车速 / 油门 / 刹车 / 方向盘 / 转向灯 / AP / 坐标），并导出带行车信息水印的视频。

本项目仅支持中兴 F50，不再提供其他随身 WiFi、开发板、NAS、x86 或通用 Linux 部署模式 · [部署速览图](docs/overview.png)

---

## 功能

| 能力 | 说明 |
|---|---|
| 🗂 虚拟 U 盘 | 稀疏 exFAT 镜像，通过 USB Gadget `mass_storage` 导出；全新安装不预建镜像，打开网页按引导到「存储分配」设置容量并创建，插车即可显示容量并开始录制，网卡 / 串口共存 |
| ⚡ 实时查看 | `tesla-live-sync.py` 每 15 秒**只读解析 exFAT**（不挂载、不断 USB），车机边写网页边看，新片段通常 15–30 秒出现；cron `*/10` 只负责写 Boombox 音频 |
| 📦 归档与滚动窗口 | 视频落到独立 ext4 归档区，不与车机同时读写镜像；`RecentClips` 跟随车机滚动缓冲只留最近 60 分钟，`SentryClips` / `SavedClips` 的**盒子副本**默认按 14 天 / 12GB 清理（车机 U 盘那份只读不动），`.pruned` 墓碑避免「删除→重抄」死循环 |
| 🎬 播放 | **默认单路**: 点开事件直接播触发摄像头那一路 (画面下方切换条可选 前视/后视/左B柱/右B柱/全览, 点哪个才加载哪个, 切换保留播放进度)；全览 4 路 2×2 / 6 路 3×2 自适应，250ms 同步校正、统一进度条、0.5–4x；**自动落在触发时刻所在的那一分钟、从触发前 5 秒起播**（片段栏红点标记） |
| 📊 遥测与元数据 | 直读码流内嵌 SEI protobuf：挡位 / 车速 / 油门 / 刹车 / 方向盘角度 / 转向灯 / AP 状态 / 坐标 / 三轴加速度，随进度逐帧跳动；`event.json` 提供时间 / 城市 / 街道 / 触发原因 / 触发画面 |
| 🔍 筛选与预览 | 触发原因 + 日期（单日或时间段）叠加筛选；`?ev=` `?date=` `?cat=` `?cam=` `?exp=1` 深链；📡「实时滚动记录」页签打开即进入连续播放器，进度条覆盖整个滚动时长，侧栏按分钟列出视频并可点击定位，带 原画 / 720p / 480p / 360p 画质选择；存储条（虚拟U盘按卷内实际写入量计，不是镜像预分配占用；进度条按占用比例配色：<30% 绿 / 30–85% 蓝 / >85% 红）与在线同步状态 |
| 📶 清晰度 | 播放可选 原画 / 720p / 480p / 360p（服务端转码缓存，按实际占用计入磁盘，不再设固定缓存预算）。**默认 480p**：与飞书推送 / 导出的默认档位同一分辨率，单路 480p 公网起播实测 0.08s；手动改过就记住你的选择，下次优先用记忆值。哨兵 / 手动保存的新事件落盘后，后台会按「预转码设置」里配置的数量（默认各 3 个最新事件）把全部摄像头（新款 6 路 / 老款 4 路）的触发段±相邻段预转成 480p（点开即播；每事件最多 18 个文件），只在没人看视频时跑。**实时滚动记录 / 准实时预览不预转**：这类片段每分钟新增、很快滚动淘汰，预转的缓存立刻作废，还会让无风扇设备持续满载转码、核心温度长期偏高；真点开时由服务端按需后台转码。**缓存没赶上的文件不会让人干等**：播放器先用已有的压缩档（都没有才用原画）开播，不必等转码；转码放后台，切段或下次打开自动用上目标档位；等待首帧时界面会显示实时转码百分比 |
| 🎞 导出 | 单路下载、zip 打包、多路合成（2×2 / 3 路时第三路铺满下排）；清晰度 原画 / 720p / 480p / 360p，**多画面按原片 4:3 取格子，不留黑边**（原画单路直接出 1280×960 + 源帧率）；带水印时一律由设备端 ffmpeg 直出 H.264 mp4（+faststart，微信 / 相册可直接发）；水印与网页播放器同款（左侧时间 / 地点，右侧车速 / 转向灯 / 挡位 / 刹车 / 油门，外加 **AP 方向盘图标**：辅助驾驶启动时蓝色、未启动时灰色）；片段没有行车遥测（停车哨兵录制）时水印退化成只显示时间 + 地点，不会悄悄不出水印 |
| 📂 文件管理 | 车机 U 盘与归档区合并为一个页面：浏览、上传、下载；操作栏顺序为上传 / 移动 / 复制 / 重命名 / 删除（realpath 防逃逸），支持多选批量操作；一键清空 `TeslaCam/` 全部录像（保留目录结构、不动 Boombox / Wraps）；U 盘管理模式 5 分钟无操作自动恢复导出 |
| 🚗 驾驶记录 | 独立 `DriveClips` 保留各路**原画**，按挡位与停车时长识别行程、按连续时间成段展示；额度满时按顺序清理较旧行程后继续录像，受保护的录像不参与清理 |
| 🔒 录像保护与存储分配 | 归档播放器可锁定 / 解锁 / 删除，驾驶记录按整段操作；锁定状态对同步、自动清理和文件管理同时生效，已删除的录像不会被重新同步回来；设置里统一展示 U 盘 / 归档 / 驾驶记录 / 空闲预留的关系（转码缓存按实际占用计入磁盘，不再单独预留缓存预算），调整 U 盘容量须先确认清空并重建 exFAT 镜像（失败保留恢复文件），镜像最大为存储卡实际容量的一半 |
| 🔔 推送 | 推送方式下拉选择飞书提醒或微信提醒；飞书支持私聊（仅本人）/ 群机器人，微信支持爱语飞飞 iyuu.cn / Server酱；触发那一帧封面卡片或触发点前 5 秒短视频，可烧行车信息水印；去重、延迟 20 秒、失败重试 3 次，导出浮层也能直接推送 |
| 🔐 登录保护 | `install.sh` 默认生成随机 10 位密码（admin），HMAC 签名 cookie 保留 30 天 + HTTP Basic；10 分钟内连错 8 次返回 429；设置 → 账号可改密码 / 退出 |
| 🔄 在线升级 | 多源并发竞速 + GitHub API 实时兜底；主程序与依赖模块一起下载、一起自检、一起安装 → 备份 → 原子替换 → 重启 → 150 秒探活，起不来自动退回上一版，也可手动一键回滚；设置 → 更新里可勾选**自动更新**（默认开启，检测到新版且没人看视频时自动升） |
| 🎨 个性化 | 标题图标与名称可自定义（默认「哨兵一剪梅」）、深 / 浅 / 自动主题，4 套界面风格（默认 / Apple / Claude / 像素），10 种界面语言、网页背景换肤、锁车音（`LockChime.wav`）、涂装 `Wraps/` |
| 🛡 稳定性 | 归档区 ext4 日志式、开机自检、服务自动拉起、LUN 被弹出后 cron `*/2` 自动重挂、断电自愈 |

> 📢 **更新日志**：2.3 之后的大版本重点变化（播放秒开 / 温控 / 新部署免格式化 / 导出水印 / 安全与 OTA）见 [docs/RELEASE-NOTES.md](docs/RELEASE-NOTES.md)。

---

## 支持设备

**仅支持中兴 F50 存储版**。已实测配置：紫光展锐 T760 / aarch64 / 1.4G RAM / eMMC 256G，OpenWrt 25.12.5、内核 7.2.9、`25100000.dwc3`；车机为 2022 Model Y（4 路摄像头），同时支持新款 6 路录像。

运行要求：F50 OpenWrt + procd、USB Gadget / configfs、内核 exFAT、loop / losetup、root、Python 3.11+，建议把镜像和归档放在 TF 卡。ffmpeg / ffprobe 用于缩略图、转码和水印导出。

装之前先自检（只读、不改系统）：

```sh
sudo ./install.sh check
```

详细要求见 [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)。

---

## 安装

**1. 部署**（在设备 SSH 里执行）

```sh
git clone https://github.com/baicyber/tesla-sentry-f50.git
cd tesla-sentry-f50
sudo ./install.sh check                                                # 仅预检（只读）
sudo ./install.sh install --port 8080 --data-mount /mnt/sdcard         # 预检 + 安装 + 启动服务
sudo ./install.sh apply   --port 8080 --data-mount /mnt/sdcard         # 预检 + 安装，不启动
sudo ./install.sh rollback                                             # 网页打不开时回滚到上次升级前
```

TF 卡不需要提前挂载：`install` 会检测未挂载/未初始化的卡，交互询问后直接格式化为 ext4 并挂载（会清空该卡数据）；非交互环境加 `--init-card` 显式允许格式化。若升级后网页打不开，SSH 执行 `sudo ./install.sh rollback` 即可恢复上一版程序与配置。

安装会：检查 F50 内核 gadget / exFAT / Python / FFmpeg → 装到 `/opt/tesla-sentry/`、写 `/etc/tesla-sentry.conf` → 注册 OpenWrt procd 服务与定时任务 → 生成随机登录密码 → 启动。

**首次使用**：全新安装不会自动生成车机 U 盘镜像，也不会预设归档区位置。用手机或电脑打开 `http://<F50-IP>:8080`，登录后按页面引导进入「设置 → 存储分配」，设置车机 U 盘容量、归档区上限与安全预留，点「调整 U 盘容量」创建镜像；完成后插到车机即可开始录制。

旧命令里的 `--size-gb` / `--archive-gb` / `--drive-gb` 仍可传入，但只作兼容保留，全新安装不再使用；容量与归档分配统一在网页「存储分配」里设置。`sudo ./install.sh --help` 可查看全部参数。

**2. 插到车上**：在网页完成首次设置、创建镜像后，插进手套箱 USB 口 → 车机识别、显示容量并开始录制。首次插入时车机若提示「格式化 USB 设备」，点确认即可（车机会写入分区表，网页会自动识别新的分区/卷偏移继续读取）。若格式化后网页仍提示「镜像未就绪」，可在存储页「车机 U 盘容量」下点「格式化 U 盘」按当前容量重建。

**3. 打开网页**：`http://<设备IP>:8080`（手机连设备热点、USB 网卡直连，或自行加 Tailscale / frp）。首次登录账号 `admin`，密码见安装输出或 `/opt/tesla-sentry/auth.conf`。视频由 15 秒在线同步落地，不用等定时归档。

**卸载**

```sh
sudo ./uninstall.sh              # 停服务、删程序与缓存，保留镜像和归档录像
sudo ./uninstall.sh --purge      # 连镜像和归档一起删（不可恢复）
```

---

## 配置与常用设置

主配置 `/etc/tesla-sentry.conf`（改完重启服务生效）：

| 变量 | 默认 | 作用 |
|---|---|---|
| `TSF_IMG` / `TSF_ARCHIVE` | `/mnt/sdcard/tesla-sentry/img/teslacam.img` / `/mnt/sdcard/tesla-sentry/archive` | 虚拟 U 盘镜像 / 归档区 |
| `TSF_PORT` | `8080` | 网页端口 |
| `TSF_LIVE_INTERVAL` | `15` | 在线只读同步轮询秒数 |
| `TSF_IDLE` | `600` | 镜像空闲秒数，仅用于 cron 挂载写 Boombox 音频 |
| `TSF_RECENT_ROLL_MIN` | `60` | RecentClips 滚动窗口（分钟），0 = 不滚动 |
| `TSF_BOX_KEEP_DAYS` | `14` | 盒子侧哨兵 / 手动保存归档的保留天数，0 = 不限 |
| `TSF_DEDUP_INTERVAL` / `TSF_DEDUP_MAX_MB` | `1800` / `4096` | 合并「实时滚动记录」与哨兵事件重复片段的检查间隔（秒，0 = 关闭）与每轮校验上限（MiB） |
| `TSF_FREE_MIN_MB` / `TSF_FREE_TARGET_MB` | `4096` / `10240` | 空间兜底清理的触发阈值与目标 |
| `TSF_PREWARM_SENTRY` / `TSF_PREWARM_SAVED` | `3` / `3` | 哨兵录像 / 手动保存各预转码缓存最新多少个事件，0 = 关闭该模块预转码（网页「预转码设置」会写回这里） |
| `TSF_CACHE` | 已废弃 | 程序缓存已并入 `TSF_TRANSCODE_DIR/app/`，旧配置保留仅为兼容 |

OTA、飞书推送、认证的参数**不写入这个 conf**，走环境变量（如 `TSF_OTA_SOURCES`、`TSF_OTA_REPO`）或网页配置。完整变量表见 `docs/COMPATIBILITY.md` 第 8 节。

网页 **⚙ 个性化** 有 11 个子页签：存储分配、播放设置、文件管理、锁车语音、涂装、推送、账号、语言、外观、更新、关于。

- **外观**：设置 → 外观，可调整界面风格、背景与品牌。标题栏左侧的图标（png/jpg/webp，≤3MB）与名称（≤24 字）都能换成自己的，改完立即生效、登录页同步；默认是内置的「哨兵一剪梅」图标与名称，一键可恢复。

- **在线升级**：设置 → 更新，自动比对仓库 `version.json`，一键升级 / 回滚；更新源可选内置仓库或「自定义源」（填写 `owner/repo` 或 GitHub 仓库链接）；配置与录像归档不在升级范围内。
- **推送**：设置 → 推送，先用下拉选择「飞书提醒」或「微信提醒」；飞书支持自建应用私聊（仅本人）/ 群机器人 webhook，微信可选爱语飞飞或 Server酱接口。支持封面卡片与触发摄像头短视频，配好先点「发送测试」。
- **认证**：凭据在 `/opt/tesla-sentry/auth.conf`（权限 600）；删除该文件并重启即关闭认证（仅建议纯内网）。
- **U 盘管理模式**：点「连接车机U盘」会临时停止导出并挂载镜像，5 分钟无操作自动恢复；行车 / 哨兵活跃时不要长时间挂着。
- **锁车音**：WAV、44.1kHz、16-bit、<1MB、1–5 秒，文件名 `LockChime.wav` 写入 U 盘根目录。
- **API**：事件、媒体、遥测、上传、文件管理、OTA、推送等数十个接口，以 `sentry-viewer.py` 的 `do_GET` / `do_POST` / `do_DELETE` 为准。

改完配置重启服务：

```sh
/etc/init.d/tesla-viewer restart
/etc/init.d/tesla-live restart
/etc/init.d/tesla-gadget restart
```

---

## 常见问题

- **车机不认盘 / 提示格式化**：确认 `/sys/class/udc/` 有控制器、`grep exfat /proc/filesystems` 有输出，再跑 `/opt/tesla-sentry/tesla-gadget.sh status`。新部署已预置 exFAT，正常不会再提示；旧版安装留下的全零空盘重装时会自动补写。若 `--size` 超过 128G（车机同款布局的寻址上限），会退回空白盘由车机格式化一次；想手动补写可执行 `python3 /opt/tesla-sentry/mkexfat.py <镜像路径> <容量GB>`（镜像里有数据时它会拒绝覆盖）。
- **播放黑屏**：视频是 H.265 / HEVC，Windows 用 Edge / Chrome（需 HEVC 解码器）、iPhone Safari 原生支持，部分安卓浏览器不支持。
- **网页没有事件**：看 `/opt/tesla-sentry/` 下的 `tesla-live.log`、`tesla-sync.log`；先确认顶部同步状态胶囊在线，再核对归档目录与镜像路径。
- **缩略图一直不出现 / 点开视频缓冲很久**：先看页面顶部有没有「FFmpeg 未配置」红条，或执行 `grep TSF_FFMPEG /etc/tesla-sentry.conf` 后用该路径跑一次 `-version`。缺少 `ffmpeg`/`ffprobe` 时缩略图会全部 404、清晰度转码会退化成原画（公网链路上就表现为一直缓冲）。修复：先按 OpenWrt 版本安装 ffmpeg（24.10 及更早 `opkg install ffmpeg`，25.12 起 `apk add ffmpeg`），再执行 `sh install.sh apply --install-deps --ffmpeg /usr/bin/ffmpeg`（已安装的设备也会走这条修复路径：补齐依赖 → 写回配置 → 重启服务）。配置里的 `TSF_FFMPEG`/`TSF_FFPROBE` 应指向 `/usr/bin/ffmpeg`、`/usr/bin/ffprobe`，不要再用来源不明的 aarch64 静态包。
- **存储分配填 0**：「归档区总上限」填 0 = 不设上限（不再按总量清理，只有磁盘空闲低于保护线时才从最旧清起）；驾驶记录上限仍须 ≥1，且不能超过归档上限。
- **手动只覆盖主程序**：`sentry-viewer.py` 在顶层使用同目录的 `storage_allocation.py` 与 `storage_resize.py`。2.8.43 起主程序内嵌了两份 gzip 副本，同目录找不到文件时会自动顶上，所以只替换主程序也能正常启动（`--ota-check` 会显示 `modules=embedded`）。网页 OTA 会自动把主程序、本地模块和 `tesla-live-sync.py` 一起下载、一起自检、一起安装，缺文件或模块损坏时**拒绝升级、保留旧版**；手动升级仍建议把相关 `.py` 一起复制，或直接重跑 `install.sh`（方便单独热修模块）。

### 检查更新失败（连不上 GitHub）

国内网络下 `raw.githubusercontent.com`、`fastly/testingcf.jsdelivr.net` 基本会被 RST 或 TLS 干扰，第三方反代也会时通时断，所以「检查更新」偶尔会报**所有下载源均不可用**。2.8.13 起的处理方式：

- 内置 7 个源（`ghfast.top`、`gcore.jsdelivr.net`、`ghproxy.net`、`gh-proxy.org`、`gh-proxy.com`、raw、jsDelivr）**并发竞速**，任一可用即可，不再按顺序一个个等超时；
- 报错会逐个列出失败的源及其原因（超时 / 连接被阻断 / 证书被劫持 / DNS 解析失败），一眼能看出是墙还是镜像挂了；
- 自动更新默认开启，网络恢复后每 5 分钟重试一次并自动升级，不用一直手点。

想自己加源，把可用的镜像写进配置即可（逗号分隔，`https://域名/<仓库路径>/main` 形式）：

```bash
echo 'TSF_OTA_SOURCES=https://ghfast.top/https://raw.githubusercontent.com/baicyber/tesla-sentry-f50/main,https://gcore.jsdelivr.net/gh/baicyber/tesla-sentry-f50@main' >> /etc/tesla-sentry.conf
/etc/init.d/tesla-viewer restart
```

**老版本（2.8.12 及更早）连检查都失败、进不了升级入口**时，直接在设备上手动覆盖一次，之后就回到自动更新：

```bash
wget -O /tmp/sv.py https://ghfast.top/https://raw.githubusercontent.com/baicyber/tesla-sentry-f50/main/sentry-viewer.py \
  || wget -O /tmp/sv.py https://gcore.jsdelivr.net/gh/baicyber/tesla-sentry-f50@main/sentry-viewer.py
python3 -m py_compile /tmp/sv.py && cp /tmp/sv.py /opt/tesla-sentry/sentry-viewer.py \
  && chmod 755 /opt/tesla-sentry/sentry-viewer.py && /etc/init.d/tesla-viewer restart
```

### 导出勾了水印，视频里却没有水印

设备端烧水印（`ffmpeg` + libass）需要**中文字体**。2.8.18 及更早版本在字体缺失时会静默跳过整条字幕链 —— 视频照样导出成功，但一个字都没有。2.8.19 起：

- 打开导出面板会显示「水印字体」状态；缺失时给出 **安装字体**（一键从官方 `google/fonts` 仓下载，约 17MB，多镜像重试）和 **上传**（本地 ttf/otf）两个按钮；
- 缺字体时导出会被明确拦下并提示，不会再给你一段没水印的视频；
- 字体探测会在 `/opt/tesla-sentry/fonts` 之外兜底扫描系统字体目录，已经装过字体的盒子升级后立刻可用。

命令行手动补字体（无外网时从别处拷贝也行）：

```bash
mkdir -p /opt/tesla-sentry/fonts
wget -O /opt/tesla-sentry/fonts/NotoSansSC.ttf \
  "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf" \
  || wget -O /opt/tesla-sentry/fonts/NotoSansSC.ttf \
  "https://ghfast.top/https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf"
ls -l /opt/tesla-sentry/fonts/     # 应约 17MB
/etc/init.d/tesla-viewer restart
```

更多排障见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)；实现原理（exFAT 只读解析、双链路互斥、SEI 解码、U 盘管理状态机）见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 安全提示

- `install.sh` 默认开启全站登录保护；**直接裸跑 `python3 sentry-viewer.py` 且没有 `auth.conf` 时认证是关闭的**，只可在可信内网使用。删除 `auth.conf` 仍是「关闭认证」的开关，但文件存在却读不出密码时服务会**拒绝启动**，不会静默裸奔。
- 登录页与 HTTP Basic 共用限速：同一 IP 10 分钟内错 8 次锁 5 分钟，Basic 方式也绕不过去。
- 上传限制 100MB，普通 JSON 接口请求体限制 1MB，单连接读写超时 30 秒、并发线程上限 48；路径被约束在归档区 / 镜像挂载点内，不可逃逸到系统目录。
- 认证与 Cookie 走 HTTP 明文，公网访问务必套 HTTPS 或 VPN；反向代理请传 `X-Forwarded-Proto: https`（或设置 `TSF_COOKIE_SECURE=1`），登录 Cookie 会自动带 `Secure`。

## 免责声明

本项目为个人爱好项目，与特斯拉公司无关。修改车机存储行为存在风险（数据丢失、录制中断等），请自行评估，建议先用小容量镜像验证。

## 参考与致谢

- [teslausb](https://github.com/marcone/teslausb) —— 树莓派伪装 U 盘归档的开山之作，本项目思路来源
- [SentryBox](https://github.com/weitanism/sentrybox) —— 车机常驻设备的产品化实践
- [tesla-sentry-viewer](https://github.com/denysvitali/tesla-sentry-viewer) —— 网页查看器形态参考

## ☕ 赞助

> 本项目完全开源免费，如果它帮到了你，欢迎支持博主一些token，助力项目持续更新~

<table>
  <tr>
    <td align="center"><img src="docs/sponsor/web_alipay.jpg" width="240" alt="支付宝收款码"><br><sub><b>支付宝</b></sub></td>
    <td align="center"><img src="docs/sponsor/web_wechatpay.jpg" width="240" alt="微信收款码"><br><sub><b>微信支付</b></sub></td>
  </tr>
</table>

## 💬 交流群

> 遇到问题、想交流玩法，欢迎扫码加入群聊~

<table>
  <tr>
    <td align="center"><img src="docs/sponsor/web_qqgroup.jpg" width="240" alt="QQ群二维码"><br><sub><b>QQ 群：831426822</b></sub></td>
    <td align="center"><img src="docs/sponsor/web_wechatgroup.jpg" width="240" alt="微信群二维码"><br><sub><b>微信群</b>（二维码 11月12日前有效，过期进群请联系作者或QQ群）</sub></td>
  </tr>
</table>

**交流加群请联系作者**：群二维码过期、或想直接交流玩法，可关注抖音 `@耶基大学懂事长`（抖音号 `yejiuni`），或扫码添加作者微信好友拉你进群。

<table>
  <tr>
    <td align="center"><img src="docs/sponsor/web_douyin.jpg" width="240" alt="抖音二维码"><br><sub><b>抖音 · @耶基大学懂事长</b></sub></td>
    <td align="center"><img src="docs/sponsor/web_wechatauthor.jpg" width="240" alt="作者微信二维码"><br><sub><b>微信 · 联系作者</b></sub></td>
  </tr>
</table>

## License

[MIT](LICENSE)
