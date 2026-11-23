#!/usr/bin/env bash
# Verify the package against manifest.sha256.
#
# The manifest covers every packaged file except the manifest files themselves
# and meta/build.log.  The log is excluded because it is still being written
# while the manifest is generated.
#
# Usage:
#   ./verify.sh            verify every listed file
#   ./verify.sh --quiet    print only failures and the final status

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd -- "$SCRIPT_DIR" || exit 1

MANIFEST="manifest.sha256"
QUIET=0

if [ "${1:-}" = "--quiet" ]; then
    QUIET=1
elif [ "$#" -gt 0 ]; then
    echo "Usage: ./verify.sh [--quiet]" >&2
    exit 2
fi

if [ ! -f "$MANIFEST" ]; then
    echo "-- $MANIFEST not found. This package cannot be verified." >&2
    exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
    CHECK=(sha256sum -c "$MANIFEST")
elif command -v shasum >/dev/null 2>&1; then
    CHECK=(shasum -a 256 -c "$MANIFEST")
else
    echo "-- Need sha256sum or shasum to verify the package." >&2
    exit 1
fi

count="$(wc -l <"$MANIFEST" | tr -d ' ')"
if [ "$QUIET" -eq 0 ]; then
    echo " -- Verifying $count files against $MANIFEST..."
fi

if [ "$QUIET" -eq 1 ]; then
    if ! "${CHECK[@]}" >/dev/null 2>&1; then
        echo "-- Verification failed. Run ./verify.sh for details." >&2
        exit 1
    fi
else
    if ! "${CHECK[@]}"; then
        echo "-- Verification failed." >&2
        exit 1
    fi
fi

echo " -- Package OK ($count files)."
