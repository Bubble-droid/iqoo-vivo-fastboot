"""Image verification: ext4 and AVB chains."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .constants import EXT4_MAGIC, EXT4_MAGIC_OFFSET
from .log import Log
from .util import command_missing


def is_ext4(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            stream.seek(EXT4_MAGIC_OFFSET)
            return stream.read(2) == EXT4_MAGIC
    except OSError:
        return False


def fsck_images(images: dict[str, Path], log: Log) -> dict[str, bool]:
    if command_missing("e2fsck"):
        log("e2fsck not found; filesystem check skipped")
        return {}
    results: dict[str, bool] = {}
    for name, path in images.items():
        if not is_ext4(path):
            continue
        result = subprocess.run(
            ["e2fsck", "-fn", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        results[name] = result.returncode == 0
        status = "ok" if results[name] else "FAILED"
        log(f"  e2fsck {name}: {status}")
    return results


def verify_avb(
    staging: Path,
    files: dict[str, Path],
    targets: list[tuple[str, Path]],
    log: Log,
) -> dict[str, bool]:
    if command_missing("avbtool"):
        log("avbtool not found; AVB verification skipped")
        return {}
    stage = Path(tempfile.mkdtemp(prefix="avb-", dir=str(staging)))
    results: dict[str, bool] = {}
    try:
        for name, path in files.items():
            link = stage / f"{name}.img"
            if not link.exists() and path.is_file():
                link.symlink_to(path.resolve())
        # Create the top-level vbmeta links first: a vbmeta image can chain
        # another vbmeta image (vivo chains vbmeta_oem / vbmeta_vgc from the
        # top-level vbmeta), so every target must be resolvable before the
        # first verification starts.
        for label, path in targets:
            link = stage / f"{label}.img"
            if not link.exists() and path.is_file():
                link.symlink_to(path.resolve())
        for label, path in targets:
            if not path.is_file():
                log(f"  avb {label}: skipped (missing)")
                continue
            link = stage / f"{label}.img"
            result = subprocess.run(
                [
                    "avbtool",
                    "verify_image",
                    "--image",
                    str(link),
                    "--follow_chain_partitions",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            results[label] = result.returncode == 0
            status = "ok" if results[label] else "FAILED"
            log(f"  avb {label} + chain: {status}")
            if not results[label]:
                log.raw(result.stderr[-1500:])
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return results
