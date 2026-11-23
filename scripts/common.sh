#!/usr/bin/env bash
# Shared helpers for the generated vivo/iQOO fastboot package.
# Sourced by flash.sh, flash-full.sh, flash-safe.sh and fix-oem.sh.
#
# All scripts run from the package root and expect:
#   package.env                  generated metadata (title, model pattern)
#   firmware-update/             partition images
#   firmware-update/oem/         OEM repair images
#   platform-tools/              optional bundled adb/fastboot (Windows target)
#
# The helpers never embed an absolute host path in the package.

# shellcheck shell=bash

if [ -z "${EXTRA_ARGS+x}" ]; then
    EXTRA_ARGS=()
fi

PACKAGE_TITLE="vivo/iQOO fastboot package"
PACKAGE_VERSION=""
MODEL_REGEX=""
OEM_IDENTITY=""

DEVICE_MODE=""
DEVICE_SERIAL=""
DEVICE_OEM=""
DEVICE_VERSION=""
DEVICE_MODEL=""
DEVICE_BATTERY=""
PRODUCT_MODEL=""

fb() { fastboot "${EXTRA_ARGS[@]}" "$@"; }
adb_() { adb "${EXTRA_ARGS[@]}" "$@"; }

cls() { if [ -t 1 ]; then clear 2>/dev/null || true; fi; }

pause() {
    local prompt="${1:-Press Enter to continue...}"
    if [ -t 0 ]; then
        read -r -p "$prompt" _ || true
    fi
}

confirm() {
    # usage: confirm "question" [y|n]  (default: n)
    local prompt="$1" default="${2:-n}" answer
    case "$default" in
    [yY]) prompt="$prompt [Y/n] " ;;
    *) prompt="$prompt [y/N] " ;;
    esac
    while :; do
        if ! read -r -p "$prompt" answer; then
            echo
            return 1
        fi
        answer="${answer:-$default}"
        case "$answer" in
        [yY] | [yY][eE][sS]) return 0 ;;
        [nN] | [nN][oO]) return 1 ;;
        *) echo "Please answer y or n." ;;
        esac
    done
}

die() {
    echo "-- $*" >&2
    pause "Press Enter to exit..."
    exit 1
}

set_title() {
    if [ -t 1 ]; then
        printf '\033]0;%s\007' "$1"
    fi
}

load_package_env() {
    local dir="${1:-.}"
    if [ -f "$dir/package.env" ]; then
        # shellcheck disable=SC1091
        . "$dir/package.env"
    fi
}

setup_bundled_tools() {
    local dir="${SCRIPT_DIR:-}/platform-tools"
    [ -n "${SCRIPT_DIR:-}" ] && [ -d "$dir" ] || return 0
    case ":$PATH:" in
    *":$dir:"*) ;;
    *)
        PATH="$dir:$PATH"
        export PATH
        ;;
    esac
}

require_fastboot() {
    command -v fastboot >/dev/null 2>&1 ||
        die "fastboot not found in PATH. Install Android platform-tools."
    if ! command -v adb >/dev/null 2>&1; then
        echo "Warning: adb not found in PATH. Only Fastboot mode is supported." >&2
    fi
}

require_tty() {
    if [ ! -t 0 ]; then
        echo "Error: run this script in an interactive terminal (confirmation required)." >&2
        exit 1
    fi
}

getprop_value() {
    command -v adb >/dev/null 2>&1 || return 0
    adb_ shell getprop "$1" 2>/dev/null | tr -d '\r' | sed -n '1p' || true
}

battery_level() {
    command -v adb >/dev/null 2>&1 || return 0
    adb_ shell dumpsys battery 2>/dev/null |
        tr -d '\r' |
        sed -n 's/.*level: *\([0-9][0-9]*\).*/\1/p' |
        head -n 1 || true
}

fastboot_devices() {
    fastboot "${EXTRA_ARGS[@]}" devices 2>/dev/null |
        sed '/^[[:space:]]*$/d' || true
}

