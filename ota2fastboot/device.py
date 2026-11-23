"""Non-destructive probing of the connected target device.

Only an ADB connection is accepted.  The properties, partition names and
mounted sizes are read first; when ADB cannot provide the exact ``super``
partition size, the phone is automatically rebooted to the bootloader, the
remaining variables are read with ``fastboot getvar``, and the phone is
rebooted back to Android.

The converter never writes to the phone's userdata or partitions.  The data
collected here makes the generated package fit the actual hardware:

* the real ``super`` partition size replaces the built-in default;
* the OEM and VGC identities reported by the phone select the matching
  variants from the OTA;
* the model is checked against the OTA before any device data is used;
* the probed model is added to ``MODEL_REGEX`` so the flash-time check accepts
  the real device.

Every query is best effort.  A missing tool, a locked device or a denied
sysfs read simply removes that field from the result.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .log import Log
from .util import fmt_size

ADB_TIMEOUT = 10
FASTBOOT_TIMEOUT = 20
BOOTLOADER_WAIT = 60
ANDROID_WAIT = 90

# Only the properties that are useful for package selection and provenance are
# stored in meta/build.json.  The full getprop table is far too large and
# contains unrelated user data.
PROP_KEYS = (
    "ro.product.device",
    "ro.product.name",
    "ro.product.model",
    "ro.product.country.region",
    "ro.vivo.oem.name",
    "ro.vivo.system.region.version",
    "ro.vgc.cust.name",
    "ro.vgc.device.name",
    "ro.build.version.release",
    "ro.build.version.incremental",
    "ro.build.date.utc",
    "ro.build.fingerprint",
    "ro.boot.vivo.hardware.pcb.detect",
    "ro.boot.hardware.sku",
    "ro.boot.product.vendor.sku",
    "ro.boot.dynamic_partitions",
    "ro.boot.slot_suffix",
    "ro.boot.slot",
    "ro.boot.serialno",
    "ro.boot.vbmeta.device_state",
    "ro.boot.flash.locked",
    "ro.boot.super_partition_size",
)

# Mount points of the dynamic partitions, used to record the mounted sizes.
MOUNT_MAP = {
    "/": "system",
    "/system_ext": "system_ext",
    "/product": "product",
    "/vendor": "vendor",
    "/vendor_dlkm": "vendor_dlkm",
    "/odm": "odm",
    "/vendor/vgc": "vgc",
    "/oem": "oem",
}


@dataclass
class DeviceInfo:
    """A snapshot of the connected device."""

    mode: str
    serial: str
    product: str = ""
    model: str = ""
    device: str = ""
    oem: str = ""
    vgc: str = ""
    android: str = ""
    fingerprint: str = ""
    hardware: str = ""
    slot: str = ""
    unlocked: bool | None = None
    super_size: int | None = None
    super_size_source: str = ""
    dynamic_partitions: list[str] = field(default_factory=list)
    partition_sizes: dict[str, int] = field(default_factory=dict)
    mounted_sizes: dict[str, int] = field(default_factory=dict)
    bootloader: dict[str, str] = field(default_factory=dict)
    props: dict[str, str] = field(default_factory=dict)
    matched: bool | None = None
    used: bool = True
    note: str = ""
    probed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def model_tokens(self) -> list[str]:
        """Return the normalized model tokens reported by the device."""
        raw = [
            self.product,
            self.device,
            self.model,
            self.hardware,
            self.oem.split("_", 1)[0] if self.oem else "",
            self.bootloader.get("product", ""),
            self.bootloader.get("product-model", ""),
        ]
        tokens: list[str] = []
        for value in raw:
            value = value.strip()
            if not value:
                continue
            candidates = [value]
            if value.upper().startswith("HW_"):
                candidates.append(value[3:])
            if "_" in value:
                candidates.append(value.split("_", 1)[0])
            for candidate in candidates:
                token = _normalize(candidate)
                if token and any(ch.isdigit() for ch in token) and token not in tokens:
                    tokens.append(token)
        return tokens

    def log_summary(self, log: Log) -> None:
        state = "unlocked" if self.unlocked else "locked"
        if self.unlocked is None:
            state = "unknown"
        log(f"  product: {self.product or 'unknown'}, model: {self.model or 'unknown'}")
        log(f"  OEM: {self.oem or 'unknown'}, VGC: {self.vgc or 'unknown'}")
        log(
            f"  Android: {self.android or 'unknown'}, slot: {self.slot or 'unknown'}, "
            f"{state}"
        )
        if self.super_size:
            log(
                f"  super: {fmt_size(self.super_size)} "
                f"({self.super_size_source or 'device'})"
            )
        else:
            log("  super: size unavailable")
        if self.dynamic_partitions:
            log(
                f"  dynamic partitions: {len(self.dynamic_partitions)} "
                f"({', '.join(self.dynamic_partitions)})"
            )
        if self.mounted_sizes:
            log(
                "  mounted sizes: "
                + ", ".join(
                    f"{name}={fmt_size(size)}"
                    for name, size in sorted(self.mounted_sizes.items())
                )
            )
        if self.bootloader:
            values = []
            for key in ("product", "product-model", "current-slot", "unlocked"):
                if self.bootloader.get(key):
                    values.append(f"{key}={self.bootloader[key]}")
            if values:
                log("  bootloader: " + ", ".join(values))

    def to_dict(self, keep_serial: bool = True) -> dict:
        from .log import mask_serial

        def scrub(value):
            if keep_serial or not self.serial:
                return value
            masked = mask_serial(self.serial)
            if isinstance(value, str):
                return value.replace(self.serial, masked)
            if isinstance(value, dict):
                return {key: scrub(item) for key, item in value.items()}
            if isinstance(value, list):
                return [scrub(item) for item in value]
            return value

        data = {
            "mode": self.mode,
            "serial": self.serial if keep_serial else mask_serial(self.serial),
            "probed_at": self.probed_at,
            "product": self.product,
            "model": self.model,
            "device": self.device,
            "oem": self.oem,
            "vgc": self.vgc,
            "android": self.android,
            "fingerprint": self.fingerprint,
            "hardware": self.hardware,
            "slot": self.slot,
            "unlocked": self.unlocked,
            "super_size": self.super_size,
            "super_size_source": self.super_size_source,
            "dynamic_partitions": self.dynamic_partitions,
            "partition_sizes": self.partition_sizes,
            "mounted_sizes": self.mounted_sizes,
            "bootloader": self.bootloader,
            "matched": self.matched,
            "used": self.used,
            "note": self.note,
            "properties": self.props,
        }
        return {key: scrub(value) for key, value in data.items()}


def probe_device(serial: str | None, log: Log) -> DeviceInfo | None:
    """Return an ADB snapshot of a connected device, or ``None``.

    Only ADB is accepted.  A phone that is visible exclusively in Fastboot is
    not probed; connect it in Android with USB debugging enabled.  The caller
    checks compatibility and then calls :func:`complete_with_bootloader` when
    ADB could not read the exact ``super`` size.
    """
    adb_devices = _adb_devices()

    if serial:
        log.register_serial(serial)
        if any(name == serial and state == "device" for name, state in adb_devices):
            log(f"  found ADB device {serial}")
            return _probe_adb(serial)
        for name, state in adb_devices:
            if name == serial:
                log(f"  ADB device {serial} is {state}; skipping probe")
                return None
        if any(name == serial for name, _ in _fastboot_devices()):
            log(
                f"  device {serial} is in Fastboot, but the probe only accepts "
                "an ADB connection; boot Android with USB debugging enabled"
            )
        else:
            log(f"  ADB device {serial} not found; continuing without a probe")
        return None

    adb_ready = [
        name
        for name, state in adb_devices
        if state == "device" and not name.startswith("emulator-")
    ]
    if adb_ready:
        log.register_serial(adb_ready[0])
        if len(adb_ready) > 1:
            log(
                f"  multiple ADB devices ({', '.join(adb_ready)}); "
                f"using {adb_ready[0]} (override with --serial)"
            )
        log(f"  found ADB device {adb_ready[0]}")
        return _probe_adb(adb_ready[0])

    unauthorized = [name for name, state in adb_devices if state == "unauthorized"]
    if unauthorized:
        log(
            "  ADB device is unauthorized; accept the USB debugging prompt "
            "on the phone and retry"
        )
    if _fastboot_devices():
        log(
            "  a Fastboot device is connected, but the probe only accepts ADB; "
            "boot Android with USB debugging enabled"
        )
    return None


def needs_bootloader(info: DeviceInfo) -> bool:
    """Return whether the bootloader can supply data that ADB could not."""
    return info.super_size is None


def complete_with_bootloader(info: DeviceInfo, log: Log) -> None:
    """Read the remaining data in the bootloader and return to Android."""
    if not needs_bootloader(info):
        return

    serial = info.serial
    log("  ADB cannot read the exact super size")
    before = {name for name, _ in _fastboot_devices()}
    log("  rebooting to the bootloader to read the remaining data")
    _adb(serial, "reboot", "bootloader", timeout=ADB_TIMEOUT)
    fastboot_serial = _wait_for_fastboot(serial, before, log)
    if fastboot_serial is None:
        log("  bootloader did not appear; continuing with ADB data only")
        return

    log(f"  reading bootloader variables from {fastboot_serial}")
    try:
        fastboot_info = _probe_fastboot(fastboot_serial)
    finally:
        log("  rebooting back to Android")
        _fastboot(fastboot_serial, "reboot", timeout=FASTBOOT_TIMEOUT)

    _merge_bootloader(info, fastboot_info)
    if _wait_for_adb(serial, log):
        log("  device is back in Android")
    else:
        log(f"  warning: device did not return to Android within {ANDROID_WAIT}s")


def _merge_bootloader(info: DeviceInfo, fastboot_info: DeviceInfo) -> None:
    """Merge bootloader values into an ADB snapshot without losing ADB data."""
    info.mode = "adb+bootloader"
    info.bootloader = dict(fastboot_info.props)
    if fastboot_info.super_size:
        info.super_size = fastboot_info.super_size
        info.super_size_source = fastboot_info.super_size_source
    if fastboot_info.partition_sizes:
        info.partition_sizes = fastboot_info.partition_sizes
    if not info.slot:
        info.slot = fastboot_info.slot
    if info.unlocked is None:
        info.unlocked = fastboot_info.unlocked
    if not info.product:
        info.product = fastboot_info.product
    if not info.device:
        info.device = fastboot_info.device
    if not info.model:
        info.model = fastboot_info.model
    if not info.hardware:
        info.hardware = fastboot_info.hardware


def _wait_for_fastboot(
    serial: str,
    before: set[str],
    log: Log,
    timeout: int = BOOTLOADER_WAIT,
) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        names = [name for name, _ in _fastboot_devices()]
        if serial in names:
            return serial
        new_devices = [name for name in names if name not in before]
        if len(new_devices) == 1:
            log.register_serial(new_devices[0])
            log(f"  bootloader reports serial {new_devices[0]}")
            return new_devices[0]
        time.sleep(1)
    return None


def _wait_for_adb(
    serial: str,
    log: Log,
    timeout: int = ANDROID_WAIT,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(
            name == serial and state == "device"
            for name, state in _adb_devices()
        ):
            return True
        time.sleep(1)
    return False


def device_matches_ota(info: DeviceInfo, props: dict[str, str]) -> tuple[bool, str]:
    """Return whether the probed device is covered by the OTA metadata."""
    from .variants import model_candidates

    ota_tokens = model_candidates(props)
    device_tokens = info.model_tokens()
    for device_token in device_tokens:
        for ota_token in ota_tokens:
            if _tokens_match(device_token, ota_token):
                return True, f"{device_token} matches OTA {ota_token}"
    return False, (
        f"device {', '.join(device_tokens) or 'unknown'} not in OTA "
        f"({', '.join(ota_tokens) or 'unknown'})"
    )


def _normalize(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _tokens_match(left: str, right: str) -> bool:
    left = _normalize(left)
    right = _normalize(right)
    if len(left) < 4 or len(right) < 4:
        return False
    return left == right or left.startswith(right) or right.startswith(left)


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _output(result: subprocess.CompletedProcess | None) -> str:
    if result is None:
        return ""
    return (result.stdout or "") + (result.stderr or "")


def _adb(serial: str, *args: str, timeout: int = ADB_TIMEOUT) -> str:
    return _output(_run(["adb", "-s", serial, *args], timeout))


def _fastboot(serial: str, *args: str, timeout: int = FASTBOOT_TIMEOUT) -> str:
    return _output(_run(["fastboot", "-s", serial, *args], timeout))


def _adb_devices() -> list[tuple[str, str]]:
    if shutil.which("adb") is None:
        return []
    result = _run(["adb", "devices"], ADB_TIMEOUT)
    states = {
        "device",
        "unauthorized",
        "offline",
        "recovery",
        "sideload",
        "bootloader",
        "host",
    }
    entries: list[tuple[str, str]] = []
    for line in _output(result).splitlines():
        tokens = line.split()
        if len(tokens) >= 2 and tokens[1] in states:
            entries.append((tokens[0], tokens[1]))
    return entries


def _fastboot_devices() -> list[tuple[str, str]]:
    if shutil.which("fastboot") is None:
        return []
    result = _run(["fastboot", "devices"], ADB_TIMEOUT)
    entries: list[tuple[str, str]] = []
    for line in _output(result).splitlines():
        tokens = line.split()
        if len(tokens) >= 2 and tokens[1] in {"fastboot", "fastbootd"}:
            entries.append((tokens[0], tokens[1]))
    return entries


def _parse_getprop(text: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\[([^\]]+)\]: \[(.*)\]$", line.strip())
        if match:
            props[match.group(1)] = match.group(2)
    return props


def _parse_fastboot_vars(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = re.sub(r"^\(bootloader\)\s*", "", line.strip())
        if not line or line.lower() in {"finished", "okay"}:
            continue
        if ": " in line:
            key, value = line.split(": ", 1)
        else:
            key, separator, value = line.rpartition(":")
            if not separator:
                continue
        key, value = key.strip(), value.strip()
        if key and value:
            values[key] = value
    return values


def _parse_size_value(value: str) -> int | None:
    value = value.strip()
    try:
        return int(value, 0)
    except ValueError:
        try:
            return int(value, 16)
        except ValueError:
            return None


def _probe_adb(serial: str) -> DeviceInfo:
    props = _parse_getprop(_adb(serial, "shell", "getprop"))
    info = DeviceInfo(
        mode="adb",
        serial=serial,
        product=props.get("ro.product.device") or props.get("ro.product.name", ""),
        device=props.get("ro.product.device", ""),
        model=props.get("ro.product.model", ""),
        oem=props.get("ro.vivo.oem.name", ""),
        vgc=props.get("ro.vgc.device.name") or props.get("ro.vgc.cust.name", ""),
        android=props.get("ro.build.version.release", ""),
        fingerprint=props.get("ro.build.fingerprint", ""),
        hardware=props.get("ro.boot.vivo.hardware.pcb.detect")
        or props.get("ro.boot.hardware.sku", ""),
        slot=props.get("ro.boot.slot_suffix", "").strip("_")
        or props.get("ro.boot.slot", ""),
        props={key: props[key] for key in PROP_KEYS if key in props},
    )
    info.unlocked = _unlocked_from_props(props)

    mapper = _adb(serial, "shell", "ls", "/dev/block/mapper")
    partitions = []
    for line in mapper.splitlines():
        name = line.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            continue
        if name in {"by-uuid", "userdata"} or name.endswith("-verity"):
            continue
        if name not in partitions:
            partitions.append(name)
    info.dynamic_partitions = sorted(partitions)

    info.mounted_sizes = _parse_df(_adb(serial, "shell", "df", "-k"))

    size, source = _adb_super_size(serial, props)
    info.super_size = size
    info.super_size_source = source
    return info


def _probe_fastboot(serial: str) -> DeviceInfo:
    values = _parse_fastboot_vars(_fastboot(serial, "getvar", "all"))
    if "partition-size:super" not in values:
        values.update(
            _parse_fastboot_vars(
                _fastboot(serial, "getvar", "partition-size:super")
            )
        )

    product = values.get("product") or values.get("product-name", "")
    model = values.get("product-model", "")
    if not model:
        output = _fastboot(serial, "oem", "product-model")
        match = re.search(r"product-model:\s*(\S+)", output, re.IGNORECASE)
        if match:
            model = match.group(1)

    unlocked: bool | None = None
    raw_unlocked = values.get("unlocked", "").lower()
    if raw_unlocked in {"yes", "true", "1"}:
        unlocked = True
    elif raw_unlocked in {"no", "false", "0"}:
        unlocked = False

    info = DeviceInfo(
        mode="fastboot",
        serial=values.get("serialno") or serial,
        product=product,
        device=product,
        model=model,
        slot=values.get("current-slot", ""),
        unlocked=unlocked,
    )
    info.props = {
        key: values[key]
        for key in (
            "product",
            "product-model",
            "current-slot",
            "slot-count",
            "unlocked",
            "serialno",
            "version-bootloader",
            "partition-size:super",
        )
        if key in values
    }
    info.partition_sizes = {
        key.split(":", 1)[1]: size
        for key, value in values.items()
        if key.startswith("partition-size:")
        for size in [_parse_size_value(value)]
        if size
    }
    super_size = info.partition_sizes.get("super")
    if super_size:
        info.super_size = super_size
        info.super_size_source = "fastboot getvar"
    return info


def _unlocked_from_props(props: dict[str, str]) -> bool | None:
    if props.get("ro.boot.vbmeta.device_state") == "unlocked":
        return True
    locked = props.get("ro.boot.flash.locked")
    if locked == "0":
        return True
    if locked == "1":
        return False
    return None


def _parse_df(text: str) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) < 6 or not tokens[0].startswith("/dev/block/"):
            continue
        mount = tokens[-1]
        name = MOUNT_MAP.get(mount)
        if not name:
            continue
        try:
            sizes[name] = int(tokens[1]) * 1024
        except ValueError:
            continue
    return sizes


def _adb_super_size(
    serial: str, props: dict[str, str]
) -> tuple[int | None, str]:
    raw = props.get("ro.boot.super_partition_size", "")
    if raw:
        size = _parse_size_value(raw)
        if size:
            return size, "ro.boot.super_partition_size"

    output = _adb(
        serial,
        "shell",
        "d=$(readlink /dev/block/by-name/super 2>/dev/null); "
        'echo "DEV=$d"; '
        'echo "BYTES=$(blockdev --getsize64 "$d" 2>/dev/null)"; '
        'echo "SECTORS=$(cat /sys/class/block/${d##*/}/size 2>/dev/null)"',
    )
    for line in output.splitlines():
        key, _, value = line.partition("=")
        if key == "BYTES":
            size = _parse_size_value(value)
            if size:
                return size, "adb blockdev"
        if key == "SECTORS":
            size = _parse_size_value(value)
            if size:
                return size * 512, "adb sysfs"
    return None, ""
