# iQOO / vivo OTA to Fastboot Package Builder

[中文](README.md) | **English**

`ota2fastboot.py` converts an **extracted vivo / iQOO full OTA** (block OTA) into a self-contained fastboot package. The package ships platform-native flash scripts: bash for Linux / macOS and PowerShell with `.cmd` launchers for Windows. The default entry point is the safe `./flash.sh` (Windows: `.\flash.cmd`); use `./flash-full.sh` (Windows: `.\flash-full.cmd`) for a full flash.

> **Disclaimer**
>
> This project and the packages it generates have only been tested by flashing a real iQOO 9 Pro. A successful test does not mean other models can be flashed safely, nor does it guarantee that the same model (other units, variants or firmware versions) can be flashed safely. Flashing carries risks such as bricking and data loss; whether to flash is your own decision, and you accept the risk and consequences.

---

## Scope

### Supported OTA packages

Only **official full OTAs** are supported, and they must be an extracted directory — not a zip, not an incremental package, not a `payload.bin`.

| Item | Requirement |
| --- | --- |
| Package type | Official full OTA, not incremental / differential |
| OTA type | `ota-type=BLOCK` in `META-INF/com/android/metadata`; `package-type` is normally `dota` |
| Partition layout | **A-only / single slot**; no `_a` / `_b` suffixes |
| Dynamic partition data | `*.transfer.list` (version 4) plus a complete `*.new.dat`, split `*.new.dat.1..N`, or Brotli `*.new.dat.br` |
| Physical images | `.img`, `.mbn`, `.elf`, `.bin` under `firmware-update/`, copied as-is |
| Variant directories | Optional `oem/` and `vgc/`, each with `dynamic_partitions_op_list_<variant>` |
| Required metadata | `post-version` (output directory name), `pre-device`, `post-build` |

The input directory must contain at least:

```text
META-INF/com/android/metadata
META-INF/com/google/android/updater-script
dynamic_partitions_op_list
*.transfer.list
*.new.dat  or  *.new.dat.N  or  *.new.dat.br
firmware-update/
oem/        # optional, OEM variants
vgc/        # optional, VGC variants
```

If you have a zip:

```bash
unzip -q <full-ota>.zip -d <full-ota>
```

**Quick compatibility check:**

1. The extracted directory has `dynamic_partitions_op_list`, `*.transfer.list` and `*.new.dat*`;
2. `metadata` says `ota-type=BLOCK`;
3. `firmware-update/` and `updater-script` are present;
4. Dynamic partition names have no `_a` / `_b` suffix;
5. Firmware uses Qualcomm names such as `xbl`, `abl`, `tz`, `hyp`, `devcfg`.

All five must hold before the package is a good fit for this tool.

### Image packaging and output format

- Dynamic partitions come from an Android block OTA as transfer-list + `new.dat` streams; the tool reconstructs raw partition images (ext4 or EROFS, as shipped by the vendor).
- Every dynamic partition plus the selected `oem/` and `vgc/` variants is merged by `lpmake` into one **sparse `super.img`**, sized to the target device's `super` partition.
- `boot`, `vendor_boot`, `dtbo`, `recovery`, `vm-bootsys` and similar images stay raw; by default only the fstab inside the existing LZ4 ramdisk frame is rewritten in place, without rebuilding the cpio.
- `vbmeta*` and bootloader / modem / UFS firmware are copied as-is; nothing is re-signed or repacked.
- The output also contains flash scripts, `package.env`, a SHA-256 manifest, a build report and the flash-item lists.

### Supported models and CPUs

| Item | Status |
| --- | --- |
| Tested model | iQOO 9 Pro (PD2171 family), with one Android 12 and one Android 15 full OTA; the same-generation iQOO 9 shares the layout but was not tested separately |
| CPU platform | Qualcomm Snapdragon 8 Gen 1 (SM8450) |
| Design scope | vivo / iQOO Qualcomm devices, A-only, block OTA, vivo dynamic partition layout |
| Other Snapdragon models | Untested. May work when the OTA layout, partition names and firmware mapping match, but booting is not guaranteed |
| Other Android versions | Untested. The code adapts to boot v3/v4, vendor_boot v3/v4, LZ4 and ext4 / EROFS, but only A12 / A15 were verified |
| Not supported | MediaTek (MTK), Samsung Exynos, Google Tensor; A/B or Virtual A/B devices; generic `payload.bin` OTAs |

