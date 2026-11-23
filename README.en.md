# iQOO / vivo OTA to Fastboot Package Builder

[中文](README.md) | **English**

`ota2fastboot.py` converts an **extracted** vivo / iQOO full OTA (block mode) into a self-contained fastboot package. It rebuilds every dynamic partition, creates a sparse `super.img` with `lpmake`, extracts all OEM variants, copies the physical images required by the flash scripts, and verifies the result with `e2fsck`, `lpdump`, `avbtool` and SHA-256 manifests.

---

## Features

- Rebuilds every dynamic partition: `system`, `vendor`, `product`, `system_ext`, `odm`, `vendor_dlkm`
- Supports split `*.new.dat`, `*.new.dat.1..N` and single-file Brotli `*.new.dat.br` streams
- Rebuilds block images outside `super`, such as `vm-bootsys` (when the OTA provides them)
- Builds a sparse `super.img` with `lpmake` sized to the device `super` partition
- Optionally probes an ADB-connected device at build time and uses its real `super` size, OEM/VGC identity and model check to make the package fit the hardware; when ADB cannot read the `super` size, the bootloader is queried automatically
- Extracts every OEM variant under `oem/` for identity repair after flashing
- Extracts the VGC variant and places it in `super`
- Copies the bootloader, modem, xbl, tz and other physical images required by `flash.sh`
- Generates `package.env`, the flash scripts, a build report and a SHA-256 manifest
- Verifies with `e2fsck`, `lpdump`, the AVB chain, script reference completeness and sparse image expansion size
- Generated files never contain the absolute path of the build machine

## Requirements

| Tool | Purpose |
| --- | --- |
| `python3` 3.8+ | Run the converter |
| `brotli` | Decompress `*.new.dat.br` |
| `lpmake` / `lpdump` | Build and inspect `super.img` |
| `e2fsck` | Check ext4 partition images |
| `avbtool` | Verify AVB signatures and chained partitions |
| `adb` / `fastboot` | Probe over ADB at build time, with an automatic bootloader pass when needed; flash the package |

Debian / Ubuntu:

```bash
sudo apt install python3 brotli android-sdk-libsparse-utils e2fsprogs
```

`avbtool` comes from AOSP `external/avb`; some distributions require you to add it to `PATH` yourself.

## Preparing the OTA

The input must be an **extracted directory** containing at least:

```text
META-INF/com/android/metadata
META-INF/com/google/android/updater-script
dynamic_partitions_op_list
*.transfer.list
*.new.dat  or  *.new.dat.N  or  *.new.dat.br
oem/        # optional, OEM variants
vgc/        # optional, VGC variants
firmware-update/
```

If you have a zip:

```bash
unzip -q <full-ota>.zip -d <full-ota>
```

## Quick start

```bash
git clone <repo>
cd <repo>

./ota2fastboot.py /path/to/<full-ota>
```

The package is written to `out/<OTA name>-fastboot/` by default, where `<OTA name>` comes from the OTA metadata field `post-version`, not the source directory name. For example, `.firmwares/PD2171_A_12.0.20.11.W10.V000L1-update-full` maps to `out/PD2171_A_12.0.20.11.W10.V000L1-fastboot/`.

If the target phone is connected over ADB (USB debugging enabled), the converter first reads its properties, partitions and mounted sizes, and prefers the device-reported OEM/VGC identity and model information. When ADB cannot read the exact `super` size, the phone is automatically rebooted to the bootloader, the remaining data is read with `fastboot getvar`, and the phone is rebooted back to Android. The probe never writes, flashes or unlocks anything. To skip the probe:

```bash
./ota2fastboot.py /path/to/<full-ota> --no-device
```

Use `--serial` to choose the ADB device when more than one is connected. To validate and print the plan without writing images:

```bash
./ota2fastboot.py /path/to/<full-ota> --dry-run
```

After the build:

```bash
cd out/<OTA name>-fastboot
chmod +x ./*.sh

./flash-safe.sh   # Android partitions only; bootloader and fastboot rollback path preserved
./flash.sh        # full flash, bootloader and UFS firmware included
```

## Output layout

```text
out/<OTA name>-fastboot/
├── BUILD.md                  # build report: partition table, variants, verification
├── build.json                # machine-readable build arguments and results
├── build.log                 # full log with host paths redacted
├── device.json               # ADB and bootloader probe result (when an ADB device was connected)
├── package.env               # package title, model pattern, default OEM identity
├── flash.sh                  # full flash
├── flash-safe.sh             # safe flash: no bootloader / storage / modem
├── fix-oem.sh                # OEM identity repair
├── common.sh                 # shared helpers for the three scripts above
├── manifest.sha256           # SHA-256 manifest of the package
├── manifest.json
└── firmware-update/
    ├── boot.img vendor_boot.img dtbo.img recovery.img vm-bootsys.img
    ├── vbmeta.img vbmeta_system.img vbmeta_oem.img vbmeta_vgc.img
    ├── super.img             # sparse image built by lpmake
    ├── NON-HLOS.bin dspso.bin BTFM.bin abl.elf tz.mbn xbl_s.melf ...
    └── oem/
        ├── OEM_<variant>.img
        └── vbmeta_oem_<variant>.img
```

