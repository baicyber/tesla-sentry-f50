# 兼容性 / 中兴 F50 专用要求

本项目仅支持**中兴 F50 存储版**。其他随身 WiFi、开发板、电视盒子、手机、NAS、x86 设备以及通用 Linux 部署模式均已停止支持，安装器也不再提供这些分支。

## 1. 已验证硬件与系统

| 项目 | 已验证配置 |
|---|---|
| 设备 | 中兴 F50 存储版 |
| 平台 | 紫光展锐 T760 / aarch64 / 8 核 / 1.4G RAM |
| 系统 | OpenWrt 25.12.5（armsr/armv8），procd |
| 内核 | 7.2.9 |
| USB 控制器 | `25100000.dwc3` |
| 数据存储 | 建议使用 TF 卡，默认挂载到 `/mnt/sdcard` |
| 车机 | 2022 Model Y（4 路）；支持新款 6 路录像 |

Windows 和 macOS 只能作为浏览器客户端访问 F50，不作为服务端部署平台。

## 2. 必需条件

| 要求 | 检查方法 |
|---|---|
| root 权限 | `id -u` 输出 `0` |
| aarch64 中兴 F50 | `uname -m` 输出 `aarch64` |
| OpenWrt procd | `/etc/rc.common` 与 `/sbin/procd` 存在 |
| USB Device / Gadget | `/sys/class/udc/` 非空 |
| configfs + mass_storage | `/sys/kernel/config/usb_gadget` 存在 |
| 内核 exFAT | `grep exfat /proc/filesystems` |
| loop、mount、losetup | `losetup -f` 可执行 |
| Python 3.11+ | `python3 --version`，或使用仓库内 F50 离线 runtime |
| cron | `/etc/crontabs/root` 可写 |

ffmpeg / ffprobe 用于缩略图、清晰度转码和水印导出，推荐 `/usr/bin/ffmpeg` 与 `/usr/bin/ffprobe`。缺少时安装器可配合 `--install-deps` 补齐。

## 3. 安装入口

```sh
sudo ./install.sh                    # 预检、安装并启动
sudo ./install.sh f50-check          # 仅预检
sudo ./install.sh f50-apply          # 安装但不启动
sudo ./install.sh f50-install        # 安装并启动
sudo ./install.sh f50 --help         # 查看容量、端口与依赖参数
```

默认将数据放在 `/mnt/sdcard/tesla-sentry`：

- 车机 U 盘镜像：`img/teslacam.img`
- 录像归档：`archive/`
- 转码缓存：`archive/transcode_cache/`
- 日志：`logs/`

镜像默认 64G，最大 128G，且不能超过数据盘实际容量的一半；还需为归档区和安全预留留出空间。

## 4. 供电、散热与存储寿命

- 使用车机 USB 供电，避免额外加装高功耗设备。
- F50 无电池，适合车内长期运行；仍应保持散热通畅。
- 持续录像与转码会产生热量。转码器按温度动态限核、降速或暂停，六路播放的首开与连续性仍应以实际车辆、固件和 TF 卡环境下的真机测试为准。
- 镜像、归档和转码缓存共用数据盘，容量分配不能超过实际可用空间。
- 车机可能直接断电，归档区使用 ext4，并由开机自检与自愈脚本处理异常。

## 5. 常用配置

`/etc/tesla-sentry.conf` 由安装器生成。每个选项与自己的注释为一组，不同选项之间保留空行；修改后重启对应服务。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TSF_VIDEO_ROOT` | `/mnt/sdcard/tesla-sentry` | 视频存储根目录 |
| `TSF_IMG` | `$TSF_VIDEO_ROOT/img/teslacam.img` | 虚拟 U 盘镜像 |
| `TSF_ARCHIVE` | `$TSF_VIDEO_ROOT/archive` | 归档区目录 |
| `TSF_TRANSCODE_DIR` | `$TSF_ARCHIVE/transcode_cache` | 转码缓存目录 |
| `TSF_PORT` | `8080` | 网页端口 |
| `TSF_FFMPEG` / `TSF_FFPROBE` | `/usr/bin/ffmpeg` / `/usr/bin/ffprobe` | 转码工具 |
| `TSF_LIVE_INTERVAL` | `15` | 在线只读同步轮询秒数 |
| `TSF_RECENT_ROLL_MIN` | `60` | 实时滚动记录保留分钟 |
| `TSF_RECENT_MAX_GB` | `12` | 实时滚动记录容量上限 G，0 为不限 |
| `TSF_DRIVE_MAX_GB` | `12` | 驾驶记录容量上限 G |
| `TSF_ARCHIVE_TOTAL_GB` | `34` | 归档区总容量上限 G，0 为不限 |
| `TSF_FREE_MIN_MB` / `TSF_FREE_TARGET_MB` | `4096` / `10240` | 空闲空间保护阈值与清理目标 |
| `TSF_IDLE` | `600` | 镜像空闲多少秒后允许挂载写 Boombox 音频 |

## 6. 快速自检

```sh
sh scripts/tsf-check.sh
sh scripts/tsf-check.sh --json
```

自检不通过时，先按输出修复 F50 的 UDC、configfs、exFAT、procd、Python 或存储挂载，再运行安装器。项目不提供退回到其他设备或纯网页查看器的部署方式。
