# 特斯拉哨兵云盘 · Tesla Sentry Drive

**中文** | [English](README.en.md)

**虚拟哨兵 U 盘 · 多路同步回放 · SEI 行车遥测 · 水印导出 —— 专为中兴 F50 优化。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![零第三方依赖](https://img.shields.io/badge/deps-%E9%9B%B6%E7%AC%AC%E4%B8%89%E6%96%B9%E5%BA%93-brightgreen.svg)](#功能列表)

把**中兴 F50 存储版**顶替原车 U 盘插进手套箱：车机照常录制，手机连 F50 WiFi 打开网页即可回看 4 / 6 路同步画面，逐帧读取视频码流里的 SEI 行车遥测（挡位 / 车速 / 油门 / 刹车 / 方向盘 / 转向灯 / AP / 坐标），并导出带行车信息水印的视频。

本项目**仅支持中兴 F50**，不提供其他随身 WiFi、开发板、NAS、x86 或通用 Linux 部署模式。

---

## 功能列表

| 功能 | 说明 |
|---|---|
| 虚拟 U 盘 | 稀疏 exFAT 镜像经 USB Gadget `mass_storage` 导出，网页设容量后即插即用 |
| 实时同步 | `tesla-live-sync` 每 15 秒**只读解析**镜像，不挂载、不断 USB，车机边写边看 |
| 多路回放 | 单路点开即播；全览 4 路 2×2 / 6 路 3×2，250 ms 同步校正，切换保留进度 |
| 行车遥测 | 直读码流 SEI：挡位 / 车速 / 油门 / 刹车 / 方向盘 / 转向灯 / AP / 坐标 / 加速度 |
| 清晰度 | 原画 / 720p / 480p / 360p，服务端转码缓存，首帧先发已有档、不等转码 |
| 预转码 | 最新事件后台预转 480p，仅无人看视频时跑；实时滚动记录不预转 |
| 筛选定位 | 触发原因 + 日期筛选，`?ev=` `?date=` `?cat=` `?cam=` 深链直达 |
| 实时滚动 | 连续播放器覆盖整个滚动时长，侧栏按分钟定位，多档画质 |
| 视频导出 | 单路 / zip / 多路合成，可选清晰度，设备端直出带水印 H.264 mp4 |
| 文件管理 | U 盘与归档合并一页，上传 / 下载 / 移动 / 复制 / 重命名 / 删除，支持多选 |
| 驾驶记录 | `DriveClips` 保留原画，按行程成段展示，额度满自动清理较旧行程 |
| 推送通知 | 飞书 / 微信 / Bark，触发截图或短视频，标题正文模板 + 实时样式预览 |
| 存储分配 | 统一设置 U 盘 / 归档 / 驾驶记录 / 安全预留，容量条按真实占用显示 |
| 录像保护 | 锁定后同步、清理、文件管理均不触碰；已删除事件不被重新抄回 |
| 在线升级 | 多源并发竞速 + GitHub API 兜底，原子替换，起不来自动回滚 |
| 登录保护 | 随机 10 位密码，HMAC cookie 保留 30 天，10 分钟错 8 次返回 429 |
| 个性化 | 品牌图标 / 名称、深浅主题、4 套界面风格、10 种语言、网页背景换肤 |

> 版本迭代记录见 [docs/RELEASE-NOTES.md](docs/RELEASE-NOTES.md)。

---

## 支持设备

**仅支持中兴 F50 存储版**。已实测：紫光展锐 T760 / aarch64 / 1.4 GB RAM / eMMC 256 GB，OpenWrt 25.12.5、内核 7.2.9、`25100000.dwc3`；车机兼容 2022 Model Y（4 路）及新款 6 路录像。

运行要求：F50 OpenWrt + procd、USB Gadget / configfs、内核 exFAT、loop / losetup、root、Python 3.11+；镜像与归档建议放在 TF 卡；ffmpeg / ffprobe 用于缩略图、转码与水印导出。

装之前先自检（只读、不改系统）：

```sh
sudo ./install.sh check
```

详细要求见 [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)。

---

## 安装部署

### 全新安装

在设备 SSH 中执行：

```sh
git clone https://github.com/jiaoting/tesla-sentry-f50.git
cd tesla-sentry-f50

sudo ./install.sh check                                        # 仅预检（只读，不改系统）
sudo ./install.sh install --port 8080 --data-mount /mnt/sdcard # 预检 + 安装 + 启动服务
sudo ./install.sh apply   --port 8080 --data-mount /mnt/sdcard # 预检 + 安装，不自动启动
sudo ./install.sh install --init-card                          # 数据卡未初始化时自动格式化为 ext4（会清空该卡）
sudo ./install.sh install --install-deps                       # 补齐 / 修复 ffmpeg、ffprobe 并写回配置
sudo ./install.sh rollback                                     # 网页打不开时回滚到上次升级前
sudo ./install.sh --help                                       # 查看全部参数
```

| 命令 / 参数 | 作用 |
|---|---|
| `./install.sh check` | 只读自检：内核 gadget / exFAT / loop / Python / FFmpeg / 权限，不改系统 |
| `./install.sh install` | 预检 + 安装到 `/opt/tesla-sentry/`、写 `/etc/tesla-sentry.conf`、注册 procd 与定时任务、启动 |
| `./install.sh apply` | 与 `install` 相同的安装动作，但不启动服务 |
| `./install.sh rollback` | 回滚到最近一次升级前的程序与配置（网页打不开时的救命命令） |
| `--port 8080` | 网页端口，默认 8080 |
| `--data-mount /mnt/sdcard` | 镜像 + 归档所在数据盘挂载点，建议 TF 卡 |
| `--init-card` | 数据卡未初始化时允许格式化为 ext4（会清空该卡数据） |
| `--start` | 仅与 `apply` 搭配：安装完成后立即启动 |
| `--install-deps` | 安装缺失的 ffmpeg / ffprobe 并写回配置；已装设备修复也用这条 |
| `--ffmpeg` / `--ffprobe` | 显式指定 ffmpeg / ffprobe 路径 |
| `--size-gb` / `--archive-gb` / `--drive-gb` | 兼容旧命令保留；全新安装不预建镜像，容量统一在网页「存储分配」设置 |
| `sudo ./uninstall.sh` | 停服务、删程序与缓存，保留镜像和归档录像 |
| `sudo ./uninstall.sh --purge` | 连镜像和归档一起删（不可恢复） |

安装流程：检查 F50 内核 gadget / exFAT / Python / FFmpeg → 装到 `/opt/tesla-sentry/`、写 `/etc/tesla-sentry.conf` → 注册 OpenWrt procd 服务与定时任务 → 生成随机登录密码 → 启动。

### 升级

已安装设备升级（保留配置与录像）：

```sh
cd /root/tesla-sentry-f50
git pull
sudo ./install.sh
```

也可在网页「设置 → 更新」里一键升级 / 回滚；配置与录像归档不在升级范围内。若升级后网页打不开，SSH 执行 `sudo ./install.sh rollback` 即可恢复上一版。

### 首次使用

1. 全新安装**不预建镜像**。打开 `http://<F50-IP>:8080`，登录后按引导进入「设置 → 存储分配」，设置车机 U 盘容量、归档区上限与安全预留，点「调整 U 盘容量」创建镜像。
2. 插进手套箱 USB 口，车机识别并开始录制。首次插入若提示「格式化 USB 设备」，点确认即可；网页会自动识别新的分区 / 卷偏移。
3. 手机连设备热点（或 USB 网卡直连 / Tailscale / frp）打开 `http://<设备IP>:8080`，账号 `admin`，密码见安装输出或 `/opt/tesla-sentry/auth.conf`。

TF 卡无需提前挂载：`install` 会检测未挂载 / 未初始化的卡，交互询问后直接格式化为 ext4 并挂载（会清空该卡）；非交互环境加 `--init-card`。

---

## 针对 F50 的专门优化

针对 F50 的 1.4 GB 内存、无风扇与 4G 上行做了系统性优化，**所有优化都在设备端后台常驻执行，网页关着、电脑关机也照常跑**。

### 磁盘管理与占用优化

- **稀疏 exFAT 镜像**：车机 U 盘镜像按实际写入量计占用，不预占满分配容量；首页容量条按卷内真实写入统计。
- **真实占用统计**：归档 / 镜像 / 缓存均以文件系统分配块（`st_blocks`）累加，硬链接、空洞文件不重复或夸大计算。
- **跨分类硬链接去重**：`dedupe_archive_clips` 把多分类间内容相同的片段合并为指向原片的硬链接，同一 inode 只占一份数据块。
- **孤儿缓存清理**：`prune_orphan_transcodes` 每小时校验，源视频已删的转码缓存一并清除；`res_cleanup` 超上限时按 mtime 从最旧淘汰。
- **墓碑防回灌**：已清理事件写 `.pruned` 墓碑，同步端读到即跳过，避免「删除 → 重抄」死循环。
- **幽灵占用回收（可选）**：`reclaim_ghost_allocations` 回收 exFAT 位图里已删未释放的占用（设 `TSF_RECLAIM_MIN_MB` 为正值启用，默认关闭，仅空闲时动手）。
- **安全预留兜底**：归档 / 驾驶记录按容量、事件数、保留天数先到先清；卡空闲跌破 10 GB 从最旧删起，回到 10 GB 以上才停；跌破暂停线（默认 4 GB）暂停新增同步并强制腾空间，不会把卡写满导致车机异常。
- **操作联动**：外置卡卸载 / 格式化、U 盘调整容量 / 格式化前自动停同步、解绑 `mass_storage`、释放 loop，完成后恢复导出并补齐目录，不再「设备忙」「卡卸不掉」。
- **UUID 写回与目录自愈**：格式化为 ext4 时写回新 UUID，开机按 UUID 挂载；归档及各分类目录缺失自动建立。

### 播放与转码

- **秒开不等转码**：目标清晰度没转好时先发首帧 / 已有压缩档，转码交后台线程；优先用 `faststart` 产物，首帧从 3–6 秒降到约 0.3 秒。
- **分片转码 + 缓存复用**：预览按 1 秒分片生成，切段 / 回看命中缓存；缓存版本号内嵌文件名，升级参数自动失效旧缓存。
- **起播缓冲 10 秒 + 预读 20–25 秒**：边播边缓存，弱网停顿少；长按画面 / 空格 3× 快进用独立倍率覆盖，不被播放循环重置。
- **画质档位**：720p / 480p / 360p 用 1500 / 900 / 520 kbps，放宽峰值与缓冲区，动态画面方块明显减少。
- **温控预转**：预转码仅 `MEDIA_ACTIVE` 归零时跑；实时滚动 / 准实时预览不预转，避免无风扇设备持续满载、核心温度长期偏高。
- **缩略图限流**：列表缩略图与推送封面共用缓存，服务端限制并发 ffmpeg，避免一次打开几十个事件拖死设备。

### 行车信息

- **直读码流 SEI**：挡位 / 车速 / 油门 / 刹车 / 方向盘角度 / 转向灯 / AP 状态 / 坐标 / 三轴加速度，随进度逐帧跳动，不依赖车机网络上传。
- **行驶信息条**：桌面端与「全览」同排靠右；窄屏自动移到下方分两行，顺序为速度 / 挡位 / 转向灯 / 方向盘+AP / 刹车 / 油门 / 加速度 / 地址 / 详情折叠。
- **`event.json` 元数据**：提供时间 / 城市 / 街道 / 触发原因 / 触发画面，供列表、推送与导出使用。

### 网络访问与升级

- **OTA 多源竞速**：内置多源并发 + GitHub API 实时兜底，主程序与依赖模块一起下载、自检、原子替换、重启探活，起不来自动回滚。
- **自动升级**：检测到新版且**无人看视频**时自动安装；未勾选自动更新时仅提示。
- **访问方式**：手机连设备热点、USB 网卡直连，或自行加 Tailscale / frp / 反向代理。
- **服务与数据解耦**：procd 自动拉起、开机自检、LUN 被弹出后定时重挂、断电自愈；数据卡异常也能进网页排查 / 格式化。

### 推送

- **多渠道**：飞书（自建应用私聊 / 群机器人 webhook）、微信（爱语飞飞 / Server酱）、Bark（iOS 通知，分组 / 铃声 / 图标 / 触发截图）。
- **内容模板**：标题 / 正文支持 `{类型}{时间}{位置}{原因}{分类}{摄像头}` 变量，设置页内实时预览样式。
- **可靠性**：触发截图或前 5 秒短视频，可烧行车信息水印；去重、延迟 20 秒、失败重试 3 次。
- **TLS 隔离**：飞书网关锁 TLS 1.2 兼容与重试，Bark / 微信走默认 TLS，互不影响。

### 地址反推

- 坐标经高德逆地理解析为省 / 市 / 区 / 街道 / 门牌，同一位置缓存；服务端每分钟最多 6 次、最快 10 秒一次，避免超配额。
- 「地址显示格式」可用 `{省}{市}{区}{街道}{门牌}{详细地址}{坐标}` 自行拼接，列表、驾驶记录起终点与推送位置统一生效，留空为默认。
- 旧缓存缺字段时自动从完整地址反推写回，升级后已有录像也按新格式显示。

### 水印导出

- 导出可选单路 / zip / 多路合成（2×2，3 路时第三路铺满下排），清晰度原画 / 720p / 480p / 360p。
- 多画面按原片 4:3 取格子不留黑边；带水印一律由设备端 ffmpeg 直出 H.264 mp4（+faststart，微信 / 相册可直接发）。
- 水印与网页播放器同款：左侧时间 / 地点，右侧车速 / 转向灯 / 挡位 / 刹车 / 油门，外加 AP 方向盘图标（辅助驾驶启动蓝色、未启动灰色）。
- 片段无行车遥测（停车哨兵）时水印退化为只显示时间 + 地点，不会悄悄不出水印。
- 缺中文字体时导出明确拦下并提示，可一键从 `google/fonts` 下载 NotoSansSC（约 17 MB）或本地上传 ttf / otf。

---

## 配置与常用设置

主配置 `/etc/tesla-sentry.conf`（改完重启服务生效）：

| 变量 | 默认 | 作用 |
|---|---|---|
| `TSF_IMG` / `TSF_ARCHIVE` | `/mnt/sdcard/tesla-sentry/img/teslacam.img` / `/mnt/sdcard/tesla-sentry/archive` | 虚拟 U 盘镜像 / 归档区 |
| `TSF_PORT` | `8080` | 网页端口 |
| `TSF_LIVE_INTERVAL` | `15` | 在线只读同步轮询秒数 |
| `TSF_RECENT_ROLL_MIN` | `60` | RecentClips 滚动窗口（分钟），0 = 不滚动 |
| `TSF_BOX_KEEP_DAYS` | `14` | 盒子侧哨兵 / 手动保存归档保留天数，0 = 不限 |
| `TSF_FREE_MIN_MB` / `TSF_FREE_TARGET_MB` | `4096` / `10240` | 空间兜底暂停线与清理目标 |
| `TSF_PREWARM_SENTRY` / `TSF_PREWARM_SAVED` | `3` / `3` | 哨兵 / 手动保存各预转码最新事件数，0 = 关闭 |
| `TSF_RECLAIM_MIN_MB` | `0` | exFAT 幽灵占用回收阈值，0 = 关闭 |

OTA、推送、认证参数不写入此 conf，走环境变量或网页配置。完整变量表见 [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)。

网页 **⚙ 设置** 子页签：存储分配、播放设置、文件管理、锁车语音、涂装、推送、账号、语言、外观、更新、关于。常用项：

- **存储分配**：设置 U 盘 / 归档 / 驾驶记录容量与安全预留，调整 / 格式化 U 盘，外置卡挂载卸载。
- **外观**：界面风格（默认 / Apple / Claude / 像素）、背景、品牌图标与名称（≤24 字、≤3 MB），登录页同步。
- **推送**：先选渠道再填配置，配好点「发送测试」；截图依赖「查看链接前缀」的公网地址。
- **账号**：改密码 / 退出；凭据存 `/opt/tesla-sentry/auth.conf`（权限 600），删除该文件并重启即关闭认证（仅建议纯内网）。
- **更新**：比对仓库 `version.json`，一键升级 / 回滚，可选自动更新与自定义源。

改完配置重启服务：

```sh
/etc/init.d/tesla-viewer restart
/etc/init.d/tesla-live restart
/etc/init.d/tesla-gadget restart
```

---

## 常见问题

- **车机不认盘 / 提示格式化**：确认 `/sys/class/udc/` 有控制器、`grep exfat /proc/filesystems` 有输出，再跑 `/opt/tesla-sentry/tesla-gadget.sh status`；首次插入提示格式化点确认即可，网页自动识别新卷偏移。
- **播放黑屏**：视频是 H.265 / HEVC，Windows 用 Edge / Chrome（需 HEVC 解码器）、iPhone Safari 原生支持，部分安卓浏览器不支持。
- **网页没有事件**：看 `/opt/tesla-sentry/` 下 `tesla-live.log`、`tesla-sync.log`；确认顶部同步状态在线，再核对归档目录与镜像路径。
- **缩略图不出现 / 缓冲很久**：缺少 ffmpeg / ffprobe。按 OpenWrt 版本安装（24.10 及更早 `opkg install ffmpeg`，25.12 起 `apk add ffmpeg`），再执行 `sudo ./install.sh install --install-deps` 写回配置；`TSF_FFMPEG` / `TSF_FFPROBE` 应指向 `/usr/bin/ffmpeg`、`/usr/bin/ffprobe`。
- **存储分配填 0**：归档区总上限填 0 = 不设上限，仅磁盘空闲低于保护线时从最旧清起；驾驶记录上限仍须 ≥1。
- **检查更新失败（连不上 GitHub）**：内置多源并发竞速，任一可用即通；报错会列出各源失败原因。可自行追加镜像源：
  ```bash
  echo 'TSF_OTA_SOURCES=https://ghfast.top/https://raw.githubusercontent.com/jiaoting/tesla-sentry-f50/main,https://gcore.jsdelivr.net/gh/jiaoting/tesla-sentry-f50@main' >> /etc/tesla-sentry.conf
  /etc/init.d/tesla-viewer restart
  ```
  老版本连检查都失败时，手动覆盖一次主程序即可回到自动更新：
  ```bash
  wget -O /tmp/sv.py https://ghfast.top/https://raw.githubusercontent.com/jiaoting/tesla-sentry-f50/main/sentry-viewer.py \
    || wget -O /tmp/sv.py https://gcore.jsdelivr.net/gh/jiaoting/tesla-sentry-f50@main/sentry-viewer.py
  python3 -m py_compile /tmp/sv.py && cp /tmp/sv.py /opt/tesla-sentry/sentry-viewer.py \
    && chmod 755 /opt/tesla-sentry/sentry-viewer.py && /etc/init.d/tesla-viewer restart
  ```
- **导出勾了水印却没有**：设备端烧水印需要中文字体。缺字体时导出会被拦下，可在导出面板一键安装 NotoSansSC 或上传 ttf / otf；手动补字体：
  ```bash
  mkdir -p /opt/tesla-sentry/fonts
  wget -O /opt/tesla-sentry/fonts/NotoSansSC.ttf \
    "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf" \
    || wget -O /opt/tesla-sentry/fonts/NotoSansSC.ttf \
    "https://ghfast.top/https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf"
  /etc/init.d/tesla-viewer restart
  ```

更多排障见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)；实现原理（exFAT 只读解析、双链路互斥、SEI 解码、U 盘管理状态机）见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 安全提示