## The three flash scripts

| Script | Writes | Risk |
| --- | --- | --- |
| `flash-safe.sh` | `boot`, `dtbo`, `vendor_boot`, `recovery`, `vm-bootsys`, `vbmeta*`, `super`, OEM repair | Low. Fastboot remains reachable, so an older package can be flashed back |
| `flash.sh` | Everything, including `xbl`, `abl`, `tz`, `hyp`, `storage`, ... | High. An interrupted bootloader write may be unrecoverable without EDL |
| `fix-oem.sh` | `vbmeta_oem` + `oem` | Low. Repairs the identity only, does not touch the system |

`flash-safe.sh` skips the modem group by default. To include it:

```bash
FLASH_SAFE_WITH_MODEM=1 ./flash-safe.sh
```

`fix-oem.sh` supports:

```bash
./fix-oem.sh --list
./fix-oem.sh --name PD2171_CN-ZH_FULL_SC_NULL
./fix-oem.sh --name BMW --yes
./fix-oem.sh --filter CT-BMW
```

## Common options

| Option | Description |
| --- | --- |
| `--output DIR` | Output directory |
| `--oem NAME\|auto\|none` | OEM variant placed in `super`. Default `auto`: prefers the connected device's OEM identity, otherwise the OTA device's `FULL_SC_NULL` |
| `--vgc NAME\|auto\|none` | VGC variant placed in `super`. Default `auto`: prefers the connected device's VGC identity, otherwise `V000` |
| `--device-size SIZE` | Device `super` partition size. Defaults to the size read over ADB or in the bootloader, otherwise `15032385536` (14 GiB, iQOO 9 / 9 Pro) |
| `--serial SERIAL` | ADB device serial to probe; default is the first ADB device |
| `--no-device` | Do not probe a connected device |
| `--require-device` | Fail when no compatible ADB device is connected |
| `--skip-device-check` | Use a connected device even when it does not match the OTA |
| `--scripts-dir DIR` | Flash script template directory, default `scripts/` in the repository |
| `--dry-run` | Parse, validate and print the plan only |
| `--resume` | Keep an existing output directory and continue |
| `--reuse-raw` | With `--resume`: skip extraction when a partition image already has the expected size |
| `--reuse-super` | With `--resume`: skip `lpmake` when `super.img` matches |
| `--keep-raw` | Keep intermediate images under `out/<name>/.raw/` |
| `--skip-fsck` / `--skip-avb` | Skip the corresponding check; not recommended |
| `--no-super` / `--no-scripts` | Generate only part of the package, for debugging |
| `--force` | Delete a non-empty output directory and rebuild |

Examples:

```bash
# Select an OEM variant explicitly
./ota2fastboot.py /path/to/<full-ota> \
    --oem PD2172C_CN-ZH_FULL_SC_CT-BMW

# Continue after an interruption
./ota2fastboot.py /path/to/<full-ota> \
    --resume --reuse-raw --reuse-super

# Other devices: set the super size (bytes, or a value such as 14GiB)
./ota2fastboot.py /path/to/<full-ota> --device-size 8GiB

# Probe one specific ADB device and require it to exist and match the OTA
./ota2fastboot.py /path/to/<full-ota> --serial <serial> --require-device
```

## Probing an ADB device

The converter accepts **ADB connections only**. A phone that is visible only in Fastboot is not probed; boot Android and enable USB debugging first. The probe never writes, flashes or unlocks anything.

| Stage | Read | Used for |
| --- | --- | --- |
| ADB | `getprop` (model, OEM/VGC identity, Android version, fingerprint, unlock state), `/dev/block/mapper`, `df -k` | OEM/VGC selection, model check, `MODEL_REGEX`, build report |
| Automatic bootloader pass | `fastboot getvar all` (`partition-size:super`, `current-slot`, `unlocked`, ...), `fastboot oem product-model` | The `super` size and current slot that ADB cannot read; the phone is rebooted back to Android afterwards |

- The phone is rebooted to the bootloader only when ADB cannot provide the exact `super` size and `--device-size` was not given; otherwise no reboot happens.
- If the bootloader does not appear within the timeout, or reading fails, the build continues with the ADB data and defaults.
- A device-reported `super` size, or the value read in the bootloader, takes precedence over the built-in default; an explicit `--device-size` still wins.
- When the device-reported OEM identity exists in the OTA, `--oem auto` selects it directly; VGC works the same way.
- When the device model does not match the OTA `pre-device` / `hardware-device`, device data is ignored by default and the package follows the OTA metadata. `--skip-device-check` forces the device data, while `--require-device` requires a compatible ADB device.
- The device model / product values are appended to `MODEL_REGEX` in `package.env`, so the flash-time check accepts the real device.
- The probe result is written to `device.json` (including a `bootloader` field) and included in `BUILD.md` and `build.json`.
- Rebooting to the bootloader and back takes some time; keep the USB cable connected.

