#!/usr/bin/env python3
"""Fail closed if an adapter run is incomplete, mismatched or unverified."""
from __future__ import print_function
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

COMMIT = "29fb0f78964cd6019029d20228ee0bee3184e64d"
MODULUS = "17855808334804902850260923831770255773646114952324966112694569107431857586177"
TIMINGS = ["input_prepare_seconds", "spark_start_seconds", "rdd_materialize_seconds",
           "r1cs_validate_seconds", "setup_seconds", "prove_seconds", "verify_seconds",
           "proof_system_seconds", "total_task_seconds"]
REQUIRED = ["run_id", "scope", "same_statement", "mode", "curve", "scalar_field_modulus",
            "requested_threads", "threads", "partitions", "repeat", "verified", "upstream_commit",
            "input_bundle_sha256", "header_sha256", "constraints_sha256", "assignment_sha256",
            "paillier_bits", "messages", "constraints", "variables", "primary_inputs", "relation_hash",
            "proof_sha256", "max_heap_bytes", "setup_query_a_count", "setup_query_b_count",
            "setup_query_h_count", "setup_delta_abc_count"] + TIMINGS
SCALE_REQUIRED = ["reference_paillier_bits", "multiplication_constraints", "linear_constraints", "generator_id"]


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", choices=("serial", "distributed"), required=True)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--partitions", type=int, required=True)
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument("--input-record")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--synthetic-smoke", action="store_true")
    modes.add_argument("--adapter-self-test", action="store_true")
    modes.add_argument("--constraint-scale", action="store_true")
    args = parser.parse_args()
    check(bool(args.input_record) != args.synthetic_smoke,
          "Use exactly one of --input-record / --synthetic-smoke")
    check(not args.adapter_self_test or (args.input_record and not args.synthetic_smoke),
          "Arithmetic self-test requires input record and cannot be synthetic smoke")
    check(not args.constraint_scale or bool(args.input_record), "Scale benchmark requires input record")
    path = Path(args.csv)
    check(path.is_file() and path.stat().st_size > 0, "Missing or empty results CSV")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        check(reader.fieldnames is not None and len(reader.fieldnames) == len(set(reader.fieldnames)),
              "Missing or duplicated CSV columns")
        check(all(name in reader.fieldnames for name in REQUIRED), "Missing required CSV columns")
        if args.constraint_scale:
            check(all(name in reader.fieldnames for name in SCALE_REQUIRED), "Missing scale CSV columns")
        rows = list(reader)
    check(len(rows) == args.repeat, "CSV row count does not equal requested repeats")
    seen = set()
    input_record = None
    if args.input_record:
        input_record = json.loads(Path(args.input_record).read_text(encoding="utf-8"))
        input_root = Path(input_record["directory"])
        for name, digest in input_record["sha256"].items():
            check(sha(input_root / name) == digest, "Input changed during run: " + name)
    for row in rows:
        check(None not in row and all(row.get(name) is not None for name in REQUIRED), "Malformed CSV row")
        rep = int(row["repeat"])
        check(1 <= rep <= args.repeat and rep not in seen, "Missing or duplicate repeat number")
        seen.add(rep)
        check(row["run_id"] == args.run_id and row["mode"] == args.mode, "Run ID or mode mismatch")
        check(row["upstream_commit"] == COMMIT, "Upstream commit mismatch")
        check(row["curve"] == "BN254b" and row["scalar_field_modulus"] == MODULUS,
              "Curve or scalar field mismatch")
        check(row["verified"] == "true", "Proof verification did not succeed")
        check(len(row["proof_sha256"]) == 64 and all(c in "0123456789abcdef" for c in row["proof_sha256"]),
              "Missing or invalid concrete proof digest")
        check(int(row["max_heap_bytes"]) > 0, "Invalid recorded Java heap")
        for name in ("setup_query_a_count", "setup_query_b_count", "setup_query_h_count", "setup_delta_abc_count"):
            check(int(row[name]) >= 0, "Invalid materialized setup query count")
        check(int(row["requested_threads"]) == args.threads, "Requested thread count mismatch")
        actual_threads = 1 if args.mode == "serial" else args.threads
        check(int(row["threads"]) == actual_threads, "Actual local parallelism mismatch")
        actual_partitions = 0 if args.mode == "serial" else args.partitions
        check(int(row["partitions"]) == actual_partitions, "Partition count mismatch")
        scope = "constraint_scale_benchmark" if args.constraint_scale else ("synthetic_smoke" if args.synthetic_smoke else ("arithmetic_self_test" if args.adapter_self_test else "v6_ring_lifted"))
        same_statement = "false" if (args.synthetic_smoke or args.adapter_self_test or args.constraint_scale) else "true"
        check(row["scope"] == scope and row["same_statement"] == same_statement, "Statement scope mismatch")
        times = {name: float(row[name]) for name in TIMINGS}
        check(all(math.isfinite(value) and value >= 0 for value in times.values()),
              "Timing is negative or non-finite")
        check(all(times[name] > 0 for name in ("setup_seconds", "prove_seconds", "verify_seconds")),
              "Proof stage has no positive measured elapsed time")
        stage_sum = times["setup_seconds"] + times["prove_seconds"] + times["verify_seconds"]
        check(abs(stage_sum - times["proof_system_seconds"]) <= max(1e-7, stage_sum * 1e-7),
              "proof_system_seconds differs from setup + prove + verify")
        check(times["total_task_seconds"] + 1e-6 >= times["proof_system_seconds"],
              "Total task time is shorter than measured proof stages")
        if input_record:
            keys = {"header.json": "header_sha256", "constraints.tsv": "constraints_sha256",
                    "assignment.tsv": "assignment_sha256"}
            for name, key in keys.items():
                check(row[key] == input_record["sha256"][name], "Input digest mismatch: " + name)
            check(row["input_bundle_sha256"] == input_record["input_bundle_sha256"], "Input bundle identity mismatch")
            header = input_record["header"]
            check(header["scope"] == scope and header["same_statement"] is (same_statement == "true"),
                  "Input snapshot scope does not match requested mode")
            for key in ("paillier_bits", "messages", "constraints", "variables", "primary_inputs"):
                check(int(row[key]) == header[key], "Header/CSV metadata mismatch: " + key)
            check(row["relation_hash"] == header["relation_hash"], "Relation identity mismatch")
            if args.constraint_scale:
                check(int(row["paillier_bits"]) == 0 and int(row["reference_paillier_bits"]) == 512,
                      "Scale CSV cannot claim an actual Paillier modulus")
                for key in ("reference_paillier_bits", "multiplication_constraints", "linear_constraints"):
                    check(int(row[key]) == header[key], "Scale header/CSV mismatch: " + key)
                check(int(row["multiplication_constraints"]) + int(row["linear_constraints"]) == int(row["constraints"]),
                      "Scale multiplication and linear counts do not sum to M")
                check(row["generator_id"] == header["generator_id"] == "field_chain_mix_v1", "Scale generator mismatch")
                check(int(row["variables"]) == int(row["constraints"]) + 3 and int(row["primary_inputs"]) == 2,
                      "Scale generated relation dimensions mismatch")
    check(seen == set(range(1, args.repeat + 1)), "Not all requested repetitions completed")
    print("Validated " + str(len(rows)) + " verified " + scope + " result rows")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        sys.exit(1)