### Missing capabilities

- **It does not flash, unlock or recover a brick.** The tool only builds and verifies a fastboot package; unlocking the bootloader, entering Fastboot and EDL recovery are up to you.
- **No incremental / differential OTAs.** A full package is required.
- **No `payload.bin` or whole-package `.bin` support.** Only extracted transfer-list + `new.dat` directories are accepted.
- **No A/B or Virtual A/B support.** There is no slot suffix, dual-slot `super` or `boot_a` / `boot_b` handling.
- **No MediaTek support.** The firmware map, `devcfg` handling and partition names are Qualcomm-specific.
- **It does not determine the `super` size automatically.** The default is 14 GiB (iQOO 9 / 9 Pro); other devices need an explicit `--device-size` or `--probe-device`.
- **Booting is not guaranteed across models or versions.** OEM / VGC identity, bootloader and anti-rollback state may differ between batches.
- **Anti-rollback is not bypassed.** A bootloader downgrade may be refused.
- **Data is not wiped automatically.** The flash scripts ask before rebooting and keep data by default; wiping removes all apps and files.
- **The build host is verified on Linux only.** macOS is untested; Windows is a flash-side target with no build workflow.
- **You must supply an AVB private key.** The default build re-signs the whole chain with the key given via `--avb-key`; without a key only `--keep-avb` is possible. Anyone who obtains the key can sign system images for your device.

## Features

- Rebuilds every dynamic partition: `system`, `vendor`, `product`, `system_ext`, `odm`, `vendor_dlkm`
- Supports a complete `*.new.dat`, split `*.new.dat.1..N` and Brotli `*.new.dat.br` streams
- Rebuilds block images outside `super`, such as `vm-bootsys` (when the OTA provides them)
- Builds a sparse `super.img` with `lpmake` sized to the device `super` partition
- Does not touch a connected phone by default; `--probe-device` reads the real `super` size, OEM/VGC identity, devcfg type and model; when ADB cannot read the `super` size, the bootloader is queried automatically
- Adapts to both Android 12 and Android 15 firmware forms: boot v3/v4, vendor_boot v3/v4, LZ4 ramdisks, ext4 or EROFS vendor, different physical partition sets and OEM/VGC variant counts
- Adds physical partitions that only exist in newer OTAs (such as A15 `qweslicstore` and `rtice`) from `updater-script`, and generates the flash-item lists the scripts consume
- Packages and flashes only the `devcfg` image matching `ro.vivo.boot.devcfg_type` (`--devcfg-type` overrides)
- Extracts the OEM repair image for the variant placed in `super`; `--all-oem` packages every variant
- Extracts the VGC variant and places it in `super`
- Copies the bootloader, modem, xbl, tz and other physical images required by the full flash scripts
- Generates a package `README.md`, `package.env`, the platform flash scripts, the verify script, a build report and a SHA-256 manifest
- The target platform selects the scripts: `linux` / `macos` get bash, `windows` gets native PowerShell plus `.cmd`
- Optional Windows target: `--target-platform windows` with `--platform-tools-dir` bundles the Windows `adb.exe` / `fastboot.exe` under `platform-tools/`
- Masks the device serial in the build log and reports by default; `--keep-serial` opts out
- Keeps dm-verity enabled by default and re-signs `boot`, `dtbo`, `vendor_boot`, `vbmeta_system`, `vbmeta_vgc` and the top-level `vbmeta` with the private key from `--avb-key`, removing only the force-encryption flags; `--keep-avb` keeps the stock chain
- Verifies with `e2fsck`, `lpdump`, the AVB chain, script reference completeness and sparse image expansion size
- Generated files never contain the absolute path of the build machine

