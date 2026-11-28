"""Small helpers shared by the converter and the standalone tools.

Only pure functions belong here: no logging, no device access and no imports
from :mod:`ota2fastboot`, so both sides can depend on this module freely.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from pathlib import Path


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


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def command_missing(name: str) -> bool:
    return shutil.which(name) is None


def mask_serial(serial: str | None) -> str:
    """Hide all but the last four characters of a device serial."""
    if not serial:
        return ""
    if len(serial) <= 4:
        return "*" * len(serial)
    return "*" * (len(serial) - 4) + serial[-4:]
