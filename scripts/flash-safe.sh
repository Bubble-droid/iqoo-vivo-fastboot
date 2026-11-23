#!/usr/bin/env bash
# Compatibility alias for packages built before the script rename.
#
# ``flash.sh`` is now the safe, Android-only entry point.  This wrapper keeps
# older instructions and external callers working.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)"
exec "$SCRIPT_DIR/flash.sh" "$@"
