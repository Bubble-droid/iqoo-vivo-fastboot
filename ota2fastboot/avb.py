"""Strip AVB, dm-verity and force-encryption flags from package images.

The generated package is meant to run with the bootloader verification
disabled.  The build therefore removes the fstab flags that ask Android to
verify partitions or to force encryption, and the flash scripts pass
``--disable-verity --disable-verification`` when they write a ``vbmeta*``
partition.

Three images are patched in place:

* ``boot.img`` / ``vendor_boot.img``: the newc cpio inside the LZ4 legacy
  ramdisk frame;
* ``vendor.img``: the EROFS file system, rewriting the compressed
  ``/etc/fstab.qcom`` inode.

The patch keeps every file size unchanged.  Removed flags are replaced by
spaces on the same line, so the cpio member size and the EROFS inode size
stay valid and no metadata has to be recomputed.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import re
import struct
from pathlib import Path

from .errors import ToolError
from .log import Log

LZ4_MAGIC = b"\x02\x21\x4c\x18"
LZ4_BLOCK = 8 * 1024 * 1024

# fstab flags that ask Android to verify a partition or to force encryption.
# ``keepverity`` / ``keepforceencrypt`` are the Magisk-style names that may
# appear when an image was already touched by a patcher.
REMOVE_EXACT = frozenset(
    {
        "avb",
        "avb_keys",
        "verify",
        "verity",
        "vverify",
        "verifyatboot",
        "keepverity",
        "keepforceencrypt",
        "forceencrypt",
        "forcefdeorfbe",
        "fileencryption",
        "keydirectory",
        "metadata_encryption",
    }
)
REMOVE_PREFIX = (
    "avb=",
    "avb_keys=",
    "fileencryption=",
    "forceencrypt=",
    "forcefdeorfbe=",
    "keydirectory=",
    "metadata_encryption=",
)
FSTAB_FIELDS = re.compile(
    r"^(\s*)(\S+)(\s+)(\S+)(\s+)(\S+)(\s+)(\S+)(\s+)(\S+)(.*)$"
)

_EROFS_MAGIC = 0xE0F5E1E2
_EROFS_SUPER_OFFSET = 1024

_LAYOUT_FLAT_PLAIN = 0
_LAYOUT_COMPRESSED_FULL = 1
_LAYOUT_FLAT_INLINE = 2
_LAYOUT_COMPRESSED_COMPACT = 3
_LAYOUT_CHUNK_BASED = 4

_TYPE_PLAIN = 0
_TYPE_HEAD1 = 1
_TYPE_NONHEAD = 2
_TYPE_HEAD2 = 3

_ADVISE_COMPACTED_2B = 0x0001
_ADVISE_BIG_PCLUSTER_1 = 0x0002
_ADVISE_INLINE_PCLUSTER = 0x0008
_FEATURE_INCOMPAT_LZ4_0PADDING = 0x00000001
_LI_D0_CBLKCNT = 1 << 11

_lz4_library: ctypes.CDLL | None = None


def _lz4() -> ctypes.CDLL:
    global _lz4_library
    if _lz4_library is None:
        name = ctypes.util.find_library("lz4")
        if not name:
            raise ToolError(
                "liblz4 not found; install liblz4 (for example "
                "`apt install liblz4-1`) or rebuild with --keep-avb"
            )
        library = ctypes.CDLL(name)
        library.LZ4_decompress_safe.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_int,
        ]
        library.LZ4_decompress_safe.restype = ctypes.c_int
        library.LZ4_compress_HC.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        library.LZ4_compress_HC.restype = ctypes.c_int
        library.LZ4_compressBound.argtypes = [ctypes.c_int]
        library.LZ4_compressBound.restype = ctypes.c_int
        _lz4_library = library
    return _lz4_library


def _lz4_decompress(block: bytes, capacity: int) -> bytes:
    library = _lz4()
    destination = ctypes.create_string_buffer(capacity)
    size = library.LZ4_decompress_safe(block, destination, len(block), capacity)
    if size < 0:
        raise ToolError(f"LZ4 decompression failed ({size})")
    return destination.raw[:size]


def _lz4_decompress_auto(block: bytes) -> bytes:
    """Decompress one legacy-frame block without knowing its plain size."""
    for capacity in (LZ4_BLOCK, 32 * 1024 * 1024, 128 * 1024 * 1024):
        try:
            return _lz4_decompress(block, capacity)
        except ToolError:
            continue
    raise ToolError("LZ4 ramdisk block is larger than 128 MiB or corrupt")


def _lz4_compress(data: bytes, level: int = 12) -> bytes:
    library = _lz4()
    bound = library.LZ4_compressBound(len(data))
    destination = ctypes.create_string_buffer(bound)
    size = library.LZ4_compress_HC(
        data, destination, len(data), bound, level
    )
    if size <= 0:
        raise ToolError("LZ4 compression failed")
    return destination.raw[:size]


def _lz4_compress_best(data: bytes, limit: int) -> bytes:
    """Compress with decreasing effort until the result fits *limit*."""

    smallest: bytes | None = None
    for level in (12, 9, 6, 3):
        compressed = _lz4_compress(data, level)
        if smallest is None or len(compressed) < len(smallest):
            smallest = compressed
        if len(compressed) <= limit:
            return compressed
    raise ToolError(
        f"compressed data needs {len(smallest or b'')} bytes "
        f"but only {limit} are available"
    )


def _round_up(value: int, align: int) -> int:
    return (value + align - 1) // align * align


def strip_fstab_flags(data: bytes) -> tuple[bytes, list[str]]:
    """Remove AVB/verity/encryption flags while preserving the file length.

    Only the fifth fstab field (the fs_mgr flags) is edited.  The removed
    text is replaced with spaces on the same line, so the returned buffer
    always has the same size as *data*.  This keeps cpio member sizes and
    EROFS inode sizes unchanged.
    """

    text = data.decode("utf-8", "surrogateescape")
    output: list[str] = []
    removed: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.endswith("\r\n"):
            body, newline = line[:-2], "\r\n"
        elif line.endswith("\n"):
            body, newline = line[:-1], "\n"
        elif line.endswith("\r"):
            body, newline = line[:-1], "\r"
        else:
            body, newline = line, ""

        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            output.append(line)
            continue

        match = FSTAB_FIELDS.match(body)
        if match is None:
            output.append(line)
            continue

        flags = match.group(10).split(",")
        kept: list[str] = []
        for flag in flags:
            if flag in REMOVE_EXACT or flag.startswith(REMOVE_PREFIX):
                removed.append(flag)
            else:
                kept.append(flag)
        if len(kept) == len(flags):
            output.append(line)
            continue

        if kept:
            new_body = "".join(match.groups()[:9]) + ",".join(kept) + match.group(11)
        else:
            new_body = "".join(match.groups()[:8]) + match.group(11)
        if len(new_body) > len(body):
            raise ToolError("fstab line grew while removing flags")
        new_body += " " * (len(body) - len(new_body))
        output.append(new_body + newline)

    patched = "".join(output).encode("utf-8", "surrogateescape")
    if len(patched) != len(data):
        raise ToolError("fstab patch changed the file size")
    return patched, removed


def _frame_blocks(frame: bytes) -> list[bytes]:
    if frame[:4] != LZ4_MAGIC:
        raise ToolError(f"unsupported ramdisk compression ({frame[:4].hex()})")
    blocks: list[bytes] = []
    position = 4
    while position + 4 <= len(frame):
        size = struct.unpack_from("<I", frame, position)[0]
        position += 4
        if size == 0:
            break
        if position + size > len(frame):
            raise ToolError("truncated LZ4 ramdisk frame")
        blocks.append(frame[position : position + size])
        position += size
    return blocks


def _build_frame(blocks: list[bytes]) -> bytes:
    output = bytearray(LZ4_MAGIC)
    for block in blocks:
        output += struct.pack("<I", len(block)) + block
    output += b"\0\0\0\0"
    return bytes(output)


def _ramdisk_layout(image: bytes) -> tuple[int, int, int, int, int]:
    """Return (ramdisk offset, ramdisk size, page, old pages, size field)."""
    if image[:8] == b"ANDROID!":
        header_version = struct.unpack_from("<I", image, 40)[0]
        if header_version < 3:
            raise ToolError(
                f"boot image header v{header_version} is not supported"
            )
        kernel_size, ramdisk_size = struct.unpack_from("<II", image, 8)
        header_size = struct.unpack_from("<I", image, 20)[0]
        page = 4096
        header_pages = _round_up(header_size, page) // page
        kernel_pages = _round_up(kernel_size, page) // page
        offset = (header_pages + kernel_pages) * page
        return offset, ramdisk_size, page, _round_up(ramdisk_size, page) // page, 12
    if image[:8] == b"VNDRBOOT":
        header_version = struct.unpack_from("<I", image, 8)[0]
        if header_version < 3:
            raise ToolError(
                f"vendor_boot image header v{header_version} is not supported"
            )
        page = struct.unpack_from("<I", image, 12)[0]
        ramdisk_size = struct.unpack_from("<I", image, 24)[0]
        header_size = struct.unpack_from("<I", image, 2096)[0]
        header_pages = _round_up(header_size, page) // page
        offset = header_pages * page
        return offset, ramdisk_size, page, _round_up(ramdisk_size, page) // page, 24
    raise ToolError("unknown boot image magic")


def _vendor_boot_table(
    image: bytes,
) -> tuple[int, int, int] | None:
    """Return (table offset, entry count, entry size) for vendor_boot v4."""

    if image[:8] != b"VNDRBOOT":
        return None
    header_version = struct.unpack_from("<I", image, 8)[0]
    if header_version < 4:
        return None
    page = struct.unpack_from("<I", image, 12)[0]
    ramdisk_size = struct.unpack_from("<I", image, 24)[0]
    header_size = struct.unpack_from("<I", image, 2096)[0]
    dtb_size = struct.unpack_from("<I", image, 2100)[0]
    entry_num, entry_size = struct.unpack_from("<II", image, 2116)
    if not entry_num or not entry_size:
        return None
    header_pages = _round_up(header_size, page) // page
    table_offset = (
        header_pages * page
        + _round_up(ramdisk_size, page)
        + _round_up(dtb_size, page)
    )
    if table_offset + entry_num * entry_size > len(image):
        raise ToolError("truncated vendor ramdisk table")
    return table_offset, entry_num, entry_size


def _vendor_boot_table_entry(image: bytes, old_size: int) -> int | None:
    """Return the file offset of the whole-ramdisk table entry, if any."""

    table = _vendor_boot_table(image)
    if table is None:
        return None
    table_offset, entry_num, entry_size = table
    entries = []
    for index in range(entry_num):
        base = table_offset + index * entry_size
        ramdisk_size, ramdisk_offset = struct.unpack_from("<II", image, base)
        entries.append((base, ramdisk_offset, ramdisk_size))
    if len(entries) != 1 or entries[0][1] != 0 or entries[0][2] != old_size:
        raise ToolError(
            "multi-fragment vendor ramdisk tables are not supported; rebuild "
            "with --keep-avb"
        )
    return entries[0][0]


def _patch_cpio(plain: bytearray, removed: list[str]) -> list[str]:
    """Patch every ``fstab.qcom`` member in a newc cpio archive in place."""

    position = 0
    changed: list[str] = []
    while True:
        if plain[position : position + 6] != b"070701":
            raise ToolError(f"bad cpio magic at offset {position}")
        fields = [
            int(
                plain[position + 6 + index * 8 : position + 6 + (index + 1) * 8],
                16,
            )
            for index in range(13)
        ]
        name_size = fields[11]
        file_size = fields[6]
        mode = fields[1]
        name_start = position + 110
        name = bytes(plain[name_start : name_start + name_size - 1])
        position = _round_up(name_start + name_size, 4)
        body_start = position
        body = bytes(plain[body_start : body_start + file_size])
        position = _round_up(body_start + file_size, 4)

        basename = name.rsplit(b"/", 1)[-1]
        is_regular = (mode & 0o170000) == 0o100000
        is_fstab = basename == b"fstab" or basename.startswith(b"fstab.")
        if is_fstab and is_regular and file_size:
            patched, flags = strip_fstab_flags(body)
            if flags:
                plain[body_start : body_start + file_size] = patched
                changed.append(name.decode("utf-8", "replace"))
                removed.extend(flags)
        if name == b"TRAILER!!!":
            break
    return changed


def patch_ramdisk_image(path: Path, log: Log) -> dict | None:
    """Patch ``boot.img`` or ``vendor_boot.img`` in place."""

    image = path.read_bytes()
    try:
        offset, size, page, old_pages, size_field = _ramdisk_layout(image)
    except ToolError as error:
        log(f"  {path.name}: skipped ({error})")
        return None
    if not size:
        log(f"  {path.name}: skipped (empty ramdisk)")
        return None

    frame = image[offset : offset + size]
    try:
        blocks = _frame_blocks(frame)
    except ToolError as error:
        log(f"  {path.name}: skipped ({error})")
        return None
    plains = [_lz4_decompress_auto(block) for block in blocks]
    plain = bytearray(b"".join(plains))

    removed: list[str] = []
    changed = _patch_cpio(plain, removed)
    if not changed:
        log(f"  {path.name}: no fstab flags to remove")
        return None

    new_plain = bytes(plain)
    old_plain = b"".join(plains)
    differences = [
        index for index, (before, after) in enumerate(zip(old_plain, new_plain))
        if before != after
    ]
    if not differences:
        log(f"  {path.name}: no fstab flags to remove")
        return None
    first, last = min(differences), max(differences) + 1

    new_blocks: list[bytes] = []
    start = 0
    for block, plain_block in zip(blocks, plains):
        end = start + len(plain_block)
        if first < end and last > start:
            new_blocks.append(_lz4_compress(new_plain[start:end]))
        else:
            new_blocks.append(block)
        start = end
    new_frame = _build_frame(new_blocks)

    capacity = old_pages * page
    if len(new_frame) > capacity:
        # Recompressing every block with the same 8 MiB input size is the
        # fallback when the original block boundaries are unlucky.
        fallback: list[bytes] = []
        for start in range(0, len(new_plain), LZ4_BLOCK):
            fallback.append(_lz4_compress(new_plain[start : start + LZ4_BLOCK]))
        new_frame = _build_frame(fallback)
    if len(new_frame) > capacity:
        raise ToolError(
            f"{path.name}: patched ramdisk needs {len(new_frame)} bytes but "
            f"only {capacity} are reserved"
        )

    signature_size = 0
    if image[:8] == b"ANDROID!":
        header_version = struct.unpack_from("<I", image, 40)[0]
        if header_version >= 4 and len(image) >= 1584:
            signature_size = struct.unpack_from("<I", image, 1580)[0]
    if signature_size:
        # The boot signature starts at the page boundary after the ramdisk.
        # A frame that still fits in the same page count keeps the signature
        # where it is; only crossing that boundary would move it.
        new_pages = _round_up(len(new_frame), page) // page
        if new_pages > old_pages:
            raise ToolError(
                f"{path.name}: patched ramdisk needs {len(new_frame)} bytes "
                f"but the signed ramdisk area is only {capacity}"
            )
        header_size = len(new_frame) if new_pages == old_pages else capacity
        zero_to = capacity
    else:
        # Keep the following sections (vendor_boot dtb/table) at their
        # original offsets if the ramdisk shrinks by more than one page.
        new_pages = _round_up(len(new_frame), page) // page
        header_size = len(new_frame) if new_pages == old_pages else capacity
        zero_to = capacity

    table_entry = (
        _vendor_boot_table_entry(image, size)
        if image[:8] == b"VNDRBOOT"
        else None
    )

    with path.open("r+b") as stream:
        stream.seek(offset)
        stream.write(new_frame)
        stream.write(b"\0" * (zero_to - len(new_frame)))
        stream.seek(size_field)
        stream.write(struct.pack("<I", header_size))
        if table_entry is not None:
            stream.seek(table_entry)
            stream.write(struct.pack("<I", len(new_frame)))

    log(
        f"  {path.name}: {', '.join(changed)} "
        f"({len(removed)} flags removed, ramdisk {size} -> {len(new_frame)} bytes)"
    )
    return {
        "file": path.name,
        "entries": changed,
        "flags": removed,
        "ramdisk_size": len(new_frame),
        "ramdisk_capacity": capacity,
    }


class ErofsImage:
    """Minimal read/write EROFS accessor for fstab patching."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.stream = path.open("r+b")
        self.stream.seek(_EROFS_SUPER_OFFSET)
        superblock = self.stream.read(128)
        if len(superblock) < 128:
            raise ToolError("truncated EROFS superblock")
        (
            magic,
            _checksum,
            _feature_compat,
            block_size_bits,
            _superblock_extslots,
            root_nid,
            _inodes,
            _epoch,
            _fixed_nsec,
            _blocks,
            meta_block,
            _xattr_block,
            _uuid,
            _volume_name,
            feature_incompat,
            _u1,
            _extra_devices,
            _device_slot_offset,
            directory_block_bits,
            _xattr_prefix_count,
            _xattr_prefix_start,
            _packed_nid,
            _xattr_filter_reserved,
            _reserved,
        ) = struct.unpack("<IIIBBHQQIIII16s16sIHHHBBIQB23s", superblock)
        if magic != _EROFS_MAGIC:
            raise ToolError(f"not an EROFS image (magic 0x{magic:08x})")
        self.block_size = 1 << block_size_bits
        self.root_nid = root_nid
        self.meta_block = meta_block
        self.directory_block_size = 1 << (directory_block_bits or block_size_bits)
        self.lz4_zero_padding = bool(
            feature_incompat & _FEATURE_INCOMPAT_LZ4_0PADDING
        )

    def close(self) -> None:
        self.stream.close()

    def __enter__(self) -> "ErofsImage":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _read_at(self, offset: int, size: int) -> bytes:
        self.stream.seek(offset)
        return self.stream.read(size)

    def _write_at(self, offset: int, data: bytes) -> None:
        self.stream.seek(offset)
        self.stream.write(data)

    def inode(self, nid: int) -> dict:
        location = self.meta_block * self.block_size + nid * 32
        raw = self._read_at(location, 64)
        if len(raw) < 32:
            raise ToolError(f"truncated inode {nid}")
        fmt, xattr_icount, mode = struct.unpack_from("<HHH", raw, 0)
        version = fmt & 1
        layout = (fmt >> 1) & 7
        xattr_size = 12 + (xattr_icount - 1) * 4 if xattr_icount else 0
        if version == 0:
            size = struct.unpack_from("<I", raw, 8)[0]
            inode_size = 32
        else:
            size = struct.unpack_from("<Q", raw, 8)[0]
            inode_size = 64
        block_address = struct.unpack_from("<I", raw, 16)[0]
        return {
            "nid": nid,
            "location": location,
            "format": fmt,
            "version": version,
            "layout": layout,
            "mode": mode,
            "size": size,
            "block_address": block_address,
            "inode_size": inode_size,
            "xattr_size": xattr_size,
            "inline_offset": location + inode_size + xattr_size,
        }

    def read_data(self, inode: dict) -> bytes:
        layout = inode["layout"]
        size = inode["size"]
        if layout == _LAYOUT_FLAT_PLAIN:
            return self._read_at(inode["block_address"] * self.block_size, size)
        if layout == _LAYOUT_FLAT_INLINE:
            full = (size // self.block_size) * self.block_size
            tail = size - full
            data = b""
            if full:
                data = self._read_at(inode["block_address"] * self.block_size, full)
            if tail:
                data += self._read_at(inode["inline_offset"], tail)
            return data
        if layout == _LAYOUT_COMPRESSED_COMPACT:
            info = self.compact_pcluster(inode)
            if info["type"] not in (_TYPE_HEAD1, _TYPE_HEAD2):
                raise ToolError(
                    f"unsupported EROFS lcluster type {info['type']} "
                    f"for nid {inode['nid']}"
                )
            if (info["algorithm"] & 0x0F) != 0:
                raise ToolError(
                    f"unsupported EROFS compression algorithm "
                    f"{info['algorithm'] & 0x0F}"
                )
            pcluster_size = info["compressed_blocks"] * self.block_size
            block = self._read_at(info["pblk"] * self.block_size, pcluster_size)
            if self.lz4_zero_padding:
                start = 0
                while start < len(block) and block[start] == 0:
                    start += 1
                if start == len(block):
                    raise ToolError(
                        f"empty EROFS pcluster for nid {inode['nid']}"
                    )
            else:
                start = 0
            # One compressed cluster can cover several logical clusters.  The
            # buffer is slightly larger than the file so a tail lcluster that
            # decompresses past i_size still validates.
            lcluster = 1 << info["lcluster_bits"]
            capacity = _round_up(size, lcluster) + lcluster
            decompressed = _lz4_decompress(block[start:], capacity)
            if len(decompressed) < size:
                raise ToolError(
                    f"EROFS pcluster for nid {inode['nid']} is too short"
                )
            return decompressed[:size]
        raise ToolError(
            f"unsupported EROFS data layout {layout} for nid {inode['nid']}"
        )

    def write_data(self, inode: dict, data: bytes) -> None:
        if len(data) != inode["size"]:
            raise ToolError("EROFS fstab patch changed the file size")
        layout = inode["layout"]
        if layout == _LAYOUT_FLAT_PLAIN:
            self._write_at(inode["block_address"] * self.block_size, data)
            return
        if layout == _LAYOUT_FLAT_INLINE:
            full = (len(data) // self.block_size) * self.block_size
            if full:
                self._write_at(inode["block_address"] * self.block_size, data[:full])
            if full < len(data):
                self._write_at(inode["inline_offset"], data[full:])
            return
        if layout == _LAYOUT_COMPRESSED_COMPACT:
            info = self.compact_pcluster(inode)
            if info["type"] not in (_TYPE_HEAD1, _TYPE_HEAD2) or (
                info["algorithm"] & 0x0F
            ) != 0:
                raise ToolError(
                    f"unsupported EROFS compressed fstab inode {inode['nid']}"
                )
            pcluster_size = info["compressed_blocks"] * self.block_size
            compressed = _lz4_compress_best(data, pcluster_size)
            if self.lz4_zero_padding:
                payload = b"\0" * (pcluster_size - len(compressed)) + compressed
            else:
                payload = compressed + b"\0" * (pcluster_size - len(compressed))
            self._write_at(info["pblk"] * self.block_size, payload)
            return
        raise ToolError(
            f"unsupported EROFS data layout {layout} for nid {inode['nid']}"
        )

    def list_directory(self, nid: int) -> dict[str, tuple[int, int]]:
        inode = self.inode(nid)
        data = self.read_data(inode)
        entries: dict[str, tuple[int, int]] = {}
        block_size = self.directory_block_size
        for start in range(0, len(data), block_size):
            block = data[start : start + block_size]
            if len(block) < 12:
                continue
            name_offset = struct.unpack_from("<H", block, 8)[0]
            if name_offset == 0 or name_offset % 12:
                continue
            count = name_offset // 12
            for index in range(count):
                entry_nid, entry_name_offset, file_type, _ = struct.unpack_from(
                    "<QHBB", block, index * 12
                )
                if index + 1 < count:
                    next_offset = struct.unpack_from(
                        "<H", block, (index + 1) * 12 + 8
                    )[0]
                else:
                    next_offset = len(block)
                if entry_name_offset >= len(block):
                    continue
                name = block[entry_name_offset:next_offset].split(b"\0", 1)[0].decode(
                    "utf-8", "replace"
                )
                entries[name] = (entry_nid, file_type)
        return entries

    def find(self, path: str) -> int:
        nid = self.root_nid
        for component in [part for part in path.split("/") if part]:
            entries = self.list_directory(nid)
            if component not in entries:
                raise KeyError(component)
            nid = entries[component][0]
        return nid

    def compact_pcluster(self, inode: dict) -> dict:
        """Decode the first lcluster of a compacted-index EROFS inode.

        The compacted index bit-packs lcluster entries into 8-byte (two 4-byte
        entries) or 32-byte (sixteen 2-byte entries) packs.  Only the first
        lcluster is needed: it carries the pcluster address used by the whole
        file.
        """

        location = inode["location"]
        header_offset = _round_up(
            location + inode["inode_size"] + inode["xattr_size"], 8
        )
        header = self._read_at(header_offset, 8)
        if len(header) < 8:
            raise ToolError(f"truncated EROFS map header for nid {inode['nid']}")
        advise = struct.unpack_from("<H", header, 4)[0]
        algorithm = header[6]
        cluster_bits = header[7] & 0x07
        if advise & _ADVISE_INLINE_PCLUSTER:
            raise ToolError(
                f"inline EROFS pcluster is not supported (nid {inode['nid']})"
            )

        block_bits = self.block_size.bit_length() - 1
        lcluster_bits = block_bits + cluster_bits
        lcluster_size = 1 << lcluster_bits
        total_index = (inode["size"] + lcluster_size - 1) >> lcluster_bits
        ebase = header_offset + 8

        compacted_4b_initial = ((32 - ebase % 32) // 4) & 7
        compacted_2b = 0
        if advise & _ADVISE_COMPACTED_2B and compacted_4b_initial < total_index:
            compacted_2b = ((total_index - compacted_4b_initial) // 16) * 16

        position = ebase
        shift = 2
        index = 0
        if index >= compacted_4b_initial:
            position += compacted_4b_initial * 4
            index -= compacted_4b_initial
            if index < compacted_2b:
                shift = 1
            else:
                position += compacted_2b * 2
                index -= compacted_2b
        position += index * (1 << shift)

        if shift == 2:
            count = 2
        elif lcluster_bits <= 12:
            count = 16
        else:
            raise ToolError("unsupported EROFS compacted index")
        pack_size = count << shift
        pack_start = position & ~(pack_size - 1)
        entry_index = (position - pack_start) >> shift
        low_bits = max(lcluster_bits, 12)
        encode_bits = ((pack_size - 4) * 8) >> (count.bit_length() - 1)

        def entry_at(entry: int) -> tuple[int, int]:
            bit = entry * encode_bits
            word = int.from_bytes(
                self._read_at(pack_start + bit // 8, 4), "little"
            ) >> (bit % 8)
            return word & ((1 << low_bits) - 1), (word >> low_bits) & 3

        cluster_offset, entry_type = entry_at(entry_index)
        pblk = 0
        if entry_type in (_TYPE_HEAD1, _TYPE_HEAD2):
            big_pcluster = bool(advise & _ADVISE_BIG_PCLUSTER_1)
            blocks = 0 if big_pcluster else 1
            cursor = entry_index
            while cursor > 0:
                cursor -= 1
                value, kind = entry_at(cursor)
                if kind == _TYPE_NONHEAD:
                    if big_pcluster:
                        if value & _LI_D0_CBLKCNT:
                            cursor -= 1
                            blocks += value & ~_LI_D0_CBLKCNT
                            continue
                        if value <= 1:
                            raise ToolError("corrupt EROFS pcluster chain")
                        cursor -= value - 2
                        continue
                    cursor -= value
                if cursor >= 0:
                    blocks += 1
            pblk = (
                struct.unpack(
                    "<I", self._read_at(pack_start + pack_size - 4, 4)
                )[0]
                + blocks
            )
        return {
            "type": entry_type,
            "cluster_offset": cluster_offset,
            "pblk": pblk,
            "lcluster_bits": lcluster_bits,
            "total_index": total_index,
            "compressed_blocks": inode["block_address"],
            "advise": advise,
            "algorithm": algorithm,
        }


def patch_erofs_image(path: Path, log: Log) -> dict | None:
    """Patch the ``/etc/fstab.*`` files inside an EROFS vendor image."""

    try:
        image = ErofsImage(path)
    except ToolError as error:
        log(f"  {path.name}: skipped ({error})")
        return None

    changed: list[str] = []
    removed: list[str] = []
    try:
        try:
            etc_nid = image.find("/etc")
        except KeyError:
            log(f"  {path.name}: no /etc directory")
            return None
        entries = image.list_directory(etc_nid)
        for name, (nid, file_type) in sorted(entries.items()):
            if not name.startswith("fstab.") or file_type != 1:
                continue
            inode = image.inode(nid)
            data = image.read_data(inode)
            patched, flags = strip_fstab_flags(data)
            if not flags:
                continue
            image.write_data(inode, patched)
            changed.append(f"/etc/{name}")
            removed.extend(flags)
        if not changed:
            log(f"  {path.name}: no fstab flags to remove")
            return None
    finally:
        image.close()

    log(
        f"  {path.name}: {', '.join(changed)} "
        f"({len(removed)} flags removed)"
    )
    return {
        "file": path.name,
        "entries": changed,
        "flags": removed,
    }


def patch_images(
    out_fw: Path,
    raw_parts: dict[str, Path],
    log: Log,
    include_vendor: bool = True,
) -> dict[str, dict | None]:
    """Patch boot, vendor_boot and vendor.  Returns a report per image."""

    report: dict[str, dict | None] = {}
    for name in ("boot", "vendor_boot"):
        path = out_fw / f"{name}.img"
        if not path.is_file():
            log(f"  {name}.img: not present")
            report[name] = None
            continue
        report[name] = patch_ramdisk_image(path, log)
    if include_vendor:
        vendor = raw_parts.get("vendor")
        if vendor is None or not vendor.is_file():
            log("  vendor.img: not present")
            report["vendor"] = None
        else:
            report["vendor"] = patch_erofs_image(vendor, log)
    else:
        log("  vendor.img: skipped (--no-super)")
        report["vendor"] = None
    return report
