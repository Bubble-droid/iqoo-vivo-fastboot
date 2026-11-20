"""Android sparse image primitives."""

from __future__ import annotations

import os
import struct
from pathlib import Path

from .constants import SPARSE_MAGIC
from .errors import ToolError


def probe_sparse_device_size(path: Path) -> int | None:
    try:
        with path.open("rb") as stream:
            header = stream.read(28)
    except OSError:
        return None
    if len(header) < 28:
        return None
    magic, _, _, _, _, block, total_blocks, _, _ = struct.unpack("<I4H4I", header)
    if magic != SPARSE_MAGIC or block == 0:
        return None
    return block * total_blocks


def read_sparse_head(path: Path, want: int) -> bytes:
    """Copy the first ``want`` bytes of an expanded raw or sparse image."""
    with path.open("rb") as stream:
        header = stream.read(28)
        if len(header) < 28:
            raise ToolError(f"truncated image: {path}")
        magic, _, _, file_header, chunk_header, block, _, chunks, _ = struct.unpack(
            "<I4H4I", header
        )
        if magic != SPARSE_MAGIC:
            stream.seek(0)
            return stream.read(want)
        output = bytearray(want)
        stream.seek(file_header)
        out_block = 0
        for _ in range(chunks):
            raw = stream.read(chunk_header)
            if len(raw) != chunk_header:
                break
            chunk_type, _, chunk_blocks, total_size = struct.unpack("<2H2I", raw)
            body = total_size - chunk_header
            start = out_block * block
            if chunk_type == 0xCAC1 and start < want:  # raw chunk
                need = min(chunk_blocks * block, want - start)
                output[start : start + need] = stream.read(need)
                stream.seek(body - need, os.SEEK_CUR)
            else:
                stream.seek(body, os.SEEK_CUR)
            out_block += chunk_blocks
        return bytes(output)
