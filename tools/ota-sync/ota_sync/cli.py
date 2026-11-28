"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, log
from .adb import Adb, AdbError, select_device
from .detect import (
    MD5_RE,
    SHA256_RE,
    detect,
    dump_db,
)
from ota2fastboot.util import parse_size
from .session import SyncSession


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ota-sync",
        description=(
            "把 vivo/iQOO 更新器正在下载的 OTA 包同步到本机，"
            "并在下载完成时冻结更新器"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "典型用法：\n"
            "  1. 手机开始下载系统更新，等有进度后暂停；\n"
            "  2. 运行本工具；\n"
            "  3. 看到“就绪”后，在手机上继续下载。\n\n"
            "USB 与无线 ADB 均可；有多台设备时用 --serial 指定。"
        ),
    )
    parser.add_argument("--serial", help="adb 序列号（默认自动选择唯一设备）")
    parser.add_argument("--path", help="设备上的 OTA 文件路径（默认自动探测）")
    parser.add_argument("--db", help="设备上的 downloads.db 路径（默认自动探测）")
    parser.add_argument("--package", help="更新器包名（默认自动探测）")
    parser.add_argument(
        "--total",
        type=parse_size,
        help="OTA 总字节数；支持 5284163108、5GiB 这类写法",
    )
    parser.add_argument(
        "--segments",
        type=int,
        help="并行分段数（默认读取 downloads.download_type）",
    )
    parser.add_argument("--md5", help="期望的服务器 MD5")
    parser.add_argument("--sha256", help="期望的服务器 SHA-256")
    parser.add_argument("--url", help="OTA 下载 URL（用于读取服务器元数据）")
    parser.add_argument(
        "--dest",
        default=str(Path.home() / "Downloads"),
        help="本机输出目录（默认 ~/Downloads）",
    )
    parser.add_argument("--keep", help="设备端保命硬链接路径")
    parser.add_argument(
        "--no-watch",
        action="store_true",
        help="不启动设备端冻结看门狗（仍会在完成时尝试冻结）",
    )
    parser.add_argument(
        "--no-freeze",
        action="store_true",
        help="完全不冻结更新器（测试用）",
    )
    parser.add_argument(
        "--detect-only",
        action="store_true",
        help="只打印探测结果，不建立链接、不传输",
    )
    parser.add_argument(
        "--dump-db",
        action="store_true",
        help="打印下载数据库的表结构和最新一行",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="建立硬链接和断点文件后退出，不开始同步",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="不等待回车确认，就绪后立即开始同步",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)

    if args.md5 and not MD5_RE.match(args.md5.strip()):
        parser.error("--md5 必须是 32 位十六进制")
    if args.sha256 and not SHA256_RE.match(args.sha256.strip()):
        parser.error("--sha256 必须是 64 位十六进制")
    if args.segments is not None and args.segments < 1:
        parser.error("--segments 必须大于 0")

    try:
        device = select_device(args.serial)
        adb = Adb(device)
        adb.ensure_root()
        properties = adb.props()
        model = properties.get("ro.product.model") or device.details.get(
            "model", ""
        )
        android = properties.get("ro.build.version.release", "")
        build = properties.get("ro.build.version.incremental", "")
        extras = [
            value
            for value in (
                model,
                f"Android {android}" if android else "",
                build,
            )
            if value
        ]
        log.info(
            f"设备：{device.serial}"
            + (f"（{'，'.join(extras)}）" if extras else "")
        )

        detection = detect(adb, args)
        print()
        for line in detection.summary():
            print(line)
        print()

        if args.dump_db:
            print(dump_db(detection))
            return 0
        if args.detect_only:
            return 0

        if not detection.file_exists:
            log.error(
                "OTA 文件尚未生成或路径不正确"
                + (f"：{detection.remote_path}" if detection.remote_path else "")
            )
            log.info(
                "请先在手机上开始下载，等有进度后暂停，再重新运行本工具。"
            )
            return 2

        session = SyncSession(adb, detection, args)
        if not session.prepare():
            return 0

        if args.prepare_only:
            log.info(
                "已就绪。请在手机上继续下载，然后重新运行本工具"
                "（或去掉 --prepare-only）开始同步。"
            )
            return 0

        return session.run()
    except (AdbError, RuntimeError, ValueError) as exc:
        log.error(str(exc))
        return 1
    except KeyboardInterrupt:
        log.info("已中断；.part 与断点状态已保留，重跑即可续传")
        return 130


if __name__ == "__main__":
    sys.exit(main())
