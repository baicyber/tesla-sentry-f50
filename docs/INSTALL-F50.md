# 全新 F50 安装

本流程安装当前仓库的查看器、录像同步、虚拟 U 盘和开机服务。目标是已能通过 SSH 登录的 ARM64 F50，运行兼容的 OpenWrt/procd 固件。仓库不包含刷机、解锁或内核安装程序；原厂系统尚未具备这些条件时，需先完成固件环境准备。

`install-f50.sh` 默认只检查。指定 `--apply` 才写入文件，`--apply --start` 在安装后启动服务。脚本不分区、不格式化物理存储卡、不替换已有安装、不重启设备。请在**新 F50** 上执行，勿把现役设备地址作为安装目标。

## 1. 选择离线完整包

维护者可使用 `scripts/build-offline-package.py` 制作 `f50-offline-*.tar.gz`；本 PR 不附带运行环境二进制或已发布的离线包。以下完整包说明适用于含已校验 `runtime/` 的构建：Python 3.13.18、标准库和配套动态库、FFmpeg/FFprobe 7.0.2 ARM64 静态构建、BusyBox、blkid、flock、Noto Sans SC 字体及公共 CA 根证书。安装不下载运行依赖，不执行 pip、apk 或 opkg。

把压缩包传到新设备的已挂载数据盘，在新设备 SSH 终端解压：

```sh
cd /mnt/mmc
tar -xzf f50-offline-<版本及提交号>.tar.gz
cd tesla-sentry-f50
sh install-f50.sh --check
sh install-f50.sh --apply --start
```

请将文件名替换为实际包名。使用 tar 解包以保留执行权限；不要把包解到容量有限的 `/tmp` 或系统根分区。压缩包旁的 `.sha256` 可用 `sha256sum -c <文件名>.sha256` 校验。包内 `PACKAGE.json` 记录源码提交，`runtime/manifest.json` 记录依赖的大小、权限和 SHA-256，`runtime/licenses/` 保留来源与许可。

运行环境会复制到 `/opt/tesla-sentry/runtime`，不覆盖系统 Python 和共享库。程序、子进程和服务均使用包内解释器；安装后不依赖解包目录。`--check` 会验证文件哈希、Python 子进程、磁盘工具，并实际测试四路合成、中文水印和 H.264 编码。

完整包仍要求 F50 的基础固件已提供 OpenWrt/procd、cron、ext4/exFAT、loop、configfs 和 USB Device 控制器；这些内核及板级功能不能由用户态运行包补齐。本包不包含原厂 Android 到 OpenWrt 的刷机过程，也不自动分区或格式化物理卡。

仅从 GitHub 下载的源码 ZIP **不含** `runtime/`。使用源码安装时，需自行满足下面的环境要求；离线完整包已包含其中的用户态依赖。

### 源码安装的运行环境

- `/usr/bin/python3`：Python 3.11+，含 `ssl`、`fcntl`、`http.server`、`zipfile` 等标准库，无运行时 pip 依赖。网页已内嵌，无需 Node.js 或前端构建。
- OpenWrt 的 `/etc/rc.common`、procd、cron，以及 `blkid`、`flock`、`mount`、`umount`、`readlink`、`setsid`、`losetup`。
- 内核具备 ext4、exFAT、configfs、loop、USB mass storage；已加载对应模块或编译为内建支持。
- 固件已经建立一个 USB Gadget 和一个 configuration；存在 USB Device 控制器。脚本保留固件的网卡、串口等功能，只增加 mass storage。多控制器设备须先由固件绑定正确控制器，安装器会记录该控制器。
- FFmpeg 和 FFprobe 使用适配 ARM64 Linux 的可执行文件；FFmpeg 必须支持 `libx264`、`scale`、`pad`、`fps`、`xstack`、`setsar`、`subtitles`。安装检查会实际编码一帧。

先确认固件使用的包管理器与软件源：

