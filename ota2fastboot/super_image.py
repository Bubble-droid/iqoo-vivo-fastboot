"""Building and inspecting ``super.img``."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from .constants import BLOCK, METADATA_SIZE, METADATA_SLOTS, SUPER_GROUP
from .errors import ToolError
from .log import Log
from .sparse import read_sparse_head
from .util import command_missing, fmt_size, run


def lp_metadata(path: Path, log: Log) -> dict:
    want = METADATA_SIZE * (METADATA_SLOTS + 1) + BLOCK * 8
    head = read_sparse_head(path, want)
    handle = tempfile.NamedTemporaryFile(
        prefix="super-head-", suffix=".img", dir=str(path.parent), delete=False
    )
    head_path = Path(handle.name)
    try:
        handle.write(head)
        handle.close()
        result = run(["lpdump", "--slot=0", str(head_path)], log)
    finally:
        head_path.unlink(missing_ok=True)

    partitions: dict[str, dict] = {}
    in_table = False
    current: dict | None = None
    for line in result.stdout.splitlines():
        if line.strip() == "Partition table:":
            in_table = True
            continue
        if line.strip() == "Super partition layout:":
            in_table = False
            current = None
            continue
        if not in_table:
            continue
        match = re.match(r"\s+Name:\s+(\S+)", line)
        if match:
            current = {"name": match.group(1), "blocks": 0, "group": ""}
            partitions[current["name"]] = current
            continue
        match = re.match(r"\s+Group:\s+(\S+)", line)
        if match and current:
            current["group"] = match.group(1)
            continue
        match = re.match(r"\s+(\d+)\s+\.\.\s+(\d+)\s+linear\s+\S+\s+\d+", line)
        if match and current:
            current["blocks"] += int(match.group(2)) - int(match.group(1)) + 1

    for entry in partitions.values():
        # lpdump prints extent boundaries in 512-byte sectors.
        entry["bytes"] = entry.pop("blocks") * 512
    return partitions


def build_super(
    raw_parts: dict[str, Path],
    sizes: dict[str, int],
    groups: dict[str, str],
    device_size: int,
    output: Path,
    log: Log,
) -> None:
    if not command_missing("lpmake"):
        log("lpmake: " + shutil.which("lpmake"))
    else:
        raise ToolError("lpmake not found in PATH (install android-sdk-libsparse-utils)")

    group_names = sorted(set(groups.values())) or [SUPER_GROUP]
    total = sum(sizes.values())
    metadata_reserve = METADATA_SIZE * METADATA_SLOTS * 2 + BLOCK * 8
    if total + metadata_reserve > device_size:
        raise ToolError(
            f"dynamic partitions need {fmt_size(total)} but super only has "
            f"{fmt_size(device_size)}"
        )

    command = [
        "lpmake",
        "--device-size",
        str(device_size),
        "--metadata-size",
        str(METADATA_SIZE),
        "--metadata-slots",
        str(METADATA_SLOTS),
        "--block-size",
        str(BLOCK),
        "--super-name",
        "super",
        "--sparse",
        "--output",
        str(output),
    ]
    for group in group_names:
        group_total = sum(size for name, size in sizes.items() if groups.get(name) == group)
        group_size = device_size if len(group_names) == 1 else group_total
        command += ["--group", f"{group}:{group_size}"]
    for name in sorted(sizes):
        group = groups.get(name) or group_names[0]
        command += ["--partition", f"{name}:readonly:{sizes[name]}:{group}"]
        if name in raw_parts:
            command += ["--image", f"{name}={raw_parts[name]}"]

    log(
        f"lpmake: {len(sizes)} partitions, {fmt_size(total)} data, "
        f"device {fmt_size(device_size)}"
    )
    run(command, log)
    if not output.is_file() or output.stat().st_size == 0:
        raise ToolError("lpmake produced no output")
