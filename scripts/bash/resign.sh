#!/usr/bin/env bash
# Re-sign the AVB images of this package after local modifications.
#
# The package was built with a custom AVB key.  If an image is changed
# afterwards (for example, boot.img patched by Magisk or KernelSU), its
# vbmeta footer no longer matches the content.  This script removes the old
# footer, recomputes the hash or dm-verity hashtree, and signs the result
# again with the same private key.
#
# The private key is never stored in the package.  Pass it with --key, set
# AVB_KEY, or place avb.pem next to this script.
#
# Usage:
#   ./resign.sh boot
#   ./resign.sh --all
#   ./resign.sh -k /path/to/avb.pem firmware-update/boot.img
#   ./resign.sh --force recovery
#
# Supported images:
#   boot, dtbo, vendor_boot       hash footer, recomputed
#   vbmeta, vbmeta_system, vbmeta_vgc
#                                 standalone vbmeta, re-signed in place
#   system, system_ext, product, vendor, vendor_dlkm, odm
#                                 hashtree footer, recomputed; the caller must
#                                 rebuild vbmeta_system afterwards
#
# The script does not rebuild the top-level vbmeta chain.  That chain stores
# public keys, not image digests, so it stays valid as long as the same key is
# used.  A different key requires a full rebuild with ota2fastboot.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd -- "$SCRIPT_DIR" || exit 1

DEFAULT_SALT="aee087a5be3b982978c923f566a94613496b417f2af592639bc80d141e34dfe7"
DEFAULT_ALGORITHM="SHA256_RSA2048"
DEFAULT_HASH="sha256"
ALL_IMAGES=(boot dtbo vendor_boot vbmeta_system vbmeta_vgc vbmeta)

KEY="${AVB_KEY:-}"
TARGETS=()
ALL=0
PARTITION_SIZE=""
SALT=""
HASH_ALGORITHM=""
ALGORITHM=""
FORCE=0
VERIFY=1
DRY_RUN=0
BACKUP=0

TMP="$(mktemp -d "${TMPDIR:-/tmp}/resign.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

note() {
    printf '%s\n' "$*"
}

die() {
    printf -- '-- %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Re-sign the AVB images of this package after local modifications.

Usage:
  ./resign.sh [options] IMAGE...

IMAGE is a partition name (boot), a file name (boot.img), or a path.

Options:
  -k, --key PATH          AVB private key (default: AVB_KEY, ./avb.pem,
                          ./avb/avb.pem, ~/.config/iqoo-vivo-fastboot/avb.pem)
  -a, --all               boot, dtbo, vendor_boot, vbmeta_system,
                          vbmeta_vgc and vbmeta, when present
      --partition-size N  partition size in bytes (single image only)
      --salt HEX          override the hash salt
      --hash-algorithm A  override sha256
      --algorithm A       override SHA256_RSA2048
      --force             allow a key mismatch, a missing footer, or a
                          boot header v4 signature that cannot be updated
      --backup            keep a .bak copy before modifying an image
      --no-verify         skip the avbtool verify_image check
  -n, --dry-run           print the commands without changing anything
  -h, --help              show this help

Examples:
  ./resign.sh boot
  ./resign.sh --all
  ./resign.sh -k ~/avb.pem firmware-update/boot.img

Supported images:
  boot, dtbo, vendor_boot       hash footer, recomputed
  vbmeta, vbmeta_system, vbmeta_vgc
                                standalone vbmeta, re-signed in place
  system, system_ext, product, vendor, vendor_dlkm, odm
                                hashtree footer, recomputed; rebuild
                                vbmeta_system afterwards

The top-level vbmeta chain stores public keys, not image digests, so it
stays valid as long as the same key is used.  A different key requires a
full rebuild with ota2fastboot.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    -k | --key)
        [ "$#" -ge 2 ] || die "$1 needs a path"
        KEY=$2
        shift 2
        ;;
    --key=*)
        KEY=${1#*=}
        shift
        ;;
    -a | --all)
        ALL=1
        shift
        ;;
    --partition-size)
        [ "$#" -ge 2 ] || die "$1 needs a size"
        PARTITION_SIZE=$2
        shift 2
        ;;
    --partition-size=*)
        PARTITION_SIZE=${1#*=}
        shift
        ;;
    --salt)
        [ "$#" -ge 2 ] || die "$1 needs a hex value"
        SALT=$2
        shift 2
        ;;
    --salt=*)
        SALT=${1#*=}
        shift
        ;;
    --hash-algorithm)
        [ "$#" -ge 2 ] || die "$1 needs a value"
        HASH_ALGORITHM=$2
        shift 2
        ;;
    --hash-algorithm=*)
        HASH_ALGORITHM=${1#*=}
        shift
        ;;
    --algorithm)
        [ "$#" -ge 2 ] || die "$1 needs a value"
        ALGORITHM=$2
        shift 2
        ;;
    --algorithm=*)
        ALGORITHM=${1#*=}
        shift
        ;;
    --force)
        FORCE=1
        shift
        ;;
    --no-verify)
        VERIFY=0
        shift
        ;;
    --dry-run | -n)
        DRY_RUN=1
        shift
        ;;
    --backup)
        BACKUP=1
        shift
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    --)
        shift
        while [ "$#" -gt 0 ]; do
            TARGETS+=("$1")
            shift
        done
        ;;
    -*)
        die "unknown option: $1 (try --help)"
        ;;
    *)
        TARGETS+=("$1")
        shift
        ;;
    esac
