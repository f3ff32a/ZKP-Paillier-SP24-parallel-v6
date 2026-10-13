#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
prepared= input= output= mode=distributed threads=16 partitions=16 repeat=1 heap_gb=4 run_id= synthetic=0 self_test=0 scale=0 serial_max_elements=2000000
usage() {
  printf '%s\n' 'Usage: bash run.sh --prepared DIR --input DIR --output NEW_DIR --run-id ID [options]'
  printf '%s\n' 'Options: --mode serial|distributed --threads N --partitions N --repeat N --heap-gb N --serial-max-elements N'
  printf '%s\n' 'An explicit --synthetic-smoke replaces --input; those rows are excluded from formal comparison.'
  printf '%s\n' 'An explicit --adapter-self-test permits arithmetic fixtures with --input; those rows are excluded from formal comparison.'
  printf '%s\n' 'An explicit --constraint-scale uses count-matched prime-field inputs; same_statement=false and a separate success marker.'
}
while (($#)); do
  case "$1" in
    --prepared|--input|--output|--run-id|--mode|--threads|--partitions|--repeat|--heap-gb|--serial-max-elements)
      (($# >= 2)) || { usage >&2; exit 2; }
      case "$1" in
        --prepared) prepared=$2;; --input) input=$2;; --output) output=$2;; --run-id) run_id=$2;;
        --mode) mode=$2;; --threads) threads=$2;; --partitions) partitions=$2;;
        --repeat) repeat=$2;; --heap-gb) heap_gb=$2;;
        --serial-max-elements) serial_max_elements=$2;;
      esac
      shift 2;;
    --synthetic-smoke) synthetic=1; shift;;
    --adapter-self-test) self_test=1; shift;;
    --constraint-scale) scale=1; shift;;
    -h|--help) usage; exit 0;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2;;
  esac
done
[[ -n "$prepared" && -n "$output" && -n "$run_id" ]] || { usage >&2; exit 2; }
[[ "$mode" == serial || "$mode" == distributed ]] || { printf '%s\n' 'Mode must be serial or distributed' >&2; exit 2; }
for value in "$threads" "$partitions" "$repeat" "$heap_gb" "$serial_max_elements"; do
  [[ "$value" =~ ^[1-9][0-9]*$ && ${#value} -le 10 ]] || { printf '%s\n' 'Numeric options must be positive integers' >&2; exit 2; }
  (( value <= 2147483647 )) || { printf '%s\n' 'Numeric options must fit a positive Java int' >&2; exit 2; }
done
(( (partitions & (partitions - 1)) == 0 )) || { printf '%s\n' 'Partitions must be a power of two for this author implementation' >&2; exit 2; }
((synthetic + self_test + scale <= 1)) || { printf '%s\n' 'Smoke, arithmetic self-test and constraint-scale modes are mutually exclusive' >&2; exit 2; }
if ((synthetic)); then
  ((!self_test)) || { printf '%s\n' '--synthetic-smoke cannot accompany --adapter-self-test' >&2; exit 2; }
  [[ -z "$input" ]] || { printf '%s\n' '--synthetic-smoke and --input are mutually exclusive' >&2; exit 2; }
else
  [[ -n "$input" ]] || { printf '%s\n' 'Formal comparison requires --input' >&2; exit 2; }
fi
"$PYTHON_BIN" "$SCRIPT_DIR/../tools/environment.py"
"$PYTHON_BIN" "$SCRIPT_DIR/../tools/check_build.py" --prepared "$prepared"
prepared=$(cd -- "$prepared" && pwd)
[[ ! -e "$output" && ! -L "$output" ]] || { printf 'Output already exists: %s\n' "$output" >&2; exit 2; }
output_check=(--prepared "$prepared" --output "$output")
if ((!synthetic)); then output_check+=(--input "$input"); fi
"$PYTHON_BIN" "$SCRIPT_DIR/../tools/check_output.py" "${output_check[@]}"
mkdir -p -- "$output"
output=$(cd -- "$output" && pwd)
"$PYTHON_BIN" "$SCRIPT_DIR/../tools/environment.py" --output "$output/environment.json"
args=(--mode "$mode" --threads "$threads" --partitions "$partitions" --repeat "$repeat" --csv "$output/results.csv" --run-id "$run_id" --memory-gb "$heap_gb" --serial-max-elements "$serial_max_elements")
if ((synthetic)); then
  args+=(--synthetic-smoke)
else
  input=$(cd -- "$input" && pwd)
  input_check=(--input "$input" --output "$output/input-files.json")
  if ((self_test)); then input_check+=(--adapter-self-test); fi
  if ((scale)); then input_check+=(--constraint-scale); fi
  "$PYTHON_BIN" "$SCRIPT_DIR/../tools/check_input.py" "${input_check[@]}"
  args+=(--input "$input")
  if ((self_test)); then args+=(--adapter-self-test); fi
  if ((scale)); then args+=(--constraint-scale); fi
fi
JAVA_BIN=java
if [[ -n "${JAVA_HOME:-}" ]]; then JAVA_BIN="$JAVA_HOME/bin/java"; fi
export SPARK_LOCAL_IP=127.0.0.1
command=("$JAVA_BIN" -ea -da:algebra.fft.FFTAuxiliary "-Xmx${heap_gb}g" -Dspark.driver.host=127.0.0.1 -Dspark.driver.bindAddress=127.0.0.1 -Dspark.ui.enabled=false -Dfile.encoding=UTF-8 -cp "$prepared/target/dizk-1.0.jar:$prepared/target/dependency/*" comparison.DizkComparison "${args[@]}")
printf '%q ' "${command[@]}" > "$output/command.txt"
printf '\n' >> "$output/command.txt"
status=0
"${command[@]}" > "$output/run.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$output/exit-code.txt"
if ((status != 0)); then
  tail -n 80 "$output/run.log" >&2
  printf 'DIZK failed with exit %s; inspect %s/run.log\n' "$status" "$output" >&2
  exit "$status"
fi
check=(--csv "$output/results.csv" --run-id "$run_id" --mode "$mode" --threads "$threads" --partitions "$partitions" --repeat "$repeat")
if ((synthetic)); then check+=(--synthetic-smoke); else check+=(--input-record "$output/input-files.json"); fi
if ((self_test)); then check+=(--adapter-self-test); fi
if ((scale)); then check+=(--constraint-scale); fi
"$PYTHON_BIN" "$SCRIPT_DIR/../tools/check_results.py" "${check[@]}"
if ((scale)); then
  printf '%s\n' 'verified_complete scope=constraint_scale_benchmark same_statement=false; counts_only_different_computation' > "$output/SCALE_BENCHMARK_COMPLETE.txt"
elif ((synthetic || self_test)); then
  printf '%s\n' 'verified_self_test_complete; excluded_from_formal_comparison' > "$output/SELF_TEST_COMPLETE.txt"
else
  printf '%s\n' 'verified_complete scope=v6_ring_lifted same_statement=true' > "$output/COMPLETE.txt"
fi
printf 'Verified results: %s/results.csv\n' "$output"
