# iQOO / vivo 卡刷包转线刷包工具

**中文** | [English](README.en.md)

`ota2fastboot.py` 把一个 **已解包** 的 vivo / iQOO 全量卡刷包（block OTA）转换成一个完整的 Fastboot 线刷包。输出包自带刷机脚本，可直接 `./flash.sh`（安全默认）或 `./flash-full.sh`（完整线刷）。

---

## 功能

- 重建全部动态分区：`system`、`vendor`、`product`、`system_ext`、`odm`、`vendor_dlkm`
- 支持分段 `*.new.dat`、`*.new.dat.1..N`、单文件 Brotli `*.new.dat.br`
- 重建 `vm-bootsys` 等 `super` 之外的块镜像（OTA 提供时）
- 用 `lpmake` 生成与设备 `super` 分区同尺寸的稀疏 `super.img`
- 构建时可选读取 ADB 连接的设备，用实际 `super` 大小、OEM/VGC 身份和机型校验结果生成更契合的包；ADB 读不到的 `super` 大小会自动进 bootloader 读取
- 提取选中 OEM 制式的修复镜像；加 `--all-oem` 时提取全部制式
- 提取 `vgc/` 变体并写入 `super`
- 复制 `flash-full.sh` 所需的 bootloader、modem、xbl、tz 等物理镜像
- 自动生成包内 `README.md`、`package.env`、刷机脚本、`verify.sh`、构建报告和 SHA-256 清单
- 可选目标平台：`--target-platform windows` 配合 `--platform-tools-dir` 时把 Windows `adb.exe` / `fastboot.exe` 放入 `platform-tools/`
- 构建日志与报告默认脱敏设备序列号，只保留后四位；`--keep-serial` 可关闭
- 校验：`e2fsck`、`lpdump`、AVB 链、脚本引用完整性、稀疏镜像展开尺寸
- 生成文件中不包含构建机的绝对路径

## 环境要求

| 工具 | 用途 |
| --- | --- |
| `python3` 3.8+ | 运行转换器 |
| `brotli` | 解压 `*.new.dat.br` |
| `lpmake` / `lpdump` | 生成并校验 `super.img` |
| `e2fsck` | 校验 ext4 分区镜像 |
| `avbtool` | 校验 AVB 签名与链式分区 |
| `adb` / `fastboot` | 构建时通过 ADB 探测；必要时自动进 bootloader 读取，刷机时使用 |

Debian / Ubuntu：

```bash
sudo apt install python3 brotli android-sdk-libsparse-utils e2fsprogs
```

`avbtool` 来自 AOSP `external/avb`；部分发行版需要自行放入 `PATH`。

## 准备卡刷包

输入必须是 **解包后的目录**，至少包含：

```text
META-INF/com/android/metadata
META-INF/com/google/android/updater-script
dynamic_partitions_op_list
*.transfer.list
*.new.dat  或  *.new.dat.N  或  *.new.dat.br
oem/        # 可选，OEM 制式
vgc/        # 可选，VGC 变体
firmware-update/
```

如果拿到的是 zip：

```bash
unzip -q <full-ota>.zip -d <full-ota>
```

## 快速开始

```bash
git clone <repo>
cd <repo>

./ota2fastboot.py /path/to/<full-ota>
```

默认输出到 `out/<OTA 名称>-fastboot/`，其中 `<OTA 名称>` 取自 OTA 元数据的 `post-version`，而非来源目录名。例如 `.firmwares/PD2171_A_12.0.20.11.W10.V000L1-update-full` 对应 `out/PD2171_A_12.0.20.11.W10.V000L1-fastboot/`。

若目标手机以 ADB 连接（已开启 USB 调试），转换器会先读取设备属性、分区和挂载信息，并优先使用设备报告的 OEM/VGC 身份与机型信息。ADB 普通权限读不到 `super` 精确大小时，会自动重启到 bootloader，用 `fastboot getvar` 读完剩余数据，再自动重启回 Android。整个过程不写入、不刷机、不解锁。跳过探测：

```bash
./ota2fastboot.py /path/to/<full-ota> --no-device
```

多设备时用 `--serial` 指定要探测的 ADB 设备。先看计划、不写镜像：

```bash
./ota2fastboot.py /path/to/<full-ota> --dry-run
```

制作完成后：

```bash
cd out/<OTA 名称>-fastboot
chmod +x ./*.sh

./verify.sh       # 校验 manifest.sha256
./flash.sh        # 默认：只刷 Android 分区，保留 bootloader 与 fastboot 退路
./flash-full.sh   # 完整线刷，包含 bootloader 与 UFS 固件
```

