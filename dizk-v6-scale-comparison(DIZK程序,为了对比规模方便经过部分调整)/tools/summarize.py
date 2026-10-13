#!/usr/bin/env python3
"""Pair fresh verified native-v6 and DIZK timings; reject historical or incomplete data."""
from __future__ import print_function
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def positive(record, key):
    value = float(record[key])
    check(math.isfinite(value) and value > 0, "Invalid timing: " + key)
    return value


def pair(case, config, mc, repeat):
    raw, lifted, run = case / "native", case / "lifted", case / "dizk"
    check((run / "COMPLETE.txt").is_file(), "DIZK run is incomplete at " + str(case))
    check(not (lifted / ".incomplete").exists(), "Integer compilation is incomplete")
    native = json.loads((raw / "v6_native_timing.json").read_text(encoding="utf-8"))
    source = json.loads((raw / "header.json").read_text(encoding="utf-8"))
    header = json.loads((lifted / "header.json").read_text(encoding="utf-8"))
    snapshot = json.loads((run / "input-files.json").read_text(encoding="utf-8"))
    with (run / "results.csv").open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    check(len(rows) == 1, "Each paired instance requires exactly one DIZK row")
    dizk = rows[0]
    check(native.get("verified") is True and dizk["verified"] == "true", "Both proofs must verify")
    check(native.get("raw_public_statement_matched") is True and native.get("raw_export_same_circuit_and_witness") is True,
          "Native proof is not bound to exported circuit")
    check(header["same_statement"] is True and dizk["same_statement"] == "true" and dizk["scope"] == "v6_ring_lifted",
          "Self-test/synthetic data cannot be included")
    check(native["statement_id"] == source["statement_id"] == header["statement_id"], "Public statement mismatch")
    public_values, seen = [], set()
    with (raw / "linear.tsv").open("r", encoding="ascii") as f:
        for line in f:
            value = line.rstrip("\r\n").split("\t")[1]
            if value not in seen:
                seen.add(value); public_values.append(value)
    check(header["public_k_values"] == public_values, "Lifted public K vector differs from original public rows")
    raw_witness_hash = sha(raw / "gates.tsv")
    check(native["witness_id"] == source["witness_id"] == raw_witness_hash == header["source_gate_witness_sha256"],
          "Private witness provenance mismatch")
    check(header["source_header_sha256"] == sha(raw / "header.json") and
          header["source_linear_sha256"] == sha(raw / "linear.tsv"), "Native input changed")
    for name, expected in snapshot["sha256"].items():
        check(sha(lifted / name) == expected, "Lifted input changed: " + name)
    for name, key in (("header.json", "header_sha256"), ("constraints.tsv", "constraints_sha256"),
                      ("assignment.tsv", "assignment_sha256")):
        check(dizk[key] == sha(lifted / name), "CSV lifted digest mismatch")
    check(dizk["relation_hash"] == header["relation_hash"], "Different R1CS relation")
    check(dizk["input_bundle_sha256"] == snapshot["input_bundle_sha256"], "Input bundle differs from checked run")
    check(int(dizk["threads"]) == int(config["threads"]), "DIZK task slots differ")
    check(native["actual_bits"] == 512 and int(dizk["paillier_bits"]) == 512 and
          int(dizk["messages"]) == mc == native["messages"], "MC or bit count differs")
    check(native["threads_requested"] == config["threads"] and native["openmp_max_threads"] == config["threads"],
          "Native requested/OpenMP thread count differs")
    check(native["repeat"] == repeat and int(dizk["repeat"]) == 1, "Repeat metadata differs")
    check(dizk["run_id"] == config["run_id"] + "_MC{}r{}".format(mc, repeat) and dizk["mode"] == "distributed",
          "DIZK run identity or mode differs")
    native_prove, native_verify = positive(native, "proof_generation_seconds"), positive(native, "verify_seconds")
    dizk_prove, dizk_verify = positive(dizk, "prove_seconds"), positive(dizk, "verify_seconds")
    return {"messages": mc, "paillier_bits": 512, "repeat": repeat, "threads": config["threads"],
            "statement_id": header["statement_id"], "witness_id": raw_witness_hash,
            "original_gates": header["original_gates"], "original_linear_rows": header["original_linear_rows"],
            "lifted_constraints": header["constraints"], "lifted_variables": header["variables"],
            "v6_generator_setup_s": positive(native, "generator_setup_seconds"),
            "dizk_trusted_setup_s": positive(dizk, "setup_seconds"),
            "integer_lift_s": header["compiler_wall_seconds"],
            "v6_complete_proof_generation_s": native_prove, "dizk_complete_prover_s": dizk_prove,
            "dizk_over_v6_prover_time_ratio": dizk_prove / native_prove,
            "v6_verify_s": native_verify, "dizk_verify_s": dizk_verify,
            "dizk_over_v6_verify_time_ratio": dizk_verify / native_verify,
            "both_verified": True, "same_statement": True, "same_witness": True}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results", required=True)
    args = p.parse_args()
    root = Path(args.results).resolve()
    config = json.loads((root / "run-config.json").read_text(encoding="utf-8"))
    check(config["phase"] == "run", "Estimate-only run contains no performance comparison")
    rows = []
    for mc in config["messages"]:
        for repeat in range(1, config["repeat"] + 1):
            rows.append(pair(root / "MC{:03d}".format(mc) / "r{:02d}".format(repeat), config, mc, repeat))
    output = root / "comparison.csv"
    check(not output.exists(), "Summary already exists; it will not be overwritten")
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    notes = {"format": "strict-comparison-notes-v1", "verified_pairs": len(rows),
             "comparison_csv_sha256": sha(output),
             "units": "All elapsed times in seconds; ratios are DIZK elapsed / v6 elapsed, greater than 1 means DIZK took longer.",
             "scope": "Same full native CBatchEnc arithmetic statement and witness. Different proof protocols, security assumptions and setup requirements.",
             "timing": "v6 proof generation includes commit+Y+polyCommit+X+prove. DIZK prover includes its complete Groth16 prover. Export/lifting/input-validation/trusted setup are separate.",
             "limits": "Single repeat is a preliminary observation; VM CPU scheduling, JIT, GC, disk and memory affect results. No historical CSV is paired."}
    (root / "comparison-notes.json").write_text(json.dumps(notes, indent=2) + "\n", encoding="utf-8")
    print("All requested statements/witnesses verified: " + str(output))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        print("PAIRING_REFUSED: " + str(exc), file=sys.stderr)
        sys.exit(2)
