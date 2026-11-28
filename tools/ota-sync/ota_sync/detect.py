"""Discover the running OTA download on the device.

The vivo updater keeps three useful records:

* ``downloads.db`` – Android DownloadProvider style row with ``_data``,
  ``total_bytes``, ``current_bytes``, ``download_type`` and ``check_sum``;
* ``shared_prefs/remote.xml`` – a JSON snapshot with the full path, total size
  and download URL;
* ``shared_prefs/update_info.xml`` – the package description, including the
  server SHA-256.

``download_type`` is the number of parallel segments (the download library's
``core size``).  Segment boundaries follow the library's own arithmetic:

    chunk = total // count + 1
    segment k = [k * chunk, (k + 1) * chunk)   (last one ends at total)

Nothing device-specific is hard-coded; every value is read from the records
above, the file itself, or the HTTP response.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Optional

from common.util import fmt_size

from . import log
from .adb import Adb, AdbError

# The download library (com.vivo.ic.dm) uses this when the app does not call
# DownloadConfig.Builder.setCoreSize().  It is only a fallback: the real value
# is read from downloads.download_type once a download has started.
DEFAULT_CORE_SIZE = 5

MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

KNOWN_DB = "/data/user_de/0/com.bbk.updater/databases/downloads.db"


@dataclass
class Segment:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass
class Detection:
    remote_path: Optional[str] = None
    keep_path: Optional[str] = None
    db_path: Optional[str] = None
    package: Optional[str] = None
    total: int = 0
    current: int = 0
    status: Optional[int] = None
    download_type: int = 0
    segments: list[Segment] = field(default_factory=list)
    server_md5: Optional[str] = None
    server_sha256: Optional[str] = None
    url: Optional[str] = None
    file_size: int = 0
    file_allocated: int = 0
    inode: int = 0
    sources: dict[str, str] = field(default_factory=dict)
    db_row: dict[str, Any] = field(default_factory=dict)
    db_tables: dict[str, list[str]] = field(default_factory=dict)
    db_matches_path: bool = True

    @property
    def file_exists(self) -> bool:
        return self.inode != 0

    def summary(self) -> list[str]:
        rows = [
            ("更新器", self.package or "未知"),
            ("OTA 文件", self.remote_path or "未找到"),
            ("下载数据库", self.db_path or "未找到"),
            ("总大小", f"{self.total:,} 字节（{fmt_size(self.total)}）"
             if self.total else "未知"),
            ("已下载", f"{self.current:,} 字节"),
            ("状态", describe_status(self.status)),
            (
                "分段",
                f"{len(self.segments)} 段（{self.sources.get('segments', '?')}）"
                if self.segments else "未知",
            ),
            ("服务器 MD5", self.server_md5 or "未知"),
            ("服务器 SHA-256", self.server_sha256 or "未知"),
            (
                "文件状态",
                f"已存在，inode {self.inode}，"
                f"已分配 {fmt_size(self.file_allocated)}"
                if self.file_exists
                else "尚未生成",
            ),
        ]
        return [f"{name:<12}: {value}" for name, value in rows]


def describe_status(status: Optional[int]) -> str:
    if status is None:
        return "未知"
    if status == 192:
        return "192 下载中"
    if status == 193:
        return "193 已暂停"
    if status == 200:
        return "200 已完成"
    return str(status)


# ---------------------------------------------------------------------------
# Pure helpers (unit tested)
# ---------------------------------------------------------------------------


def derive_segments(total: int, count: int) -> list[Segment]:
    """Reproduce com.vivo.ic.dm's segment layout."""
    if total <= 0:
        raise ValueError("total 必须大于 0")
    count = max(1, min(int(count), 256))
    if count == 1:
        return [Segment(0, total)]

    chunk = total // count + 1
    segments: list[Segment] = []
    for index in range(count):
        start = index * chunk
        if start >= total:
            break
        end = total if index == count - 1 else min(start + chunk, total)
        segments.append(Segment(start, end))
    return segments


def pick_total(
    explicit: Optional[int],
    db_total: Optional[int],
    remote_total: Optional[int],
    update_length: Optional[int],
    head_length: Optional[int],
) -> tuple[int, str]:
    candidates = (
        (explicit, "--total"),
        (db_total, "downloads.total_bytes"),
        (remote_total, "remote.xml totalBytes"),
        (update_length, "update_info.xml fileLength"),
        (head_length, "HTTP Content-Length"),
    )
    for value, source in candidates:
        if value is not None and int(value) > 0:
            return int(value), source
    return 0, ""