## Verification

1. `e2fsck -fn` on every ext4 image, including `oem` and `vgc`
2. `lpdump` to confirm that every dynamic partition exists in `super.img` and is not smaller than the op list declares
3. `avbtool verify_image --follow_chain_partitions` for:
   - `vbmeta` (covering `boot`, `dtbo`, `vendor_boot`, `recovery`, `vm-bootsys`)
   - `vbmeta_system` (covering all dynamic partitions)
   - `vbmeta_oem`
   - `vbmeta_vgc`
4. Script reference check: every required image referenced by `flash.sh` / `flash-safe.sh` exists
5. The sparse image expands to exactly the requested `super` size (`--device-size`, the device probe value, or the default)
6. The SHA-256 manifest can be verified:

```bash
sha256sum -c manifest.sha256
```

## Model check

The converter derives the model list from the OTA metadata fields `pre-device` and `hardware-device` and writes it to `MODEL_REGEX` in `package.env`. When a compatible device is probed at build time, its model / product values are appended as well. The flash scripts read `fastboot oem product-model` from the phone and compare it.

To skip the check when you are certain:

```bash
SKIP_MODEL_CHECK=1 ./flash-safe.sh
```

## Safety

- The converter **never flashes anything**; it only builds and verifies the package.
- Device probing never writes, flashes or unlocks. When needed it reboots the phone to the bootloader and then back to Android.
- `flash.sh` writes the bootloader chain and UFS firmware. When EDL recovery is unavailable, prefer `flash-safe.sh`.
- `flash-safe.sh` does not write `storage`, `xbl`, `xbl_config`, `abl`, `tz`, `hyp`, `aop`, `devcfg`, `keymaster`, `uefisecapp`, `uefi`, `shrm`, `cpucp`, `imagefv`, `multiimg*`, `storsec`, `featenabler`, `toolsfv` or `xbl_ramdump`.
- Flashing across versions may raise the anti-rollback counter; the device may refuse a bootloader downgrade.
- Flashing wipes data; back up first.
- The bootloader must be unlocked.

## Troubleshooting

**`brotli not found`**: install `brotli`, or make sure it is in `PATH`.

**`lpmake not found`**: install `android-sdk-libsparse-utils`.

**Not enough disk space**: peak usage is roughly dynamic partition total × 2 + firmware size × 2. A large package needs about 25 GiB free.

**Stuck at the boot logo after flashing**: flash the older package back with `./flash-safe.sh`, or check whether the bootloader must be updated together.

**"System corrupt" or wrong OEM identity**: run `./fix-oem.sh` and select the variant that matches the phone.

**Wrong `super.img` size**: connect the target device over ADB so the converter can read the real size; a Fastboot-only connection is not probed. Alternatively pass `--device-size`. The converter does not read a package already installed on the host to guess the size.

## Repository layout

| Path | Description |
| --- | --- |
| `ota2fastboot.py` | Executable entry point; keeps `./ota2fastboot.py` working |
| `ota2fastboot/` | Converter source package |
| `ota2fastboot/cli.py` | Command line arguments |
| `ota2fastboot/builder.py` | Build pipeline |
| `ota2fastboot/device.py` | ADB probe, automatic bootloader pass, model matching |
| `ota2fastboot/ota.py` | OTA metadata, transfer lists, op lists |
| `ota2fastboot/stream.py` | `.new.dat` extraction |
| `ota2fastboot/sparse.py` | Sparse image reading and writing |
| `ota2fastboot/super_image.py` | `super.img` build and inspection |
| `ota2fastboot/verify.py` | ext4 / AVB verification |
| `ota2fastboot/variants.py` | OEM / VGC variant selection |
| `ota2fastboot/firmware.py` | Physical image mapping |
| `ota2fastboot/package.py` | Flash scripts and `package.env` |
| `ota2fastboot/report.py` | Manifest and build report |
| `scripts/common.sh` | Shared flash script helpers |
| `scripts/flash.sh` | Full flash template |
| `scripts/flash-safe.sh` | Safe flash template |
| `scripts/fix-oem.sh` | OEM repair template |
| `out/` | Generated fastboot packages (git ignored) |

The converter can also be run as a module:

```bash
python3 -m ota2fastboot /path/to/<full-ota>
```

`pyproject.toml` provides an optional `pip install .` install. `package.env` in the generated package is written by the converter and loaded automatically by the flash scripts.