## Requirements

### Build host

| Tool | Purpose |
| --- | --- |
| Linux x86_64 | Build host; only Linux is verified, macOS / Windows builds are untested |
| `python3` 3.8+ | Run the converter |
| `brotli` | Decompress `*.new.dat.br` |
| `lpmake` / `lpdump` | Build and inspect `super.img` |
| `e2fsck` | Check ext4 partition images |
| `avbtool` | Verify and re-sign the AVB chain, recompute hashtrees; `--skip-avb` skips verification |
| `liblz4` | Recompress the vendor_boot ramdisk and EROFS fstab when patched; `--keep-avb` skips it |
| `adb` / `fastboot` | Only used for `--probe-device` / `--require-device`; also used to flash the package |
| ~25 GiB free disk | Peak usage is roughly dynamic partition total × 2 + firmware size × 2 |

Debian / Ubuntu:

```bash
sudo apt install python3 brotli android-sdk-libsparse-utils e2fsprogs liblz4-1
```

`avbtool` comes from AOSP `external/avb`; some distributions require you to add it to `PATH` yourself.

### Flash host

| Platform | Requirement |
| --- | --- |
| Linux / macOS | `bash`, Android platform-tools (`adb` / `fastboot`), USB connection |
| Windows | PowerShell 5.1+ (included with Windows 10/11), Android platform-tools, USB connection |

Packages follow the build host by default; add `--target-platform windows` when preparing a package for Windows users.

## Quick start

```bash
git clone <repo>
cd <repo>

./ota2fastboot.py /path/to/<full-ota>
```

The package is written to `out/<OTA name>-fastboot/` by default, where `<OTA name>` comes from the OTA metadata field `post-version`, not the source directory name. For example, `.firmwares/PD2171_A_12.0.20.11.W10.V000L1-update-full` maps to `out/PD2171_A_12.0.20.11.W10.V000L1-fastboot/`.

By default the converter does **not** read a connected phone, so the same OTA and flags always produce the same `super.img`. Pass `--probe-device` to read the `super` size, OEM/VGC identity or devcfg type from the current phone:

```bash
./ota2fastboot.py /path/to/<full-ota> --probe-device
```

The probe accepts **ADB connections only** (USB debugging enabled). When ADB cannot read the exact `super` size, the phone is automatically rebooted to the bootloader, the remaining data is read with `fastboot getvar`, and the phone is rebooted back to Android. The probe never writes, flashes or unlocks anything. Use `--serial` when more than one device is connected. Release packages should pin `--device-size` instead of relying on a probe.

To validate and print the plan without writing images:

```bash
./ota2fastboot.py /path/to/<full-ota> --dry-run
```

After the build, flash with the scripts that match the package target.

Linux / macOS package (the default, follows the build host):

```bash
cd out/<OTA name>-fastboot
chmod +x ./*.sh

./verify.sh       # verify manifest.sha256
./flash.sh        # default: Android partitions only; bootloader and fastboot rollback path preserved
./flash-full.sh   # full flash, bootloader and UFS firmware included
```

Windows package (`--target-platform windows`):

```powershell
cd out\<OTA name>-fastboot

.\verify.cmd      # verify manifest.sha256
.\flash.cmd       # default: Android partitions only
.\flash-full.cmd  # full flash, bootloader and UFS firmware included
```

The `.cmd` launchers invoke the matching `.ps1` with `-ExecutionPolicy Bypass`, so no system policy change is needed.

After every partition is written and before the automatic reboot, the scripts ask whether to erase data (default: keep). Only `userdata`, `metadata`, `cache` and `frp` are cleared; the system partitions just written are left intact.

## Output layout

Linux / macOS package:

