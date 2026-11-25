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
    avb_stripped: bool,
) -> str:
    version = props.get("post-version", props.get("post-build-incremental", "unknown"))
    device = props.get("pre-device", "unknown")
    windows = target_platform == "windows"
    fix_cmd = ".\\fix-oem.cmd" if windows else "./fix-oem.sh"
    oem_text = (
        f"未写入 `super`，刷机后仍可通过 `{fix_cmd}` 修复"
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
        tools_note = "本包附带 `platform-tools/`，脚本会自动把它加入 `PATH`。"
    else:
        tools_note = (
            "本包未附带 `adb` / `fastboot`。请先安装 Android platform-tools，"
            "或把 `platform-tools/` 放到包目录，脚本会自动加入 `PATH`。"
        )

    if windows:
        requirements = (
            "- 刷机脚本是原生 PowerShell，Windows 10/11 自带的 PowerShell 5.1+ 即可。\n"
            "- 建议运行包内 `.cmd` 启动器；它会用 `-ExecutionPolicy Bypass` 调用 `.ps1`。\n"
            "- 直接运行 `.ps1` 时，如被执行策略阻止，请使用 `.cmd` 启动器。"
        )
        verify_block = (
            "```powershell\n"
            "Unblock-File .\\*.ps1\n"
            ".\\verify.cmd\n"
            "```"
        )
        flash_block = (
            "```powershell\n"
            ".\\flash.cmd        # 默认：只写 Android 分区，保留 bootloader 与 Fastboot 退路\n"
            ".\\flash-full.cmd   # 完整线刷：包含 bootloader 链与 UFS 固件，风险高\n"
            "```"
        )
        modem_block = (
            "```powershell\n"
            "$env:FLASH_SAFE_WITH_MODEM = \"1\"\n"
            ".\\flash.cmd\n"
            "```"
        )
        fix_block = (
            "```powershell\n"
            ".\\fix-oem.cmd --list\n"
            ".\\fix-oem.cmd --name <变体名>\n"
            "```"
        )
        alias_note = "`flash-safe.cmd` 是旧名称，保留为 `flash.cmd` 的兼容别名。"
        platform_note = (
            f"{tools_note}\n\n"
            "Windows 包只包含 PowerShell 脚本（`.ps1` 与 `.cmd` 启动器），"
            "不需要 Git Bash 或 WSL。"
        )
    else:
        requirements = (
            "- 刷机脚本需要 bash。Linux 与 macOS 原生可用。"
        )
        verify_block = (
            "```bash\n"
            "chmod +x ./*.sh\n"
            "./verify.sh\n"
            "```"
        )
        flash_block = (
            "```bash\n"
            "./flash.sh        # 默认：只写 Android 分区，保留 bootloader 与 Fastboot 退路\n"
            "./flash-full.sh   # 完整线刷：包含 bootloader 链与 UFS 固件，风险高\n"
            "```"
        )
        modem_block = (
            "```bash\n"
            "FLASH_SAFE_WITH_MODEM=1 ./flash.sh\n"
            "```"
        )
        fix_block = (
            "```bash\n"
            "./fix-oem.sh --list\n"
            "./fix-oem.sh --name <变体名>\n"
            "```"
        )
        alias_note = "`flash-safe.sh` 是旧名称，保留为 `flash.sh` 的兼容别名。"
        platform_note = (
            f"{tools_note}\n\n"
            "Linux 与 macOS 包只包含 bash 脚本。"
        )

    if avb_stripped:
        avb_note = (
            "## AVB 与强制加密\n\n"
            "本包保留 dm-verity，并以构建时指定的自建 AVB 密钥重签整条校验链：\n\n"
            "- `boot.img`、`dtbo.img`：内容不变，仅重签 vbmeta 尾部\n"
            "- `vendor_boot.img`：移除 `fileencryption=`、`keydirectory=`、"
            "`metadata_encryption=` 等强制加密标记后，重算 hash footer\n"
            "- `vendor.img`：移除 `/etc/fstab.qcom` 的强制加密标记，重算 hashtree，"
            "并重建 `vbmeta_system`\n"
            "- `vbmeta_system`、`vbmeta_vgc`、`vbmeta`：以自建密钥重签，flags 保持 0\n\n"
            "本机 bootloader 拒绝 AVB 的 disable-verity / disable-verification 标记"
            "（flags=1 与 flags=3 均失败），因此 `avb=` 与 dm-verity 必须保留；"
            "修改过的分区会以自建密钥重算 hashtree，verity 仍会校验本包内容。"
            "刷机脚本按普通镜像直接写入 `vbmeta*`，不再给 fastboot 附加 "
            "`--disable-verity` / `--disable-verification`。若需要保留原厂校验，"
            "重新构建时加 `--keep-avb`。\n\n"
            "移除强制加密后，首次启动可能需要清空 `userdata`。刷机脚本在全部写入"
            "完成、重启之前会询问是否清空数据（默认保留）：确认后优先用 "
            "`fastboot -w` 擦除并格式化 `userdata`、`cache`、`metadata`，再补擦 "
            "`frp`；旧版 platform-tools 回退为 `fastboot erase`。只清理"
            "数据分区，系统、boot、vendor、super、modem 与 OEM 分区保持已刷入状态。"
        )
    else:
        avb_note = (
            "## AVB 与强制加密\n\n"
            "本包保留了 OTA 原始的 AVB / dm-verity / 强制加密设置"
            "（构建时使用 `--keep-avb`）。`vbmeta*` 镜像按 OTA 原样写入，"
            "刷机脚本不附加任何禁用参数。"
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

## 免责声明

- 本项目仅在 **iQOO 9 Pro** 上完成过实机刷入测试。
- 测试通过不代表其他机型可以安全刷入，也不代表同机型（其他批次、制式或系统版本）一定可以安全刷入。
- 刷机存在变砖、数据丢失等风险；请先确认包与设备匹配，并自行判断是否刷入，风险与后果自负。

## 前置条件

- 手机已解锁 Bootloader。
- 电量高于 35%，USB 数据线保持连接。
- 刷机完成后可选择清空数据；清空会丢失全部应用与文件，请先备份。
{requirements}

## 校验

刷机前先校验包内文件：

{verify_block}

`manifest.sha256` 覆盖除 `meta/build.log` 和清单文件自身以外的全部文件。

## 刷机

{flash_block}

刷机脚本会写入 `boot`、`dtbo`、`vendor_boot`、`recovery`、`vm-bootsys`、
`vbmeta*`、`super`，并在最后修复 OEM 身份。需要连同 modem 组一起写入时：

{modem_block}

完整线刷脚本额外写入 `xbl`、`abl`、`tz`、`hyp`、`storage` 等。bootloader
写入中断可能只能靠 EDL 恢复。没有 EDL 手段时不要使用。

{alias_note}

{avb_note}

## OEM 与 VGC

- `super` 内默认 OEM: {oem_text}
- `super` 内默认 VGC: `{vgc_name}`
- {oem_note}

刷机脚本会优先读取手机当前的 OEM 身份并恢复对应镜像；无法读取时才回退到
本包默认值。刷完后如需更换制式：

{fix_block}

## 平台说明

{platform_note}

## 刷机记录

脚本会在包目录写入 `device-info.txt`，记录刷机前的 product model、OEM、
Android 版本和包版本。它不参与 SHA-256 清单。

## 其他文件

- `firmware-update/`：所有分区镜像。
- `firmware-update/flash-items-*.txt`：刷机脚本读取的镜像清单。
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
    avb_stripped: bool = False,
    avb_patch: dict | None = None,
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
            "No device was probed at build time (the default); the package",
            "follows the OTA metadata and the explicit command-line options.",
            "Add `--probe-device` to use the connected phone instead.",
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

    lines += ["", "## AVB / verity / force-encryption", ""]
    if avb_stripped:
        lines += [
            "dm-verity stays enabled, but the whole AVB chain is re-signed with a",
            "build-time custom key. `boot.img` and `dtbo.img` keep their content",
            "and only get a new vbmeta footer; `vendor_boot.img` and `vendor.img`",
            "lose the force-encryption flags and get a freshly computed hash",
            "footer / hashtree. `vbmeta_system`, `vbmeta_vgc` and the top-level",
            "`vbmeta` are re-signed with the same key, with `flags` kept at 0.",
            "",
        ]
        patch = avb_patch or {}
        rows = []
        for name in ("boot", "vendor_boot", "vendor"):
            entry = patch.get(name)
            if not entry:
                continue
            if entry.get("entries"):
                entries = ", ".join(f"`{item}`" for item in entry["entries"])
                rows.append(
                    f"| `{name}.img` | {entries} | "
                    f"{len(entry.get('flags', []))} |"
                )
            else:
                rows.append(f"| `{name}.img` | (content preserved) | 0 |")
        if rows:
            lines += [
                "| image | fstab entries patched | encryption flags removed |",
                "|-------|-----------------------|--------------------------|",
                *rows,
                "",
            ]
        vbmeta = patch.get("vbmeta") or {}
        if vbmeta:
            signed = ", ".join(f"`{name}`" for name in vbmeta.get("signed", []))
            if signed:
                lines += [f"Re-signed with the custom key: {signed}.", ""]
            if vbmeta.get("key"):
                lines += [f"Public key: `{vbmeta['key']}`.", ""]
            if vbmeta.get("verified") is not None:
                state = "verified" if vbmeta["verified"] else "did not verify"
                lines += [f"Local `avbtool verify_image` run: {state}.", ""]
        lines += [
            "Because the AVB disable flags are rejected by this bootloader, the",
            "`avb=` and `verify`/`vverify` markers are kept and `vbmeta*` images",
            "are flashed unmodified by the scripts. Boot loader verification",
            "still runs, against the custom key and the recomputed digests.",
            "",
            "Removing force-encryption may require clearing `userdata`. After",
            "the writes the flash scripts ask whether to erase data (default:",
            "keep); a confirmed wipe uses `fastboot -w` for `userdata`, `cache`",
            "and `metadata`, then erases `frp`, falling back to",
            "`fastboot erase` on older platform-tools. Only data partitions are",
            "touched, so the system partitions stay as written.",
            "",
        ]
    else:
        lines += [
            "AVB removal was disabled with `--keep-avb`. The OTA AVB metadata,",
            "dm-verity and force-encryption flags are kept, and the `vbmeta*`",
            "images are flashed unchanged.",
            "",
        ]

    if target_platform == "windows":
        flashing = [
            "```powershell",
            ".\\verify.cmd     # verify manifest.sha256",
            ".\\flash.cmd      # default: Android partitions only",
            ".\\flash-full.cmd # full flash, bootloader included",
            "```",
            "",
            "Both scripts require an unlocked bootloader. `flash-full.cmd` writes",
            "the bootloader chain (`xbl`, `abl`, `tz`, `hyp`, `aop`, `devcfg`,",
            "...) and the UFS firmware. A failed bootloader write can leave the",
            "phone unbootable without EDL recovery; use `flash.cmd` when that",
            "recovery path is unavailable. `flash-safe.cmd` remains as an alias",
            "for `flash.cmd`.",
            "",
            "If the OEM identity is wrong after flashing, run `.\\fix-oem.cmd` and",
            "select the variant that matches the phone.",
        ]
    else:
        flashing = [
            "```bash",
            "chmod +x ./*.sh",
            "./verify.sh       # verify manifest.sha256",
            "./flash.sh        # default: Android partitions only",
            "./flash-full.sh   # full flash, bootloader included",
            "```",
            "",
            "Both scripts require an unlocked bootloader. `flash-full.sh` writes",
            "the bootloader chain (`xbl`, `abl`, `tz`, `hyp`, `aop`, `devcfg`,",
            "...) and the UFS firmware. A failed bootloader write can leave the",
            "phone unbootable without EDL recovery; use `flash.sh` when that",
            "recovery path is unavailable. `flash-safe.sh` remains as an alias",
            "for `flash.sh`.",
            "",
            "If the OEM identity is wrong after flashing, run `./fix-oem.sh` and",
            "select the variant that matches the phone.",
        ]

    lines += [
        "",
        f"OEM variant in super: `{oem_name}` ({oem_note}).",
        f"VGC variant in super: `{vgc_name}` ({vgc_note}).",
        f"OEM repair variants packaged: {oem_variant_count}.",
        "",
        "## Flashing",
        "",
    ]
    lines += flashing
    lines += [
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
        "`manifest.json`; run the package verify script (`verify.sh` or",
        "`verify.cmd`) to check it.",
        "",
        "## Not included",
        "",
        f"- `{package.name}/dyn/`, `oem_zip/`: vivo dynamic-feature images that",
        "  belong under `/data/vivo-common/`, not on fastboot partitions.",
        "- `META-INF/`: OTA update-script and certificates.",
        "",
    ]
    return "\n".join(lines)
