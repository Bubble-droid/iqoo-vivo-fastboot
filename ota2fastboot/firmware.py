"""Copying the physical images referenced by ``flash.sh``."""

from __future__ import annotations

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


def copy_firmware(package: Path, out_fw: Path, log: Log) -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    for partition, candidates, out_name, required in FIRMWARE_MAP:
        if out_name in seen:
            raise ToolError(f"duplicate output name {out_name}")
        seen.add(out_name)
        source = None
        for candidate in candidates:
            path = package / candidate
            if path.is_file():
                source = path
                break
        if source is None:
            if not required:
                log(f"  skip {partition}: none of {', '.join(candidates)}")
                continue
            raise ToolError(
                f"package needs {partition} but the OTA has none of: "
                + ", ".join(candidates)
            )
        destination = out_fw / out_name
        shutil.copy2(source, destination)
        records.append(
            {
                "partition": partition,
                "file": f"firmware-update/{out_name}",
                "source": source.relative_to(package).as_posix(),
                "size": destination.stat().st_size,
            }
        )
        log(f"  {partition} <- {source.relative_to(package).as_posix()}")
    return records


def firmware_size(package: Path) -> int:
    """Total size of the first existing candidate of every firmware entry."""
    total = 0
    for _, candidates, _, _ in FIRMWARE_MAP:
        for candidate in candidates:
            path = package / candidate
            if path.is_file():
                total += path.stat().st_size
                break
    return total
