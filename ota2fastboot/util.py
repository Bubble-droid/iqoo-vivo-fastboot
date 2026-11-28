"""Small helpers: subprocesses and disk space.

Sizes and hashing live in :mod:`common.util` because the tools under
``tools/`` use them too.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from common.util import fmt_size

from .errors import ToolError
from .log import Log


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
