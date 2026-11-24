"""ota2fastboot - turn a vivo/iQOO full OTA (block mode) into a fastboot package.

The input is an *extracted* full OTA directory (the one that contains
``META-INF/com/android/metadata``, ``*.transfer.list``, ``*.new.dat*`` and
``firmware-update/``).  The output is a self-contained package::

    <out>/README.md                # package usage notes (Chinese)
    # Linux / macOS target:
    <out>/flash.sh                 # default: Android partitions only
    <out>/flash-full.sh            # full flash (bootloader included)
    <out>/flash-safe.sh            # alias for flash.sh
    <out>/fix-oem.sh               # OEM identity repair
    <out>/verify.sh                # check manifest.sha256
    <out>/common.sh                # shared helpers for the scripts above
    # Windows target:
    <out>/flash.ps1  / flash.cmd   # default: Android partitions only
    <out>/flash-full.ps1 / .cmd    # full flash (bootloader included)
    <out>/flash-safe.ps1 / .cmd    # alias for flash.ps1
    <out>/fix-oem.ps1 / .cmd       # OEM identity repair
    <out>/verify.ps1 / .cmd        # check manifest.sha256
    <out>/common.ps1               # shared helpers for the scripts above
    # Shared:
    <out>/package.env              # title, model pattern, OEM identity, AVB state
    <out>/meta/BUILD.md            # build report
    <out>/meta/build.json          # machine-readable build result
    <out>/meta/build.log           # build log (paths and serial redacted)
    <out>/firmware-update/
        boot.img vendor_boot.img dtbo.img recovery.img ...
        super.img                  # rebuilt with lpmake, sparse
        oem/OEM_<NAME>.img         # selected OEM variant (all with --all-oem)
        oem/vbmeta_oem_<NAME>.img
        vbmeta.img vbmeta_system.img vbmeta_vgc.img
    <out>/manifest.sha256
    <out>/manifest.json

What the converter does:

1. validates the OTA (block-based full package, device, stream files);
2. optionally probes an ADB-connected target device for its properties,
   OEM/VGC identity and dynamic partitions; if ADB cannot read the exact
   ``super`` size, the phone is rebooted to the bootloader, the remaining
   variables are read there, and the phone is rebooted back to Android.  The
   probe never writes to the phone, and its result can select the OEM/VGC
   variants and size ``super.img`` to the actual hardware; an explicit
   ``--device-size`` skips the bootloader pass.  The serial is masked in the
   reports and log unless ``--keep-serial`` is used;
3. rebuilds every dynamic partition from its transfer list, including split
   ``.new.dat`` streams and single-file Brotli ``.new.dat.br`` streams;
4. rebuilds ``vm-bootsys`` (block image that lives outside ``super``) when the
   OTA provides it;
5. extracts the OEM variant placed in ``super`` so the package repair script can
   restore the identity; ``--all-oem`` packages every variant from the OTA;
6. copies the physical images referenced by the full flash script (boot chain,
   modem, xbl, ...), using the OTA filenames as the fallback list;
7. verifies the original images: ``e2fsck`` on every ext4 image, ``lpdump`` on
   the generated super metadata, and ``avbtool verify_image
   --follow_chain_partitions`` for ``vbmeta``, ``vbmeta_system``,
   ``vbmeta_oem`` and ``vbmeta_vgc``;
8. removes the AVB, dm-verity and force-encryption flags from the fstab inside
   ``boot``, ``vendor_boot`` and ``vendor`` (skip with ``--keep-avb``), then
   builds a sparse ``super.img`` with ``lpmake`` from the OTA op-list sizes,
   including the selected ``oem`` and ``vgc`` variants;
9. writes the package README, a SHA-256 manifest, a build report and the
   platform-specific scripts from ``scripts/bash`` or ``scripts/windows``;
   with ``--target-platform windows`` and ``--platform-tools-dir DIR`` the
   Windows adb/fastboot tools are bundled under ``platform-tools/``.

Nothing is flashed.  The output directory is left ready for ``./flash.sh`` /
``./flash-full.sh`` on Linux and macOS, or ``.\\flash.cmd`` /
``.\\flash-full.cmd`` on Windows.  Generated files never contain the absolute
host path: the repository, OTA and output directories are redacted to
``<repo>``, ``<ota>`` and ``<out>`` in the build log, the device serial is
masked by default, and the reports use relative names.

Usage:
    ./ota2fastboot.py /path/to/PD2171_A_12.0.20.11.W10.V000L1-update-full
    python3 -m ota2fastboot /path/to/ota --oem PD2172C_CN-ZH_FULL_SC_CT-BMW --dry-run
    ./ota2fastboot.py /path/to/ota --target-platform windows \\
        --platform-tools-dir /path/to/platform-tools
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
