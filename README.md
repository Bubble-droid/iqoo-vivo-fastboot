# iQOO / vivo 卡刷包转线刷包工具

**中文** | [English](README.en.md)

`ota2fastboot.py` 把一个 **已解包的 vivo / iQOO 全量卡刷包**（block OTA）转换成完整的 Fastboot 线刷包。输出包自带平台原生刷机脚本：Linux / macOS 用 bash，Windows 用 PowerShell（附 `.cmd` 启动器）。默认入口是安全线刷 `./flash.sh`（Windows 为 `.\flash.cmd`），完整线刷用 `./flash-full.sh`（Windows 为 `.\flash-full.cmd`）。

> **免责声明**
>
> 本项目及生成的刷机包仅在 iQOO 9 Pro 上完成过实机刷入测试。测试通过不代表其他机型可以安全刷入，也不代表同机型（其他批次、制式或系统版本）一定可以安全刷入。刷机存在变砖、数据丢失等风险；是否刷入由你自行判断，风险与后果由你自行承担。

---

## 适用范围

### 支持的卡刷包

只支持 **官方完整（全量）卡刷包**，且必须是解包后的目录，不是 zip、不是增量包、不是 `payload.bin`。

| 项目 | 要求 |
| --- | --- |
| 包类型 | 官方完整卡刷包（full OTA），非增量 / 差异包 |
| OTA 类型 | `META-INF/com/android/metadata` 中 `ota-type=BLOCK`；`package-type` 通常为 `dota` |
| 分区布局 | **A-only / 单槽**，分区名不带 `_a` / `_b` 后缀 |
| 动态分区数据 | `*.transfer.list`（版本 4）+ 完整 `*.new.dat`、分段 `*.new.dat.1..N` 或 Brotli `*.new.dat.br` |
| 物理镜像 | `firmware-update/` 下的 `.img`、`.mbn`、`.elf`、`.bin`，按原文件名和内容复制 |
| 变体目录 | `oem/`、`vgc/` 可选，各自带 `dynamic_partitions_op_list_<变体名>` |
| 必需元数据 | `post-version`（输出目录名）、`pre-device`、`post-build` |

输入目录至少应包含：

```text
META-INF/com/android/metadata
META-INF/com/google/android/updater-script
dynamic_partitions_op_list
*.transfer.list
*.new.dat  或  *.new.dat.N  或  *.new.dat.br
firmware-update/
oem/        # 可选，OEM 制式
vgc/        # 可选，VGC 变体
```

如果拿到的是 zip：

```bash
unzip -q <full-ota>.zip -d <full-ota>
```

**快速判断能否转换：**

1. 解包目录里有 `dynamic_partitions_op_list`、`*.transfer.list`、`*.new.dat*`；
2. `metadata` 中 `ota-type=BLOCK`；
3. 有 `firmware-update/` 和 `updater-script`；
4. 动态分区名不带 `_a` / `_b`；
5. 固件是 `xbl`、`abl`、`tz`、`hyp`、`devcfg` 这类高通命名。

五项都满足才适合使用本工具。

### 镜像打包方式与输出格式

- 输入动态分区采用 Android block OTA 的 transfer list + `new.dat` 数据流，工具按块还原为 raw 分区镜像（ext4 或 EROFS，取决于原厂）。
- 所有动态分区加上 `oem/`、`vgc/` 选中的变体，由 `lpmake` 合并成一个 **稀疏 `super.img`**，大小等于目标设备的 `super` 分区。
- `boot`、`vendor_boot`、`dtbo`、`recovery`、`vm-bootsys` 等保持 raw 镜像；默认只在原 LZ4 ramdisk 帧内原地改写 fstab，不重建 cpio。
- `vbmeta*` 与 bootloader / modem / UFS 固件按原文件复制，不重新签名、不重新打包。
- 输出包同时包含刷机脚本、`package.env`、SHA-256 清单、构建报告和刷机镜像清单。

### 支持的机型和 CPU

