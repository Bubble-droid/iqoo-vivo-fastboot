"""Copying the physical images referenced by the flash scripts.

The OTA's ``updater-script`` is the authoritative list of physical partitions
and their source files.  The static table below covers the names used by every
released build so far; files that only appear in newer OTAs are added from the
updater script automatically.  The generated flash-item lists keep the target
scripts independent of the OTA version.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .errors import ToolError
from .log import Log

# Images referenced by the full flash script.  Each entry is
#   (fastboot partition, OTA-relative candidates, output name, required)
# The first existing candidate wins.  ``boot`` and ``vm-bootsys`` are
# special-cased because they come from the package root / a transfer list.
FIRMWARE_MAP = [
    ("boot", ["boot.img", "firmware-update/boot.img"], "boot.img", True),
    ("modem", ["firmware-update/NON-HLOS.bin"], "NON-HLOS.bin", False),
    ("dsp", ["firmware-update/dspso.bin"], "dspso.bin", False),
    ("bluetooth", ["firmware-update/BTFM.bin"], "BTFM.bin", False),
    ("dtbo", ["firmware-update/dtbo.img", "dtbo.img"], "dtbo.img", True),
    ("qupfw", ["firmware-update/qupv3fw.elf", "firmware-update/qupfw.elf"], "qupv3fw.elf", False),
    ("keymaster", ["firmware-update/keymint.mbn", "firmware-update/keymaster.mbn"], "keymint.mbn", False),
    ("uefisecapp", ["firmware-update/uefi_sec.mbn", "firmware-update/uefisecapp.mbn"], "uefi_sec.mbn", False),
    ("uefi", ["firmware-update/uefi.elf", "firmware-update/uefi.mbn"], "uefi.elf", False),
    ("shrm", ["firmware-update/shrm.elf", "firmware-update/shrm.mbn"], "shrm.elf", False),
    ("multiimgoem", ["firmware-update/multi_image.mbn", "firmware-update/multiimgoem.mbn"], "multi_image.mbn", False),
    ("multiimgqti", ["firmware-update/multi_image_qti.mbn", "firmware-update/multiimgqti.mbn"], "multi_image_qti.mbn", False),
    ("cpucp", ["firmware-update/cpucp.elf", "firmware-update/cpucp.mbn"], "cpucp.elf", False),
    ("abl", ["firmware-update/abl.elf", "firmware-update/abl.mbn"], "abl.elf", True),
    ("tz", ["firmware-update/tz.mbn", "firmware-update/tz.elf"], "tz.mbn", True),
    ("aop", ["firmware-update/aop.mbn", "firmware-update/aop.elf"], "aop.mbn", True),
    ("aop_config", ["firmware-update/aop_devcfg.mbn", "firmware-update/aop_config.mbn"], "aop_devcfg.mbn", False),
    ("imagefv", ["firmware-update/imagefv.elf", "firmware-update/imagefv.mbn"], "imagefv.elf", False),
    ("hyp", ["firmware-update/hypvm.mbn", "firmware-update/hyp.mbn"], "hypvm.mbn", True),
    ("xbl_config", ["firmware-update/xbl_config.elf", "firmware-update/xbl_config.mbn"], "xbl_config.elf", True),
    ("xbl", ["firmware-update/xbl_s.melf", "firmware-update/xbl.elf", "firmware-update/xbl.mbn"], "xbl_s.melf", True),
    ("storage", ["firmware-update/firmware.bin"], "firmware.bin", False),
    ("featenabler", ["firmware-update/featenabler.mbn"], "featenabler.mbn", False),
    ("vbmeta", ["firmware-update/vbmeta.img", "vbmeta.img"], "vbmeta.img", True),
    ("vbmeta_system", ["firmware-update/vbmeta_system.img", "vbmeta_system.img"], "vbmeta_system.img", False),
    ("vendor_boot", ["firmware-update/vendor_boot.img", "vendor_boot.img"], "vendor_boot.img", True),
    ("recovery", ["firmware-update/recovery.img", "recovery.img"], "recovery.img", False),
    ("toolsfv", ["firmware-update/tools.fv"], "tools.fv", False),
    ("xbl_ramdump", ["firmware-update/XblRamdump.elf", "firmware-update/xbl_ramdump.elf"], "XblRamdump.elf", False),
    ("storsec", ["firmware-update/storsec.mbn"], "storsec.mbn", False),
    ("devcfg", ["firmware-update/devcfg_overlay1.mbn"], "devcfg_overlay1.mbn", False),
    ("devcfg", ["firmware-update/devcfg.mbn"], "devcfg.mbn", True),
]

EXTRACT_CALL = re.compile(
    r'package_extract_file\(\s*"([^"]+)"\s*,\s*"[^"]*by-name/([^"]+)"\s*\)'
)
VBMETA_VARIANT = re.compile(r"firmware-update/vbmeta_(?:oem|vgc)_")

# Android-only and modem groups for the safe flash script.
SAFE_PARTITIONS = (
    "boot",
    "dtbo",
    "vendor_boot",
    "recovery",
    "vm-bootsys",
    "vbmeta",
    "vbmeta_system",
    "vbmeta_vgc",
    "super",
)
MODEM_PARTITIONS = ("modem", "dsp", "bluetooth", "qupfw")


def _updater_mappings(package: Path) -> list[tuple[str, str]]:
    """Return ``(source, partition)`` pairs in updater-script order."""

    script = package / "META-INF" / "com" / "google" / "android" / "updater-script"
    if not script.is_file():
        return []
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    pairs: list[tuple[str, str]] = []
    for match in EXTRACT_CALL.finditer(text):
        source, partition = match.group(1), match.group(2)
        if not source.startswith("firmware-update/"):
            continue
        if VBMETA_VARIANT.match(source):
            continue
        if (source, partition) not in pairs:
            pairs.append((source, partition))
    return pairs


def firmware_entries(
    package: Path, devcfg_type: int, log: Log
) -> list[dict]:
    """Return the copy list for this OTA, including version-specific files."""

    entries: list[dict] = []
    seen_sources: set[Path] = set()
    seen_names: set[str] = set()

    for partition, candidates, out_name, required in FIRMWARE_MAP:
        source = None
        for candidate in candidates:
            path = package / candidate
            if path.is_file():
                source = path
                break
        if source is None:
            if required:
                raise ToolError(
                    f"package needs {partition} but the OTA has none of: "
                    + ", ".join(candidates)
                )
            continue
        seen_sources.add(source.resolve())
        seen_names.add(out_name)
        entries.append(
            {
                "partition": partition,
                "source": source,
                "out_name": out_name,
                "optional": not required,
            }
        )

    # Newer OTAs may add physical partitions.  The updater script names both
    # the source file and the by-name target, so no table update is needed.
    for relative, partition in _updater_mappings(package):
        path = package / relative
        if not path.is_file():
            continue
        if path.resolve() in seen_sources or path.name in seen_names:
            continue
        entries.append(
            {
                "partition": partition,
                "source": path,
                "out_name": path.name,
                "optional": True,
            }
        )
        seen_sources.add(path.resolve())
        seen_names.add(path.name)
        log(
            f"  {partition} <- {relative} "
            "(adaptive: not in the static firmware table)"
        )

    entries = _select_devcfg(entries, devcfg_type, log)
    return entries


def _select_devcfg(entries: list[dict], devcfg_type: int, log: Log) -> list[dict]:
    """Keep only the devcfg image the OTA updater would write."""

    options = [entry for entry in entries if entry["partition"] == "devcfg"]
    if not options:
        return entries
    wanted_overlay = devcfg_type == 1
    chosen = None
    for entry in options:
        is_overlay = "overlay" in entry["source"].name.lower()
        if is_overlay == wanted_overlay:
            chosen = entry
            break
    if chosen is None:
        chosen = options[0]
        log(
            f"  devcfg: requested type {devcfg_type} has no matching image; "
            f"using {chosen['out_name']}"
        )
    return [
        entry
        for entry in entries
        if entry["partition"] != "devcfg" or entry is chosen
    ]


def copy_firmware(
    package: Path,
    out_fw: Path,
    log: Log,
    devcfg_type: int = 0,
) -> list[dict]:
    records: list[dict] = []
    for entry in firmware_entries(package, devcfg_type, log):
        destination = out_fw / entry["out_name"]
        shutil.copy2(entry["source"], destination)
        records.append(
            {
                "partition": entry["partition"],
                "file": f"firmware-update/{entry['out_name']}",
                "source": entry["source"].relative_to(package).as_posix(),
                "size": destination.stat().st_size,
                "optional": entry["optional"],
            }
        )
        log(f"  {entry['partition']} <- {entry['source'].relative_to(package).as_posix()}")
    return records


def firmware_size(package: Path) -> int:
    """Total size of every source file that may be copied."""

    total = 0
    names = {name for _, _, name, _ in FIRMWARE_MAP}
    for _, candidates, _, _ in FIRMWARE_MAP:
        for candidate in candidates:
            path = package / candidate
            if path.is_file():
                total += path.stat().st_size
                break
    for relative, _ in _updater_mappings(package):
        path = package / relative
        if path.is_file() and path.name not in names:
            total += path.stat().st_size
    return total


def _item_line(record: dict) -> str:
    line = f"{record['partition']}|{record['file']}"
    if record["optional"]:
        line += "|optional"
    return line


def write_flash_items(out: Path, records: list[dict], log: Log) -> None:
    """Write the flash lists consumed by the packaged scripts."""

    full = [_item_line(record) for record in records]
    for name in ("vm-bootsys", "vbmeta_vgc"):
        image = out / "firmware-update" / f"{name}.img"
        if image.is_file():
            full.append(f"{name}|firmware-update/{name}.img|optional")
    super_image = out / "firmware-update" / "super.img"
    if super_image.is_file():
        full.append("super|firmware-update/super.img")

    safe = [line for line in full if line.split("|", 1)[0] in SAFE_PARTITIONS]
    modem = [line for line in full if line.split("|", 1)[0] in MODEM_PARTITIONS]

    for filename, lines in (
        ("flash-items-full.txt", full),
        ("flash-items-safe.txt", safe),
        ("flash-items-modem.txt", modem),
    ):
        path = out / "firmware-update" / filename
        path.write_text(
            "# Generated by ota2fastboot.py. Do not edit by hand.\n"
            + "\n".join(lines)
            + "\n",
            encoding="utf-8",
        )
        log(f"  {filename}: {len(lines)} items")