- `install.sh` 默认开启全站登录保护；直接裸跑 `python3 sentry-viewer.py` 且无 `auth.conf` 时认证关闭，仅限可信内网。`auth.conf` 存在却读不出密码时服务会**拒绝启动**，不会静默裸奔。
- 登录页与 HTTP Basic 共用限速：同一 IP 10 分钟内错 8 次锁 5 分钟，Basic 绕不过去。
- 上传限制 100 MB，普通 JSON 接口请求体限制 1 MB，单连接读写超时 30 秒、并发线程上限 48；路径约束在归档区 / 镜像挂载点内，不可逃逸到系统目录。
- 认证与 Cookie 走 HTTP 明文，公网访问务必套 HTTPS 或 VPN；反向代理请传 `X-Forwarded-Proto: https`（或设 `TSF_COOKIE_SECURE=1`），登录 Cookie 会自动带 `Secure`。

---

## 免责声明

本项目为个人爱好项目，与特斯拉公司无关。修改车机存储行为存在风险（数据丢失、录制中断等），请自行评估，建议先用小容量镜像验证。

---

## 上游感谢

- [baicyber/tesla-sentry-f50](https://github.com/baicyber/tesla-sentry-f50) —— 本项目的上游仓库，本项目在其基础上继续开发，感谢原作者的开源与持续贡献
- [teslausb](https://github.com/marcone/teslausb) —— 树莓派伪装 U 盘归档的开山之作，本项目思路来源
- [SentryBox](https://github.com/weitanism/sentrybox) —— 车机常驻设备的产品化实践
- [tesla-sentry-viewer](https://github.com/denysvitali/tesla-sentry-viewer) —— 网页查看器形态参考

---

## License

[MIT](LICENSE)
