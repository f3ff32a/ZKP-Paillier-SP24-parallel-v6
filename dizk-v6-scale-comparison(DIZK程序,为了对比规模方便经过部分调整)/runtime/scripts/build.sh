#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
MAVEN_BIN=${MAVEN_BIN:-mvn}
upstream= adapter= dest=
usage() {
  printf '%s\n' 'Usage: bash build.sh --upstream DIR --adapter DIR --dest NEW_DIR'
  printf '%s\n' 'Select JDK 8 with JAVA_HOME/PATH. Optional: MAVEN_BIN, PYTHON_BIN.'
}
while (($#)); do
  case "$1" in
    --upstream|--adapter|--dest)
      (($# >= 2)) || { usage >&2; exit 2; }
      case "$1" in --upstream) upstream=$2;; --adapter) adapter=$2;; --dest) dest=$2;; esac
      shift 2;;
    -h|--help) usage; exit 0;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2;;
  esac
done
[[ -n "$upstream" && -n "$adapter" && -n "$dest" ]] || { usage >&2; exit 2; }
"$PYTHON_BIN" "$SCRIPT_DIR/../tools/environment.py" --require-maven
"$PYTHON_BIN" "$SCRIPT_DIR/../tools/prepare.py" --upstream "$upstream" --adapter "$adapter" --dest "$dest"
dest=$(cd -- "$dest" && pwd)
"$PYTHON_BIN" "$SCRIPT_DIR/../tools/environment.py" --require-maven --output "$dest/build-environment.json"
cd -- "$dest"
MAVEN_REPO=${MAVEN_REPO:-"$dest/m2"}
# Author tests are copied for inspection. Compile this adapter and author main code.
# Do not launch the author's synthetic profiling or full test suite here.
"$MAVEN_BIN" -B --settings "$SCRIPT_DIR/../maven-settings.xml" "-Dmaven.repo.local=$MAVEN_REPO" -Dstyle.color=never -Dmaven.test.skip=true package dependency:copy-dependencies -DincludeScope=runtime >build.log 2>&1 || {
  cat build.log >&2; exit 1;
}
[[ -s target/dizk-1.0.jar && -d target/dependency ]] || { printf '%s\n' 'Build did not produce JAR/dependencies' >&2; exit 1; }
"$PYTHON_BIN" "$SCRIPT_DIR/../tools/check_build.py" --prepared "$dest"
printf 'Prepared build: %s\n' "$dest"