| 项目 | 情况 |
| --- | --- |
| 实测机型 | iQOO 9 Pro（PD2171 系列），Android 12 与 Android 15 各一份全量包；同代 iQOO 9 共用布局，未单独实测 |
| CPU 平台 | 高通骁龙 8 Gen 1（SM8450） |
| 设计范围 | vivo / iQOO 高通平台、A-only、block OTA、vivo 动态分区布局 |
| 其他骁龙机型 | 未实测。OTA 结构、分区名与固件映射一致时可以尝试，但不保证可开机 |
| 其他 Android 版本 | 未实测。代码自适应 boot v3/v4、vendor_boot v3/v4、LZ4、ext4 / EROFS，但仅 A12 / A15 验证过 |
| 不支持 | 联发科（MTK）、三星 Exynos、Google Tensor；A/B 或 Virtual A/B 机型；`payload.bin` 通用 OTA |

### 欠缺的能力

- **不刷机、不解锁、不救砖。** 工具只生成并校验线刷包；解锁 Bootloader、进入 Fastboot、EDL 恢复都要自行处理。
- **不处理增量包 / 差异包。** 必须是完整全量包。
- **不支持 `payload.bin` 和整包 `.bin` 格式。** 只认 transfer list + `new.dat` 的解包目录。
- **不支持 A/B、Virtual A/B。** 没有槽位后缀、`super` 双槽或 `boot_a` / `boot_b` 的处理逻辑。
- **不支持联发科。** 固件映射、`devcfg` 与分区命名都按高通平台编写。
- **不自动确定 `super` 大小。** 默认 14 GiB（iQOO 9 / 9 Pro）；其他机型需显式 `--device-size`，或用 `--probe-device` 读取手机。
- **不保证跨机型、跨版本可开机。** 不同批次的 OEM / VGC 身份、bootloader 与 anti-rollback 状态都可能不同。
- **不规避 anti-rollback。** 跨版本降级可能被 bootloader 拒绝。
- **不自动清空数据。** 刷机脚本只在重启前询问，默认保留；清空会丢失全部应用和文件。
- **构建端只在 Linux 上验证。** macOS 未验证；Windows 只作为刷机端，不提供构建流程。
- **需要自备 AVB 私钥。** 默认构建用 `--avb-key` 指定的密钥重签整条校验链；没有密钥时只能 `--keep-avb`。私钥泄露等于任何人都能给你的设备签系统镜像。

## 功能

- 重建全部动态分区：`system`、`vendor`、`product`、`system_ext`、`odm`、`vendor_dlkm`
- 支持完整 `*.new.dat`、分段 `*.new.dat.1..N` 与 Brotli `*.new.dat.br`
- 重建 `vm-bootsys` 等 `super` 之外的块镜像（OTA 提供时）
- 用 `lpmake` 生成与设备 `super` 分区同尺寸的稀疏 `super.img`
- 构建时默认不读取设备；`--probe-device` 可读取实际 `super` 大小、OEM/VGC 身份、`devcfg` 类型和机型；ADB 读不到 `super` 大小时自动进 bootloader 补读
- 自适应 Android 12 / Android 15 两类固件：boot v3/v4、vendor_boot v3/v4、LZ4 ramdisk、ext4 或 EROFS vendor、不同物理分区集合与 OEM/VGC 变体数量
- 从 `updater-script` 自动补全静态表之外的物理分区（如 A15 的 `qweslicstore`、`rtice`），并生成刷机脚本读取的镜像清单
- `devcfg` 按 `ro.vivo.boot.devcfg_type` 只打包并刷写匹配的镜像（`--devcfg-type` 可覆盖）
- 提取选中 OEM 制式的修复镜像；`--all-oem` 时提取全部制式
- 提取 `vgc/` 变体并写入 `super`
- 复制完整线刷所需的 bootloader、modem、xbl、tz 等物理镜像
- 自动生成包内 `README.md`、`package.env`、平台对应的刷机脚本、校验脚本、构建报告和 SHA-256 清单
- 目标平台决定脚本类型：`linux` / `macos` 为 bash，`windows` 为原生 PowerShell + `.cmd`
- 可选 Windows 目标：`--target-platform windows` 配合 `--platform-tools-dir` 时把 Windows `adb.exe` / `fastboot.exe` 放入 `platform-tools/`
- 构建日志与报告默认脱敏设备序列号，只保留后四位；`--keep-serial` 可关闭
- 默认保留 dm-verity，用 `--avb-key` 指定的私钥重签 `boot`、`dtbo`、`vendor_boot`、`vbmeta_system`、`vbmeta_vgc` 与顶层 `vbmeta`，并只移除 fstab 中的强制加密标记；`--keep-avb` 可保留原厂校验
- 校验：`e2fsck`、`lpdump`、AVB 链、脚本引用完整性、稀疏镜像展开尺寸
- 生成文件中不包含构建机的绝对路径

