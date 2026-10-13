#!/usr/bin/env python3
"""Plan or run real DIZK proofs at the archived v6 N+Q constraint scale.

Python 3.6 standard library. No original Paillier source/proofs are modified.
"""
from __future__ import print_function
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from summarize_scale import (POINTS, SCOPE, GENERATOR, read_reference, reference_for,
                             save_json, sha, summarize, check)

ROOT = Path(__file__).resolve().parents[1]


def command(argv, log=None):
    argv = [str(x) for x in argv]
    print("Running: " + " ".join(argv), flush=True)
    if log is None:
        subprocess.check_call(argv)
    else:
        with Path(log).open("w", encoding="utf-8") as f:
            subprocess.check_call(argv, stdout=f, stderr=subprocess.STDOUT)


def memory_check(heap_gib, meminfo_path=Path("/proc/meminfo")):
    values = {}
    for line in Path(meminfo_path).read_text(encoding="ascii").splitlines():
        fields = line.split()
        if fields and fields[0] == "MemAvailable:" and len(fields) >= 3 and fields[2] == "kB":
            values["available_bytes"] = int(fields[1]) * 1024
    check("available_bytes" in values, "Linux /proc/meminfo lacks MemAvailable")
    required = (heap_gib + 4) * (1 << 30)
    check(values["available_bytes"] >= required,
          "MemAvailable {:.2f} GiB is below heap {} GiB + 4 GiB reserve; choose resources/settings explicitly".format(values["available_bytes"] / float(1 << 30), heap_gib))
    return dict(values, heap_gib=heap_gib, reserve_gib=4, required_available_bytes=required,
                note="Preflight capacity check only; it does not guarantee setup/proof completion")


def verify_adapter_identity(prepared, adapter):
    manifest = json.loads((prepared / "comparison-build-manifest.json").read_text(encoding="utf-8"))
    actual = {p.relative_to(adapter).as_posix(): sha(p)
              for p in (adapter / "src" / "main" / "java" / "comparison").rglob("*.java")}
    check(actual and actual == manifest["adapter_files"],
          "Prepared build does not match this scale adapter source; use a fresh build/dizk-scale, not the old strict build")
    text = (adapter / "src" / "main" / "java" / "comparison" / "DizkComparison.java").read_text(encoding="utf-8")
    check(SCOPE in text and "--constraint-scale" in text, "Current Java adapter lacks explicit scale benchmark support")
    return {"manifest_sha256": sha(prepared / "comparison-build-manifest.json"), "adapter_files": actual,
            "prepared_directory": str(prepared), "matches_current_scale_adapter": True}


def plan_records(refs, args, points):
    rows = []
    for mc in points:
        for repeat in range(1, args.repeat + 1):
            r = reference_for(refs, mc, args.threads, repeat)
            rows.append({"messages": mc, "repeat": repeat, "reference_paillier_bits": 512,
                "paillier_bits": 0, "multiplication_constraints": r["multiplication_constraints"],
                "linear_constraints": r["linear_constraints"], "constraints": r["constraints"],
                "within_constraint_budget": r["constraints"] <= args.max_constraints,
                "v6_verification": r["verification"], "v6_metadata_source": r["metadata_source"],
                "v6_thread_metadata_source": r["thread_metadata_source"]})
    return rows


