"""Build log with host-path redaction."""

from __future__ import annotations

import time
from pathlib import Path


class Log:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("w", encoding="utf-8")
        self.redactions: list[tuple[str, str]] = []

    def add_redaction(self, value: Path | str, token: str) -> None:
        text = str(value)
        if text:
            self.redactions.append((text, token))
            self.redactions.sort(key=lambda item: len(item[0]), reverse=True)

    def redact(self, text: str) -> str:
        for needle, token in self.redactions:
            text = text.replace(needle, token)
        return text

    def __call__(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {self.redact(message)}"
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()

    def raw(self, text: str) -> None:
        if not text:
            return
        text = self.redact(text)
        self.fh.write(text)
        if not text.endswith("\n"):
            self.fh.write("\n")
        self.fh.flush()

    def close(self) -> None:
        self.fh.close()