## 环境要求

### 构建端

| 工具 | 用途 |
| --- | --- |
| Linux x86_64 | 构建主机；当前只在 Linux 验证，macOS / Windows 构建未验证 |
| `python3` 3.8+ | 运行转换器 |
| `brotli` | 解压 `*.new.dat.br` |
| `lpmake` / `lpdump` | 生成并校验 `super.img` |
| `e2fsck` | 校验 ext4 分区镜像 |
| `avbtool` | 校验并重签 AVB 链、重算 hashtree；`--skip-avb` 可跳过校验 |
| `liblz4` | 修改 vendor_boot ramdisk 与 EROFS fstab 时重压缩；`--keep-avb` 可跳过 |
| `adb` / `fastboot` | 仅在 `--probe-device` / `--require-device` 时探测；刷机时使用 |
| 约 25 GiB 空闲磁盘 | 峰值约为动态分区总和 × 2 + 固件大小 × 2 |

Debian / Ubuntu：

```bash
sudo apt install python3 brotli android-sdk-libsparse-utils e2fsprogs liblz4-1
```

`avbtool` 来自 AOSP `external/avb`；部分发行版需要自行放入 `PATH`。

### 刷机端

| 平台 | 要求 |
| --- | --- |
| Linux / macOS | `bash`、Android platform-tools（`adb` / `fastboot`）、USB 连接 |
| Windows | PowerShell 5.1+（Windows 10/11 自带）、Android platform-tools、USB 连接 |

刷机包默认跟随构建主机生成脚本；给 Windows 用户制作时加 `--target-platform windows`。

## 快速开始

```bash
git clone <repo>
cd <repo>

./ota2fastboot.py /path/to/<full-ota>
```

默认输出到 `out/<OTA 名称>-fastboot/`，其中 `<OTA 名称>` 取自 OTA 元数据的 `post-version`，而非来源目录名。例如 `.firmwares/PD2171_A_12.0.20.11.W10.V000L1-update-full` 对应 `out/PD2171_A_12.0.20.11.W10.V000L1-fastboot/`。

默认**不读取**已连接设备，产物只由 OTA 与命令行参数决定，因此同一个 OTA 重复构建得到相同的 `super.img`。需要按当前手机读取 `super` 大小、OEM/VGC 身份或 `devcfg` 类型时，显式加 `--probe-device`：

```bash
./ota2fastboot.py /path/to/<full-ota> --probe-device
```

探测只接受 ADB 连接（已开启 USB 调试）。ADB 普通权限读不到 `super` 精确大小时，会自动重启到 bootloader 用 `fastboot getvar` 读完，再重启回 Android。整个过程不写入、不刷机、不解锁。多设备时用 `--serial` 指定。发布包建议固定 `--device-size`，不要依赖探测结果。

先看计划、不写镜像：

```bash
./ota2fastboot.py /path/to/<full-ota> --dry-run
```

制作完成后，按包的目标平台刷机。

Linux / macOS 包（默认跟随制作系统）：

```bash
cd out/<OTA 名称>-fastboot
chmod +x ./*.sh

./verify.sh       # 校验 manifest.sha256
./flash.sh        # 默认：只刷 Android 分区，保留 bootloader 与 fastboot 退路
./flash-full.sh   # 完整线刷，包含 bootloader 与 UFS 固件
```

Windows 包（`--target-platform windows`）：

```powershell
cd out\<OTA 名称>-fastboot

.\verify.cmd      # 校验 manifest.sha256
.\flash.cmd       # 默认：只刷 Android 分区
.\flash-full.cmd  # 完整线刷，包含 bootloader 与 UFS 固件
```

`.cmd` 启动器会以 `-ExecutionPolicy Bypass` 调用同名 `.ps1`，无需修改系统执行策略。

全部分区写完、自动重启之前，脚本会询问是否清空数据（默认保留）。确认后只清理 `userdata`、`metadata`、`cache`、`frp`，已写入的系统分区不受影响。

## 输出结构

