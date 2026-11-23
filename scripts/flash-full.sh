#!/usr/bin/env bash
# Full fastboot flashing script for a generated vivo/iQOO package.
#
# Writes every partition produced by ota2fastboot.py, including the bootloader
# chain (xbl, abl, tz, hyp, ...) and the UFS firmware (storage).  An interrupted
# bootloader write can leave the phone unbootable without EDL recovery.  Use
# ./flash.sh when you want an AP-only flash that keeps fastboot reachable.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)"
cd -- "$SCRIPT_DIR" || exit 1

EXTRA_ARGS=("$@")

# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"
load_package_env "$SCRIPT_DIR"

require_fastboot
require_tty
set_title "${PACKAGE_TITLE:-vivo/iQOO fastboot package}"

FLASH_ITEMS=(
    "boot|firmware-update/boot.img"
    "modem|firmware-update/NON-HLOS.bin|optional"
    "dsp|firmware-update/dspso.bin|optional"
    "bluetooth|firmware-update/BTFM.bin|optional"
    "dtbo|firmware-update/dtbo.img"
    "qupfw|firmware-update/qupv3fw.elf|optional"
    "keymaster|firmware-update/keymint.mbn|optional"
    "uefisecapp|firmware-update/uefi_sec.mbn|optional"
    "uefi|firmware-update/uefi.elf|optional"
    "shrm|firmware-update/shrm.elf|optional"
    "multiimgoem|firmware-update/multi_image.mbn|optional"
    "multiimgqti|firmware-update/multi_image_qti.mbn|optional"
    "cpucp|firmware-update/cpucp.elf|optional"
    "abl|firmware-update/abl.elf"
    "tz|firmware-update/tz.mbn"
    "aop|firmware-update/aop.mbn"
    "aop_config|firmware-update/aop_devcfg.mbn|optional"
    "imagefv|firmware-update/imagefv.elf|optional"
    "hyp|firmware-update/hypvm.mbn"
    "xbl_config|firmware-update/xbl_config.elf"
    "xbl|firmware-update/xbl_s.melf"
    "storage|firmware-update/firmware.bin|optional"
    "featenabler|firmware-update/featenabler.mbn|optional"
    "vbmeta|firmware-update/vbmeta.img"
    "vbmeta_system|firmware-update/vbmeta_system.img|optional"
    "vendor_boot|firmware-update/vendor_boot.img"
    "recovery|firmware-update/recovery.img|optional"
    "toolsfv|firmware-update/tools.fv|optional"
    "xbl_ramdump|firmware-update/XblRamdump.elf|optional"
    "storsec|firmware-update/storsec.mbn|optional"
    "devcfg|firmware-update/devcfg_overlay1.mbn|optional"
    "devcfg|firmware-update/devcfg.mbn"
    "vm-bootsys|firmware-update/vm-bootsys.img|optional"
    "vbmeta_vgc|firmware-update/vbmeta_vgc.img|optional"
    "super|firmware-update/super.img"
)

preflight_items "${FLASH_ITEMS[@]}"

echo
echo " -- This is a FULL flash: bootloader and UFS firmware will be written."
echo " -- An interrupted bootloader write can make the phone unrecoverable"
echo " -- without EDL. Keep the USB cable connected and do not touch the phone."
echo " -- For an AP-only flash that keeps fastboot reachable, use ./flash.sh."
echo
echo " -- Before use, make sure the phone is connected and either booted with"
echo " -- USB debugging enabled, or already in Fastboot mode."
echo " -- In ADB mode the script records the real OEM identity first, so the"
echo " -- matching OEM partition can be restored after flashing."
echo
if ! confirm "Start flashing now?" y; then
    echo "-- Canceled."
    exit 0
fi

cls
acquire_device
cls
show_device_info
enter_fastboot

cls
echo
check_unlocked
check_model
write_device_info

echo " -- Device unlocked"
echo " -- Device check passed"
echo
echo "-------------------------------------------"
sleep 1
cls
echo

flash_items "${FLASH_ITEMS[@]}"
repair_oem
final_reboot
pause