```text
out/<OTA name>-fastboot/
├── README.md                 # package usage notes (Chinese)
├── package.env               # package title, model pattern, default OEM identity
├── flash.sh                  # default safe flash: no bootloader / storage / modem
├── flash-full.sh             # full flash
├── flash-safe.sh             # compatibility alias for flash.sh
├── fix-oem.sh                # OEM identity repair
├── verify.sh                 # verify manifest.sha256
├── common.sh                 # shared helpers for the scripts above
├── manifest.sha256           # package integrity manifest
├── manifest.json
├── meta/
│   ├── BUILD.md              # build report: partition table, variants, verification
│   ├── build.json            # machine-readable build result (including the device probe)
│   └── build.log             # full log; host paths and serial masked by default
└── firmware-update/
    ├── boot.img vendor_boot.img dtbo.img recovery.img vm-bootsys.img
    ├── vbmeta.img vbmeta_system.img vbmeta_vgc.img
    ├── super.img             # sparse image built by lpmake
    ├── NON-HLOS.bin dspso.bin BTFM.bin abl.elf tz.mbn xbl_s.melf ...
    └── oem/
        ├── OEM_<selected variant>.img
        └── vbmeta_oem_<selected variant>.img
```

Windows package:

```text
out/<OTA name>-fastboot/
├── README.md
├── package.env
├── flash.ps1 / flash.cmd                  # default safe flash
├── flash-full.ps1 / flash-full.cmd        # full flash
├── flash-safe.ps1 / flash-safe.cmd        # compatibility alias
├── fix-oem.ps1 / fix-oem.cmd              # OEM identity repair
├── verify.ps1 / verify.cmd                # verify manifest.sha256
├── common.ps1                             # shared helpers
├── manifest.sha256
├── manifest.json
├── meta/
│   ├── BUILD.md
│   ├── build.json
│   └── build.log
├── platform-tools/           # optional: present with --platform-tools-dir
└── firmware-update/
    ├── ... same images as the Linux package ...
    └── oem/
        ├── OEM_<selected variant>.img
        └── vbmeta_oem_<selected variant>.img
```

`manifest.sha256` covers every file except `meta/build.log` and the manifest files themselves. `device-info.txt` is written into the package when flashing and records the pre-flash model, OEM, Android version and package version.

## Flash scripts

| Action | Linux / macOS | Windows |
| --- | --- | --- |
| Default safe flash | `flash.sh` | `flash.ps1` or `flash.cmd` |
| Full flash | `flash-full.sh` | `flash-full.ps1` or `flash-full.cmd` |
| OEM repair | `fix-oem.sh` | `fix-oem.ps1` or `fix-oem.cmd` |
| Verify the package | `verify.sh` | `verify.ps1` or `verify.cmd` |
| Old-name alias | `flash-safe.sh` | `flash-safe.ps1` / `flash-safe.cmd` |

The safe flash writes `boot`, `dtbo`, `vendor_boot`, `recovery`, `vm-bootsys`, `vbmeta*`, `super` and repairs the OEM identity. Fastboot remains reachable, so an older package can be flashed back if it fails. The full flash also writes `xbl`, `abl`, `tz`, `hyp`, `storage` and the rest of the bootloader chain; an interrupted bootloader write may be unrecoverable without EDL.

`flash-safe.sh` / `flash-safe.cmd` is the old name and remains as a compatibility alias.

The modem group is skipped by default. To include it:

```bash
FLASH_SAFE_WITH_MODEM=1 ./flash.sh
```

```powershell
$env:FLASH_SAFE_WITH_MODEM = "1"
.\flash.cmd
```

OEM repair:

```bash
./fix-oem.sh --list
./fix-oem.sh --name PD2171_CN-ZH_FULL_SC_NULL
./fix-oem.sh --name BMW --yes
./fix-oem.sh --filter CT-BMW
```

```powershell
.\fix-oem.cmd --list
.\fix-oem.cmd --name PD2171_CN-ZH_FULL_SC_NULL
.\fix-oem.cmd --name BMW --yes
.\fix-oem.cmd --filter CT-BMW
```

By default only the OEM variant used in `super` is packaged. Add `--all-oem` at build time to keep every variant.