Linux / macOS 包：

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
└── firmware-update/
    ├── boot.img vendor_boot.img dtbo.img recovery.img vm-bootsys.img
    ├── vbmeta.img vbmeta_system.img vbmeta_vgc.img
    ├── super.img             # lpmake 生成的稀疏镜像
    ├── NON-HLOS.bin dspso.bin BTFM.bin abl.elf tz.mbn xbl_s.melf ...
    └── oem/
        ├── OEM_<选中制式>.img
        └── vbmeta_oem_<选中制式>.img
```

Windows 包：

```text
out/<OTA 名称>-fastboot/
├── README.md
├── package.env
├── flash.ps1 / flash.cmd                  # 默认安全线刷
├── flash-full.ps1 / flash-full.cmd        # 完整线刷
├── flash-safe.ps1 / flash-safe.cmd        # flash 的兼容别名
├── fix-oem.ps1 / fix-oem.cmd              # OEM 身份修复
├── verify.ps1 / verify.cmd                # 按 manifest.sha256 校验包
├── common.ps1                             # 上面脚本共用的函数
├── manifest.sha256
├── manifest.json
├── meta/
│   ├── BUILD.md
│   ├── build.json
│   └── build.log
├── platform-tools/           # 可选：指定 --platform-tools-dir 时附带
└── firmware-update/
    ├── ... 与 Linux 包相同 ...
    └── oem/
        ├── OEM_<选中制式>.img
        └── vbmeta_oem_<选中制式>.img
```

`manifest.sha256` 覆盖除 `meta/build.log` 和清单文件自身以外的全部文件。`device-info.txt` 在刷机时写入包目录，记录刷机前的机型、OEM、Android 版本和包版本。

## 刷机与校验脚本

| 功能 | Linux / macOS | Windows |
| --- | --- | --- |
| 默认安全线刷 | `flash.sh` | `flash.ps1` 或 `flash.cmd` |
| 完整线刷 | `flash-full.sh` | `flash-full.ps1` 或 `flash-full.cmd` |
| OEM 修复 | `fix-oem.sh` | `fix-oem.ps1` 或 `fix-oem.cmd` |
| 校验包 | `verify.sh` | `verify.ps1` 或 `verify.cmd` |
| 旧名称别名 | `flash-safe.sh` | `flash-safe.ps1` / `flash-safe.cmd` |

安全线刷会写入 `boot`、`dtbo`、`vendor_boot`、`recovery`、`vm-bootsys`、`vbmeta*`、`super`，并在最后修复 OEM 身份，风险低：失败后仍可进入 Fastboot。完整线刷额外写入 `xbl`、`abl`、`tz`、`hyp`、`storage` 等，风险高：bootloader 写入中断可能无法用 EDL 之外的方式恢复。

`flash-safe.sh` / `flash-safe.cmd` 为旧名称，保留为 `flash` 的兼容别名。

默认不写 modem 组。需要时：

```bash
FLASH_SAFE_WITH_MODEM=1 ./flash.sh
```

```powershell
$env:FLASH_SAFE_WITH_MODEM = "1"
.\flash.cmd
```

OEM 修复：

```bash
./fix-oem.sh --list
./fix-oem.sh --name PD2171_CN-ZH_FULL_SC_NULL
./fix-oem.sh --name BMW --yes
./fix-oem.sh --filter CT-BMW
```

```powershell
.\fix-oem.cmd --list
.\fix-oem.cmd --name PD2171_CN-ZH_FULL_SC_NULL
.\fix-oem.cmd --name BMW --yes
.\fix-oem.cmd --filter CT-BMW
```

默认只打包 `super` 使用的 OEM 变体。需要包内保留全部制式时，构建时加 `--all-oem`。

## AVB、dm-verity 与强制加密

默认构建**保留 dm-verity**，并用 `--avb-key` 指定的私钥重签整条校验链：

- `boot.img`、`dtbo.img`：内容不动，仅用 `avbtool resign_image` 重签尾部 vbmeta，避免破坏 boot header v4 自带的 4096 字节签名；
- `vendor_boot.img`：改写 ramdisk 中 `fstab.qcom`，移除 `fileencryption=...`、`keydirectory=...`、`metadata_encryption=...` 等强制加密标记，然后用 `erase_footer` + `add_hash_footer` 重算 hash footer；
- `vendor.img`：改写 `/etc/fstab.qcom`（ext4 / EROFS 自动识别），移除同样三类标记，再重算 dm-verity hashtree；bootloader 为 `vbmeta_system` 里的每个逻辑分区计算新的 descriptor；
- `vbmeta_system`：用各分区镜像的最新 descriptor 重建并自签；`vbmeta_vgc` 直接 `resign_image`；
- 顶层 `vbmeta`：解析原 chain descriptors，已重签的分区换成自建公钥，其余保留原公钥；`flags` 保持 `0`。

之所以不是“去掉 AVB”，是因为 iQOO 9 Pro 的 bootloader 拒绝 AVB disable 标记：`flags=1` 与 `flags=3` 都会在几秒内退回 fastboot，且 fstab 里的 `avb=` 一旦删除，first-stage 挂载直接失败。保留 `avb=`、`verify`、`vverify` 与 `flags=0`、改用自签密钥，是唯一验证可行的路径。刷机脚本按普通镜像直接写入 `vbmeta*`，不再附加 `--disable-verity` / `--disable-verification`，因此也不会触发部分 platform-tools 的 `Failed to find AVB_MAGIC at offset: 0` 缺陷。

补丁保持每个文件大小不变（删除的标记用空格补齐），不需要重建 cpio，也不改动 ext4 inode / EROFS 元数据。AVB 链校验在打补丁之前对原始 OTA 镜像完成，构建末尾再对重签结果做一次本地 `avbtool verify_image --follow_chain_partitions`（失败只告警，不阻断出包）。

`add_hashtree_footer` 在本机走 `--do_not_generate_fec`：`avbtool` 生成 FEC 需要外部 `fec` 工具，而 dm-verity 校验不依赖 FEC。若需要与原厂一致的 FEC 布局，自行安装 `fec` 后去掉该参数。

若希望保留原厂校验与强制加密，构建时加 `--keep-avb`；包内 `package.env` 的 `AVB_STRIPPED` 仅用于记录最终状态，镜像内容本身才是依据。

### 生成 AVB 密钥

AVB 使用 2048 位 RSA 私钥（算法 `SHA256_RSA2048`）。密钥只需生成一次，之后所有构建都用同一把，否则新旧包不能混刷：

```bash
mkdir -p ~/.config/iqoo-vivo-fastboot
chmod 700 ~/.config/iqoo-vivo-fastboot
openssl genrsa -out ~/.config/iqoo-vivo-fastboot/avb.pem 2048
chmod 600 ~/.config/iqoo-vivo-fastboot/avb.pem
openssl rsa -in ~/.config/iqoo-vivo-fastboot/avb.pem -check -noout
avbtool extract_public_key \
    --key ~/.config/iqoo-vivo-fastboot/avb.pem \
    --output ~/.config/iqoo-vivo-fastboot/avb.avbpubkey
