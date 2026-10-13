#!/usr/bin/env python3
"""Reject output destinations inside any immutable source/input/build tree."""
from __future__ import print_function
import argparse
import json
from pathlib import Path
import sys


def contained(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--input")
    args = parser.parse_args()
    prepared = Path(args.prepared).resolve()
    output = Path(args.output).resolve()
    manifest = json.loads((prepared / "comparison-build-manifest.json").read_text(encoding="utf-8"))
    roots = [prepared, Path(manifest["upstream_source"]).resolve(), Path(manifest["adapter_source"]).resolve()]
    if args.input:
        roots.append(Path(args.input).resolve())
    if output.exists():
        raise RuntimeError("Output already exists; choose a fresh directory")
    if any(contained(output, root) or contained(root, output) for root in roots):
        raise RuntimeError("Output must be separate from upstream, adapter, prepared build and input directories")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        sys.exit(1)
