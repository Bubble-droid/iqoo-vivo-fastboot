"""Resume state for the local ``.part`` file."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SyncState:
    inode: int
    total: int
    segments: list[tuple[int, int]]
    copied: list[int]

    @classmethod
    def fresh(
        cls, inode: int, total: int, segments: list[tuple[int, int]]
    ) -> "SyncState":
        return cls(
            inode=inode,
            total=total,
            segments=[(int(start), int(end)) for start, end in segments],
            copied=[int(start) for start, _ in segments],
        )

    def clamp(self) -> None:
        for index, (start, end) in enumerate(self.segments):
            self.copied[index] = max(start, min(int(self.copied[index]), end))


def load_state(
    path: Path,
    inode: int,
    total: int,
    segments: list[tuple[int, int]],
) -> Optional[SyncState]:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        state = SyncState(
            inode=int(raw["inode"]),
            total=int(raw["total"]),
            segments=[(int(a), int(b)) for a, b in raw["segments"]],
            copied=[int(value) for value in raw["copied"]],
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if state.inode != inode or state.total != total:
        return None
    if state.segments != [(int(a), int(b)) for a, b in segments]:
        return None
    if len(state.copied) != len(state.segments):
        return None
    state.clamp()
    return state


def save_state(path: Path, state: SyncState) -> None:
    path.write_text(
        json.dumps(
            {
                "inode": state.inode,
                "total": state.total,
                "segments": state.segments,
                "copied": state.copied,
            }
        )
    )