first_fastboot_device() {
    fastboot_devices | head -n 1 || true
}

wait_for_fastboot() {
    local timeout="${1:-60}" i=0
    while [ -z "$(fastboot_devices | head -n 1)" ]; do
        i=$((i + 1))
        if [ "$i" -ge "$timeout" ]; then
            return 1
        fi
        sleep 1
    done
    return 0
}

# Find an ADB device first (it carries the real OEM identity), then a device
# already sitting in fastboot.  Sets DEVICE_* globals.
acquire_device() {
    local adb_serial fb_serial
    DEVICE_MODE=""
    DEVICE_SERIAL=""
    DEVICE_OEM=""
    DEVICE_VERSION=""
    DEVICE_MODEL=""
    DEVICE_BATTERY=""

    while :; do
        if command -v adb >/dev/null 2>&1; then
            adb_serial="$(adb "${EXTRA_ARGS[@]}" devices 2>/dev/null |
                awk 'NR > 1 && $2 == "device" { print $1; exit }')" || adb_serial=""
            if [ -n "$adb_serial" ]; then
                DEVICE_MODE="adb"
                DEVICE_SERIAL="$adb_serial"
                DEVICE_OEM="$(getprop_value ro.vivo.oem.name)"
                DEVICE_VERSION="$(getprop_value ro.build.version.release)"
                DEVICE_MODEL="$(getprop_value ro.product.model)"
                DEVICE_BATTERY="$(battery_level)"
                return 0
            fi
        fi

        fb_serial="$(first_fastboot_device)"
        if [ -n "$fb_serial" ]; then
            DEVICE_MODE="fastboot"
            DEVICE_SERIAL="$fb_serial"
            return 0
        fi

        cls
        echo
        echo " -- No device connected! Waiting for the phone..."
        echo " -- Connect it in ADB or Fastboot mode, or press Ctrl+C to abort"
        sleep 2
    done
}

show_device_info() {
    if [ "$DEVICE_MODE" = "adb" ]; then
        echo " -- Device connected in ADB mode:"
        echo " -- serial: $DEVICE_SERIAL"
        echo " -- model: ${DEVICE_MODEL:-unknown}"
        echo " -- Android: ${DEVICE_VERSION:-unknown}"
        echo " -- OEM: ${DEVICE_OEM:-unknown}"
        echo " -- battery: ${DEVICE_BATTERY:-unknown}%"
        case "${DEVICE_BATTERY:-}" in
        "" | *[!0-9]*) ;;
        *)
            if [ "$DEVICE_BATTERY" -lt 35 ]; then
                echo " -- Warning: battery is below 35%. Charge the phone before flashing." >&2
            fi
            ;;
        esac
    elif [ "$DEVICE_MODE" = "fastboot" ]; then
        echo " -- Device connected in Fastboot mode:"
        echo " -- $DEVICE_SERIAL"
    fi
}

enter_fastboot() {
    if [ "$DEVICE_MODE" = "adb" ]; then
        echo " -- Rebooting into fastboot..."
        adb_ reboot bootloader || die "reboot error"
        wait_for_fastboot 90 || die "No devices are connected! Flashing aborted."
    fi
}

check_unlocked() {
    if ! fb getvar unlocked 2>&1 | grep -qE 'unlocked:[[:space:]]*yes'; then
        echo "-- Device is locked. Unlock the bootloader first." >&2
        pause
        exit 1
    fi
}

