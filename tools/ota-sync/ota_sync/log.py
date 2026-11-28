"""Console logging helpers."""

from __future__ import annotations

import sys
import time


def _stamp() -> str:
    return time.strftime("%H:%M:%S")


def info(message: str) -> None:
    print(f"[{_stamp()}] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[{_stamp()}] 警告：{message}", file=sys.stderr, flush=True)


def error(message: str) -> None:
    print(f"[{_stamp()}] 错误：{message}", file=sys.stderr, flush=True)
