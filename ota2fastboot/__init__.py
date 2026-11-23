"""ota2fastboot - turn a vivo/iQOO full OTA (block mode) into a fastboot package.

The input is an *extracted* full OTA directory (the one that contains
``META-INF/com/android/metadata``, ``*.transfer.list``, ``*.new.dat*`` and
``firmware-update/``).  The output is a self-contained package::

    <out>/flash.sh                 # full flash (bootloader included)
    <out>/flash-safe.sh            # Android partitions only
    <out>/fix-oem.sh               # OEM identity repair
    <out>/common.sh                # shared helpers for the scripts above
    <out>/package.env              # title, model pattern, default OEM identity
    <out>/device.json              # ADB probe plus bootloader pass
    <out>/firmware-update/
        boot.img vendor_boot.img dtbo.img recovery.img ...
        super.img                  # rebuilt with lpmake, sparse
        oem/OEM_<NAME>.img         # every OEM variant in the OTA
        oem/vbmeta_oem_<NAME>.img
        vbmeta.img vbmeta_system.img vbmeta_oem.img vbmeta_vgc.img
    <out>/manifest.sha256
    <out>/manifest.json
    <out>/BUILD.md

What the converter does:

1. validates the OTA (block-based full package, device, stream files);
2. optionally probes an ADB-connected target device for its properties,
   OEM/VGC identity and dynamic partitions; if ADB cannot read the exact
   ``super`` size, the phone is rebooted to the bootloader, the remaining
   variables are read there, and the phone is rebooted back to Android.  The
   probe never writes to the phone, and its result can select the OEM/VGC
   variants and size ``super.img`` to the actual hardware; an explicit
   ``--device-size`` skips the bootloader pass;
3. rebuilds every dynamic partition from its transfer list, including split
   ``.new.dat`` streams and single-file Brotli ``.new.dat.br`` streams;
4. rebuilds ``vm-bootsys`` (block image that lives outside ``super``) when the
   OTA provides it;
5. builds a sparse ``super.img`` with ``lpmake`` from the OTA op-list sizes,
   including the ``oem`` and ``vgc`` variants selected on the command line
   (defaults: the connected device's identity, otherwise a ``FULL_SC_NULL``
   variant for the OTA device and ``V000``);
6. extracts every OEM variant so ``fix-oem.sh`` can restore the identity;
7. copies the physical images referenced by ``flash.sh`` (boot chain, modem,
   xbl, ...), using the OTA filenames as the fallback list;
8. verifies the result: ``e2fsck`` on every ext4 image, ``lpdump`` on the
   generated super metadata, and ``avbtool verify_image
   --follow_chain_partitions`` for ``vbmeta``, ``vbmeta_system``,
   ``vbmeta_oem`` and ``vbmeta_vgc``;
9. writes a SHA-256 manifest and a build report, and copies the package
   scripts from ``scripts/``.

Nothing is flashed.  The output directory is left ready for ``./flash.sh`` or
``./flash-safe.sh``.  Generated files never contain the absolute host path:
the repository, OTA and output directories are redacted to ``<repo>``,
``<ota>`` and ``<out>`` in the build log, and the reports use relative names.

Usage:
    ./ota2fastboot.py /path/to/PD2171_A_12.0.20.11.W10.V000L1-update-full
    python3 -m ota2fastboot /path/to/ota --oem PD2172C_CN-ZH_FULL_SC_CT-BMW --dry-run
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
