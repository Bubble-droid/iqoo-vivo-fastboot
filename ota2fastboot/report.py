"""Manifest, package README and human-readable build report."""

from __future__ import annotations

import json
from pathlib import Path

from .constants import METADATA_SLOTS, SUPER_GROUP
from .device import DeviceInfo
from .log import Log, mask_serial
from .util import fmt_size, sha256_file

# The build log is written while the manifest is generated and is therefore
# excluded, together with the manifest files themselves.  Every other file is
# covered, including the report, build.json, README and scripts.
MANIFEST_EXCLUDE = {
    "manifest.json",
    "manifest.sha256",
    "build.log",
    "meta/build.log",
}


def collect_records(out: Path) -> list[dict]:
    records: list[dict] = []
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(out).as_posix()
        if relative in MANIFEST_EXCLUDE:
            continue
        if relative.startswith(".raw/"):
            continue
        records.append(
            {
                "file": relative,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return records


def write_manifest(out: Path, log: Log, records: list[dict] | None = None) -> list[dict]:
    if records is None:
        records = collect_records(out)
    with (out / "manifest.sha256").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(f"{record['sha256']}  {record['file']}\n")
    (out / "manifest.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log(f"manifest: {len(records)} files, {fmt_size(sum(r['size'] for r in records))}")
    return records


def render_package_readme(
    package: Path,
    props: dict[str, str],
    model_regex: str,
    oem_name: str,
    vgc_name: str,
    oem_variant_count: int,
    all_oem: bool,
    device_size: int,
    target_platform: str,
    platform_tools_bundled: bool,
) -> str:
    version = props.get("post-version", props.get("post-build-incremental", "unknown"))
    device = props.get("pre-device", "unknown")
    oem_text = (
        "未写入 `super`，刷机后仍可通过 `fix-oem.sh` 修复"
        if oem_name == "none"
        else f"`{oem_name}`"
    )
    if all_oem:
        oem_note = f"已打包全部 {oem_variant_count} 个 OEM 变体。"
    elif oem_variant_count:
        oem_note = (
            f"只打包了 {oem_variant_count} 个与目标设备匹配的 OEM 变体。"
            "如需要其他制式，重新构建时加 `--all-oem`。"
        )
    else:
        oem_note = "未打包 OEM 修复镜像。"
    if platform_tools_bundled:
        tools_note = (
            "本包附带 `platform-tools/`，脚本会自动把它加入 `PATH`。"
            "Windows 请在 Git Bash 中运行。"
        )
    else:
        tools_note = (
            "本包未附带 `adb` / `fastboot`。请先安装 Android platform-tools，"
            "或把 `platform-tools/` 放到包目录并加入 `PATH`。"
        )
    return f"""# {device} Fastboot 刷机包

- 来源 OTA: `{package.name}`
- 版本: `{version}`
- 目标机型匹配: `{model_regex or 'unknown'}`
- 默认 OEM 制式: {oem_text}
- 默认 VGC 变体: `{vgc_name}`
- `super` 分区大小: {device_size} bytes ({fmt_size(device_size)})
- 目标平台: `{target_platform}`
- 生成工具: `ota2fastboot.py`

## 前置条件

- 手机已解锁 Bootloader。
- 电量高于 35%，USB 数据线保持连接。
- 刷机会清空数据，先备份。
- 刷机脚本需要 bash。Linux 原生可用；Windows 请在 Git Bash 中运行。

## 校验

刷机前先校验包内文件：

```bash
chmod +x ./*.sh
./verify.sh
```

`manifest.sha256` 覆盖除 `meta/build.log` 和清单文件自身以外的全部文件。

## 刷机

```bash
./flash.sh        # 默认：只写 Android 分区，保留 bootloader 与 Fastboot 退路
./flash-full.sh   # 完整线刷：包含 bootloader 链与 UFS 固件，风险高
```

`flash.sh` 会写入 `boot`、`dtbo`、`vendor_boot`、`recovery`、`vm-bootsys`、
`vbmeta*`、`super`，并在最后修复 OEM 身份。需要连同 modem 组一起写入时：

```bash
FLASH_SAFE_WITH_MODEM=1 ./flash.sh
```

`flash-full.sh` 额外写入 `xbl`、`abl`、`tz`、`hyp`、`storage` 等。bootloader
写入中断可能只能靠 EDL 恢复。没有 EDL 手段时不要使用。

`flash-safe.sh` 是旧名称，保留为 `flash.sh` 的兼容别名。

## OEM 与 VGC

- `super` 内默认 OEM: {oem_text}
- `super` 内默认 VGC: `{vgc_name}`
- {oem_note}

刷机脚本会优先读取手机当前的 OEM 身份并恢复对应镜像；无法读取时才回退到
本包默认值。刷完后如需更换制式：

```bash
./fix-oem.sh --list
./fix-oem.sh --name <变体名>
```

## Windows

{tools_note}

Windows 目标平台只影响是否附带 `platform-tools/`；包内刷机脚本仍是 bash，
因此需要 Git Bash 或 WSL。使用 WSL 时请把 `platform-tools/` 内的 Windows
程序加入可执行的 Windows 路径，或直接在 Git Bash 中运行。

## 刷机记录

脚本会在包目录写入 `device-info.txt`，记录刷机前的 product model、OEM、
Android 版本和包版本。它不参与 SHA-256 清单。

## 其他文件

- `firmware-update/`：所有分区镜像。
- `firmware-update/oem/`：OEM 修复镜像与对应 `vbmeta_oem_*.img`。
- `meta/BUILD.md`：构建报告。
- `meta/build.json`：机器可读的构建结果。
- `meta/build.log`：构建日志；主机路径与设备序列号默认脱敏。
- `package.env`：脚本读取的包元数据。
"""


def render_build_md(
    package: Path,
    props: dict[str, str],
    device_size: int,
    device_size_source: str,
    super_parts: dict[str, int],
    oem_name: str,
    oem_note: str,
    vgc_name: str,
    vgc_note: str,
    records: list[dict],
    log: Log,
    device: DeviceInfo | None = None,
    keep_serial: bool = True,
    target_platform: str = "linux",
    oem_variant_count: int = 0,
) -> str:
    del log  # kept for call-site symmetry with the other builders
    lines = [
        "# Fastboot package build report",
        "",
        f"Source OTA: `{package.name}`",
        f"Target device (OTA metadata): `{props.get('pre-device', 'unknown')}`",
        f"Build fingerprint: `{props.get('post-build', 'unknown')}`",
        f"Version: `{props.get('post-version', props.get('post-build-incremental', 'unknown'))}`",
        f"Target platform: `{target_platform}`",
        "",
        "Generated by `ota2fastboot.py`.",
        "",
        "## Target device probe",
        "",
    ]
    if device is None:
        lines += [
            "No device was probed at build time; the package follows the OTA",
            "metadata only.",
            "",
        ]
    else:
        state = "used for this build" if device.used else "ignored for this build"
        serial = device.serial if keep_serial else mask_serial(device.serial)
        if device.bootloader:
            stage = (
                f"Probed over ADB, then the bootloader (`{serial}`), "
                f"{state}."
            )
        else:
            stage = f"Probed over ADB (`{serial}`), {state}."
        lines += [
            stage,
            "",
            "| property | value |",
            "|----------|-------|",
            f"| product | `{device.product or 'unknown'}` |",
            f"| model | `{device.model or 'unknown'}` |",
            f"| OEM identity | `{device.oem or 'unknown'}` |",
            f"| VGC identity | `{device.vgc or 'unknown'}` |",
            f"| Android | `{device.android or 'unknown'}` |",
            f"| fingerprint | `{device.fingerprint or 'unknown'}` |",
            f"| slot | `{device.slot or 'unknown'}` |",
            "| unlocked | "
            f"`{device.unlocked if device.unlocked is not None else 'unknown'}` |",
            f"| super size | "
            f"`{fmt_size(device.super_size) if device.super_size else 'unknown'}` |",
            "",
        ]
        if device.note:
            lines += [f"Model check: {device.note}.", ""]
        if device.bootloader:
            values = [
                f"`{key}={device.bootloader[key]}`"
                for key in (
                    "product",
                    "product-model",
                    "current-slot",
                    "unlocked",
                    "partition-size:super",
                )
                if device.bootloader.get(key)
            ]
            if values:
                lines += ["Bootloader variables: " + ", ".join(values) + ".", ""]
        if device.dynamic_partitions:
            lines += [
                "Dynamic partitions on the device: "
                + ", ".join(f"`{name}`" for name in device.dynamic_partitions)
                + ".",
                "",
            ]
    lines += [
        "## super.img",
        "",
        f"Device size: {device_size} bytes ({fmt_size(device_size)}), "
        f"source: {device_size_source or 'unspecified'}; "
        f"metadata slots {METADATA_SLOTS}, group `{SUPER_GROUP}`.",
        "",
        "| partition | size (bytes) |",
        "|-----------|--------------|",
    ]
    for name in sorted(super_parts):
        lines.append(f"| `{name}` | {super_parts[name]} |")
    lines += [
        "",
        f"OEM variant in super: `{oem_name}` ({oem_note}).",
        f"VGC variant in super: `{vgc_name}` ({vgc_note}).",
        f"OEM repair variants packaged: {oem_variant_count}.",
        "",
        "## Flashing",
        "",
        "```bash",
        "chmod +x ./*.sh",
        "./verify.sh       # verify manifest.sha256",
        "./flash.sh        # default: Android partitions only",
        "./flash-full.sh   # full flash, bootloader included",
        "```",
        "",
        "Both scripts require an unlocked bootloader. `flash-full.sh` writes the",
        "bootloader chain (`xbl`, `abl`, `tz`, `hyp`, `aop`, `devcfg`, ...) and",
        "the UFS firmware. A failed bootloader write can leave the phone",
        "unbootable without EDL recovery; use `flash.sh` when that recovery",
        "path is unavailable. `flash-safe.sh` remains as an alias for `flash.sh`.",
        "",
        "If the OEM identity is wrong after flashing, run `./fix-oem.sh` and",
        "select the variant that matches the phone.",
        "",
        "## Images",
        "",
        "| file | size | sha256 |",
        "|------|------|--------|",
    ]
    for record in records:
        lines.append(
            f"| `{record['file']}` | {record['size']} | `{record['sha256'][:16]}...` |"
        )
    lines += [
        "",
        "The complete file list, including scripts and this report, is in",
        "`manifest.json`; `./verify.sh` checks it.",
        "",
        "## Not included",
        "",
        f"- `{package.name}/dyn/`, `oem_zip/`: vivo dynamic-feature images that",
        "  belong under `/data/vivo-common/`, not on fastboot partitions.",
        "- `META-INF/`: OTA update-script and certificates.",
        "",
    ]
    return "\n".join(lines)
