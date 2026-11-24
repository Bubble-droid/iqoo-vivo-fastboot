"""Command line interface."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import __doc__ as TOOL_DOC
from .builder import build
from .constants import DEFAULT_DEVICE_SIZE
from .util import parse_size


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert an extracted vivo/iQOO full OTA into a fastboot package",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=TOOL_DOC,
    )
    parser.add_argument("package", type=Path, help="extracted OTA directory")
    parser.add_argument("--output", type=Path, help="output directory")
    parser.add_argument(
        "--oem",
        default="auto",
        help=(
            "OEM variant to place in super (NAME, auto, none); "
            "auto uses the identity reported by --probe-device when present, "
            "otherwise a matching FULL_SC variant from the OTA"
        ),
    )
    parser.add_argument(
        "--vgc",
        default="auto",
        help=(
            "VGC variant to place in super (NAME, auto, none); "
            "auto uses the identity reported by --probe-device when present, "
            "otherwise V000 from the OTA"
        ),
    )
    parser.add_argument(
        "--device-size",
        type=parse_size,
        help=(
            "physical super partition size in bytes "
            f"(default: {DEFAULT_DEVICE_SIZE}, the iQOO 9 / 9 Pro size; "
            "--probe-device uses the connected phone instead)"
        ),
    )
    parser.add_argument(
        "--devcfg-type",
        choices=("auto", "0", "1"),
        default="auto",
        help=(
            "devcfg image to package: 0 for devcfg.mbn, 1 for "
            "devcfg_overlay1.mbn; auto reads ro.vivo.boot.devcfg_type with "
            "--probe-device, otherwise 0"
        ),
    )
    parser.add_argument(
        "--all-oem",
        action="store_true",
        help=(
            "package every OEM repair variant from the OTA; by default only the "
            "variant placed in super is kept (use the package OEM repair script)"
        ),
    )
    parser.add_argument(
        "--target-platform",
        choices=("linux", "windows", "macos"),
        help=(
            "target platform for the package (default: the build host); "
            "linux/macos packages ship bash scripts, windows packages ship "
            "native PowerShell scripts"
        ),
    )
    parser.add_argument(
        "--platform-tools-dir",
        type=Path,
        help=(
            "directory with adb.exe/fastboot.exe to copy into a windows-target "
            "package; skipped when omitted"
        ),
    )
    parser.add_argument(
        "--keep-serial",
        action="store_true",
        help=(
            "write the device serial in full to the reports and log; by default "
            "only the last four characters are kept"
        ),
    )
    parser.add_argument(
        "--scripts-dir",
        type=Path,
        help=(
            "package script template root with bash/ and windows/ "
            "(default: <repo>/scripts)"
        ),
    )
    parser.add_argument(
        "--serial",
        help=(
            "serial of the ADB device to probe with --probe-device "
            "(default: first connected ADB device)"
        ),
    )
    device_group = parser.add_mutually_exclusive_group()
    device_group.add_argument(
        "--probe-device",
        action="store_true",
        help=(
            "read the connected phone over ADB/bootloader and use its super "
            "size, OEM/VGC identity and devcfg type; disabled by default so "
            "the same OTA always produces the same package"
        ),
    )
    device_group.add_argument(
        "--no-device",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    device_group.add_argument(
        "--require-device",
        action="store_true",
        help="probe the connected phone and fail when it is absent or mismatched",
    )
    parser.add_argument(
        "--skip-device-check",
        action="store_true",
        help="with --probe-device, use the phone even when it does not match the OTA",
    )
    parser.add_argument("--no-super", action="store_true", help="skip super.img")
    parser.add_argument(
        "--no-scripts",
        action="store_true",
        help="do not copy the flash scripts into the package",
    )
    parser.add_argument("--skip-fsck", action="store_true", help="skip e2fsck")
    parser.add_argument("--skip-avb", action="store_true", help="skip avbtool checks")
    parser.add_argument(
        "--keep-avb",
        action="store_true",
        help=(
            "keep AVB, dm-verity and force-encryption untouched; by default the "
            "build removes them from the fstab inside boot, vendor_boot and "
            "vendor, and the flash scripts disable vbmeta verification"
        ),
    )
    parser.add_argument("--keep-raw", action="store_true", help="keep extracted partition images")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="keep an existing output directory instead of refusing it",
    )
    parser.add_argument(
        "--reuse-raw",
        action="store_true",
        help="with --resume: skip extraction when an image already has the expected size",
    )
    parser.add_argument(
        "--reuse-super",
        action="store_true",
        help="with --resume: reuse super.img when it matches the requested partitions",
    )
    parser.add_argument("--force", action="store_true", help="delete a non-empty output directory")
    parser.add_argument("--dry-run", action="store_true", help="validate and print the plan")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    return build(args)
