# iQOO / vivo 卡刷包转线刷包工具

**中文** | [English](README.en.md)

`ota2fastboot.py` 把一个 **已解包** 的 vivo / iQOO 全量卡刷包（block OTA）转换成一个完整的 Fastboot 线刷包。输出包自带刷机脚本，可直接 `./flash-safe.sh` 或 `./flash.sh`。

---

## 功能

- 重建全部动态分区：`system`、`vendor`、`product`、`system_ext`、`odm`、`vendor_dlkm`
- 支持分段 `*.new.dat`、`*.new.dat.1..N`、单文件 Brotli `*.new.dat.br`
- 重建 `vm-bootsys` 等 `super` 之外的块镜像（OTA 提供时）
- 用 `lpmake` 生成与设备 `super` 分区同尺寸的稀疏 `super.img`
- 提取 `oem/` 下的全部 OEM 制式，供刷机后修复身份
- 提取 `vgc/` 变体并写入 `super`
- 复制 `flash.sh` 所需的 bootloader、modem、xbl、tz 等物理镜像
- 自动生成 `package.env`、刷机脚本、构建报告和 SHA-256 清单
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
| `adb` / `fastboot` | 刷机时使用 |

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

默认输出到 `out/<OTA 名称>/`。先看计划、不写镜像：

```bash
./ota2fastboot.py /path/to/<full-ota> --dry-run
```

制作完成后：

```bash
cd out/<OTA 名称>
chmod +x ./*.sh

./flash-safe.sh   # 只刷 Android 分区，保留 bootloader 与 fastboot 退路
./flash.sh        # 完整线刷，包含 bootloader 与 UFS 固件
```

## 输出结构

```text
out/<OTA 名称>/
├── BUILD.md                  # 构建报告：分区表、变体、校验结果
├── build.json                # 机器可读的构建参数与结果
├── build.log                 # 完整日志，路径已脱敏
├── package.env               # 包标题、机型匹配、默认 OEM 身份
├── flash.sh                  # 完整线刷
├── flash-safe.sh             # 安全线刷：不写 bootloader / storage / modem
├── fix-oem.sh                # OEM 身份修复
├── common.sh                 # 上面三个脚本共用的函数
├── manifest.sha256           # 产物 SHA-256 清单
├── manifest.json
└── firmware-update/
    ├── boot.img vendor_boot.img dtbo.img recovery.img vm-bootsys.img
    ├── vbmeta.img vbmeta_system.img vbmeta_oem.img vbmeta_vgc.img
    ├── super.img             # lpmake 生成的稀疏镜像
    ├── NON-HLOS.bin dspso.bin BTFM.bin abl.elf tz.mbn xbl_s.melf ...
    └── oem/
        ├── OEM_<每个制式>.img
        └── vbmeta_oem_<每个制式>.img
```

## 三个刷机脚本

| 脚本 | 写入内容 | 风险 |
| --- | --- | --- |
| `flash-safe.sh` | `boot`、`dtbo`、`vendor_boot`、`recovery`、`vm-bootsys`、`vbmeta*`、`super`、OEM 修复 | 低。失败后仍可进入 Fastboot，用旧包回滚 |
| `flash.sh` | 完整包，包括 `xbl`、`abl`、`tz`、`hyp`、`storage` 等 | 高。bootloader 写入中断可能无法用 EDL 之外的方式恢复 |
| `fix-oem.sh` | `vbmeta_oem` + `oem` | 低。只修复制式，不碰系统 |

`flash-safe.sh` 默认不写 modem 组。需要时：

```bash
FLASH_SAFE_WITH_MODEM=1 ./flash-safe.sh
```

`fix-oem.sh` 支持：

```bash
./fix-oem.sh --list
./fix-oem.sh --name PD2171_CN-ZH_FULL_SC_NULL
./fix-oem.sh --name BMW --yes
./fix-oem.sh --filter CT-BMW
```

## 常用选项

