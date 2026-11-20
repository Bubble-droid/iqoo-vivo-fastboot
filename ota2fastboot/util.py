"""Small helpers: sizes, hashing, subprocesses and disk space."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

from .errors import ToolError
from .log import Log


def fmt_size(value: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    number = float(value)
    for unit in units:
        if number < 1024 or unit == units[-1]:
            return f"{number:.1f} {unit}" if unit != "B" else f"{value} B"
        number /= 1024
    return f"{value} B"


def parse_size(text: str) -> int:
    text = text.strip()
    try:
        return int(text, 0)
    except ValueError:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGT]i?B?)", text, re.I)
        if not match:
            raise argparse.ArgumentTypeError(f"invalid size: {text}")
        value = float(match.group(1))
        unit = match.group(2).upper()
        factor = {
            "K": 1024,
            "KB": 1024,
            "KIB": 1024,
            "M": 1024**2,
            "MB": 1024**2,
            "MIB": 1024**2,
            "G": 1024**3,
            "GB": 1024**3,
            "GIB": 1024**3,
            "T": 1024**4,
            "TB": 1024**4,
            "TIB": 1024**4,
        }[unit]
        return int(value * factor)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def command_missing(name: str) -> bool:
    return shutil.which(name) is None


def run(command: list[str], log: Log, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    log.raw(result.stdout)
    if result.returncode != 0:
        log.raw(result.stderr)
        if check:
            raise ToolError(
                f"{Path(command[0]).name} failed (rc={result.returncode}): "
                f"{result.stderr.strip().splitlines()[-1] if result.stderr.strip() else 'no output'}"
            )
    return result


def reuse_image(path: Path, size: int, enabled: bool, log: Log) -> bool:
    if enabled and size > 0 and path.is_file() and path.stat().st_size == size:
        log(f"  {path.name}: reused ({fmt_size(size)})")
        return True
    return False


def free_space_check(out: Path, need: int) -> None:
    probe = out
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    if usage.free < need:
        raise ToolError(
            f"not enough disk space: need about {fmt_size(need)}, "
            f"only {fmt_size(usage.free)} free"
        )