def pick_segment_count(
    explicit: Optional[int],
    download_type: Optional[int],
    total: int,
) -> tuple[int, str]:
    if explicit:
        return max(1, int(explicit)), "--segments"
    if download_type is not None and int(download_type) > 0:
        return int(download_type), f"downloads.download_type={int(download_type)}"
    if 0 < total < (1 << 20):
        return 1, "文件小于 1 MiB"
    return DEFAULT_CORE_SIZE, f"下载库默认 core size={DEFAULT_CORE_SIZE}"


def pick_md5(
    explicit: Optional[str],
    head_md5: Optional[str],
    db_sum: Optional[str],
) -> tuple[Optional[str], str]:
    for value, source in (
        (explicit, "--md5"),
        (head_md5, "HTTP x-kss-meta-md5"),
        (db_sum, "downloads.check_sum"),
    ):
        if value and MD5_RE.match(value.strip()):
            return value.strip().lower(), source
    return None, ""


def pick_sha256(
    explicit: Optional[str], update_sha: Optional[str]
) -> tuple[Optional[str], str]:
    for value, source in (
        (explicit, "--sha256"),
        (update_sha, "update_info.xml sha256"),
    ):
        if value and SHA256_RE.match(value.strip()):
            return value.strip().lower(), source
    return None, ""


def package_from_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    match = re.match(r"^/data/(?:user_de/\d+|data)/([^/]+)/", path)
    if match:
        return match.group(1)
    match = re.match(r"^/data/vivo-updater/([^/]+)/", path)
    if match:
        return match.group(1)
    return None


def keep_basename(basename: str) -> str:
    if basename.startswith("ota-keep-"):
        return basename
    return "ota-keep-" + basename


# ---------------------------------------------------------------------------
# SQLite download record
# ---------------------------------------------------------------------------


def _pick_download_table(tables: dict[str, list[str]]) -> Optional[str]:
    def score(name: str, columns: list[str]) -> int:
        lower = name.lower()
        value = 0
        if lower == "downloads":
            value += 10
        if "download" in lower:
            value += 3
        if "total_bytes" in columns:
            value += 4
        if "current_bytes" in columns:
            value += 3
        if "_data" in columns:
            value += 2
        if "uri" in columns:
            value += 1
        return value

    ranked = sorted(
        tables.items(), key=lambda item: score(item[0], item[1]), reverse=True
    )
    if ranked and score(ranked[0][0], ranked[0][1]) > 0:
        return ranked[0][0]
    return None


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def analyze_db(path: str) -> dict[str, Any]:
    connection = sqlite3.connect(path)
    try:
        tables: dict[str, list[str]] = {}
        for (name,) in connection.execute(
            "select name from sqlite_master "
            "where type='table' and name not like 'sqlite_%'"
        ):
            columns = [
                row[1]
                for row in connection.execute(
                    f"pragma table_info({_quote_ident(name)})"
                )
            ]
            tables[name] = columns

        table = _pick_download_table(tables)
        row: dict[str, Any] = {}
        if table:
            columns = tables[table]
            order = "_id" if "_id" in columns else "rowid"
            cursor = connection.execute(
                f"select * from {_quote_ident(table)} "
                f"order by {order} desc limit 1"
            )
            result = cursor.fetchone()
            if result is not None:
                row = dict(zip([d[0] for d in cursor.description], result))
        return {"tables": tables, "table": table, "row": row}
    finally:
        connection.close()


def cleanup_db_temp(temp_dir: Optional[str]) -> None:
    if not temp_dir:
        return
    try:
        for name in os.listdir(temp_dir):
            try:
                os.unlink(os.path.join(temp_dir, name))
            except OSError:
                pass
        os.rmdir(temp_dir)
    except OSError:
        pass


def copy_and_analyze_db(
    adb: Adb, db_path: str
) -> tuple[str, dict[str, Any]]:
    """Copy the database (and its WAL) to a temporary directory and inspect it."""
    temp_dir = tempfile.mkdtemp(prefix="ota-sync-db-")
    local = os.path.join(temp_dir, "downloads.db")
    for suffix in ("", "-wal", "-shm"):
        data = adb.read_file_optional(db_path + suffix)
        if data:
            with open(local + suffix, "wb") as handle:
                handle.write(data)

    try:
        return temp_dir, analyze_db(local)
    except sqlite3.DatabaseError:
        # A half-copied WAL can make the snapshot unreadable.  Retry with the
        # main database only.
        for suffix in ("-wal", "-shm"):
            try:
                os.unlink(local + suffix)
            except OSError:
                pass
        try:
            return temp_dir, analyze_db(local)
        except sqlite3.DatabaseError as exc:
            log.warn(f"下载数据库无法读取：{exc}")
            return temp_dir, {"tables": {}, "table": None, "row": {}}