sha1sum ~/.config/iqoo-vivo-fastboot/avb.avbpubkey
```

构建时传 `--avb-key ~/.config/iqoo-vivo-fastboot/avb.pem`。私钥不要提交进仓库、不要外传；丢失后无法再给已刷入的自签系统追加签名，泄露则任何人都能给你的设备签系统镜像。换钥匙后必须把 `boot`、`dtbo`、`vendor_boot`、`vbmeta_system`、`vbmeta_vgc`、`vbmeta` 和 `super` 整套一起重刷。

移除强制加密后，已加密的 `userdata` 可能需要清空才能启动。刷机脚本在全部写入完成、自动重启之前会询问是否清空数据（默认保留）：确认后改用 `fastboot erase userdata`，再补擦 `metadata`、`cache`、`frp`，Android 首次开机会自行建立文件系统。`fastboot -w` 不再默认使用：它会让主机生成与整个 userdata 等大的 f2fs 镜像再以 sparse 写入，部分 A15 bootloader 会在传输中卡死并留下半格式化的 userdata，导致 bootloop；确需时设 `WIPE_WITH_FASTBOOT_W=1`。只清理数据分区，系统、boot、vendor、super、modem 与 OEM 分区保持已刷入状态。

## 常用选项

| 选项 | 说明 |
| --- | --- |
| `--output DIR` | 指定输出目录 |
| `--oem NAME\|auto\|none` | 放进 `super` 的 OEM 制式。默认 `auto`：使用 `--probe-device` 报告的 OEM 身份，否则匹配 OTA 机型的 `FULL_SC` 变体 |
| `--vgc NAME\|auto\|none` | 放进 `super` 的 VGC 变体。默认 `auto`：使用 `--probe-device` 报告的 VGC 身份，否则优先 `V000` |
| `--device-size SIZE` | 设备 `super` 分区大小。默认 `15032385536`（14 GiB，iQOO 9 / 9 Pro）；`--probe-device` 时改用手机报告值 |
| `--devcfg-type auto\|0\|1` | 选择 `devcfg` 镜像：`0` 用 `devcfg.mbn`，`1` 用 `devcfg_overlay1.mbn`；`auto` 在探测时读取 `ro.vivo.boot.devcfg_type`，否则按 `0` |
| `--serial SERIAL` | 配合 `--probe-device` 指定 ADB 设备；默认使用第一个 ADB 设备 |
| `--keep-serial` | 在报告与日志中保留完整设备序列号；默认只保留后四位 |
| `--all-oem` | 打包 OTA 内全部 OEM 修复变体；默认只保留写入 `super` 的变体 |
| `--target-platform PLATFORM` | 包目标平台：`linux`、`windows`、`macos`；默认跟随制作系统。`linux` / `macos` 生成 bash 脚本，`windows` 生成 PowerShell + `.cmd` |
| `--platform-tools-dir DIR` | 仅 Windows 目标：把该目录的 `adb.exe` / `fastboot.exe` 复制到包内 `platform-tools/`；省略则跳过 |
| `--probe-device` | 读取已连接手机（ADB，必要时进 bootloader），使用其 `super` 大小、OEM/VGC 身份、`devcfg` 类型与机型；默认关闭 |
| `--require-device` | 等同于 `--probe-device`，且没有兼容设备时直接失败 |
| `--no-device` | 旧参数，保留兼容；现在默认就不探测 |
| `--skip-device-check` | 配合 `--probe-device`，设备与 OTA 机型不匹配时仍使用设备数据 |
| `--scripts-dir DIR` | 脚本模板根目录，需包含 `bash/` 与 `windows/`，默认仓库内 `scripts/` |
| `--dry-run` | 只解析、校验并打印计划 |
| `--resume` | 保留已有输出目录继续执行 |
| `--reuse-raw` | 配合 `--resume`：分区镜像尺寸匹配时跳过解包 |
| `--reuse-super` | 配合 `--resume`：`super.img` 匹配时跳过 `lpmake` |
| `--keep-raw` | 保留 `out/<名>/.raw/` 下的中间镜像 |
| `--avb-key FILE` | PEM 私钥，用于重签 AVB 链并重算修改分区的 hashtree；未加 `--keep-avb` 时必填 |
| `--keep-avb` | 保留 OTA 原始的 AVB / dm-verity / 强制加密设置 |
| `--skip-fsck` / `--skip-avb` | 跳过对应校验，不建议 |
| `--no-super` / `--no-scripts` | 只生成部分内容，调试用 |
| `--force` | 删除非空输出目录后重建 |

示例：

```bash
# 默认：自签 AVB 链并取消强制加密，需要提供 PEM 私钥
./ota2fastboot.py /path/to/<full-ota> --avb-key ~/avb/testkey.pem

