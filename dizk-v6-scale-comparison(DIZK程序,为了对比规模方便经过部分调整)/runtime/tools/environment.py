#!/usr/bin/env python3
"""Diagnose Java 8 / Maven and save the actual local environment."""
from __future__ import print_function

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys


def command(argv):
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        data = p.communicate()[0].decode("utf-8", errors="replace")
        return {"argv": argv, "exit_code": p.returncode,
                "output": re.sub(r"\x1b\[[0-9;]*m", "", data).strip()}
    except OSError as exc:
        return {"argv": argv, "exit_code": None, "output": str(exc)}


def selected_java(name):
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        path = Path(java_home) / "bin" / name
        if os.name == "nt":
            path = path.with_suffix(".exe")
        return str(path)
    return shutil.which(name) or name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--require-maven", action="store_true")
    args = parser.parse_args()
    java = command([selected_java("java"), "-version"])
    javac = command([selected_java("javac"), "-version"])
    maven_path = os.environ.get("MAVEN_BIN") or shutil.which("mvn") or "mvn"
    maven = command([maven_path, "-version"])
    os_release = ""
    release_file = Path("/etc/os-release")
    if release_file.is_file():
        os_release = release_file.read_text(encoding="utf-8", errors="replace")
    cpuinfo = ""
    cpu_file = Path("/proc/cpuinfo")
    if cpu_file.is_file():
        cpuinfo = cpu_file.read_text(encoding="utf-8", errors="replace")
    meminfo = ""
    mem_file = Path("/proc/meminfo")
    if mem_file.is_file():
        meminfo = mem_file.read_text(encoding="utf-8", errors="replace")
    java8 = java["exit_code"] == 0 and bool(re.search(r'version "(?:1\.8\.|8(?:\.|"))', java["output"]))
    javac8 = javac["exit_code"] == 0 and bool(re.search(r'javac (?:1\.8\.|8(?:\.|\s|$))', javac["output"]))
    mvn_java8 = bool(re.search(r'Java version: (?:1\.8\.|8(?:\.|,|\s|$))', maven["output"]))
    errors = []
    if sys.version_info < (3, 6):
        errors.append("Python 3.6 or newer is required")
    if not java8 or not javac8:
        errors.append("Select a complete JDK 8 with JAVA_HOME and PATH; this Spark 2.2.2 runtime requires Java 8")
    if args.require_maven and (maven["exit_code"] != 0 or not mvn_java8):
        errors.append("Maven must be available and must report Java version 1.8/8; set MAVEN_BIN if necessary")
    result = {"format": "dizk-local-environment-v1", "python": sys.version,
              "platform": platform.platform(), "os_release": os_release,
              "visible_cpu_count": os.cpu_count(), "cpuinfo": cpuinfo, "meminfo": meminfo,
              "java_home": os.environ.get("JAVA_HOME"), "java": java,
              "javac": javac, "maven": maven, "errors": errors,
              "spark_version": "2.2.2", "scala_binary_version": "2.10"}
    if args.output:
        path = Path(args.output)
        if path.exists():
            raise RuntimeError("Environment output already exists: " + str(path))
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Java: " + java["output"].split("\n")[0])
    print("Javac: " + javac["output"].split("\n")[0])
    maven_summary = next((line for line in maven["output"].split("\n") if "Apache Maven " in line),
                         maven["output"].split("\n")[0])
    print("Maven: " + maven_summary)
    print("Visible CPUs: " + str(os.cpu_count()))
    for error in errors:
        print("ERROR: " + error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        sys.exit(1)
