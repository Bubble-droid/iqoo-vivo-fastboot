"""OTA metadata, transfer lists and dynamic partition op lists."""

from __future__ import annotations

from pathlib import Path

from .errors import ToolError


def read_props(path: Path) -> dict[str, str]:
    props: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        props[key.strip()] = value.strip()
    return props


class TransferList:
    def __init__(self, path: Path):
        self.path = path
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(lines) < 4:
            raise ToolError(f"truncated transfer list: {path}")
        if lines[0] != "4":
            raise ToolError(f"unsupported transfer list version {lines[0]}: {path}")
        self.total_blocks = int(lines[1])
        self.max_block = 0
        self.commands: list[tuple[str, list[tuple[int, int]]]] = []
        for line in lines[4:]:
            tokens = line.split()
            if len(tokens) != 2:
                raise ToolError(f"invalid transfer command in {path}: {line}")
            command, raw_ranges = tokens
            if command not in {"new", "zero", "erase"}:
                raise ToolError(f"unsupported transfer command in {path}: {line}")
            numbers = [int(item) for item in raw_ranges.split(",")]
            if numbers[0] != len(numbers) - 1 or len(numbers) % 2 != 1:
                raise ToolError(f"invalid range list in {path}: {line}")
            ranges = list(zip(numbers[1::2], numbers[2::2]))
            for start, end in ranges:
                if start < 0 or end < start:
                    raise ToolError(f"invalid block range in {path}: {line}")
                self.max_block = max(self.max_block, end)
            self.commands.append((command, ranges))
        if not any(command == "new" for command, _ in self.commands):
            raise ToolError(f"no new-data commands in {path}")


def find_streams(tl_path: Path) -> list[Path] | None:
    stem = tl_path.name[: -len(".transfer.list")]
    folder = tl_path.parent
    single = folder / f"{stem}.new.dat.br"
    if single.is_file():
        return [single]
    base = folder / f"{stem}.new.dat"
    if not base.is_file():
        return None
    streams = [base]
    index = 1
    while True:
        part = folder / f"{stem}.new.dat.{index}"
        if not part.is_file():
            break
        streams.append(part)
        index += 1
    return streams


def parse_op_list(path: Path) -> tuple[dict[str, int], dict[str, str]]:
    """Return {partition: size} and {partition: group} from an op list."""
    sizes: dict[str, int] = {}
    groups: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        tokens = line.split()
        if not tokens or tokens[0].startswith("#"):
            continue
        if tokens[0] == "add" and len(tokens) == 3:
            groups[tokens[1]] = tokens[2]
        elif tokens[0] == "resize" and len(tokens) == 3:
            sizes[tokens[1]] = int(tokens[2])
    return sizes, groups
