#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
DEST=${1:-"$ROOT/build/dizk-scale"}
exec bash "$ROOT/runtime/scripts/build.sh" --upstream "$ROOT/vendor/dizk" --adapter "$ROOT/adapter" --dest "$DEST"
