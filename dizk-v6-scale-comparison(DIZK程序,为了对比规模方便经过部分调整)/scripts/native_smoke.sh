#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
OUT=${1:-"$ROOT/results/native-smoke-01"}
PYTHON_BIN=${PYTHON_BIN:-python3}
"$PYTHON_BIN" "$ROOT/native/build_and_export.py" --v6-source "$ROOT/vendor/v6" --output "$OUT" \
  --bits 32 --messages 1 --range-proof-count 4 --slot-bytes 1 --messages-per-batch 1 \
  --threads 2 --repeat 1 --run-v6-proof
"$PYTHON_BIN" "$ROOT/native/validate_raw.py" --input "$OUT" --report "$OUT/raw_audit.json"
printf '%s\n' 'Small native v6 smoke test only; not a 512-bit performance result.'