done

command -v avbtool >/dev/null 2>&1 ||
    die "avbtool not found in PATH. Install android-sdk-libsparse-utils."

file_size() {
    wc -c <"$1" | tr -d ' '
}

sha1_file() {
    if command -v sha1sum >/dev/null 2>&1; then
        sha1sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 1 "$1" | awk '{print $1}'
    else
        die "need sha1sum or shasum"
    fi
}

truncate_file() {
    local file=$1 size=$2
    if command -v truncate >/dev/null 2>&1; then
        truncate -s "$size" "$file"
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c 'import os,sys; os.truncate(sys.argv[1], int(sys.argv[2]))' \
            "$file" "$size"
    else
        local tmp="$file.truncate.$$"
        head -c "$size" "$file" >"$tmp"
        mv "$tmp" "$file"
    fi
}

run_avbtool() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  would run: avbtool'
        printf ' %q' "$@"
        printf '\n'
    else
        avbtool "$@"
    fi
}

# Print the value of a field from "avbtool info_image" output.
# The optional third argument selects a descriptor section.
info_get() {
    local info=$1 key=$2 section=${3:-}
    printf '%s\n' "$info" | awk -v key="$key" -v section="$section" '
        /^[[:space:]]+Hash descriptor:/ { cur = "hash"; next }
        /^[[:space:]]+Hashtree descriptor:/ { cur = "hashtree"; next }
        /^[[:space:]]+Chain Partition descriptor:/ { cur = "chain"; next }
        /^[[:space:]]+Prop:/ { cur = "prop"; next }
        /^[^[:space:]]/ { cur = "" }
        {
            line = $0
            sub(/^[[:space:]]+/, "", line)
            if (index(line, key ":") == 1) {
                if (section == "" || cur == section) {
                    value = substr(line, length(key) + 2)
                    sub(/^[[:space:]]+/, "", value)
                    print value
                    exit
                }
            }
        }'
}

info_props() {
    printf '%s\n' "$1" |
        sed -n "s/^[[:space:]]*Prop: \([^ ]*\) -> '\(.*\)'$/\1:\2/p"
}

resolve_image() {
    local arg=$1
    if [ -f "$arg" ]; then
        printf '%s\n' "$arg"
    elif [ -f "firmware-update/$arg" ]; then
        printf '%s\n' "firmware-update/$arg"
    elif [ -f "firmware-update/$arg.img" ]; then
        printf '%s\n' "firmware-update/$arg.img"
    else
        return 1
    fi
}

if [ -z "$KEY" ]; then
    for candidate in \
        "$SCRIPT_DIR/avb.pem" \
        "$SCRIPT_DIR/avb/avb.pem" \
        "${HOME:-}/.config/iqoo-vivo-fastboot/avb.pem"; do
        if [ -f "$candidate" ]; then
            KEY=$candidate
            break
        fi
    done
fi

[ -n "$KEY" ] ||
    die "no AVB private key. Pass --key PATH, set AVB_KEY, or put avb.pem next to this script."
[ -f "$KEY" ] || die "AVB private key not found: $KEY"