## 输出结构

```text
out/<OTA 名称>-fastboot/
├── README.md                 # 包内使用说明（中文）
├── package.env               # 包标题、机型匹配、默认 OEM 身份
├── flash.sh                  # 默认安全线刷：不写 bootloader / storage / modem
├── flash-full.sh             # 完整线刷
├── flash-safe.sh             # flash.sh 的兼容别名
├── fix-oem.sh                # OEM 身份修复
├── verify.sh                 # 按 manifest.sha256 校验包
├── common.sh                 # 上面脚本共用的函数
├── manifest.sha256           # 包完整性清单
├── manifest.json
├── meta/
│   ├── BUILD.md              # 构建报告：分区表、变体、校验结果
│   ├── build.json            # 机器可读的构建参数与结果（含设备探测）
│   └── build.log             # 完整日志；主机路径与序列号默认脱敏
├── platform-tools/           # 可选：仅 Windows 目标且指定了目录
└── firmware-update/
    ├── boot.img vendor_boot.img dtbo.img recovery.img vm-bootsys.img
    ├── vbmeta.img vbmeta_system.img vbmeta_vgc.img
    ├── super.img             # lpmake 生成的稀疏镜像
    ├── NON-HLOS.bin dspso.bin BTFM.bin abl.elf tz.mbn xbl_s.melf ...
    └── oem/
        ├── OEM_<选中制式>.img
        └── vbmeta_oem_<选中制式>.img
```

`manifest.sha256` 覆盖除 `meta/build.log` 和清单文件自身以外的全部文件。
`device-info.txt` 在刷机时写入包目录，记录刷机前的机型、OEM、Android 版本和包版本。

## 三个刷机脚本

| 脚本 | 写入内容 | 风险 |
| --- | --- | --- |
| `flash.sh` | `boot`、`dtbo`、`vendor_boot`、`recovery`、`vm-bootsys`、`vbmeta*`、`super`、OEM 修复 | 低。失败后仍可进入 Fastboot，用旧包回滚 |
| `flash-full.sh` | 完整包，包括 `xbl`、`abl`、`tz`、`hyp`、`storage` 等 | 高。bootloader 写入中断可能无法用 EDL 之外的方式恢复 |
| `fix-oem.sh` | `vbmeta_oem` + `oem` | 低。只修复制式，不碰系统 |

`flash-safe.sh` 为旧名称，保留为 `flash.sh` 的兼容别名。

`flash.sh` 默认不写 modem 组。需要时：

```bash
FLASH_SAFE_WITH_MODEM=1 ./flash.sh
```

`fix-oem.sh` 支持：

```bash
./fix-oem.sh --list
./fix-oem.sh --name PD2171_CN-ZH_FULL_SC_NULL
./fix-oem.sh --name BMW --yes
./fix-oem.sh --filter CT-BMW
```

默认只打包 `super` 使用的 OEM 变体。需要包内保留全部制式时，构建时加
`--all-oem`。

## 常用选项

| 选项 | 说明 |
| --- | --- |
| `--output DIR` | 指定输出目录 |
| `--oem NAME\|auto\|none` | 放进 `super` 的 OEM 制式。默认 `auto`：优先使用 ADB 设备报告的 OEM 身份，否则匹配 OTA 机型的 `FULL_SC_NULL` |
| `--vgc NAME\|auto\|none` | 放进 `super` 的 VGC 变体。默认 `auto`：优先使用 ADB 设备报告的 VGC 身份，否则优先 `V000` |
| `--device-size SIZE` | 设备 `super` 分区大小。默认取 ADB 或 bootloader 读取的实际大小，否则 `15032385536`（14 GiB，iQOO 9 / 9 Pro） |
| `--serial SERIAL` | 指定要探测的 ADB 设备；默认使用第一个 ADB 设备 |
| `--keep-serial` | 在报告与日志中保留完整设备序列号；默认只保留后四位 |
| `--all-oem` | 打包 OTA 内全部 OEM 修复变体；默认只保留写入 `super` 的变体 |
| `--target-platform PLATFORM` | 包目标平台：`linux`、`windows`、`macos`；默认跟随制作系统 |
| `--platform-tools-dir DIR` | 仅 Windows 目标：把该目录的 `adb.exe` / `fastboot.exe` 复制到包内 `platform-tools/`；省略则跳过 |
| `--no-device` | 不探测已连接设备 |
| `--require-device` | 没有兼容的 ADB 设备时直接失败 |
| `--skip-device-check` | 设备与 OTA 机型不匹配时仍使用设备数据 |
| `--scripts-dir DIR` | 刷机脚本模板目录，默认仓库内 `scripts/` |
| `--dry-run` | 只解析、校验并打印计划 |
| `--resume` | 保留已有输出目录继续执行 |
| `--reuse-raw` | 配合 `--resume`：分区镜像尺寸匹配时跳过解包 |
| `--reuse-super` | 配合 `--resume`：`super.img` 匹配时跳过 `lpmake` |
| `--keep-raw` | 保留 `out/<名>/.raw/` 下的中间镜像 |
| `--skip-fsck` / `--skip-avb` | 跳过对应校验，不建议 |
| `--no-super` / `--no-scripts` | 只生成部分内容，调试用 |
| `--force` | 删除非空输出目录后重建 |

