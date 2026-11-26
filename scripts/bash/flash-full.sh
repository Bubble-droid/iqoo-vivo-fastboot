#!/usr/bin/env bash
# Full fastboot flashing script for a generated vivo/iQOO package.
#
# Writes every partition produced by ota2fastboot.py, including the bootloader
# chain (xbl, abl, tz, hyp, ...) and the UFS firmware (storage).  An interrupted
# bootloader write can leave the phone unbootable without EDL recovery.  Use
# ./flash.sh when you want an AP-only flash that keeps fastboot reachable.
# After every write, the script asks whether to erase user data before rebooting.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd -- "$SCRIPT_DIR" || exit 1

EXTRA_ARGS=("$@")

# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"
load_package_env "$SCRIPT_DIR"

require_fastboot
require_tty
set_title "${PACKAGE_TITLE:-vivo/iQOO fastboot package}"

FLASH_ITEMS=()
[ -f firmware-update/flash-items-full.txt ] || die "firmware-update/flash-items-full.txt not found; package is incomplete"
while IFS= read -r item; do
    case "$item" in
    '' | '#'*) continue ;;
    esac
    FLASH_ITEMS+=("$item")
done <firmware-update/flash-items-full.txt

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
enter_bootloader

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
ask_wipe_user_data
final_reboot
pause
