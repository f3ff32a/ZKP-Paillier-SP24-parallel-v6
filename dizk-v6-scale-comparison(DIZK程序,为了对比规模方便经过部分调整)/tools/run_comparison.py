#!/usr/bin/env python3
"""Ubuntu 18.04 full v6/DIZK comparison on the same Paillier statement."""
from __future__ import print_function
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from compile_ring_inputs import inspect, compile_relation

ROOT = Path(__file__).resolve().parents[1]
POINTS = (1, 10, 20, 40, 80, 100, 200, 400)


def command(args, log=None):
    print("Running: " + " ".join(str(x) for x in args), flush=True)
    if log:
        with log.open("w", encoding="utf-8") as f:
            subprocess.check_call([str(x) for x in args], stdout=f, stderr=subprocess.STDOUT)
    else:
        subprocess.check_call([str(x) for x in args])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=("estimate", "run"), default="estimate")
    p.add_argument("--output", required=True, help="new result directory; existing results are preserved")
    p.add_argument("--points", default="1,10,20,40,80,100,200,400")
    p.add_argument("--threads", type=int, default=16)
    p.add_argument("--partitions", type=int, default=16)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--heap-gb", type=int, default=16)
    p.add_argument("--max-constraints", type=int, default=10000000,
                   help="stop before lifting above a chosen job budget; raise after reviewing estimates")
    p.add_argument("--prepared", default=str(ROOT / "build" / "dizk"))
    p.add_argument("--v6-source", default=str(ROOT / "vendor" / "v6"))
    args = p.parse_args()
    points = [int(x) for x in args.points.split(",")]
    if not points or len(points) != len(set(points)) or any(x not in POINTS for x in points):
        p.error("points must be distinct members of 1,10,20,40,80,100,200,400")
    if min(args.threads, args.partitions, args.repeat, args.heap_gb, args.max_constraints) < 1:
        p.error("counts, memory and job budget must be positive")
    if args.partitions & (args.partitions - 1):
        p.error("author fillRDD requires power-of-two partitions")
    if os.name != "posix":
        p.error("formal v6 comparison must be run on Linux; Windows is used only for adapter self-tests")
    out = Path(args.output).resolve()
    if out.exists():
        p.error("result directory already exists; choose a new --output")
    v6 = Path(args.v6_source).resolve()
    prepared = Path(args.prepared).resolve()
    if not (v6 / "src" / "cpp" / "app" / "CBatchEnc.cpp").is_file():
        p.error("v6 source is missing")
    if args.phase == "run":
        if not prepared.exists():
            command(["bash", ROOT / "runtime" / "scripts" / "build.sh", "--upstream", ROOT / "vendor" / "dizk",
                     "--adapter", ROOT / "adapter", "--dest", prepared])
        command([sys.executable, ROOT / "runtime" / "tools" / "check_build.py", "--prepared", prepared])
    out.mkdir(parents=True)
    run_id = "strict512_" + datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    (out / "run-config.json").write_text(json.dumps({"run_id": run_id, "phase": args.phase,
        "messages": points, "bits": 512, "threads": args.threads, "partitions": args.partitions,
        "repeat": args.repeat, "heap_gb": args.heap_gb, "max_constraints": args.max_constraints,
        "prepared": str(prepared), "v6_source": str(v6), "same_statement_required": True}, indent=2) + "\n", encoding="utf-8")
    command([sys.executable, ROOT / "runtime" / "tools" / "environment.py", "--output", out / "environment.json"])
    estimates = []
    for mc in points:
        for rep in range(1, args.repeat + 1):
            case = out / ("MC{:03d}".format(mc)) / ("r{:02d}".format(rep))
            case.mkdir(parents=True)
            raw = case / "native"
            export = [sys.executable, ROOT / "native" / "build_and_export.py", "--v6-source", v6,
                      "--output", raw, "--bits", "512", "--messages", str(mc), "--threads", str(args.threads),
                      "--repeat", str(rep)]
            if args.phase == "run":
                export.append("--run-v6-proof")
            command(export, case / "export-command.log")
            command([sys.executable, ROOT / "native" / "validate_raw.py", "--input", raw], case / "raw-validation.log")
            meta, public, estimate = inspect(raw, 64)
            estimate.update({"messages": mc, "repeat": rep})
            estimates.append(estimate)
            (case / "estimate.json").write_text(json.dumps(estimate, indent=2) + "\n", encoding="utf-8")
            (out / "resource-estimates.json").write_text(json.dumps(estimates, indent=2) + "\n", encoding="utf-8")
            print("MC={} repeat={} estimated constraints <= {:,}; text disk ~{} GB; Java planning heap ~{} GB".format(
                mc, rep, estimate["estimated_constraints_upper"], estimate["estimated_text_disk_gb_range"],
                estimate["estimated_java_memory_gb_range"]), flush=True)
            if args.phase == "estimate":
                continue
            if estimate["estimated_constraints_upper"] > args.max_constraints:
                raise RuntimeError("Job exceeds --max-constraints budget. Estimate and same-instance v6 baseline are saved. "
                                   "Review disk/heap capacity and rerun to a fresh directory with an explicit higher budget; "
                                   "no DIZK proof or ratio is reported for this case.")
            lifted = case / "lifted"
            header = compile_relation(raw, lifted, progress=True)
            if header["statement_id"] != json.loads((raw / "v6_native_timing.json").read_text(encoding="utf-8"))["statement_id"]:
                raise RuntimeError("native and lifted statement IDs differ")
            command(["bash", ROOT / "runtime" / "scripts" / "run.sh", "--prepared", prepared, "--input", lifted,
                     "--output", case / "dizk", "--run-id", run_id + "_MC{}r{}".format(mc, rep),
                     "--mode", "distributed", "--threads", str(args.threads), "--partitions", str(args.partitions),
                     "--repeat", "1", "--heap-gb", str(args.heap_gb)], case / "dizk-command.log")
    if args.phase == "run":
        command([sys.executable, ROOT / "tools" / "summarize.py", "--results", out])
        (out / "COMPLETE.txt").write_text("All requested points verified and paired on the same public statement and witness.\n", encoding="ascii")
    else:
        (out / "ESTIMATES_COMPLETE.txt").write_text("Resource estimates only; no DIZK performance results.\n", encoding="ascii")
    print("Saved " + str(out))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print("COMPARISON_STOPPED: " + str(exc), file=sys.stderr)
        sys.exit(2)