## AVB, dm-verity and force-encryption

The default build **keeps dm-verity** and re-signs the entire verification chain with the private key given via `--avb-key`:

- `boot.img` and `dtbo.img` keep their content and only get a new vbmeta footer via `avbtool resign_image`, preserving the 4096-byte boot header v4 signature.
- `vendor_boot.img`: the `fstab.qcom` inside the ramdisk is edited to drop the force-encryption flags (`fileencryption=...`, `keydirectory=...`, `metadata_encryption=...`), then `erase_footer` + `add_hash_footer` recomputes the hash footer.
- `vendor.img`: `/etc/fstab.qcom` (ext4 or EROFS, detected automatically) loses the same three flag families and the dm-verity hashtree is recomputed; `vbmeta_system` gets fresh descriptors for every logical partition.
- `vbmeta_system` is rebuilt from the latest partition descriptors and signed with the custom key; `vbmeta_vgc` is re-signed in place.
- the top-level `vbmeta` is rebuilt from the original chain descriptors: re-signed partitions get our public key, the rest keep theirs, and `flags` stays `0`.

AVB is not stripped because the iQOO 9 Pro bootloader rejects the disable flags: both `flags=1` and `flags=3` fall back to fastboot within seconds, and removing `avb=` from the fstab breaks first-stage mounting. Keeping `avb=`, `verify`, `vverify` and `flags=0` while signing with our own key is the only configuration that boots. The flash scripts write `vbmeta*` as ordinary images without `--disable-verity` / `--disable-verification`, so the platform-tools `Failed to find AVB_MAGIC at offset: 0` defect cannot trigger either.

The patch keeps every file size unchanged by padding removed flags with spaces, so no cpio archive is rebuilt and no ext4 inode or EROFS metadata changes. The AVB chain is verified on the original OTA images before patching, and the re-signed chain is verified again at the end of the build (warning only).

`add_hashtree_footer` runs with `--do_not_generate_fec` here: generating FEC requires the external `fec` tool, and dm-verity does not depend on FEC. Install `fec` and drop the flag if you want the stock FEC layout.

Pass `--keep-avb` at build time to keep the stock verification and encryption settings. The generated `package.env` records the final state in `AVB_STRIPPED` for reference; the image content is what matters.

### Generating the AVB key

AVB uses a 2048-bit RSA private key (`SHA256_RSA2048`). Generate it once and reuse it for every build; images signed with different keys cannot be mixed:

```bash
mkdir -p ~/.config/iqoo-vivo-fastboot
chmod 700 ~/.config/iqoo-vivo-fastboot
openssl genrsa -out ~/.config/iqoo-vivo-fastboot/avb.pem 2048
chmod 600 ~/.config/iqoo-vivo-fastboot/avb.pem
openssl rsa -in ~/.config/iqoo-vivo-fastboot/avb.pem -check -noout
avbtool extract_public_key \
    --key ~/.config/iqoo-vivo-fastboot/avb.pem \
    --output ~/.config/iqoo-vivo-fastboot/avb.avbpubkey
sha1sum ~/.config/iqoo-vivo-fastboot/avb.avbpubkey
```

Pass `--avb-key ~/.config/iqoo-vivo-fastboot/avb.pem` to the build. Never commit or share the private key: losing it means you cannot sign further updates for an already flashed custom system, and leaking it lets anyone sign system images for your device. After changing keys, reflash `boot`, `dtbo`, `vendor_boot`, `vbmeta_system`, `vbmeta_vgc`, `vbmeta` and `super` together.

Removing force-encryption can make an already encrypted `userdata` partition unbootable until it is cleared. After all writes and before the automatic reboot, the flash scripts ask whether to erase data (default: keep). When confirmed they use `fastboot -w` to erase and format `userdata`, `cache` and `metadata`, then erase `frp`; older platform-tools fall back to traditional `fastboot erase userdata` calls. Only data partitions are touched, so the freshly written system, boot, vendor, super, modem and OEM partitions stay intact.

## Common options

