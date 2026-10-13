#!/usr/bin/env python3
"""Verify copied author files and record built JAR/dependency digests."""
from __future__ import print_function
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True)
    args = parser.parse_args()
    root = Path(args.prepared).resolve()
    manifest = json.loads((root / "comparison-build-manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["upstream_files"].items():
        actual_name = "upstream-pom.xml" if name == "pom.xml" else name
        path = root / actual_name
        if not path.is_file() or digest(path) != expected:
            raise RuntimeError("Copied author file changed: " + name)
    if digest(root / "pom.xml") != manifest["comparison_pom_sha256"]:
        raise RuntimeError("Comparison POM changed since preparation")
    for name, expected in manifest["adapter_files"].items():
        if digest(root / name) != expected:
            raise RuntimeError("Adapter source changed since preparation: " + name)
    jar = root / "target" / "dizk-1.0.jar"
    with zipfile.ZipFile(str(jar)) as archive:
        if "comparison/DizkComparison.class" not in archive.namelist():
            raise RuntimeError("Built JAR is missing comparison.DizkComparison")
        for name in archive.namelist():
            if name.endswith(".class"):
                header = archive.read(name)[:8]
                if len(header) != 8 or int.from_bytes(header[6:8], "big") > 52:
                    raise RuntimeError("Class is not Java 8 compatible: " + name)
    dependencies = sorted((root / "target" / "dependency").glob("*.jar"))
    required = ["spark-core_2.10-2.2.2.jar", "spark-sql_2.10-2.2.2.jar"]
    names = {path.name for path in dependencies}
    if not all(name in names for name in required):
        raise RuntimeError("Missing unified Spark 2.2.2 runtime JARs")
    if any(name.startswith("spark-") and "_2.10-" in name and not name.endswith("-2.2.2.jar") for name in names):
        raise RuntimeError("Conflicting Spark dependency versions")
    output = root / "build-artifacts.json"
    data = {"format": "dizk-built-artifacts-v1", "jar_sha256": digest(jar),
            "jar": "target/dizk-1.0.jar",
            "dependencies": {path.name: digest(path) for path in dependencies},
            "author_files_unchanged": True, "java_class_version_max": 52}
    # Rechecking a build must not discard conflicting previous artifact identity.
    if output.exists() and json.loads(output.read_text(encoding="utf-8")) != data:
        raise RuntimeError("Built artifacts differ from previous identity record")
    output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Verified copied author files, Java 8 classes and " + str(len(dependencies)) + " dependencies")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        sys.exit(1)