# 指定 OEM 制式
./ota2fastboot.py /path/to/<full-ota> --avb-key ~/avb/testkey.pem \
    --oem PD2172C_CN-ZH_FULL_SC_CT-BMW

# 保留原厂 AVB 与强制加密（不需要私钥）
./ota2fastboot.py /path/to/<full-ota> --keep-avb

# 中断后继续
./ota2fastboot.py /path/to/<full-ota> --avb-key ~/avb/testkey.pem \
    --resume --reuse-raw --reuse-super

# 其他机型，指定 super 大小（字节或 14GiB 这类写法）
./ota2fastboot.py /path/to/<full-ota> --avb-key ~/avb/testkey.pem \
    --device-size 8GiB

# 只探测指定 ADB 设备，并要求它必须存在且与 OTA 匹配
./ota2fastboot.py /path/to/<full-ota> --avb-key ~/avb/testkey.pem \
    --probe-device --serial <serial> --require-device

# Windows 目标，并附带 platform-tools
./ota2fastboot.py /path/to/<full-ota> \
    --target-platform windows \
    --platform-tools-dir /path/to/platform-tools

# 包内保留全部 OEM 修复变体
./ota2fastboot.py /path/to/<full-ota> --all-oem
```

## 设备探测（可选）

默认不探测设备。只有传入 `--probe-device` 或 `--require-device` 时才读取。转换器只接受 **ADB 连接**。若手机只处于 Fastboot 模式，不会探测；请先启动 Android 并开启 USB 调试。探测不写入、不刷机、不解锁。

| 阶段 | 读取内容 | 用途 |
| --- | --- | --- |
| ADB | `getprop`（机型、OEM/VGC 身份、Android 版本、指纹、解锁状态、`ro.vivo.boot.devcfg_type`）、`/dev/block/mapper`、`df -k` | 选择 OEM/VGC、`devcfg` 类型、机型校验、`MODEL_REGEX`、构建报告 |
| 自动进 bootloader | `fastboot getvar all`（`partition-size:super`、`current-slot`、`unlocked` 等）、`fastboot oem product-model` | ADB 读不到的 `super` 大小、当前槽位等；读完自动 `fastboot reboot` 回 Android |

- 不传 `--probe-device` 时，`super` 大小取 `--device-size`，否则为 14 GiB；OEM/VGC 取 OTA 自动匹配结果；`devcfg` 默认按类型 `0`。
- 探测时 ADB 取不到 `super` 精确大小，才自动重启进 bootloader；若 ADB 已能读到，或显式传了 `--device-size`，则不会重启。
- 进 bootloader 后若在超时内未出现，或读取失败，则继续使用 ADB 数据与默认值，不中断构建。
- 设备报告的 `super` 大小或 bootloader 读取值优先于内置默认值；显式传入 `--device-size` 时仍以命令行为准。
- 设备报告的 OEM 身份存在于 OTA 中时，`--oem auto` 直接选它；VGC 同理。跨 Android 版本转换时，新版本身份可能不在旧 OTA 中，此时会回退到 OTA 机型匹配并记录日志。
- 设备机型与 OTA 的 `pre-device` / `hardware-device` 不匹配时，默认忽略设备数据，继续按 OTA 元数据构建；`--skip-device-check` 强制使用设备数据，`--require-device` 要求必须有兼容的 ADB 设备。
- 设备报告的 model / product 会追加到 `package.env` 的 `MODEL_REGEX`，让刷机时的机型检查接受真实设备。
- 探测结果写入 `meta/build.json` 的 `target_device` 字段，并出现在 `meta/BUILD.md` 中；序列号默认脱敏为后四位。
- 构建日志中的设备序列号同样默认脱敏；需要完整记录时加 `--keep-serial`。
- ADB 重启至 bootloader 再返回 Android 需要一定时间；期间请保持 USB 连接。
- 为了可复现产物，发布包建议 **不加** `--probe-device`，并显式指定 `--device-size`。

## 构建校验

1. `e2fsck -fn`：所有 ext4 镜像，包括 `oem`、`vgc`
2. `lpdump`：确认 `super.img` 中每个动态分区存在且不小于 op list 声明值
3. `avbtool verify_image --follow_chain_partitions`：
   - `vbmeta`（含 `boot`、`dtbo`、`vendor_boot`、`recovery`、`vm-bootsys`）
   - `vbmeta_system`（含全部动态分区）
   - `vbmeta_oem`
   - `vbmeta_vgc`

   默认的自签构建会先在打补丁之前对原始 OTA 镜像执行一次；构建末尾还会对自签结果再校验一次，失败仅告警。
4. 脚本引用检查：生成的 `firmware-update/flash-items-*.txt` 中每个必需镜像都存在
5. 稀疏镜像展开尺寸等于请求的 `super` 大小（`--device-size`、设备探测值或默认值）
6. SHA-256 清单可验证：

```bash
./verify.sh
```

```powershell
.\verify.cmd
```

清单覆盖除 `meta/build.log` 和清单文件自身以外的全部文件。

## 机型检查

转换器从 OTA 元数据的 `pre-device` 与 `hardware-device` 推导机型列表，写入 `package.env` 的 `MODEL_REGEX`。若构建时探测到兼容设备，还会把设备报告的 model / product 追加进去。刷机脚本读取手机 `fastboot oem product-model` 并比对。

确需跳过：

```bash
SKIP_MODEL_CHECK=1 ./flash.sh
```

```powershell
$env:SKIP_MODEL_CHECK = "1"
.\flash.cmd
```

## 安全说明

- 转换器 **不会刷机**，只生成包并校验。
- 设备探测只读：不写入、不刷机、不解锁；必要时会自动重启进 bootloader 读取数据，然后重启回 Android。
- 完整线刷脚本（`flash-full.sh` / `flash-full.cmd`）会写 bootloader 链和 UFS 固件。没有 EDL 恢复手段时，优先使用安全线刷脚本（`flash.sh` / `flash.cmd`）。
- 安全线刷脚本不写 `storage`、`xbl`、`xbl_config`、`abl`、`tz`、`hyp`、`aop`、`devcfg`、`keymaster`、`uefisecapp`、`uefi`、`shrm`、`cpucp`、`imagefv`、`multiimg*`、`storsec`、`featenabler`、`toolsfv`、`xbl_ramdump`。
- 跨版本刷入可能抬高 anti-rollback 计数；降级 bootloader 可能被设备拒绝。
- 默认生成的包保留 dm-verity，但整条 AVB 链由自建密钥签名；请妥善保管 `--avb-key` 私钥并只在自己的设备上使用。
- 刷机完成后可选择清空数据；清空会丢失全部应用与文件，请先备份。
- 必须解锁 Bootloader。

## 常见问题

**提示 `brotli not found`**：安装 `brotli`，或确认它在 `PATH`。

**提示 `lpmake not found`**：安装 `android-sdk-libsparse-utils`。

**提示磁盘不足**：峰值约为动态分区总和 × 2 + 固件大小 × 2。大包需要约 25 GiB 空闲空间。

**提示 `dynamic_partitions_op_list not found`**：输入不是完整解包目录，或拿到的不是 block OTA。重新解包官方全量包。

**提示 `unsupported ota-type`**：`metadata` 中不是 `BLOCK`，例如 `payload.bin` 的 A/B OTA，本工具不支持。

**刷完卡在开机 logo**：用 `./flash.sh`（Windows 用 `.\flash.cmd`）回刷旧包；或确认是否需要同步更新 bootloader。

**系统提示“系统破损”或 OEM 不对**：运行 `./fix-oem.sh`（Windows 用 `.\fix-oem.cmd`），选择与手机一致的制式；包内没有该制式时，重新构建并加 `--all-oem`。

**生成的 `super.img` 尺寸不对**：用 `--device-size` 显式指定；或加 `--probe-device` 让转换器读取目标手机。默认 14 GiB。转换器不会读取本机已安装的包来猜测尺寸。

## 目录说明

| 路径 | 说明 |
| --- | --- |
| `ota2fastboot.py` | 可执行入口，保留 `./ota2fastboot.py` 用法 |
| `ota2fastboot/` | 转换器源码包 |
| `ota2fastboot/cli.py` | 命令行参数 |
| `ota2fastboot/builder.py` | 构建流程 |
| `ota2fastboot/avb.py` | 修改 fstab 强制加密标记、重签 AVB 链、重算 hashtree |
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
| `scripts/bash/common.sh` | Linux / macOS 刷机脚本共用函数 |
| `scripts/bash/flash.sh` | 默认安全线刷模板 |
| `scripts/bash/flash-full.sh` | 完整线刷模板 |
| `scripts/bash/flash-safe.sh` | 旧名称兼容别名 |
| `scripts/bash/fix-oem.sh` | OEM 修复模板 |
| `scripts/bash/verify.sh` | 包完整性校验模板 |
| `scripts/windows/common.ps1` | Windows 刷机脚本共用函数 |
| `scripts/windows/flash.ps1` | 默认安全线刷模板 |
| `scripts/windows/flash-full.ps1` | 完整线刷模板 |
| `scripts/windows/flash-safe.ps1` | 旧名称兼容别名 |
| `scripts/windows/fix-oem.ps1` | OEM 修复模板 |
| `scripts/windows/verify.ps1` | 包完整性校验模板 |
| `scripts/windows/*.cmd` | 绕过执行策略的启动器 |
| `out/` | 生成的线刷包（git 忽略） |

也可用模块方式运行：

```bash
python3 -m ota2fastboot /path/to/<full-ota>
```

`pyproject.toml` 提供可选的 `pip install .` 安装方式。生成包中的 `package.env` 由转换器写入，刷机脚本启动时自动加载。