| Option | Description |
| --- | --- |
| `--output DIR` | Output directory |
| `--oem NAME\|auto\|none` | OEM variant placed in `super`. Default `auto`: uses the `--probe-device` identity, otherwise a matching `FULL_SC` variant from the OTA |
| `--vgc NAME\|auto\|none` | VGC variant placed in `super`. Default `auto`: uses the `--probe-device` identity, otherwise `V000` |
| `--device-size SIZE` | Device `super` partition size. Default `15032385536` (14 GiB, iQOO 9 / 9 Pro); `--probe-device` uses the phone value |
| `--devcfg-type auto\|0\|1` | `0` packages `devcfg.mbn`, `1` packages `devcfg_overlay1.mbn`; `auto` reads `ro.vivo.boot.devcfg_type` when probing, otherwise uses `0` |
| `--serial SERIAL` | ADB device serial for `--probe-device`; default is the first ADB device |
| `--keep-serial` | Keep the full device serial in the reports and log; default keeps only the last four characters |
| `--all-oem` | Package every OEM repair variant from the OTA; default keeps only the variant placed in `super` |
| `--target-platform PLATFORM` | Package target: `linux`, `windows` or `macos`; default follows the build host. `linux` / `macos` get bash scripts, `windows` gets PowerShell plus `.cmd` |
| `--platform-tools-dir DIR` | Windows target only: copy `adb.exe` / `fastboot.exe` from this directory into `platform-tools/`; omitted means skip |
| `--probe-device` | Read a connected phone (ADB, then the bootloader when needed) and use its `super` size, OEM/VGC identity, devcfg type and model; off by default |
| `--require-device` | Same as `--probe-device`, and fail when no compatible device is connected |
| `--no-device` | Legacy no-op; no probe happens by default |
| `--skip-device-check` | With `--probe-device`, use the phone even when it does not match the OTA |
| `--scripts-dir DIR` | Script template root containing `bash/` and `windows/`; default `scripts/` in the repository |
| `--dry-run` | Parse, validate and print the plan only |
| `--resume` | Keep an existing output directory and continue |
| `--reuse-raw` | With `--resume`: skip extraction when a partition image already has the expected size |
| `--reuse-super` | With `--resume`: skip `lpmake` when `super.img` matches |
| `--keep-raw` | Keep intermediate images under `out/<name>/.raw/` |
| `--avb-key FILE` | PEM private key used to re-sign the AVB chain and recompute hashtrees for modified partitions; required unless `--keep-avb` is given |
| `--keep-avb` | Keep the OTA AVB / dm-verity / force-encryption settings |
| `--skip-fsck` / `--skip-avb` | Skip the corresponding check; not recommended |
| `--no-super` / `--no-scripts` | Generate only part of the package, for debugging |
| `--force` | Delete a non-empty output directory and rebuild |

Examples:

```bash
# Default: re-sign the AVB chain and drop force-encryption (needs a PEM key)
./ota2fastboot.py /path/to/<full-ota> --avb-key ~/avb/testkey.pem

# Select an OEM variant explicitly
./ota2fastboot.py /path/to/<full-ota> --avb-key ~/avb/testkey.pem \
    --oem PD2172C_CN-ZH_FULL_SC_CT-BMW

# Keep the stock AVB chain and force-encryption (no key needed)
./ota2fastboot.py /path/to/<full-ota> --keep-avb

# Continue after an interruption
./ota2fastboot.py /path/to/<full-ota> --avb-key ~/avb/testkey.pem \
    --resume --reuse-raw --reuse-super

# Other devices: set the super size (bytes, or a value such as 14GiB)
./ota2fastboot.py /path/to/<full-ota> --avb-key ~/avb/testkey.pem \
    --device-size 8GiB

# Probe one specific ADB device and require it to exist and match the OTA
./ota2fastboot.py /path/to/<full-ota> --avb-key ~/avb/testkey.pem \
    --probe-device --serial <serial> --require-device

# Windows target with bundled platform-tools
./ota2fastboot.py /path/to/<full-ota> \
    --target-platform windows \
    --platform-tools-dir /path/to/platform-tools

# Keep every OEM repair variant in the package
./ota2fastboot.py /path/to/<full-ota> --all-oem
```

