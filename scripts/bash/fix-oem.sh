#!/usr/bin/env bash
# Repair a vivo/iQOO OEM identity after flashing.
#
# Usage:
#   ./fix-oem.sh                     interactive menu
#   ./fix-oem.sh --list              list available OEM images
#   ./fix-oem.sh --name PD2171_CN-ZH_FULL_SC_NULL
#   ./fix-oem.sh --name BMW --yes    non-interactive, skip confirmation
#   ./fix-oem.sh --filter CT-BMW
#   ./fix-oem.sh -s <serial>         extra arguments go to fastboot
#
# The package stores the OEM variants selected at build time: the variant used
# for super by default, or every variant with --all-oem.  The correct identity
# can be restored even when the phone reports a damaged system.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd -- "$SCRIPT_DIR" || exit 1

OEM_DIR="firmware-update/oem"
EXTRA_ARGS=()
SELECT_NAME=""
FILTER=""
ASSUME_YES=0
LIST_ONLY=0

usage() {
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    --list | -l)
        LIST_ONLY=1
        shift
        ;;
    --name | -n)
        [ "$#" -ge 2 ] || {
            echo "Error: $1 needs a value." >&2
            exit 2
        }
        SELECT_NAME="$2"
        shift 2
        ;;
    --filter | -f)
        [ "$#" -ge 2 ] || {
            echo "Error: $1 needs a value." >&2
            exit 2
        }
        FILTER="$2"
        shift 2
        ;;
    --yes | -y)
        ASSUME_YES=1
        shift
        ;;
    --help | -h)
        usage
        exit 0
        ;;
    *)
        EXTRA_ARGS+=("$1")
        shift
        ;;
    esac
done

# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"
load_package_env "$SCRIPT_DIR"

set_title "Fix vivo/iQOO OEM identity"

declare -a names=() paths=() kinds=()

add_choice() {
    local name="$1" path="$2" kind="$3"
    if [ -n "$FILTER" ]; then
        local lower_name lower_filter
        lower_name="$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')"
        lower_filter="$(printf '%s' "$FILTER" | tr '[:upper:]' '[:lower:]')"
        case "$lower_name" in
        *"$lower_filter"*) ;;
        *) return 0 ;;
        esac
    fi
    names+=("$name")
    paths+=("$path")
    kinds+=("$kind")
}

if [ -d "$OEM_DIR" ]; then
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        base="$(basename -- "$f")"
        add_choice "${base#OEM_}" "$f" "file"
    done < <(find "$OEM_DIR" -maxdepth 1 -type f -name 'OEM_*.img' -print | sort)

    while IFS= read -r d; do
        [ -n "$d" ] || continue
        add_choice "$(basename -- "$d")" "$d" "folder"
    done < <(find "$OEM_DIR" -maxdepth 1 -type d -name 'oem_*' -print | sort)
fi

count=${#names[@]}

if [ "$LIST_ONLY" -eq 1 ]; then
    if [ "$count" -eq 0 ]; then
        echo "No OEM repair images found in $OEM_DIR."
        exit 0
    fi
    for i in "${!names[@]}"; do
        if [ "${kinds[$i]}" = "file" ]; then
            printf '%s\n' "${names[$i]}"
        else
            printf 'folder:%s\n' "${names[$i]}"
        fi
    done
    exit 0
fi

if [ "$count" -eq 0 ]; then
    if [ -n "$FILTER" ]; then
        echo "No OEM repair image matches filter '$FILTER' in $OEM_DIR."
    else
        echo "No OEM repair images found in $OEM_DIR."
        echo "This package was built without OEM variants."
    fi
    if [ -t 0 ]; then
        pause
    fi
    exit 0
fi

# Prefer the identity reported by the connected phone, then the identity that
# the package itself was built with.
preferred="$(getprop_value ro.vivo.oem.name)"
[ -n "$preferred" ] || preferred="${OEM_IDENTITY:-}"

default_index=0
if [ -n "$preferred" ]; then
    for i in "${!names[@]}"; do
        if [ "${names[$i]%.img}" = "$preferred" ]; then
            default_index=$((i + 1))
            break
        fi
    done
fi

find_choice() {
    # Sets MATCH_INDEX and MATCH_COUNT for an exact name or a unique substring.
    local query="$1" lower_query lower_name
    MATCH_INDEX=0
    MATCH_COUNT=0
    lower_query="$(printf '%s' "$query" | tr '[:upper:]' '[:lower:]')"
    for i in "${!names[@]}"; do
        lower_name="$(printf '%s' "${names[$i]}" | tr '[:upper:]' '[:lower:]')"
        if [ "$lower_name" = "$lower_query" ]; then
            MATCH_INDEX=$((i + 1))
            MATCH_COUNT=1
            return 0
        fi
        case "$lower_name" in
        *"$lower_query"*)
            MATCH_INDEX=$((i + 1))
            MATCH_COUNT=$((MATCH_COUNT + 1))
            ;;
        esac
    done
}