# ---------------------------------------------------------------------------
# Shared preferences snapshots
# ---------------------------------------------------------------------------


def read_pref_json(adb: Adb, package: str) -> tuple[dict[str, Any], dict[str, Any]]:
    base = f"/data/user_de/0/{package}/shared_prefs"
    values: dict[str, str] = {}
    for name in ("remote.xml", "update_info.xml"):
        raw = adb.read_file_optional(f"{base}/{name}")
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue
        for node in root.iter():
            key = node.get("name")
            if key is not None and node.get("value") is not None:
                values[key] = node.get("value") or ""

    remote: dict[str, Any] = {}
    update: dict[str, Any] = {}
    try:
        remote = json.loads(values.get("download_info", "{}"))
    except (TypeError, ValueError):
        pass
    try:
        update = json.loads(values.get("active_package_info", "{}"))
    except (TypeError, ValueError):
        pass
    return remote, update


# ---------------------------------------------------------------------------
# HTTP metadata
# ---------------------------------------------------------------------------


def _header(headers: Any, name: str) -> Optional[str]:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def http_probe(url: str, timeout: float = 12.0) -> dict[str, Any]:
    """Read Content-Length / x-kss-meta-md5 without downloading the file."""
    result: dict[str, Any] = {}
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "ota-sync/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            headers = dict(response.headers.items())
            length = _header(headers, "Content-Length")
            result["length"] = int(length) if length and length.isdigit() else None
            result["md5"] = _header(headers, "x-kss-meta-md5")
            result["etag"] = _header(headers, "ETag")
            result["url"] = response.geturl()
            return result
    except Exception as head_error:  # noqa: BLE001 - best effort
        result["head_error"] = str(head_error)

    # Some CDNs reject HEAD.  A one-byte ranged GET is enough to read headers.
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ota-sync/0.1",
            "Range": "bytes=0-0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            headers = dict(response.headers.items())
            length: Optional[int] = None
            content_range = _header(headers, "Content-Range")
            if content_range and "/" in content_range:
                tail = content_range.rsplit("/", 1)[1].strip()
                if tail.isdigit():
                    length = int(tail)
            if length is None:
                raw_length = _header(headers, "Content-Length")
                if raw_length and raw_length.isdigit():
                    length = int(raw_length)
            result["length"] = length
            result["md5"] = _header(headers, "x-kss-meta-md5")
            result["etag"] = _header(headers, "ETag")
            result["url"] = response.geturl()
            response.read(1)
            return result
    except Exception as exc:  # noqa: BLE001 - best effort
        result["error"] = str(exc)
        return result


# ---------------------------------------------------------------------------
# Device discovery
# ---------------------------------------------------------------------------


