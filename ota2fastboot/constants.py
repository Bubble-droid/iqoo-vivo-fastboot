"""Constants shared by the converter modules."""

from __future__ import annotations

BLOCK = 4096
SPARSE_MAGIC = 0xED26FF3A

# Default size of the physical ``super`` partition.  14 GiB is the layout used
# by iQOO 9 / 9 Pro (PD2171 / PD2172).  Other devices must pass
# --device-size; the converter never probes an installed package because a
# clean checkout does not contain one.
DEFAULT_DEVICE_SIZE = 0x380000000
METADATA_SIZE = 65536
METADATA_SLOTS = 3
SUPER_GROUP = "qti_dynamic_partitions"

EXT4_MAGIC_OFFSET = 0x438
EXT4_MAGIC = b"\x53\xef"