示例：

```bash
# 指定 OEM 制式
./ota2fastboot.py /path/to/<full-ota> \
    --oem PD2172C_CN-ZH_FULL_SC_CT-BMW

# 中断后继续
./ota2fastboot.py /path/to/<full-ota> \
    --resume --reuse-raw --reuse-super

# 其他机型，指定 super 大小（字节或 14GiB 这类写法）
./ota2fastboot.py /path/to/<full-ota> --device-size 8GiB

# 只探测指定 ADB 设备，并要求它必须存在且与 OTA 匹配
./ota2fastboot.py /path/to/<full-ota> --serial <serial> --require-device

# Windows 目标，并附带 platform-tools
./ota2fastboot.py /path/to/<full-ota> \
    --target-platform windows \
    --platform-tools-dir /path/to/platform-tools

# 包内保留全部 OEM 修复变体
./ota2fastboot.py /path/to/<full-ota> --all-oem
```

## 读取 ADB 设备

转换器只接受 **ADB 连接**。若手机只处于 Fastboot 模式，不会探测；请先启动 Android 并开启 USB 调试。探测不写入、不刷机、不解锁。

| 阶段 | 读取内容 | 用途 |
| --- | --- | --- |
| ADB | `getprop`（机型、OEM/VGC 身份、Android 版本、指纹、解锁状态）、`/dev/block/mapper`、`df -k` | 选择 OEM/VGC、机型校验、`MODEL_REGEX`、构建报告 |
| 自动进 bootloader | `fastboot getvar all`（`partition-size:super`、`current-slot`、`unlocked` 等）、`fastboot oem product-model` | ADB 读不到的 `super` 大小、当前槽位等；读完自动 `fastboot reboot` 回 Android |

- ADB 取不到 `super` 精确大小时，才自动重启进 bootloader；若 ADB 已能读到，或显式传了 `--device-size`，则不会重启。
- 进 bootloader 后若在超时内未出现，或读取失败，则继续使用 ADB 数据与默认值，不中断构建。
- ADB 设备报告的 `super` 大小或 bootloader 读取值优先于内置默认值；显式传入 `--device-size` 时仍以命令行为准。
- 设备报告的 OEM 身份存在于 OTA 中时，`--oem auto` 直接选它；VGC 同理。
- 设备机型与 OTA 的 `pre-device` / `hardware-device` 不匹配时，默认忽略设备数据，继续按 OTA 元数据构建；`--skip-device-check` 强制使用设备数据，`--require-device` 要求必须有兼容的 ADB 设备。
- 设备报告的 model / product 会追加到 `package.env` 的 `MODEL_REGEX`，让刷机时的机型检查接受真实设备。
- 探测结果写入 `meta/build.json` 的 `target_device` 字段，并出现在 `meta/BUILD.md` 中；序列号默认脱敏为后四位。
- 构建日志中的设备序列号同样默认脱敏；需要完整记录时加 `--keep-serial`。
- ADB 重启至 bootloader 再返回 Android 需要一定时间；期间请保持 USB 连接。

## 校验内容

1. `e2fsck -fn`：所有 ext4 镜像，包括 `oem`、`vgc`
2. `lpdump`：确认 `super.img` 中每个动态分区存在且不小于 op list 声明值
3. `avbtool verify_image --follow_chain_partitions`：
   - `vbmeta`（含 `boot`、`dtbo`、`vendor_boot`、`recovery`、`vm-bootsys`）
   - `vbmeta_system`（含全部动态分区）
   - `vbmeta_oem`
   - `vbmeta_vgc`