def find_db(adb: Adb, explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        return explicit
    if adb.exists(KNOWN_DB):
        return KNOWN_DB

    try:
        out = adb.shell(
            "find /data/user_de /data/data -maxdepth 5 -type f "
            "-name downloads.db 2>/dev/null",
            timeout=60,
        )
    except AdbError:
        return None
    paths = [line.strip() for line in out.splitlines() if line.strip()]
    if not paths:
        return None
    preferred = [path for path in paths if "updater" in path.lower()]
    return (preferred or paths)[0]


def find_ota_file(
    adb: Adb,
    explicit: Optional[str],
    db_row: dict[str, Any],
    remote: dict[str, Any],
    package: Optional[str],
) -> Optional[str]:
    if explicit:
        return explicit

    intended: Optional[str] = None
    for candidate in (db_row.get("_data"), remote.get("fullFilePath")):
        if not candidate:
            continue
        if adb.exists(str(candidate)):
            return str(candidate)
        if intended is None:
            intended = str(candidate)

    roots: list[str] = []
    if package:
        roots.append(f"/data/vivo-updater/{package}/OTAPackage")
    roots.append("/data/vivo-updater")

    for root in roots:
        try:
            out = adb.shell(
                f"find {shlex.quote(root)} -maxdepth 4 -type f "
                "-name '*.zip' 2>/dev/null",
                timeout=60,
            )
        except AdbError:
            continue
        files = [line.strip() for line in out.splitlines() if line.strip()]
        if files:
            return _newest_file(adb, files)

    try:
        out = adb.shell(
            "find /data/local/tmp /data/local -maxdepth 1 -type f "
            "-name 'ota-keep-*.zip' 2>/dev/null",
            timeout=60,
        )
    except AdbError:
        out = ""
    files = [line.strip() for line in out.splitlines() if line.strip()]
    if files:
        return _newest_file(adb, files)

    return intended


def _newest_file(adb: Adb, files: list[str]) -> str:
    if len(files) == 1:
        return files[0]
    try:
        out = adb.shell(
            "stat -c '%Y %n' " + " ".join(shlex.quote(path) for path in files),
            timeout=30,
        )
    except AdbError:
        return files[0]
    best_path = files[0]
    best_time = -1
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            if int(parts[0]) > best_time:
                best_time = int(parts[0])
                best_path = parts[1]
    return best_path


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------


def detect(adb: Adb, args: Any) -> Detection:
    db_path = find_db(adb, getattr(args, "db", None))
    temp_dir: Optional[str] = None
    analysis: dict[str, Any] = {"tables": {}, "table": None, "row": {}}
    if db_path:
        temp_dir, analysis = copy_and_analyze_db(adb, db_path)

    row = analysis.get("row") or {}
    try:
        package = (
            getattr(args, "package", None)
            or package_from_path(db_path)
            or package_from_path(row.get("_data"))
        )
        remote: dict[str, Any] = {}
        update: dict[str, Any] = {}
        if package:
            remote, update = read_pref_json(adb, package)

        remote_path = find_ota_file(
            adb,
            getattr(args, "path", None),
            row,
            remote,
            package,
        )

        # An explicit --path overrides the database.  Do not mix in the status,
        # counters or checksum of an unrelated row when the paths disagree.
        explicit_path = getattr(args, "path", None)
        use_row = True
        if explicit_path and row.get("_data"):
            use_row = os.path.basename(str(explicit_path)) == os.path.basename(
                str(row["_data"])
            ) or str(explicit_path) == str(row["_data"])
        if not use_row:
            row = {}
            log.info("--path 与数据库记录不同，忽略数据库中的下载进度")

        total, total_source = pick_total(
            getattr(args, "total", None),
            row.get("total_bytes"),
            remote.get("totalBytes"),
            update.get("fileLength"),
            None,
        )

        url = (
            getattr(args, "url", None)
            or row.get("uri")
            or remote.get("downloadUrl")
        )
        head: dict[str, Any] = {}
        need_head = bool(url) and (
            total <= 0
            or not (
                getattr(args, "md5", None)
                or row.get("check_sum")
                or update.get("sha256")
            )
        )
        if need_head:
            log.info("读取服务器元数据（HEAD）…")
            head = http_probe(str(url))
            if head.get("error"):
                log.warn(f"服务器元数据读取失败：{head['error']}")

        total, total_source = pick_total(
            getattr(args, "total", None),
            row.get("total_bytes"),
            remote.get("totalBytes"),
            update.get("fileLength"),
            head.get("length"),
        )
        if total <= 0:
            raise RuntimeError(
                "无法确定 OTA 总大小；请用 --total 指定（例如 --total 5284163108）"
            )

        count, count_source = pick_segment_count(
            getattr(args, "segments", None),
            row.get("download_type"),
            total,
        )
        segments = derive_segments(total, count)

        server_md5, md5_source = pick_md5(
            getattr(args, "md5", None),
            head.get("md5"),
            row.get("check_sum"),
        )
        server_sha256, sha_source = pick_sha256(
            getattr(args, "sha256", None),
            update.get("sha256"),
        )

        stat = adb.stat(remote_path) if remote_path else None
        detection = Detection(
            remote_path=remote_path,
            db_path=db_path,
            package=package,
            total=total,
            current=int(row.get("current_bytes") or remote.get("currentBytes") or 0),
            status=int(row["status"]) if row.get("status") is not None else None,
            download_type=int(row.get("download_type") or 0),
            segments=segments,
            server_md5=server_md5,
            server_sha256=server_sha256,
            url=str(url) if url else None,
            file_size=stat.size if stat else 0,
            file_allocated=stat.allocated if stat else 0,
            inode=stat.inode if stat else 0,
            sources={
                "total": total_source,
                "segments": count_source,
                "md5": md5_source,
                "sha256": sha_source,
                "path": "命令行 --path"
                if getattr(args, "path", None)
                else ("downloads._data" if row.get("_data") else "扫描"),
            },
            db_row=analysis.get("row") or {},
            db_tables=analysis.get("tables") or {},
            db_matches_path=use_row,
        )
        return detection
    finally:
        cleanup_db_temp(temp_dir)


def dump_db(detection: Detection) -> str:
    lines: list[str] = []
    if not detection.db_path:
        return "未找到下载数据库"
    lines.append(f"数据库：{detection.db_path}")
    for table, columns in sorted(detection.db_tables.items()):
        lines.append(f"  表 {table}: {', '.join(columns)}")
    lines.append("最新一行：")
    for key, value in detection.db_row.items():
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        lines.append(f"  {key} = {text}")
    return "\n".join(lines)