## Probing an ADB device (optional)

No probe happens by default. Add `--probe-device` or `--require-device` to read the connected phone. The converter accepts **ADB connections only**. A phone that is visible only in Fastboot is not probed; boot Android and enable USB debugging first. The probe never writes, flashes or unlocks anything.

| Stage | Read | Used for |
| --- | --- | --- |
| ADB | `getprop` (model, OEM/VGC identity, Android version, fingerprint, unlock state, `ro.vivo.boot.devcfg_type`), `/dev/block/mapper`, `df -k` | OEM/VGC selection, devcfg type, model check, `MODEL_REGEX`, build report |
| Automatic bootloader pass | `fastboot getvar all` (`partition-size:super`, `current-slot`, `unlocked`, ...), `fastboot oem product-model` | The `super` size and current slot that ADB cannot read; the phone is rebooted back to Android afterwards |

- Without `--probe-device`, `super` comes from `--device-size` or the 14 GiB default; OEM/VGC use the OTA auto-selection; devcfg defaults to type `0`.
- The phone is rebooted to the bootloader only when ADB cannot provide the exact `super` size and `--device-size` was not given; otherwise no reboot happens.
- If the bootloader does not appear within the timeout, or reading fails, the build continues with the ADB data and defaults.
- A device-reported `super` size, or the value read in the bootloader, takes precedence over the built-in default; an explicit `--device-size` still wins.
- When the device-reported OEM identity exists in the OTA, `--oem auto` selects it directly; VGC works the same way. When converting across Android versions the newer identity may be absent from the older OTA; the build falls back to the OTA model match and records that in the log.
- When the device model does not match the OTA `pre-device` / `hardware-device`, device data is ignored by default and the package follows the OTA metadata. `--skip-device-check` forces the device data, while `--require-device` requires a compatible ADB device.
- The device model / product values are appended to `MODEL_REGEX` in `package.env`, so the flash-time check accepts the real device.
- The probe result is written to `target_device` in `meta/build.json` and included in `meta/BUILD.md`; the serial is masked to its last four characters by default.
- The device serial is also masked in the build log; use `--keep-serial` to keep it in full.
- Rebooting to the bootloader and back takes some time; keep the USB cable connected.
- For reproducible release packages, do **not** pass `--probe-device`; pin `--device-size` instead.

## Build verification

1. `e2fsck -fn` on every ext4 image, including `oem` and `vgc`
2. `lpdump` to confirm that every dynamic partition exists in `super.img` and is not smaller than the op list declares
3. `avbtool verify_image --follow_chain_partitions` for:
   - `vbmeta` (covering `boot`, `dtbo`, `vendor_boot`, `recovery`, `vm-bootsys`)
   - `vbmeta_system` (covering all dynamic partitions)
   - `vbmeta_oem`
   - `vbmeta_vgc`

   With the default custom-key build this runs on the original OTA images before patching; the build also verifies the re-signed chain at the end and only warns on failure.
4. Flash-item lists: every required image in the generated `firmware-update/flash-items-*.txt` exists
5. The sparse image expands to exactly the requested `super` size (`--device-size`, the device probe value, or the default)
6. The SHA-256 manifest can be verified:

```bash
./verify.sh
```

```powershell
.\verify.cmd
```

The manifest covers every file except `meta/build.log` and the manifest files themselves.

## Model check

The converter derives the model list from the OTA metadata fields `pre-device` and `hardware-device` and writes it to `MODEL_REGEX` in `package.env`. When a compatible device is probed at build time, its model / product values are appended as well. The flash scripts read `fastboot oem product-model` from the phone and compare it.

To skip the check when you are certain:

```bash
SKIP_MODEL_CHECK=1 ./flash.sh
```

