#!/usr/bin/env bash
# Default fastboot flashing script for a generated vivo/iQOO package.
#
# Writes only Android partitions (boot chain + dynamic super) and the AVB
# metadata that belongs to them.  It never writes:
#   xbl, xbl_config, abl, tz, hyp, aop, devcfg, keymaster, uefisecapp,
#   uefi, shrm, cpucp, imagefv, multiimg*, storsec, featenabler, toolsfv,
#   xbl_ramdump, storage (UFS firmware), modem, dsp, bluetooth, qupfw.
#
# If a write fails, the bootloader and fastboot are still intact, so the phone
# can normally be recovered by flashing the previous package.
# Use ./flash-full.sh for a complete flash that includes the bootloader chain
# and the UFS firmware.
# Set FLASH_SAFE_WITH_MODEM=1 to include the modem group as well.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd -- "$SCRIPT_DIR" || exit 1

EXTRA_ARGS=("$@")

# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"
load_package_env "$SCRIPT_DIR"

require_fastboot
require_tty
set_title "${PACKAGE_TITLE:-vivo/iQOO fastboot package} (safe)"

SAFE_ITEMS=()
[ -f firmware-update/flash-items-safe.txt ] || die "firmware-update/flash-items-safe.txt not found; package is incomplete"
while IFS= read -r item; do
    case "$item" in
    '' | '#'*) continue ;;
    esac
    SAFE_ITEMS+=("$item")
done <firmware-update/flash-items-safe.txt

if [ "${FLASH_SAFE_WITH_MODEM:-0}" = "1" ]; then
    if [ -f firmware-update/flash-items-modem.txt ]; then
        while IFS= read -r item; do
            case "$item" in
            '' | '#'*) continue ;;
            esac
            SAFE_ITEMS+=("$item")
        done <firmware-update/flash-items-modem.txt
    fi
    echo " -- FLASH_SAFE_WITH_MODEM=1: the modem group will be written too."
fi

preflight_items "${SAFE_ITEMS[@]}"

echo
echo " -- SAFE flash: Android partitions only."
echo " -- The bootloader, bootloader configuration, UFS firmware and modem"
echo " -- firmware are NOT touched, so fastboot remains reachable if this"
echo " -- flash fails or the new system does not boot."
echo " -- The currently installed bootloader stays in place. If the new system"
echo " -- requires a newer bootloader, it may stop at the boot logo; recover by"
echo " -- flashing the previous package."
echo
if ! confirm "Start safe flashing now?" y; then
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

flash_items "${SAFE_ITEMS[@]}"
repair_oem
final_reboot
pause