echo
echo "Script notes:"
echo "  -- Restores the OEM/cust identity after a fastboot flash."
echo "  -- The phone must be unlocked and in Fastboot mode."
echo "  -- Select by number or by part of the image name."
echo
echo "----------------------------------"
echo

for i in "${!names[@]}"; do
    marker=""
    if [ "$((i + 1))" -eq "$default_index" ]; then
        marker="  <- current device identity"
    fi
    if [ "${kinds[$i]}" = "file" ]; then
        printf '%3d. File:   %s%s\n' "$((i + 1))" "${names[$i]}" "$marker"
    else
        printf '%3d. Folder: %s%s\n' "$((i + 1))" "${names[$i]}" "$marker"
    fi
done
echo

if [ -n "$SELECT_NAME" ]; then
    find_choice "$SELECT_NAME"
    if [ "$MATCH_COUNT" -eq 0 ]; then
        echo "Error: no OEM image matches '$SELECT_NAME'." >&2
        exit 1
    fi
    if [ "$MATCH_COUNT" -gt 1 ]; then
        echo "Error: '$SELECT_NAME' matches $MATCH_COUNT images. Be more specific." >&2
        exit 1
    fi
    choice_num="$MATCH_INDEX"
else
    if [ ! -t 0 ]; then
        echo "Error: no --name given and stdin is not a terminal." >&2
        echo "Use --list and --name NAME for non-interactive use." >&2
        exit 2
    fi
    choice_num=0
    while :; do
        if [ "$default_index" -gt 0 ]; then
            if ! read -r -p "Select an option (number or name) [${names[$((default_index - 1))]}]: " answer; then
                echo
                echo "Error: input closed; aborting." >&2
                exit 2
            fi
        else
            if ! read -r -p "Select an option (number or name): " answer; then
                echo
                echo "Error: input closed; aborting." >&2
                exit 2
            fi
        fi
        if [ -z "$answer" ] && [ "$default_index" -gt 0 ]; then
            choice_num="$default_index"
            break
        fi
        case "$answer" in
        q | Q | quit | exit) echo "Canceled."; exit 0 ;;
        esac
        if [[ "$answer" =~ ^[0-9]+$ ]]; then
            choice_num=$((10#$answer))
            if [ "$choice_num" -ge 1 ] && [ "$choice_num" -le "$count" ]; then
                break
            fi
            echo "Invalid number. Enter 1-$count, a name, or q to quit."
            continue
        fi
        if [ -n "$answer" ]; then
            find_choice "$answer"
            if [ "$MATCH_COUNT" -eq 1 ]; then
                choice_num="$MATCH_INDEX"
                break
            elif [ "$MATCH_COUNT" -gt 1 ]; then
                echo "'$answer' matches $MATCH_COUNT images. Be more specific."
                continue
            fi
        fi
        echo "No match. Enter a number, part of a name, or q to quit."
    done
fi

idx=$((choice_num - 1))
selected_path="${paths[$idx]}"
selected_name="${names[$idx]}"
ptype="${kinds[$idx]}"

cls
echo
echo "  -- Target: $selected_name"
echo "  -- Path:   $selected_path"

if [ "$ptype" = "file" ]; then
    vbmeta="$OEM_DIR/vbmeta_oem_${selected_name%.img}.img"
    if [ ! -f "$vbmeta" ]; then
        die "Missing companion image: $vbmeta"
    fi
fi

if [ "$ASSUME_YES" -ne 1 ]; then
    if ! confirm "Flash the selected OEM repair image(s)?" n; then
        echo "-- Canceled."
        exit 0
    fi
fi

# Wait for Fastboot.
while :; do
    dev="$(first_fastboot_device)"
    if [ -n "$dev" ]; then
        break
    fi
    echo
    echo " No device connected in Fastboot mode. Waiting..."
    sleep 2
done

if ! fb getvar unlocked 2>&1 | grep -qE 'unlocked:[[:space:]]*yes'; then
    die "Device is locked. Unlocking the bootloader is required."
fi

echo
if [ "$ptype" = "file" ]; then
    vbmeta="$OEM_DIR/vbmeta_oem_${selected_name%.img}.img"
    flash_part vbmeta_oem "$vbmeta"
    reboot_fastboot
    flash_part oem "$selected_path"
else
    slot_info="$(fb getvar current-slot 2>&1)" || slot_info=""
    if printf '%s\n' "$slot_info" | grep -qE 'current-slot:[[:space:]]*a'; then
        slot="a"
    else
        slot="b"
    fi
    flash_part "vbmeta_oem_${slot}" "$selected_path/vbmeta_oem.img"
    reboot_fastboot
    flash_part "cust_${slot}" "$selected_path/cust.img"
fi

final_reboot
echo
echo "Finished."
echo
pause