if [ "$ALL" -eq 1 ]; then
    for name in "${ALL_IMAGES[@]}"; do
        if [ -f "firmware-update/$name.img" ]; then
            TARGETS+=("$name")
        fi
    done
fi

[ "${#TARGETS[@]}" -gt 0 ] ||
    die "no images specified. Use ./resign.sh boot, ./resign.sh --all, or pass an image path."

if [ -n "$PARTITION_SIZE" ] && [ "${#TARGETS[@]}" -ne 1 ]; then
    die "--partition-size can only be used with a single image"
fi

# The package ships the public half of the build key.  Compare it with the
# supplied private key so a typo or a stale key cannot silently break the
# chain.
key_sha1=""
avbtool extract_public_key --key "$KEY" --output "$TMP/custom.avbpubkey" \
    >/dev/null 2>&1 || die "cannot read the AVB private key: $KEY"
key_sha1="$(sha1_file "$TMP/custom.avbpubkey")"
package_sha1=""
if [ -f "avb/custom.avbpubkey" ]; then
    package_sha1="$(sha1_file "avb/custom.avbpubkey")"
    if [ "$key_sha1" != "$package_sha1" ]; then
        if [ "$FORCE" -eq 0 ]; then
            die "the key ($key_sha1) does not match this package's AVB key ($package_sha1). Use the key passed to --avb-key at build time, or rebuild the whole chain with a new key. Pass --force to sign anyway."
        fi
        note "-- warning: signing with a key that does not match avb/custom.avbpubkey"
    fi
fi

note "-- key: $KEY (sha1 $key_sha1)"

signed=0
hashtree_signed=0