```powershell
$env:SKIP_MODEL_CHECK = "1"
.\flash.cmd
```

## Safety

- The converter **never flashes anything**; it only builds and verifies the package.
- Device probing never writes, flashes or unlocks. When needed it reboots the phone to the bootloader and then back to Android.
- The full flash scripts (`flash-full.sh` / `flash-full.cmd`) write the bootloader chain and UFS firmware. When EDL recovery is unavailable, prefer the safe scripts (`flash.sh` / `flash.cmd`).
- The safe scripts do not write `storage`, `xbl`, `xbl_config`, `abl`, `tz`, `hyp`, `aop`, `devcfg`, `keymaster`, `uefisecapp`, `uefi`, `shrm`, `cpucp`, `imagefv`, `multiimg*`, `storsec`, `featenabler`, `toolsfv` or `xbl_ramdump`.
- Flashing across versions may raise the anti-rollback counter; the device may refuse a bootloader downgrade.
- Generated packages keep dm-verity but sign the whole AVB chain with your own key; guard the `--avb-key` private key and use them only on your own device.
- Flashing can optionally wipe data after the writes; wiping removes all apps and files, so back up first.
- The bootloader must be unlocked.

## Troubleshooting

**`brotli not found`**: install `brotli`, or make sure it is in `PATH`.

**`lpmake not found`**: install `android-sdk-libsparse-utils`.

**Not enough disk space**: peak usage is roughly dynamic partition total × 2 + firmware size × 2. A large package needs about 25 GiB free.

**`dynamic_partitions_op_list not found`**: the input is not a fully extracted directory, or it is not a block OTA. Extract the official full package again.

**`unsupported ota-type`**: `metadata` is not `BLOCK`, for example a `payload.bin` A/B OTA; this tool does not support it.

**Stuck at the boot logo after flashing**: flash the older package back with `./flash.sh` (on Windows, `.\flash.cmd`), or check whether the bootloader must be updated together.

**"System corrupt" or wrong OEM identity**: run `./fix-oem.sh` (on Windows, `.\fix-oem.cmd`) and select the variant that matches the phone; rebuild with `--all-oem` if that variant was not packaged.

**Wrong `super.img` size**: pass `--device-size` explicitly, or add `--probe-device` to read the target phone. The default is 14 GiB. The converter does not read a package already installed on the host to guess the size.

## Repository layout

| Path | Description |
| --- | --- |
| `ota2fastboot.py` | Executable entry point; keeps `./ota2fastboot.py` working |
| `ota2fastboot/` | Converter source package |
| `ota2fastboot/cli.py` | Command line arguments |
| `ota2fastboot/builder.py` | Build pipeline |
| `ota2fastboot/avb.py` | Remove force-encryption flags, re-sign the AVB chain, recompute hashtrees |
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
| `scripts/bash/common.sh` | Shared helpers for Linux / macOS flash scripts |
| `scripts/bash/flash.sh` | Default safe flash template |
| `scripts/bash/flash-full.sh` | Full flash template |
| `scripts/bash/flash-safe.sh` | Old-name compatibility alias |
| `scripts/bash/fix-oem.sh` | OEM repair template |
| `scripts/bash/verify.sh` | Package integrity verification template |
| `scripts/windows/common.ps1` | Shared helpers for Windows flash scripts |
| `scripts/windows/flash.ps1` | Default safe flash template |
| `scripts/windows/flash-full.ps1` | Full flash template |
| `scripts/windows/flash-safe.ps1` | Old-name compatibility alias |
| `scripts/windows/fix-oem.ps1` | OEM repair template |
| `scripts/windows/verify.ps1` | Package integrity verification template |
| `scripts/windows/*.cmd` | Launchers that bypass the execution policy |
| `out/` | Generated fastboot packages (git ignored) |

The converter can also be run as a module:

```bash
python3 -m ota2fastboot /path/to/<full-ota>
```

`pyproject.toml` provides an optional `pip install .` install. `package.env` in the generated package is written by the converter and loaded automatically by the flash scripts.