check_model() {
    local raw
    raw="$(fb oem product-model 2>&1 || true)"
    PRODUCT_MODEL="$(printf '%s\n' "$raw" |
        tr -d '\r' |
        sed -n '1{s/^[^:]*:[[:space:]]*//;p;}')"

    if [ -z "$PRODUCT_MODEL" ]; then
        echo " -- Warning: could not read the product model." >&2
        if [ "${SKIP_MODEL_CHECK:-0}" != "1" ]; then
            die "Device model check failed. Set SKIP_MODEL_CHECK=1 to override."
        fi
        return 0
    fi

    if [ -z "${MODEL_REGEX:-}" ]; then
        echo " -- Warning: package.env has no MODEL_REGEX; model check skipped." >&2
        return 0
    fi

    if ! printf '%s\n' "$PRODUCT_MODEL" | grep -qE "$MODEL_REGEX"; then
        echo " -- Detected model: $PRODUCT_MODEL" >&2
        echo " -- Expected model(s): $MODEL_REGEX" >&2
        if [ "${SKIP_MODEL_CHECK:-0}" != "1" ]; then
            die "Device check failed. This package does not target this model."
        fi
        echo " -- SKIP_MODEL_CHECK=1 set; continuing anyway." >&2
    fi
}

write_device_info() {
    local oem="${DEVICE_OEM:-${OEM_IDENTITY:-}}"
    {
        echo "product-model=${PRODUCT_MODEL:-${DEVICE_MODEL:-}}"
        echo "oem=${oem}"
        echo "android=${DEVICE_VERSION:-}"
        echo "package=${PACKAGE_VERSION:-}"
    } >device-info.txt
}

# Flash a partition.  Exits on a missing file or a failed command.
flash_part() {
    local part="$1" img="$2"
    if [ ! -f "$img" ]; then
        echo "-- File not found: $img" >&2
        pause "Press Enter to exit..."
        exit 1
    fi
    echo " -- $part <- $img"
    if ! fb flash "$part" "$img"; then
        echo "-- Flash $part failed." >&2
        pause "Press Enter to exit..."
        exit 1
    fi
}

# Flash a partition when its image exists; otherwise print a skip notice.
flash_optional() {
    local part="$1" img="$2"
    if [ ! -f "$img" ]; then
        echo " -- Skipping $part: $img not found"
        return 0
    fi
    flash_part "$part" "$img"
}

# Validate every "partition|image[|optional]" item before touching the phone.
preflight_items() {
    local item part img opt missing=0
    for item in "$@"; do
        IFS='|' read -r part img opt <<<"$item"
        if [ ! -f "$img" ]; then
            if [ "${opt:-}" = "optional" ]; then
                echo " -- Optional image missing, will skip: $part ($img)"
            else
                echo "-- Required image missing: $img" >&2
                missing=1
            fi
        fi
    done
    if [ "$missing" -ne 0 ]; then
        die "Package is incomplete."
    fi
}

flash_items() {
    local item part img opt
    for item in "$@"; do
        IFS='|' read -r part img opt <<<"$item"
        if [ ! -f "$img" ] && [ "${opt:-}" = "optional" ]; then
            echo " -- Skipping $part: $img not found"
            continue
        fi
        flash_part "$part" "$img"
    done
}

reboot_fastboot() {
    if ! fb reboot fastboot; then
        die "reboot error"
    fi
    wait_for_fastboot 90 || die "No devices are connected! Flashing aborted."
}

# Restore the exact OEM identity read from the device before flashing.
repair_oem() {
    local oem="${DEVICE_OEM:-${OEM_IDENTITY:-}}"
    local vbmeta="firmware-update/oem/vbmeta_oem_${oem}.img"
    local image="firmware-update/oem/OEM_${oem}.img"

    echo
    if [ -z "$oem" ]; then
        echo " -- OEM identity unknown; skipping automatic OEM repair."
        return 0
    fi

    echo " -- OEM repair target: $oem"
    if [ -f "$vbmeta" ]; then
        flash_part vbmeta_oem "$vbmeta"
        reboot_fastboot
    else
        echo " -- $vbmeta not found; keeping the vbmeta_oem already flashed."
    fi

    if [ -f "$image" ]; then
        flash_part oem "$image"
    else
        echo " -- $image not found; run ./fix-oem.sh after boot to select an identity."
    fi
}

final_reboot() {
    echo
    echo " -- Flashing finished. Rebooting..."
    if ! fb reboot; then
        die "reboot error"
    fi
}

# A Windows-target package may ship platform-tools next to the scripts.
setup_bundled_tools
