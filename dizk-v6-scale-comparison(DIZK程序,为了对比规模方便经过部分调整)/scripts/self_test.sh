#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PREPARED=${1:-"$ROOT/build/dizk"}
OUT=${2:-"$ROOT/results/self-test"}
PYTHON_BIN=${PYTHON_BIN:-python3}
"$PYTHON_BIN" "$ROOT/runtime/tools/environment.py"
"$PYTHON_BIN" "$ROOT/runtime/tools/check_build.py" --prepared "$PREPARED"
[[ ! -e "$OUT" && ! -L "$OUT" ]] || { printf '%s\n' 'Self-test output already exists; choose a new directory' >&2; exit 2; }
mkdir -p -- "$OUT"
"$PYTHON_BIN" "$ROOT/tools/test_compile_ring.py"
"$PYTHON_BIN" "$ROOT/tools/test_compile_ring.py" --fixtures "$OUT/fixtures"
PREPARED=$(cd -- "$PREPARED" && pwd)
OUT=$(cd -- "$OUT" && pwd)
for fixture in toy_paillier_lifted ring512_lifted; do
  for mode in serial distributed; do
    bash "$ROOT/runtime/scripts/run.sh" --prepared "$PREPARED" --adapter-self-test \
      --input "$OUT/fixtures/$fixture" --mode "$mode" --threads 2 --partitions 2 \
      --repeat 1 --heap-gb 4 --run-id "$fixture-$mode-selftest" --output "$OUT/$fixture-$mode"
  done
done
printf '%s\n' 'Self-tests only; no formal v6 performance comparison.' > "$OUT/SELF_TESTS_COMPLETE.txt"
printf 'Self-tests saved in %s\n' "$OUT"
