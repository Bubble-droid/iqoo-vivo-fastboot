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
            "auto prefers the identity reported by the ADB device"
        ),
    )
    parser.add_argument(
        "--vgc",
        default="auto",
        help=(
            "VGC variant to place in super (NAME, auto, none); "
            "auto prefers the identity reported by the ADB device"
        ),
    )
    parser.add_argument(
        "--device-size",
        type=parse_size,
        help=(
            "physical super partition size in bytes "
            f"(default: size read from the ADB device or its bootloader, "
            f"otherwise {DEFAULT_DEVICE_SIZE} for iQOO 9 / 9 Pro)"
        ),
    )
    parser.add_argument(
        "--scripts-dir",
        type=Path,
        help="package script templates (default: <repo>/scripts)",
    )
    parser.add_argument(
        "--serial",
        help=(
            "serial of the ADB device to probe "
            "(default: first connected ADB device)"
        ),
    )
    device_group = parser.add_mutually_exclusive_group()
    device_group.add_argument(
        "--no-device",
        action="store_true",
        help="do not probe a connected device",
    )
    device_group.add_argument(
        "--require-device",
        action="store_true",
        help="fail when no compatible ADB device is connected",
    )
    parser.add_argument(
        "--skip-device-check",
        action="store_true",
        help="use a connected device even when it does not match the OTA",
    )
    parser.add_argument("--no-super", action="store_true", help="skip super.img")
    parser.add_argument(
        "--no-scripts",
        action="store_true",
        help="do not copy the flash scripts into the package",
    )
    parser.add_argument("--skip-fsck", action="store_true", help="skip e2fsck")
    parser.add_argument("--skip-avb", action="store_true", help="skip avbtool checks")
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