def run_cases(out, config, refs, args, prepared):
    try:
        command([sys.executable, ROOT / "runtime" / "tools" / "environment.py", "--output", out / "environment.json"])
        summarize(out)
        for mc in config["messages"]:
            for repeat in range(1, args.repeat + 1):
                ref = reference_for(refs, mc, args.threads, repeat)
                case = out / "MC{:03d}".format(mc) / "r{:02d}".format(repeat)
                case_start = time.monotonic()
                case.mkdir(parents=True)
                process_times = {"format": "constraint-scale-process-timing-v1", "status": "failed_before_generator",
                    "generator_wall_seconds": None, "dizk_launcher_wall_seconds": None, "case_wall_seconds": None,
                    "scope_note": "Monotonic driver wall times. Generator includes its Python subprocess; launcher includes full runtime shell checks/Java subprocess; case excludes summary writing. Neither launcher nor case is pure prover time."}
                try:
                    save_json(case / "memory-preflight.json", memory_check(args.heap_gb))
                    check(ref["constraints"] <= args.max_constraints,
                          "MC={} N+Q={} exceeds --max-constraints {}; earlier verified cases remain saved".format(mc, ref["constraints"], args.max_constraints))
                    process_times["status"] = "failed_during_generator"
                    generator_start = time.monotonic()
                    try:
                        command([sys.executable, ROOT / "tools" / "generate_scale_inputs.py", "--output", case / "input",
                            "--messages", mc, "--multiplication-constraints", ref["multiplication_constraints"],
                            "--linear-constraints", ref["linear_constraints"],
                            "--reference-csv-sha256", config["reference_csv_sha256"]], case / "generator-command.log")
                    finally:
                        process_times["generator_wall_seconds"] = time.monotonic() - generator_start
                    run = ["bash", ROOT / "runtime" / "scripts" / "run.sh", "--constraint-scale", "--prepared", prepared,
                        "--input", case / "input", "--output", case / "dizk", "--run-id", config["run_id"] + "_MC{}r{}".format(mc, repeat),
                        "--mode", "distributed", "--threads", args.threads, "--partitions", args.partitions,
                        "--repeat", 1, "--heap-gb", args.heap_gb]
                    gnu_time = Path("/usr/bin/time")
                    if gnu_time.is_file() and os.access(str(gnu_time), os.X_OK):
                        run = [gnu_time, "-f", "%M", "-o", case / "dizk-maxrss-kib.txt"] + run
                    process_times["status"] = "failed_during_dizk_launcher"
                    launcher_start = time.monotonic()
                    try:
                        command(run, case / "dizk-command.log")
                    finally:
                        process_times["dizk_launcher_wall_seconds"] = time.monotonic() - launcher_start
                    process_times["status"] = "launcher_completed"
                finally:
                    process_times["case_wall_seconds"] = time.monotonic() - case_start
                    save_json(case / "process-timing.json", process_times)
                result = summarize(out)
                current = next(x for x in result["cases"] if x["messages"] == mc and x["repeat"] == repeat)
                check(current["status"] == "verified_scale_result", "Completed runtime result failed summary guards: " + current["detail"])
        result = summarize(out)
        check(result["complete"], "Missing verified scale cases; partial results preserved")
        (out / "SCALE_BENCHMARK_COMPLETE.txt").write_text(
            "All requested constraint-scale proofs verified. scope=constraint_scale_benchmark same_statement=false scale_only=true archived_v6_baseline\n", encoding="ascii")
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        save_json(out / "RUN_STOPPED.json", {"scope": SCOPE, "same_statement": False, "error": str(exc),
            "automatic_retry": False, "completed_cases_preserved": True})
        try:
            summarize(out)
        except (ValueError, OSError, KeyError) as summary_error:
            save_json(out / "SUMMARY_STOPPED.json", {"error": str(summary_error)})
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=("plan", "run"), default="plan")
    p.add_argument("--output", required=True, help="new directory; no automatic resumption or overwrite")
    p.add_argument("--v6-csv", default=str(ROOT / "reference" / "v6_full_t16_r1.csv"))
    p.add_argument("--v6-provenance", help="optional hash-bound verification/configuration evidence for a custom CSV")
    p.add_argument("--points", default=",".join(str(x) for x in POINTS))
    p.add_argument("--threads", type=int, default=16)
    p.add_argument("--partitions", type=int, default=16)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--reference-threads", type=int, default=16, help="declared label only if custom CSV/evidence omits threads")
    p.add_argument("--reference-repeat", type=int, default=1, help="declared label only if custom CSV/evidence omits repeat")
    p.add_argument("--heap-gb", type=int, default=12, help="Java -Xmx g suffix, actually GiB")
    p.add_argument("--max-constraints", type=int, default=2000000)
    p.add_argument("--prepared", default=str(ROOT / "build" / "dizk-scale"))
    args = p.parse_args()
    try:
        points = [int(x) for x in args.points.split(",")]
    except ValueError:
        p.error("points must be integers")
    if not points or len(points) != len(set(points)) or any(x not in POINTS for x in points):
        p.error("points must be distinct members of 1,10,20,40,80,100,200,400")
    if min(args.threads, args.partitions, args.repeat, args.heap_gb, args.max_constraints, args.reference_threads, args.reference_repeat) < 1:
        p.error("counts, memory and budget must be positive")
    if args.partitions & (args.partitions - 1):
        p.error("author implementation requires power-of-two partitions")
    out = Path(args.output).resolve()
    if out.exists():
        p.error("output already exists; choose a new --output")
    source = Path(args.v6_csv).resolve()
    default_csv = (ROOT / "reference" / "v6_full_t16_r1.csv").resolve()
    provenance = Path(args.v6_provenance).resolve() if args.v6_provenance else (
        ROOT / "reference" / "provenance.json" if source == default_csv else None)
    if args.v6_provenance and not provenance.is_file():
        p.error("explicit --v6-provenance does not exist")
    refs, evidence = read_reference(source, provenance, args.reference_threads, args.reference_repeat)
    planned = plan_records(refs, args, points)
    plan = {"format": "constraint-scale-plan-v1", "phase": args.phase, "scope": SCOPE,
        "same_statement": False, "scale_only": True, "generator_id": GENERATOR,
        "baseline_kind": "archived_v6_baseline", "messages": points, "threads": args.threads,
        "partitions": args.partitions, "repeat": args.repeat, "heap_gib": args.heap_gb,
        "heap_gb_cli_uses_binary_gib": True, "max_constraints": args.max_constraints,
        "reference_csv_absolute_path": str(source), "reference_csv_sha256": sha(source),
        "reference_provenance_source_sha256": sha(provenance) if evidence else None,
        "reference_provenance": evidence, "cases": planned,
        "count_definition": "M=N+Q: original v6 multiplication gates plus linear rows; no non-native integer lift",
        "claim_boundary": "Scale comparison only; no same Paillier statement, witness, operations or security claim",
        "process_resource_reuse": "Cases run sequentially in separate Java processes; no automatic resource/constraint/thread changes"}
    if args.phase == "plan":
        out.mkdir(parents=True)
        save_json(out / "scale-plan.json", plan)
        print("Saved plan only: " + str(out / "scale-plan.json"))
        return
    check(platform.system() == "Linux", "Formal scale proofs must run on Linux; plan and unit tests may run on Windows")
    mem = memory_check(args.heap_gb)
    prepared = Path(args.prepared).resolve()
    if not prepared.exists():
        command(["bash", ROOT / "runtime" / "scripts" / "build.sh", "--upstream", ROOT / "vendor" / "dizk",
                 "--adapter", ROOT / "adapter", "--dest", prepared])
    command([sys.executable, ROOT / "runtime" / "tools" / "check_build.py", "--prepared", prepared])
    guard = verify_adapter_identity(prepared, ROOT / "adapter")
    out.mkdir(parents=True)
    shutil.copyfile(str(source), str(out / "v6-reference.csv"))
    check(sha(out / "v6-reference.csv") == plan["reference_csv_sha256"] == sha(source), "Reference CSV changed during copying")
    if evidence:
        save_json(out / "v6-provenance.json", evidence)
    config = dict(plan, run_id="scale512_" + datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"),
        reference_threads=args.reference_threads, reference_repeat=args.reference_repeat,
        reference_provenance_sha256=sha(out / "v6-provenance.json") if evidence else None,
        prepared=str(prepared), adapter_build_identity=guard, memory_preflight=mem)
    save_json(out / "run-config.json", config)
    save_json(out / "scale-plan.json", plan)
    run_cases(out, config, refs, args, prepared)
    print("Saved verified scale benchmark: " + str(out))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        print("SCALE_BENCHMARK_STOPPED: " + str(exc), file=sys.stderr)
        sys.exit(2)
