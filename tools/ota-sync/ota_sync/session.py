"""Host-side sync engine.

The device downloader writes several equally sized segments in parallel.  For
each segment the valid data is a contiguous prefix, so the host repeatedly
probes the end of every prefix and copies only the new bytes.  A device-side
watchdog freezes the updater the moment the package is complete, which keeps
the updater from deleting the file or rebooting into recovery.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

from common.util import fmt_size, md5_file, sha256_file

from . import log
from .adb import Adb, AdbError
from .detect import (
    Detection,
    Segment,
    cleanup_db_temp,
    copy_and_analyze_db,
    describe_status,
    keep_basename,
)
from .state import SyncState, load_state, save_state

DEVICE_DIR = Path(__file__).resolve().parent.parent / "device"
WORK_DIR = "/data/local/tmp/ota-sync"
BLOCK = 65536
COPY_CHUNK = 32 << 20
TAIL_REWIND = 64 << 20


def quote(text: str) -> str:
    return shlex.quote(str(text))


def safe_bound(segment: Segment, first_empty_block: int) -> int:
    """Highest offset that is certainly written, from a block probe result.

    ``first_empty_block`` is the first 64 KiB block with no data.  The block
    before it may still be receiving writes, so only everything up to the
    start of that frontier block is trusted.
    """
    safe = (first_empty_block - 1) * BLOCK
    return max(segment.start, min(safe, segment.end))


class SyncSession:
    def __init__(self, adb: Adb, detection: Detection, args):
        self.adb = adb
        self.detection = detection
        self.args = args

        basename = os.path.basename(detection.remote_path or "ota.zip")
        if basename.startswith("ota-keep-"):
            basename = basename[len("ota-keep-") :]
        self.name = basename
        self.dest_dir = Path(args.dest).expanduser()
        self.dest = self.dest_dir / self.name
        self.part = self.dest_dir / (self.name + ".part")
        self.state_path = self.dest_dir / (self.name + ".part.state.json")

        self.total = detection.total
        self.segments: list[Segment] = detection.segments
        self.package = detection.package
        self.keep_path = args.keep or f"/data/local/tmp/{keep_basename(self.name)}"
        self.backup_path = f"/data/local/{keep_basename(self.name)}"
        self.source_path = detection.remote_path or self.keep_path

        stem = self.name.rsplit(".", 1)[0]
        self.mark = f"{WORK_DIR}/{stem}.frozen"
        self.watch_pid = f"{WORK_DIR}/{stem}.watch.pid"
        self.watch_log = f"{WORK_DIR}/{stem}.watch.log"

        self.use_watch = not getattr(args, "no_watch", False) and not getattr(
            args, "no_freeze", False
        )
        self.freeze_on_complete = not getattr(args, "no_freeze", False)
        if self.use_watch and not self.package:
            log.warn("未识别更新器包名，已禁用设备端看门狗")
            self.use_watch = False

        self._last_report = 0.0
        self._last_bytes = 0
        self._last_time = time.monotonic()
        self._last_status: Optional[int] = None
        self.tracks_db = bool(detection.db_row) and detection.db_matches_path

    # -- preparation -------------------------------------------------------

    def prepare(self) -> bool:
        """Return False when the destination is already complete."""
        self.dest_dir.mkdir(parents=True, exist_ok=True)

        if self.dest.exists() and self.verify_existing(self.dest):
            log.info(f"目标文件已存在且校验通过：{self.dest}")
            return False

        self.push_scripts()
        self.inode = self.ensure_links()
        self.ensure_part(self.inode)
        return True

    def verify_existing(self, path: Path) -> bool:
        try:
            if path.stat().st_size != self.total:
                return False
        except OSError:
            return False
        log.info(f"目标文件大小正确，校验 ZIP：{path}")
        if not self.zip_test(path):
            return False
        if self.detection.server_md5:
            log.info("校验服务器 MD5（约数十秒）…")
            if md5_file(path) != self.detection.server_md5:
                log.warn("已有文件与服务器 MD5 不符，重新同步")
                return False
        elif self.detection.server_sha256:
            log.info("校验服务器 SHA-256（约数十秒）…")
            if sha256_file(path) != self.detection.server_sha256:
                log.warn("已有文件与服务器 SHA-256 不符，重新同步")
                return False
        return True

    def push_scripts(self) -> None:
        self.adb.shell(f"mkdir -p {quote(WORK_DIR)}", timeout=30)
        for name in ("probe_bounds.sh", "freeze_watch.sh"):
            local = DEVICE_DIR / name
            if not local.exists():
                raise RuntimeError(f"缺少设备脚本：{local}")
            self.adb.push(str(local), f"{WORK_DIR}/{name}", timeout=60)
        self.adb.shell(
            f"chmod 755 {quote(WORK_DIR + '/probe_bounds.sh')} "
            f"{quote(WORK_DIR + '/freeze_watch.sh')}",
            timeout=30,
        )

    def ensure_links(self) -> int:
        source = self.detection.remote_path
        source_stat = self.adb.stat(source) if source else None
        keep_stat = self.adb.stat(self.keep_path)

        if source_stat is not None:
            if source == self.keep_path:
                keep_stat = source_stat
            elif keep_stat is None or keep_stat.inode != source_stat.inode:
                try:
                    self.adb.shell(
                        f"ln -f {quote(source)} {quote(self.keep_path)}",
                        timeout=30,
                    )
                except AdbError as exc:
                    raise RuntimeError(
                        "无法建立硬链接；请确认源文件与 /data/local/tmp "
                        f"在同一文件系统（{exc}）"
                    ) from exc
                keep_stat = self.adb.stat(self.keep_path)
            if keep_stat is None:
                raise RuntimeError(f"硬链接建立失败：{self.keep_path}")
            self.source_path = source
        else:
            if keep_stat is None:
                raise RuntimeError(
                    "OTA 文件尚未生成。请先在手机上开始下载，"
                    "等有进度后暂停，再重新运行本工具。"
                )
            log.info(f"源文件已不存在，改用硬链接：{self.keep_path}")
            self.source_path = self.keep_path

        try:
            backup_stat = self.adb.stat(self.backup_path)
            if backup_stat is None or backup_stat.inode != keep_stat.inode:
                self.adb.shell(
                    f"ln -f {quote(self.keep_path)} {quote(self.backup_path)}",
                    timeout=30,
                )
        except AdbError as exc:
            log.warn(f"第二硬链接建立失败（不影响主流程）：{exc}")

        log.info(
            f"硬链接就绪：{self.source_path} -> inode {keep_stat.inode}，"
            f"当前 {fmt_size(keep_stat.size)}"
        )
        return keep_stat.inode

    def ensure_part(self, inode: int) -> None:
        rebuilt = False
        if self.part.exists():
            try:
                if self.part.stat().st_size != self.total:
                    log.info("旧的 .part 大小不符，重建")
                    self.part.unlink()
                    rebuilt = True
            except OSError:
                rebuilt = True
        else:
            rebuilt = True

        if rebuilt:
            # A state file without its .part would make the host skip ranges
            # that are actually zero-filled in the new file.
            self.state_path.unlink(missing_ok=True)

        if not self.part.exists():
            with self.part.open("wb") as handle:
                handle.truncate(self.total)

        segments = [(segment.start, segment.end) for segment in self.segments]
        state = load_state(self.state_path, inode, self.total, segments)
        if state is None:
            state = SyncState.fresh(inode, self.total, segments)
            log.info("新建同步状态")
        else:
            done = sum(state.copied) - sum(start for start, _ in segments)
            log.info(f"从断点续传：{fmt_size(done)} 已在本机")
        save_state(self.state_path, state)

    # -- watchdog ----------------------------------------------------------

    def start_watchdog(self) -> None:
        if not self.use_watch:
            return
        self.stop_watchdog()
        self.adb.shell(
            f"rm -f {quote(self.mark)} {quote(self.watch_log)}", timeout=20
        )
        script = f"{WORK_DIR}/freeze_watch.sh"
        command = (
            f"nohup sh {quote(script)} {quote(self.source_path)} {self.total} "
            f"{quote(self.mark)} {quote(self.watch_log)} {quote(self.package)} "
            f">/dev/null 2>&1 & echo $! > {quote(self.watch_pid)}"
        )
        self.adb.shell(command, timeout=30)
        time.sleep(0.4)
        pid = self.adb.shell(f"cat {quote(self.watch_pid)} 2>/dev/null", timeout=20)
        if pid.isdigit():
            log.info(f"设备端冻结看门狗已启动（pid {pid}）")
        else:
            log.warn("看门狗未能确认启动；硬链接仍会保护文件")

    def stop_watchdog(self) -> None:
        try:
            pid = self.adb.shell(
                f"cat {quote(self.watch_pid)} 2>/dev/null", timeout=20
            )
            if pid.isdigit():
                self.adb.shell(f"kill {pid} 2>/dev/null", timeout=20)
            self.adb.shell(f"rm -f {quote(self.watch_pid)}", timeout=20)
        except AdbError:
            pass

    def marker(self) -> str:
        try:
            return self.adb.shell(
                f"cat {quote(self.mark)} 2>/dev/null", timeout=20
            )
        except AdbError:
            return ""

    def freeze(self) -> None:
        pids = self.adb.pids_of(self.package) if self.package else []
        if not pids:
            log.warn("未找到更新器进程，无法冻结")
            return
        for pid in pids:
            try:
                self.adb.shell(f"kill -STOP {pid} 2>/dev/null", timeout=20)
            except AdbError:
                pass
        log.info("已向更新器发送 SIGSTOP：" + " ".join(str(pid) for pid in pids))

    def thaw(self) -> None:
        pids = self.adb.pids_of(self.package) if self.package else []
        for pid in pids:
            try:
                self.adb.shell(f"kill -CONT {pid} 2>/dev/null", timeout=20)
            except AdbError:
                pass
        if pids:
            log.info("已恢复更新器：" + " ".join(str(pid) for pid in pids))

    def ensure_frozen(self) -> bool:
        if not self.package:
            return False
        for _ in range(20):
            states = self.adb.process_states(self.package)
            if not states or all(value in ("T", "t") for value in states.values()):
                return True
            self.freeze()
            time.sleep(0.3)
        log.warn("未能确认更新器已冻结；硬链接仍在，文件不会丢")
        return False

    # -- device I/O --------------------------------------------------------

    def probe_bounds(self, copied: list[int]) -> list[int]:
        specs: list[str] = []
        for segment, done in zip(self.segments, copied):
            start_block = (segment.start + BLOCK - 1) // BLOCK
            end_block = (segment.end + BLOCK - 1) // BLOCK
            last_block = min(done // BLOCK, end_block - 1)
            last_block = max(start_block, last_block)
            specs.append(f"{start_block}:{end_block}:{last_block}")

        command = (
            f"sh {quote(WORK_DIR + '/probe_bounds.sh')} "
            f"{quote(self.source_path)} "
            + " ".join(quote(spec) for spec in specs)
        )
        out = self.adb.shell(command, timeout=300)
        numbers = [int(value) for value in re.findall(r"\d+", out)]
        if len(numbers) != len(self.segments):
            raise RuntimeError(f"probe_bounds 输出异常：{out!r}")

        bounds: list[int] = []
        for segment, block in zip(self.segments, numbers):
            bounds.append(safe_bound(segment, block))
        return bounds

    def copy_range(self, handle, offset: int, length: int) -> int:
        command = (
            f"dd if={quote(self.source_path)} bs=1048576 "
            f"iflag=skip_bytes,count_bytes skip={offset} count={length} "
            "2>/dev/null"
        )
        process = subprocess.Popen(
            self.adb.exec_out_argv(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        handle.seek(offset)
        got = 0
        try:
            while got < length:
                chunk = process.stdout.read(min(1 << 20, length - got))
                if not chunk:
                    break
                handle.write(chunk)
                got += len(chunk)
        finally:
            process.stdout.close()
            try:
                process.wait(timeout=600)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise AdbError(
                    f"读取设备数据超时：偏移 {offset}，长度 {length}"
                ) from None
        return got

    def copy_ranges(self, handle, ranges: list[tuple[int, int]], state: SyncState) -> None:
        for index, (start, end) in enumerate(ranges):
            offset = start
            while offset < end:
                length = min(end - offset, COPY_CHUNK)
                got = self.copy_range(handle, offset, length)
                if got <= 0:
                    raise RuntimeError(
                        f"拷贝失败：段 {index} 偏移 {offset}；设备可能已断开"
                    )
                offset += got
                state.copied[index] = max(state.copied[index], offset)
                save_state(self.state_path, state)
                self.report(state, force=False)
            log.info(f"段 {index} 完成：{start:,}..{end:,}")

    def zip_tail_ok(self) -> bool:
        stat = self.adb.stat(self.source_path)
        if stat is None or stat.size < 128 * 1024:
            return False
        try:
            tail = self.adb.exec_out(
                f"dd if={quote(self.source_path)} bs=131072 "
                f"iflag=skip_bytes,count_bytes skip={stat.size - 131072} "
                "count=131072 2>/dev/null",
                timeout=60,
            )
        except AdbError:
            return False
        if len(tail) != 131072:
            return False
        position = tail.rfind(b"PK\x05\x06")
        if position < 0 or position + 22 > len(tail):
            return False
        comment_length = int.from_bytes(tail[position + 20 : position + 22], "little")
        return position + 22 + comment_length == len(tail)

    def read_db(self) -> dict:
        if not self.detection.db_path:
            return {}
        temp_dir = None
        try:
            temp_dir, analysis = copy_and_analyze_db(
                self.adb, self.detection.db_path
            )
            return analysis.get("row") or {}
        except AdbError:
            return {}
        finally:
            cleanup_db_temp(temp_dir)

    # -- verification ------------------------------------------------------

    def md5_remote(self) -> str:
        log.info("设备端计算 MD5（约数十秒）…")
        try:
            out = self.adb.shell(
                f"md5sum {quote(self.source_path)}", timeout=900
            )
        except AdbError as exc:
            log.warn(f"设备端 MD5 失败：{exc}")
            return ""
        return out.split()[0] if out else ""

    @staticmethod
    def zip_test(path: Path) -> bool:
        log.info("ZIP 完整性校验（CRC）…")
        try:
            with zipfile.ZipFile(path) as archive:
                bad = archive.testzip()
            if bad is not None:
                log.warn(f"ZIP 内文件 CRC 错误：{bad}")
                return False
            return True
        except Exception as exc:  # noqa: BLE001 - report any zip problem
            log.warn(f"ZIP 校验失败：{exc}")
            return False

    # -- reporting ---------------------------------------------------------

    def report(self, state: SyncState, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_report < 10:
            return
        done = sum(state.copied) - sum(segment.start for segment in self.segments)
        elapsed = max(now - self._last_time, 1.0)
        speed = (done - self._last_bytes) / elapsed
        percent = done * 100.0 / self.total if self.total else 0.0
        log.info(
            f"本机 {fmt_size(done)}/{fmt_size(self.total)} "
            f"({percent:.2f}%) {speed / 1e6:.1f} MB/s"
        )
        self._last_report = now
        self._last_time = now
        self._last_bytes = done

    # -- main loop ---------------------------------------------------------

    def run(self) -> int:
        if not hasattr(self, "inode"):
            self.inode = self.ensure_links()
            self.ensure_part(self.inode)

        segments = [(segment.start, segment.end) for segment in self.segments]
        state = load_state(self.state_path, self.inode, self.total, segments)
        if state is None:
            state = SyncState.fresh(self.inode, self.total, segments)
            save_state(self.state_path, state)
        self._last_bytes = sum(state.copied) - sum(start for start, _ in segments)

        stat = self.adb.stat(self.source_path)
        already_complete = (
            bool(stat and stat.size >= self.total) and self.zip_tail_ok()
        )
        self.start_watchdog()

        if already_complete:
            log.info("文件已完整（尺寸与 ZIP 尾部均正确），直接进入校验")
        else:
            log.info("已就绪：硬链接、断点文件与看门狗均已建立。")
            log.info(f"  设备端保命链接：{self.keep_path}")
            log.info(f"  本机断点文件　：{self.part}")
            if not getattr(self.args, "yes", False) and sys.stdin.isatty():
                try:
                    input("请先在手机上继续下载，然后按回车开始同步…")
                except EOFError:
                    pass
            else:
                log.info(
                    "请在手机上继续下载；脚本会自动同步，"
                    "下载完成时冻结更新器。"
                )

        attempt = 0
        while True:
            try:
                finished = self._sync_round(state)
            except AdbError as exc:
                log.warn(f"adb 通信失败：{exc}；3 秒后重试")
                time.sleep(3)
                continue

            if finished:
                return 0

            attempt += 1
            if attempt >= 2:
                raise RuntimeError(
                    "校验连续失败；已保留 .part 与断点状态，请检查网络后重跑"
                )
            log.info("校验未通过，重置断点后继续等待下载")
            state.copied = [segment.start for segment in self.segments]
            save_state(self.state_path, state)
            self.thaw()
            self.adb.shell(f"rm -f {quote(self.mark)}", timeout=20)
            if self.use_watch:
                self.start_watchdog()

    def _sync_round(self, state: SyncState) -> bool:
        last_db_check = 0.0

        with self.part.open("r+b") as handle:
            while True:
                marker = self.marker()
                if marker:
                    stat = self.adb.stat(self.source_path)
                    if (
                        stat is not None
                        and stat.size >= self.total
                        and self.zip_tail_ok()
                    ):
                        log.info(f"看门狗已冻结：{marker}；尺寸与 ZIP 尾部完整")
                        if self.freeze_on_complete:
                            self.ensure_frozen()
                        break
                    log.info("看门狗冻结过早，恢复更新器并重新布防")
                    self.thaw()
                    self.adb.shell(f"rm -f {quote(self.mark)}", timeout=20)
                    if self.use_watch:
                        self.start_watchdog()
                    continue

                if self.source_path != self.keep_path:
                    current = self.adb.stat(self.source_path)
                    if current is not None and current.inode != state.inode:
                        log.info(
                            f"源文件 inode 变化 {state.inode} -> {current.inode}，"
                            "重建硬链接并重新同步"
                        )
                        try:
                            state.inode = self.ensure_links()
                        except RuntimeError as exc:
                            log.warn(str(exc))
                            time.sleep(3)
                            continue
                        self.reset_part(state)
                        self._last_bytes = 0
                        self._last_time = time.monotonic()
                        continue

                stat = self.adb.stat(self.keep_path)
                if stat is None:
                    time.sleep(2)
                    continue

                try:
                    bounds = self.probe_bounds(state.copied)
                except (AdbError, RuntimeError) as exc:
                    log.warn(f"探测已写位置失败：{exc}；稍后重试")
                    time.sleep(3)
                    continue

                complete = (
                    stat.size >= self.total
                    and self.zip_tail_ok()
                    and all(
                        bound >= segment.end - BLOCK
                        for bound, segment in zip(bounds, self.segments)
                    )
                )
                if complete:
                    log.info("各段均已到末端，且 ZIP 尾部完整；冻结更新器")
                    if self.freeze_on_complete:
                        self.freeze()
                        self.ensure_frozen()
                    break

                now = time.monotonic()
                if self.tracks_db and now - last_db_check >= 5:
                    row = self.read_db()
                    last_db_check = now
                    status = row.get("status")
                    threads = int(row.get("complete_threads") or 0)
                    if stat.size >= self.total and (
                        status == 200 or threads >= len(self.segments)
                    ):
                        log.info(
                            f"数据库显示下载完成（status={status}, "
                            f"complete_threads={threads}）；冻结更新器"
                        )
                        if self.freeze_on_complete:
                            self.freeze()
                            self.ensure_frozen()
                        break
                    if status != self._last_status:
                        self._last_status = status
                        log.info(f"更新器状态：{describe_status(status)}")
                        if status == 193:
                            log.info("下载处于暂停状态，等待继续…")

                moved = 0
                for index, segment in enumerate(self.segments):
                    while state.copied[index] < bounds[index]:
                        length = min(
                            bounds[index] - state.copied[index], COPY_CHUNK
                        )
                        got = self.copy_range(handle, state.copied[index], length)
                        if got <= 0:
                            break
                        state.copied[index] += got
                        moved += got
                        save_state(self.state_path, state)
                        self.report(state)

                handle.flush()
                if moved == 0:
                    time.sleep(1)

        return self.finalize(state)

    def reset_part(self, state: SyncState) -> None:
        with self.part.open("wb") as handle:
            handle.truncate(self.total)
        state.copied = [segment.start for segment in self.segments]
        save_state(self.state_path, state)

    def finalize(self, state: SyncState) -> bool:
        log.info("收尾补齐各段…")
        starts = [
            max(segment.start, state.copied[index] - TAIL_REWIND)
            for index, segment in enumerate(self.segments)
        ]
        ranges = list(zip(starts, [segment.end for segment in self.segments]))
        with self.part.open("r+b") as handle:
            self.copy_ranges(handle, ranges, state)

        local_md5 = md5_file(self.part)
        log.info(f"本机 MD5：{local_md5}")
        remote_md5 = self.md5_remote()
        log.info(f"设备 MD5：{remote_md5 or '不可用'}")

        accepted = bool(remote_md5) and local_md5 == remote_md5
        if not accepted and self.detection.server_md5:
            if local_md5 == self.detection.server_md5:
                log.info("本机 MD5 与服务器记录一致，接受该文件")
                accepted = True

        if not accepted:
            log.info("本机与设备 MD5 不一致，全量重拷一次…")
            state.copied = [segment.start for segment in self.segments]
            with self.part.open("r+b") as handle:
                self.copy_ranges(
                    handle,
                    [(segment.start, segment.end) for segment in self.segments],
                    state,
                )
            local_md5 = md5_file(self.part)
            remote_md5 = self.md5_remote()
            log.info(f"重拷后 本机 {local_md5} / 设备 {remote_md5 or '不可用'}")
            accepted = bool(remote_md5) and local_md5 == remote_md5
            if not accepted and self.detection.server_md5:
                accepted = local_md5 == self.detection.server_md5

        if not accepted:
            log.info("MD5 校验失败，可能下载尚未完成")
            return False

        if self.detection.server_sha256 and not self.detection.server_md5:
            log.info("校验服务器 SHA-256（约数十秒）…")
            if sha256_file(self.part) != self.detection.server_sha256:
                log.info("SHA-256 与服务器记录不符")
                return False

        if not self.zip_test(self.part):
            log.info("ZIP 校验失败，可能下载尚未完成")
            return False

        if (
            self.detection.server_md5
            and local_md5 != self.detection.server_md5
        ):
            log.warn(
                "ZIP 完整，但 MD5 与服务器记录不同"
                f"（{local_md5} != {self.detection.server_md5}）"
            )

        self.part.rename(self.dest)
        self.state_path.unlink(missing_ok=True)
        log.info(f"完成：{self.dest}（{self.dest.stat().st_size:,} 字节）")
        if self.freeze_on_complete:
            log.info(
                "更新器仍处于冻结状态；如需继续升级：\n"
                f"  adb -s {self.adb.serial} shell "
                "'for p in $(pidof "
                + (self.package or "com.bbk.updater")
                + ") $(pidof "
                + (self.package or "com.bbk.updater")
                + ":remote); do kill -CONT $p; done'"
            )
        return True