4. 脚本引用检查：`flash.sh` / `flash-full.sh` 中每个必需镜像都存在
5. 稀疏镜像展开尺寸等于请求的 `super` 大小（`--device-size`、设备探测值或默认值）
6. SHA-256 清单可验证：

```bash
./verify.sh
```

清单覆盖除 `meta/build.log` 和清单文件自身以外的全部文件。

## 机型检查

转换器从 OTA 元数据的 `pre-device` 与 `hardware-device` 推导机型列表，写入 `package.env` 的 `MODEL_REGEX`。若构建时探测到兼容设备，还会把设备报告的 model / product 追加进去。刷机脚本读取手机 `fastboot oem product-model` 并比对。

确需跳过：

```bash
SKIP_MODEL_CHECK=1 ./flash.sh
```

## 安全说明

- 转换器 **不会刷机**，只生成包并校验。
- 设备探测只读：不写入、不刷机、不解锁；必要时会自动重启进 bootloader 读取数据，然后重启回 Android。
- `flash-full.sh` 会写 bootloader 链和 UFS 固件。没有 EDL 恢复手段时，优先使用 `flash.sh`。
- `flash.sh` 不写 `storage`、`xbl`、`xbl_config`、`abl`、`tz`、`hyp`、`aop`、`devcfg`、`keymaster`、`uefisecapp`、`uefi`、`shrm`、`cpucp`、`imagefv`、`multiimg*`、`storsec`、`featenabler`、`toolsfv`、`xbl_ramdump`。
- 跨版本刷入可能抬高 anti-rollback 计数；降级 bootloader 可能被设备拒绝。
- 刷机会清空数据，先备份。
- 必须解锁 Bootloader。

## 常见问题

**提示 `brotli not found`**：安装 `brotli`，或确认它在 `PATH`。

**提示 `lpmake not found`**：安装 `android-sdk-libsparse-utils`。

**提示磁盘不足**：峰值约为动态分区总和 × 2 + 固件大小 × 2。大包需要约 25 GiB 空闲空间。

**刷完卡在开机 logo**：用 `./flash.sh` 回刷旧包；或确认是否需要同步更新 bootloader。

**系统提示“系统破损”或 OEM 不对**：运行 `./fix-oem.sh`，选择与手机一致的制式；包内没有该制式时，重新构建并加 `--all-oem`。

**生成的 `super.img` 尺寸不对**：用 ADB 连接目标设备后让转换器自动读取；仅处于 Fastboot 模式时不会探测。也可用 `--device-size` 指定。转换器不会读取本机已安装的包来猜测尺寸。

## 目录说明

| 路径 | 说明 |
| --- | --- |
| `ota2fastboot.py` | 可执行入口，保留 `./ota2fastboot.py` 用法 |
| `ota2fastboot/` | 转换器源码包 |
| `ota2fastboot/cli.py` | 命令行参数 |
| `ota2fastboot/builder.py` | 构建流程 |
| `ota2fastboot/device.py` | ADB 探测、必要时进 bootloader 补读、机型匹配 |
| `ota2fastboot/ota.py` | OTA 元数据、transfer list、op list |
| `ota2fastboot/stream.py` | `.new.dat` 解包 |
| `ota2fastboot/sparse.py` | 稀疏镜像读写 |
| `ota2fastboot/super_image.py` | `super.img` 生成与校验 |
| `ota2fastboot/verify.py` | ext4 / AVB 校验 |
| `ota2fastboot/variants.py` | OEM / VGC 变体选择 |
| `ota2fastboot/firmware.py` | 物理镜像映射 |
| `ota2fastboot/package.py` | 刷机脚本与 `package.env` |
| `ota2fastboot/report.py` | 清单与构建报告 |
| `scripts/common.sh` | 刷机脚本共用函数 |
| `scripts/flash.sh` | 默认安全线刷模板 |
| `scripts/flash-full.sh` | 完整线刷模板 |
| `scripts/flash-safe.sh` | 旧名称兼容别名 |
| `scripts/fix-oem.sh` | OEM 修复模板 |
| `scripts/verify.sh` | 包完整性校验模板 |
| `out/` | 生成的线刷包（git 忽略） |

也可用模块方式运行：

```bash
python3 -m ota2fastboot /path/to/<full-ota>
```

`pyproject.toml` 提供可选的 `pip install .` 安装方式。生成包中的 `package.env` 由转换器写入，刷机脚本启动时自动加载。
