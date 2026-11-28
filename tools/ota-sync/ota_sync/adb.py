"""Transport-agnostic ADB access.

Only the ``adb`` command line is used, so USB and wireless debugging work the
same way.  Every shell command can optionally be wrapped in ``su`` because the
OTA directories are private to the updater application.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Optional


class AdbError(RuntimeError):
    """An adb invocation failed."""


@dataclass
class RemoteStat:
    size: int
    allocated: int
    inode: int


@dataclass
class DeviceEntry:
    serial: str
    state: str
    details: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        model = self.details.get("model", "")
        product = self.details.get("product", "")
        bits = [self.serial]
        if model:
            bits.append(model)
        elif product:
            bits.append(product)
        return " ".join(bits)


def list_devices() -> list[DeviceEntry]:
    try:
        proc = subprocess.run(
            ["adb", "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise AdbError("未找到 adb；请安装 platform-tools 并把 adb 加入 PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbError("adb devices 超时") from exc

    if proc.returncode != 0:
        raise AdbError(proc.stderr.strip() or "adb devices 失败")

    devices: list[DeviceEntry] = []
    for line in proc.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        serial = parts[0]
        state = parts[1] if len(parts) > 1 else "unknown"
        details: dict[str, str] = {}
        for item in parts[2:]:
            if ":" in item:
                key, value = item.split(":", 1)
                details[key] = value
        devices.append(DeviceEntry(serial, state, details))
    return devices


def select_device(serial: Optional[str] = None) -> DeviceEntry:
    wanted = serial or os.environ.get("ANDROID_SERIAL") or None
    devices = list_devices()

    if wanted:
        for device in devices:
            if device.serial == wanted:
                if device.state != "device":
                    raise AdbError(
                        f"设备 {wanted} 状态为 {device.state}；"
                        "请先在手机上确认 USB 调试授权"
                    )
                return device
        raise AdbError(f"没有找到 adb 设备 {wanted}")

    usable = [d for d in devices if d.state == "device"]
    if len(usable) == 1:
        return usable[0]

    if not usable:
        if not devices:
            raise AdbError("没有在线的 adb 设备；请连接 USB 或开启无线调试")
        lines = [f"  {d.serial}  {d.state}" for d in devices]
        raise AdbError(
            "没有可用设备（状态不是 device）：\n" + "\n".join(lines)
        )

    lines = [f"  {d.serial}  {d.state}  {d.label}" for d in usable]
    raise AdbError(
        "检测到多台 adb 设备，请用 --serial 指定一台：\n" + "\n".join(lines)
    )


class Adb:
    """A single selected adb device."""

    def __init__(self, device: DeviceEntry):
        self.device = device
        self.serial = device.serial
        self.su_template = ""

    # -- low level ---------------------------------------------------------

    def _base(self) -> list[str]:
        return ["adb", "-s", self.serial]

    def _run(
        self,
        args: list[str],
        timeout: Optional[float] = 120,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        try:
            proc = subprocess.run(
                self._base() + args,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"adb {' '.join(args)} 超时") from exc
        if check and proc.returncode != 0:
            message = proc.stderr.decode(errors="replace").strip()
            raise AdbError(
                f"adb {' '.join(args)} 失败（{proc.returncode}）：{message}"
            )
        return proc

    def raw_shell(self, command: str, timeout: Optional[float] = 120) -> bytes:
        return self._run(["shell", command], timeout=timeout).stdout

    def _wrap(self, command: str) -> str:
        if not self.su_template:
            return command
        return self.su_template.format(q=shlex.quote(command))

    def shell(self, command: str, timeout: Optional[float] = 120) -> str:
        data = self.raw_shell(self._wrap(command), timeout=timeout)
        return data.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()

    def exec_out(self, command: str, timeout: Optional[float] = 300) -> bytes:
        proc = self._run(
            ["exec-out", self._wrap(command)],
            timeout=timeout,
        )
        return proc.stdout

    def exec_out_argv(self, command: str) -> list[str]:
        """Argument vector for a streaming ``exec-out`` (used with Popen)."""
        return self._base() + ["exec-out", self._wrap(command)]

    # -- capabilities ------------------------------------------------------

    def ensure_root(self) -> None:
        out = self.raw_shell("id -u", timeout=20).decode(errors="replace").strip()
        if out == "0":
            self.su_template = ""
            return

        candidates = [
            "su",
            "/data/adb/ksu/bin/su",
            "/system/xbin/su",
            "/system/bin/su",
            "/debug_ramdisk/su",
            "/sbin/su",
        ]
        for candidate in candidates:
            for template in (
                candidate + " -c {q}",
                candidate + " 0 sh -c {q}",
            ):
                try:
                    probe = self.raw_shell(
                        template.format(q=shlex.quote("id -u")), timeout=20
                    )
                except AdbError:
                    continue
                if probe.decode(errors="replace").strip() == "0":
                    self.su_template = template
                    return

        raise AdbError(
            "需要 root 才能读取更新器的私有目录；"
            "请在 KernelSU / Magisk 中授权 adb shell 后重试"
        )

    def props(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for key in (
            "ro.product.model",
            "ro.product.device",
            "ro.build.version.release",
            "ro.build.version.incremental",
        ):
            try:
                result[key] = self.shell(f"getprop {key}", timeout=20)
            except AdbError:
                result[key] = ""
        return result

    # -- files -------------------------------------------------------------

    def exists(self, path: str) -> bool:
        try:
            return self.shell(f"test -e {shlex.quote(path)} && echo yes", timeout=20) == "yes"
        except AdbError:
            return False

    def stat(self, path: str) -> Optional[RemoteStat]:
        try:
            out = self.shell(
                f"stat -c '%s %b %i' {shlex.quote(path)} 2>/dev/null",
                timeout=30,
            )
        except AdbError:
            return None
        parts = out.split()
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            return None
        size, blocks, inode = (int(p) for p in parts)
        return RemoteStat(size=size, allocated=blocks * 512, inode=inode)

    def read_file(self, path: str, timeout: Optional[float] = 120) -> bytes:
        return self.exec_out(f"cat {shlex.quote(path)}", timeout=timeout)

    def read_file_optional(self, path: str, timeout: Optional[float] = 120) -> bytes:
        try:
            return self.read_file(path, timeout=timeout)
        except AdbError:
            return b""

    def push(self, local: str, remote: str, timeout: Optional[float] = 120) -> None:
        self._run(["push", str(local), remote], timeout=timeout)

    # -- processes ---------------------------------------------------------

    def pids_of(self, package: str) -> list[int]:
        """Return every process whose executable name is *package* or a child."""
        pids: list[int] = []
        try:
            out = self.shell("ps -A -o PID,ARGS 2>/dev/null", timeout=30)
        except AdbError:
            out = ""
        for line in out.splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2 or not parts[0].isdigit():
                continue
            pid = int(parts[0])
            args = parts[1].strip()
            name = args.split()[0] if args else ""
            if name == package or name.startswith(package + ":"):
                pids.append(pid)
        if not pids:
            for name in (package, package + ":remote"):
                try:
                    out = self.shell(f"pidof {shlex.quote(name)} 2>/dev/null", timeout=20)
                except AdbError:
                    continue
                for token in out.split():
                    if token.isdigit():
                        pids.append(int(token))
        return sorted(set(pids))

    def process_states(self, package: str) -> dict[int, str]:
        states: dict[int, str] = {}
        for pid in self.pids_of(package):
            try:
                out = self.shell(
                    f"awk '/^State:/{{print $2}}' /proc/{pid}/status 2>/dev/null",
                    timeout=20,
                )
            except AdbError:
                continue
            if out:
                states[pid] = out.split()[0]
        return states