for target in "${TARGETS[@]}"; do
    path="$(resolve_image "$target")" ||
        die "image not found: $target (tried the path, firmware-update/$target and firmware-update/$target.img)"
    [ -f "$path" ] || die "not a regular file: $path"

    note ""
    note "-- $path"

    is_vbmeta=0

    # A boot image header v4 carries a separate signature over the kernel and
    # ramdisk.  Re-signing the AVB footer does not update it, so changing the
    # content requires a patcher that knows how to re-sign that header.
    if [ "$(head -c 8 "$path" 2>/dev/null || true)" = "ANDROID!" ]; then
        header_version="$(od -An -tu4 -j40 -N4 "$path" 2>/dev/null | tr -d ' ')"
        if [ -n "$header_version" ] && [ "$header_version" -ge 4 ] 2>/dev/null; then
            boot_signature="$(od -An -tu4 -j1580 -N4 "$path" 2>/dev/null | tr -d ' ')"
            if [ -n "$boot_signature" ] && [ "$boot_signature" -gt 0 ] 2>/dev/null; then
                note "  warning: boot header v4 signature is present ($boot_signature bytes); only the AVB footer is updated"
                if [ "$FORCE" -eq 0 ]; then
                    die "this script cannot recompute the boot header signature. Pass --force if the patcher already updated it, or rebuild boot with the vendor tools."
                fi
            fi
        fi
    fi

    if [ "$BACKUP" -eq 1 ]; then
        if [ "$DRY_RUN" -eq 1 ]; then
            note "  would back up to $path.bak"
        elif [ -e "$path.bak" ]; then
            note "  keeping existing backup: $path.bak"
        else
            cp -p "$path" "$path.bak"
            note "  backup: $path.bak"
        fi
    fi

    info=""
    if info="$(avbtool info_image --image "$path" 2>&1)"; then
        :
    else
        info=""
    fi

    if [ -z "$info" ]; then
        [ "$FORCE" -eq 1 ] ||
            die "$path has no AVB footer. Patch the image with AVB preservation enabled, or pass --force with --partition-size to add a new footer."
        name="$(basename "$path")"
        name="${name%.img}"
        partition_size="${PARTITION_SIZE:-$(file_size "$path")}"
        [ "$partition_size" -gt "$(file_size "$path")" ] ||
            die "$path is not padded to the partition size; pass --partition-size (for example 100663296 for boot)."
        salt="${SALT:-$DEFAULT_SALT}"
        hash_alg="${HASH_ALGORITHM:-$DEFAULT_HASH}"
        algorithm="${ALGORITHM:-$DEFAULT_ALGORITHM}"
        note "  no footer; adding a new hash footer for $name ($partition_size bytes)"
        run_avbtool add_hash_footer \
            --image "$path" \
            --partition_name "$name" \
            --partition_size "$partition_size" \
            --hash_algorithm "$hash_alg" \
            --salt "$salt" \
            --key "$KEY" \
            --algorithm "$algorithm"
        if [ "$VERIFY" -eq 1 ] && [ "$DRY_RUN" -eq 0 ]; then
            avbtool verify_image --image "$path" >/dev/null ||
                die "verification failed for $path"
        fi
        signed=$((signed + 1))
        continue
    fi

    current_key="$(info_get "$info" "Public key (sha1)")"
    if [ -n "$current_key" ] && [ "$current_key" != "$key_sha1" ]; then
        note "  note: the image is currently signed with key $current_key"
    fi

    if printf '%s\n' "$info" | grep -q '^Footer version:'; then
        if printf '%s\n' "$info" | grep -q '^[[:space:]]*Hash descriptor:'; then
            name="$(info_get "$info" "Partition Name" hash)"
            salt="$(info_get "$info" "Salt" hash)"
            hash_alg="$(info_get "$info" "Hash Algorithm" hash)"
            flags="$(info_get "$info" "Flags")"
            rollback="$(info_get "$info" "Rollback Index")"
            rollback_loc="$(info_get "$info" "Rollback Index Location")"
            algorithm="$(info_get "$info" "Algorithm")"
            cut="$(info_get "$info" "VBMeta offset")"
            cut="${cut%% *}"

            [ -n "$name" ] || die "$path: no partition name in the hash descriptor"
            [ -n "$cut" ] || die "$path: no VBMeta offset in the footer"
            salt="${SALT:-${salt:-$DEFAULT_SALT}}"
            hash_alg="${HASH_ALGORITHM:-${hash_alg:-$DEFAULT_HASH}}"
            algorithm="${ALGORITHM:-${algorithm:-$DEFAULT_ALGORITHM}}"
            [ "$algorithm" != "NONE" ] || algorithm="$DEFAULT_ALGORITHM"
            flags="${flags:-0}"
            rollback="${rollback:-0}"
            rollback_loc="${rollback_loc:-0}"
            partition_size="${PARTITION_SIZE:-$(file_size "$path")}"

            note "  hash footer: partition=$name size=$partition_size content=$cut"
            [ "$partition_size" -ge "$cut" ] ||
                die "$path: partition size ($partition_size) is smaller than the content ($cut)"
            [ "$cut" -le "$(file_size "$path")" ] ||
                die "$path: the footer points past the end of the file; re-extract the image"

            if [ "$DRY_RUN" -eq 1 ]; then
                note "  would truncate to $cut bytes"
            else
                truncate_file "$path" "$cut"
            fi

            args=(
                add_hash_footer
                --image "$path"
                --partition_name "$name"
                --partition_size "$partition_size"
                --hash_algorithm "$hash_alg"
                --salt "$salt"
                --key "$KEY"
                --algorithm "$algorithm"
                --flags "$flags"
                --rollback_index "$rollback"
                --rollback_index_location "$rollback_loc"
            )
            while IFS= read -r prop; do
                [ -n "$prop" ] && args+=(--prop "$prop")
            done < <(info_props "$info")

            run_avbtool "${args[@]}"
            signed=$((signed + 1))
        elif printf '%s\n' "$info" | grep -q '^[[:space:]]*Hashtree descriptor:'; then
            name="$(info_get "$info" "Partition Name" hashtree)"
            salt="$(info_get "$info" "Salt" hashtree)"
            hash_alg="$(info_get "$info" "Hash Algorithm" hashtree)"
            block_size="$(info_get "$info" "Data Block Size" hashtree)"
            fec_roots="$(info_get "$info" "FEC num roots" hashtree)"
            flags="$(info_get "$info" "Flags")"
            rollback="$(info_get "$info" "Rollback Index")"
            rollback_loc="$(info_get "$info" "Rollback Index Location")"
            algorithm="$(info_get "$info" "Algorithm")"
            cut="$(info_get "$info" "Tree Offset" hashtree)"
            cut="${cut%% *}"
            block_size="${block_size%% *}"

            [ -n "$name" ] || die "$path: no partition name in the hashtree descriptor"
            [ -n "$cut" ] || die "$path: no tree offset in the hashtree descriptor"
            salt="${SALT:-${salt:-$DEFAULT_SALT}}"
            hash_alg="${HASH_ALGORITHM:-${hash_alg:-$DEFAULT_HASH}}"
            algorithm="${ALGORITHM:-${algorithm:-$DEFAULT_ALGORITHM}}"
            [ "$algorithm" != "NONE" ] || algorithm="$DEFAULT_ALGORITHM"
            block_size="${block_size:-4096}"
            flags="${flags:-0}"
            rollback="${rollback:-0}"
            rollback_loc="${rollback_loc:-0}"
            partition_size="${PARTITION_SIZE:-$(file_size "$path")}"

            note "  hashtree footer: partition=$name size=$partition_size content=$cut block=$block_size fec=${fec_roots:-0}"
            [ "$partition_size" -ge "$cut" ] ||
                die "$path: partition size ($partition_size) is smaller than the content ($cut)"
            [ "$cut" -le "$(file_size "$path")" ] ||
                die "$path: the tree offset points past the end of the file; re-extract the image"

            if [ "$DRY_RUN" -eq 1 ]; then
                note "  would truncate to $cut bytes"
            else
                truncate_file "$path" "$cut"
            fi

            args=(
                add_hashtree_footer
                --image "$path"
                --partition_name "$name"
                --partition_size "$partition_size"
                --hash_algorithm "$hash_alg"
                --salt "$salt"
                --block_size "$block_size"
                --key "$KEY"
                --algorithm "$algorithm"
                --flags "$flags"
                --rollback_index "$rollback"
                --rollback_index_location "$rollback_loc"
            )
            used_fec=0
            if [ -n "$fec_roots" ] && [ "$fec_roots" -gt 0 ] 2>/dev/null; then
                if command -v fec >/dev/null 2>&1; then
                    args+=(--fec_num_roots "$fec_roots")
                    used_fec=1
                else
                    note "  note: the fec tool is not installed; FEC will be omitted"
                    args+=(--do_not_generate_fec)
                fi
            else
                args+=(--do_not_generate_fec)
            fi

            if ! run_avbtool "${args[@]}"; then
                if [ "$used_fec" -eq 1 ]; then
                    note "  warning: the image does not fit with FEC; retrying without it"
                    fixed=()
                    idx=0
                    while [ "$idx" -lt "${#args[@]}" ]; do
                        if [ "${args[$idx]}" = "--fec_num_roots" ]; then
                            idx=$((idx + 2))
                            continue
                        fi
                        fixed+=("${args[$idx]}")
                        idx=$((idx + 1))
                    done
                    fixed+=(--do_not_generate_fec)
                    run_avbtool "${fixed[@]}" ||
                        die "could not add a hashtree footer to $path"
                else
                    die "could not add a hashtree footer to $path"
                fi
            fi
            signed=$((signed + 1))
            hashtree_signed=$((hashtree_signed + 1))
        else
            die "$path: unsupported AVB footer (neither hash nor hashtree)"
        fi
    else
        algorithm="$(info_get "$info" "Algorithm")"
        algorithm="${ALGORITHM:-${algorithm:-$DEFAULT_ALGORITHM}}"
        if [ "$algorithm" = "NONE" ]; then
            note "  unsigned vbmeta; left untouched"
            continue
        fi
        note "  standalone vbmeta: re-signing in place"
        run_avbtool resign_image \
            --image "$path" \
            --key "$KEY" \
            --algorithm "$algorithm"
        signed=$((signed + 1))
        is_vbmeta=1
    fi

    if [ "$VERIFY" -eq 1 ] && [ "$DRY_RUN" -eq 0 ]; then
        if [ "$is_vbmeta" -eq 1 ]; then
            note "  signature rewritten; full chain verification needs the logical partition images"
        else
            if ! avbtool verify_image --image "$path" >/dev/null 2>&1; then
                avbtool verify_image --image "$path" >&2 || true
                die "verification failed for $path"
            fi
            note "  verified"
        fi
    fi
done

note ""
note "-- re-signed $signed image(s)."
if [ "$hashtree_signed" -gt 0 ]; then
    note "-- note: hashtree descriptors live in vbmeta_system. Rebuild it with ota2fastboot (or pass the updated images to it) so the new root digests are included."
fi
note "-- the top-level vbmeta chain was not modified; it stays valid only with the build key."
