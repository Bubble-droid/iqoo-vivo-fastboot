"""Reading and applying ``*.new.dat`` streams."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from common.util import command_missing, fmt_size

from .constants import BLOCK
from .errors import ToolError
from .log import Log
from .ota import TransferList, find_streams


class ChainReader:
    """Read a raw or Brotli .new.dat stream without intermediate files."""

    def __init__(self, paths: list[Path]):
        self.paths = paths
        self.index = 0
        self.file = None
        self.process = None

    def _close_current(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None
        if self.process is not None:
            error = self.process.stderr.read()
            status = self.process.wait()
            self.process.stderr.close()
            self.process = None
            if status != 0:
                raise ToolError(
                    "brotli failed: " + error.decode(errors="replace").strip()
                )

    def _open_next(self) -> bool:
        if self.index >= len(self.paths):
            return False
        path = self.paths[self.index]
        self.index += 1
        if path.suffix == ".br":
            if command_missing("brotli"):
                raise ToolError("brotli not found in PATH")
            self.process = subprocess.Popen(
                ["brotli", "-dc", "--", str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.file = self.process.stdout
        else:
            self.file = path.open("rb")
        return True

    def read(self, size: int) -> bytes:
        output = bytearray()
        while len(output) < size:
            if self.file is None and not self._open_next():
                break
            chunk = self.file.read(size - len(output))
            if chunk:
                output.extend(chunk)
                continue
            self._close_current()
        return bytes(output)

    def close(self) -> None:
        self._close_current()


def extract_partition(
    name: str,
    tl_path: Path,
    out_path: Path,
    expected_size: int,
    log: Log,
) -> dict:
    streams = find_streams(tl_path)
    if streams is None:
        raise ToolError(f"missing data stream for {tl_path}")
    transfer = TransferList(tl_path)
    minimum_size = transfer.max_block * BLOCK
    size = expected_size or minimum_size
    if size < minimum_size:
        raise ToolError(
            f"{name}: partition size {size} < data size {minimum_size}"
        )

    started = time.monotonic()
    reader = ChainReader(streams)
    written = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temporary file and rename only after the whole stream has been
    # consumed.  A reboot or kill during extraction used to leave a full-size
    # sparse file behind, which --reuse-raw then accepted as valid.
    tmp_path = out_path.with_name(out_path.name + ".part")
    try:
        tmp_path.unlink(missing_ok=True)
        with tmp_path.open("wb") as destination:
            destination.truncate(size)
            for command, ranges in transfer.commands:
                if command != "new":
                    continue
                for start, end in ranges:
                    position = start * BLOCK
                    remaining = (end - start) * BLOCK
                    while remaining > 0:
                        chunk = reader.read(min(remaining, 32 * 1024 * 1024))
                        if not chunk:
                            raise ToolError(f"{name}: data stream ended early")
                        destination.seek(position)
                        destination.write(chunk)
                        position += len(chunk)
                        remaining -= len(chunk)
                        written += len(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if reader.read(1):
            raise ToolError(f"{name}: data stream has trailing bytes")
        tmp_path.replace(out_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        reader.close()

    elapsed = max(time.monotonic() - started, 0.001)
    log(
        f"  {name}: {fmt_size(size)} in {elapsed:.0f}s "
        f"({written / elapsed / 1024 / 1024:.0f} MiB/s, {len(streams)} stream file(s))"
    )
    return {
        "partition": name,
        "size": size,
        "data_bytes": written,
        "seconds": round(elapsed, 1),
        "streams": [path.name for path in streams],
    }
