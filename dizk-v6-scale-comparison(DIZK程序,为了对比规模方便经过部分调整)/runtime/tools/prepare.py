#!/usr/bin/env python3
"""Create a fresh comparison tree; never modify or overwrite author files."""
from __future__ import print_function

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "29fb0f78964cd6019029d20228ee0bee3184e64d"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def contained(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def fail(message):
    raise RuntimeError(message)


def verify(upstream):
    expected = json.loads((ROOT / "upstream-source-sha256.json").read_text(encoding="utf-8"))
    for rel, digest in expected["files"].items():
        path = upstream / rel
        if not path.is_file() or path.is_symlink() or sha(path) != digest:
            fail("Upstream does not match pinned author source: " + rel)
    expected_java = {rel for rel in expected["files"] if rel.endswith(".java")}
    actual_java = {path.relative_to(upstream).as_posix()
                   for path in (upstream / "src").rglob("*.java")}
    if actual_java != expected_java:
        fail("Unexpected author Java source files; use the supplied pinned source tree")
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--dest", required=True)
    args = parser.parse_args()
    upstream = Path(args.upstream).resolve()
    adapter = Path(args.adapter).resolve()
    dest = Path(args.dest).resolve()
    if not upstream.is_dir() or not adapter.is_dir():
        fail("--upstream and --adapter must be existing directories")
    if dest.exists():
        fail("Destination already exists; choose a fresh --dest directory: " + str(dest))
    if contained(dest, upstream) or contained(upstream, dest) or contained(dest, adapter) or contained(adapter, dest):
        fail("Destination must be separate from upstream and adapter directories")
    guard = verify(upstream)
    java = adapter / "src" / "main" / "java" / "comparison"
    if not java.is_dir():
        fail("Adapter must contain src/main/java/comparison")
    if not (java / "DizkComparison.java").is_file():
        fail("Missing adapter main class comparison.DizkComparison")
    if any(path.is_symlink() for path in java.rglob("*")):
        fail("Adapter symlinks are unsupported")
    adapter_files = {path.relative_to(adapter).as_posix(): sha(path)
                     for path in java.rglob("*") if path.is_file()}
    if any(not rel.endswith(".java") for rel in adapter_files):
        fail("Only Java source files are allowed inside adapter comparison directory")
    dest.mkdir(parents=True)
    # Copy exact author source bytes. Cluster-launch submodules are unnecessary.
    for rel in guard["files"]:
        source = upstream / rel
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(source), str(target))
    shutil.copyfile(str(dest / "pom.xml"), str(dest / "upstream-pom.xml"))
    shutil.copyfile(str(ROOT / "pom.xml"), str(dest / "pom.xml"))
    shutil.copytree(str(java), str(dest / "src" / "main" / "java" / "comparison"))
    manifest = {
        "format": "dizk-comparison-build-v1", "upstream_commit": COMMIT,
        "upstream_repository": "https://github.com/scipr-lab/dizk",
        "upstream_files": guard["files"], "adapter_files": adapter_files,
        "comparison_pom_sha256": sha(dest / "pom.xml"),
        "spark_version": "2.2.2", "scala_binary_version": "2.10", "java_major": 8,
        "upstream_source": str(upstream), "adapter_source": str(adapter),
        "timing_adapter": "comparison.DizkComparison"
    }
    (dest / "comparison-build-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(str(dest))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        sys.exit(1)