```sh
cat /etc/openwrt_release
uname -m
command -v apk || command -v opkg
python3 --version
```

依赖包应从该固件匹配的软件源安装；不同固件的包名和内核版本可能不同，不混装其他版本的内核模块。系统自带的精简 FFmpeg 若没有 `libx264`，需换用具备上述能力的构建，或将经过来源与哈希校验的 ARM64 FFmpeg、FFprobe 放入单独目录，再用安装参数指定。

```sh
/path/to/ffmpeg -hide_banner -encoders
/path/to/ffprobe -version
```

USB Device 是硬件要求，普通 USB Host 接口无法通过安装软件变成车载 U 盘。参考 [Linux Gadget/configfs 文档](https://docs.kernel.org/usb/gadget_configfs.html)和 [FFmpeg libx264 文档](https://ffmpeg.org/ffmpeg-codecs.html#libx264_002c-libx264rgb)。

## 2. 准备数据盘

使用已准备好的 ext4 数据分区，挂载到 `/mnt/mmc`。先查看设备分区、文件系统和实际挂载情况：

```sh
blkid
cat /proc/mounts
df -h /mnt/mmc /opt
```

确认新的数据分区后，用它自己的 UUID 挂载。例如将下方占位符替换为 `blkid` 查到的 UUID：

```sh
mkdir -p /mnt/mmc
mount -t ext4 -o noatime "$(blkid -U '<新数据分区UUID>')" /mnt/mmc
```

尚无 ext4 分区时，先在确认选中空白卡后完成分区和格式化，再回到此步骤。格式化会清空选中分区，安装器不会代为执行。已有其他磁盘挂载在 `/mnt/mmc` 时，先核对挂载目标。

默认预留容量：64 GiB 车载镜像＋34 GiB 归档＋4 GiB 缓存预算＋10 GiB 空闲余量，共需 **112 GiB 可用空间**。缓存预算用于安装容量估算；实际运行仍由缓存清理和空闲空间保护管理。离线完整包的 `/opt` 另需约 280 MiB 空闲空间（运行环境加 128 MiB 余量，检查时按实际大小计算）；源码安装需 128 MiB。小容量卡可降低配额，例如 16＋12＋4＋10＝42 GiB：

```sh
sh install-f50.sh --check --size-gb 16 --archive-gb 12 --drive-gb 4
```

镜像使用稀疏文件，但容量检查按将来写满后的空间计算。行车记录配额包含在总归档配额内。

## 3. 获取完整源码（使用离线包可跳过）

在电脑或新设备上获取项目源码：

```sh
git clone https://github.com/baicyber/tesla-sentry-f50.git
cd tesla-sentry-f50
git rev-parse HEAD
```

新设备不便登录 GitHub 时，可在电脑上下载该仓库的 ZIP，或用 `git archive` 导出当前提交，再通过 SCP 传入新设备并解包。不需要把 GitHub 访问令牌留在设备上。

必须保留完整目录，包括 `scripts/` 和 `openwrt/init.d/`。安装器不覆盖 `/etc/rc.local`。

## 4. 检查与安装

以下命令在**新 F50 的 SSH 终端**、源码根目录执行：

```sh
sh install-f50.sh --check
sh install-f50.sh --apply --start
```

自定义容量、路径或工具时，两次命令应使用相同参数。例如：

```sh
sh install-f50.sh --check --size-gb 16 --archive-gb 12 --drive-gb 4 \
  --ffmpeg /opt/ffmpeg/ffmpeg --ffprobe /opt/ffmpeg/ffprobe
sh install-f50.sh --apply --start --size-gb 16 --archive-gb 12 --drive-gb 4 \
  --ffmpeg /opt/ffmpeg/ffmpeg --ffprobe /opt/ffmpeg/ffprobe
```

### FFmpeg 在安装阶段配好

缩略图、清晰度转码、导出合成都依赖 `ffmpeg`/`ffprobe`，安装检查会实际编码一帧、核对 `libx264` 与 `scale/pad/fps/xstack/setsar/subtitles` 滤镜，缺任何一项都会在安装阶段直接失败，不会等到网页里点开视频才发现。

设备上已经有安装、但依赖后来丢了（固件升级常把 `/opt/mu300` 冲掉），用同一条命令补：

```sh
sh install-f50.sh --apply --install-deps
```

`--install-deps` 会先按固件的包管理器（`apk` / `opkg`）尝试安装 FFmpeg，再做完整校验；已安装的设备只更新 `/etc/tesla-sentry.conf` 里的 `TSF_FFMPEG`/`TSF_FFPROBE` 并重启服务，不动录像和其他配置。装不上的原因（例如系统仓库里的精简版没有 `libx264`）会原样报出来。

运行期也有一层兜底：主程序启动时检查两个二进制，缺失会在页面顶部显示「FFmpeg 未配置」红条，`/thumb` 请求返回 503 并带 `X-TSF-Reason: ffmpeg-missing`，不会再静默变成空白缩略图。

其他参数：`--data-mount /mnt/mmc`、`--port 8080`。安装目录固定为 `/opt/tesla-sentry`。检查过程读取设备能力并执行短暂编码探测，不挂载磁盘、不加载内核模块、不修改 USB 或服务。

安装生成：

| 位置 | 内容 |
| --- | --- |
| `/opt/tesla-sentry/` | 程序、脚本、安装记录、网页登录凭据、设置 |
| `/etc/tesla-sentry.conf` | 本设备 UUID、控制器、路径、配额和端口 |
| `/mnt/mmc/tesla-sentry/teslacam.img` | 含 TeslaCam 目录的 exFAT 镜像 |
| `/mnt/mmc/tesla-sentry/archive/` | 哨兵、手动保存、最近一小时和行车记录 |
| `/mnt/mmc/tesla-sentry/cache/` | 缩略图、转码及遥测缓存 |
| `/mnt/mmc/tesla-sentry/logs/` | 项目脚本日志 |
| `/etc/init.d/` | 四个项目服务，仅 `tsf-mount` 注册开关机启动链接 |
| `/etc/crontabs/root` | 追加项目区块，保留原有任务 |

启动门控先核验 UUID、ext4 和读写挂载，再启动 USB 导出、同步、查看器。定时任务仅在服务运行且磁盘匹配时执行；音频队列为空时不运行音频同步。

网页为 `http://新设备IP:8080`。用户名为 `admin`，随机密码和独立会话密钥写入权限为 `0600` 的 `auth.conf`。通过 SSH 在设备上查看密码后登录；不要把该文件提交到 GitHub。默认 `settings.json` 为 `{"auto_update":false}`。本改动增加了配套 Python 模块；维护者完成发布及升级兼容处理前，请保持自动更新关闭，并使用完整源码部署。

未使用 `--start` 时可稍后运行：

```sh
/etc/init.d/tsf-mount start
```

同一份源码、相同参数重复安装直接返回，不重建镜像、不覆盖凭据和设置、不重启服务。检测到旧安装、版本不一致或不完整安装时会拒绝覆盖；此安装器不承担原设备升级。

## 存储分配与容量调整

登录后打开“设置 → 存储分配”。数据盘容量只读，各输入框右侧的问号解释用途。页面按实际文件系统位置计算预算：车机 U 盘容量＋归档总额＋本盘缓存预算＋安全预留，不重复计入行车记录额度；缓存放在其他分区时不占本盘预算。1 GiB = 1024³ 字节。

- 归档总容量：哨兵、手动保存、最近一小时与行车记录共用的额度。
- 行车记录容量：归档总额内的行车记录上限。
- 安全预留空间：低空间清理希望恢复的目标余量。
- 最低空闲空间：低于此值时暂停新增同步，不能高于安全预留空间。

保存归档设置不重启播放器，在下一轮归档清理和同步检查中生效。降低额度或提高预留空间可能淘汰旧录像，页面会要求确认；受保护或正在写入的片段可能延后清理。保存于 `/opt/tesla-sentry/storage.json`，优先于 `/etc/tesla-sentry.conf` 中的初始配额。请一并备份此文件。

首次部署按指定容量创建空白 U 盘。已部署设备通过“调整 U 盘容量”按钮修改，支持 1～128 GiB。调整会清空车机 U 盘中的全部录像和其他文件，盒子归档保留；页面明确确认后才执行，取消不会改动。需要保留的 U 盘文件应自行导出。先结束网页 U 盘文件管理；调整期间车机暂停录像，请保持供电。

容量调整创建空白 exFAT 镜像并重新导出，不迁移录像，不向内部存储暂存视频。仍会校验容量分配是否合理；创建或导出失败时尝试恢复旧镜像。成功后释放旧镜像，清空无法撤销。不要在调整期间断电或重启服务。

`teslacam.img.resize-state`、`.resize-new` 或 `.resize-backup` 残留时，同步和 USB 自动恢复保持暂停。新流程的状态标记内容为 `recreate`；旧版本标记可能记录临时挂载目录。恢复前检查 USB 绑定、挂载、loop 和两个镜像，确认目标镜像有效且未挂载，再恢复导出并清理对应恢复文件。不要直接删除旧镜像。断电后的恢复需现场核验。

这些设置不改变物理分区表。镜像和归档需在同一数据盘；升级已有安装时须一起更新 `sentry-viewer.py`、`tesla-live-sync.py`、`storage_allocation.py`、`storage_resize.py`、`tesla-gadget.sh` 和 `tesla-sync.sh`，并确保安装目录已有 `mkexfat.py`，不能只替换网页程序。

## 5. 验收与故障定位

```sh
/etc/init.d/tesla-live running
/etc/init.d/tesla-viewer running
python3 -B /opt/tesla-sentry/sentry-viewer.py --ota-check
logread -e tsf-mount
```

离线完整包的模块检查使用 `/opt/tesla-sentry/runtime/bin/python3`。先完成网页登录，再连接车机验证 U 盘识别和新录像持续进入归档。五个播放入口逐一检查单路、四路的播放暂停、拖动、切段和切换录像，并验证导出。离线包已包含中文字体；源码安装可在导出面板安装字体。

最后在新设备上重启，检查挂载、USB 导出、同步和网页能否恢复。`--start` 的进程与 HTTP 检查不能代替冷启动和真实车机枚举验收。

已有安装测试覆盖隔离文件系统和 ARM64 模拟环境；尚未完成空白 F50 的完整物理设备验收。本次贡献未重新运行测试。包内 `scripts/check-runtime.py` 可在目标 ARM64 设备上验证运行环境。

安装文件发布过程中遇到可捕获的异常，会清理本次新增内容并保留原有任务；启动检查失败时保留已安装文件供排查。断电、进程被强制杀死或文件系统损坏无法保证自动清理，重新运行会拒绝覆盖残留安装。检查 `/tmp/tsf-install.lock` 前先确认没有另一安装进程，勿盲目删除安装目录或数据盘内容。

常见阻塞：

- `已有路径`：目标并非全新安装，或上次安装中断；先核对文件与安装记录。
- `ext4` / UUID 错误：数据卡未挂载、挂错分区或只读。服务不会退回系统盘写录像。
- `libx264` / 滤镜失败：FFmpeg 构建不满足要求，用参数指定完整构建。
- Gadget / mass storage 错误：固件 USB 环境未准备好，或已有其他 U 盘导出；安装器不自动改造该环境。
- 启动失败：查看 `logread` 和数据盘 `logs/tesla-gadget.log`，修复后手动启动 `tsf-mount`。