| 选项 | 说明 |
| --- | --- |
| `--output DIR` | 指定输出目录 |
| `--oem NAME\|auto\|none` | 放进 `super` 的 OEM 制式。默认 `auto`：优先匹配 OTA 机型的 `FULL_SC_NULL` |
| `--vgc NAME\|auto\|none` | 放进 `super` 的 VGC 变体。默认 `auto`：优先 `V000` |
| `--device-size SIZE` | 设备 `super` 分区大小。默认 `15032385536`（14 GiB，iQOO 9 / 9 Pro）；其他机型必须按实际分区调整 |
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
```

## 校验内容

1. `e2fsck -fn`：所有 ext4 镜像，包括 `oem`、`vgc`
2. `lpdump`：确认 `super.img` 中每个动态分区存在且不小于 op list 声明值
3. `avbtool verify_image --follow_chain_partitions`：
   - `vbmeta`（含 `boot`、`dtbo`、`vendor_boot`、`recovery`、`vm-bootsys`）
   - `vbmeta_system`（含全部动态分区）
   - `vbmeta_oem`
   - `vbmeta_vgc`
4. 脚本引用检查：`flash.sh` / `flash-safe.sh` 中每个必需镜像都存在
5. 稀疏镜像展开尺寸等于 `--device-size`
6. SHA-256 清单可验证：

```bash
sha256sum -c manifest.sha256
```

## 机型检查

转换器从 OTA 元数据的 `pre-device` 与 `hardware-device` 推导机型列表，写入 `package.env` 的 `MODEL_REGEX`。刷机脚本读取手机 `fastboot oem product-model` 并比对。

确需跳过：

```bash
SKIP_MODEL_CHECK=1 ./flash-safe.sh
```

## 安全说明

- 转换器 **不会刷机**，只生成包并校验。
- `flash.sh` 会写 bootloader 链和 UFS 固件。没有 EDL 恢复手段时，优先使用 `flash-safe.sh`。
- `flash-safe.sh` 不写 `storage`、`xbl`、`xbl_config`、`abl`、`tz`、`hyp`、`aop`、`devcfg`、`keymaster`、`uefisecapp`、`uefi`、`shrm`、`cpucp`、`imagefv`、`multiimg*`、`storsec`、`featenabler`、`toolsfv`、`xbl_ramdump`。
- 跨版本刷入可能抬高 anti-rollback 计数；降级 bootloader 可能被设备拒绝。
- 刷机会清空数据，先备份。
- 必须解锁 Bootloader。

## 常见问题

**提示 `brotli not found`**：安装 `brotli`，或确认它在 `PATH`。

**提示 `lpmake not found`**：安装 `android-sdk-libsparse-utils`。

**提示磁盘不足**：峰值约为动态分区总和 × 2 + 固件大小 × 2。大包需要约 25 GiB 空闲空间。

**刷完卡在开机 logo**：用 `./flash-safe.sh` 回刷旧包；或确认是否需要同步更新 bootloader。

**系统提示“系统破损”或 OEM 不对**：运行 `./fix-oem.sh`，选择与手机一致的制式。

**生成的 `super.img` 尺寸不对**：用 `--device-size` 指定实际 `super` 分区大小。转换器不会读取本机已安装的包来猜测尺寸。

## 目录说明

| 路径 | 说明 |
| --- | --- |
| `ota2fastboot.py` | 可执行入口，保留 `./ota2fastboot.py` 用法 |
| `ota2fastboot/` | 转换器源码包 |
| `ota2fastboot/cli.py` | 命令行参数 |
| `ota2fastboot/builder.py` | 构建流程 |
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
| `scripts/flash.sh` | 完整线刷模板 |
| `scripts/flash-safe.sh` | 安全线刷模板 |
| `scripts/fix-oem.sh` | OEM 修复模板 |
| `out/` | 生成的线刷包（git 忽略） |

也可用模块方式运行：

```bash
python3 -m ota2fastboot /path/to/<full-ota>
```

`pyproject.toml` 提供可选的 `pip install .` 安装方式。生成包中的 `package.env` 由转换器写入，刷机脚本启动时自动加载。
