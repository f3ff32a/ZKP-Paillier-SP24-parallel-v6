#!/usr/bin/env python3
"""Compare measured DIZK constraint-scale timings with an archived v6 baseline.

This is deliberately not a same-statement or same-witness comparison.
Python 3.6 standard library only.
"""
from __future__ import print_function
import argparse
import csv
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import sys

SCOPE = "constraint_scale_benchmark"
GENERATOR = "field_chain_mix_v1"
FR = "17855808334804902850260923831770255773646114952324966112694569107431857586177"
POINTS = (1, 10, 20, 40, 80, 100, 200, 400)
TIMINGS = ("input_prepare_seconds", "spark_start_seconds", "rdd_materialize_seconds",
           "r1cs_validate_seconds", "setup_seconds", "prove_seconds", "verify_seconds",
           "proof_system_seconds", "total_task_seconds")
PROCESS_TIMINGS = ("generator_wall_seconds", "dizk_launcher_wall_seconds", "case_wall_seconds")
FIELDS = ("messages", "reference_paillier_bits", "paillier_bits", "threads", "partitions",
          "repeat", "reference_repeat", "dizk_invocation_repeat", "multiplication_constraints",
          "linear_constraints", "constraints", "variables", "primary_inputs", "reference_csv_sha256",
          "scope", "same_statement", "scale_only", "baseline_kind", "generator_id",
          "v6_verification", "v6_metadata_source", "v6_thread_metadata_source", "dizk_verified", "v6_commit_seconds",
          "v6_prove_seconds", "v6_commit_plus_prove_seconds", "v6_verify_seconds") + TIMINGS + (
          "dizk_over_archived_v6_prover_time_ratio", "maxrss_kib", "maxrss_note") + PROCESS_TIMINGS + (
          "process_timing_status", "process_timing_note", "header_sha256",
          "constraints_sha256", "assignment_sha256", "input_bundle_sha256", "proof_sha256")


def check(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def save_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_reference(path, provenance_path=None, declared_threads=16, declared_repeat=1):
    path = Path(path)
    digest = sha(path)
    provenance = {}
    if provenance_path and Path(provenance_path).is_file():
        provenance = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
        recorded = provenance.get("csv_sha256", provenance.get("reference_csv_sha256"))
        check(recorded == digest, "Reference provenance CSV SHA-256 mismatch")
    records = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        check(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)), "Duplicate/missing reference columns")
        for row in reader:
            check(None not in row and all(v is not None for v in row.values()), "Malformed reference CSV row")
            mc = int(row.get("message count", row.get("messages", "0")))
            bits = int(row.get("actual_bits", row.get("byte length(bit)", row.get("paillier_bits", "0"))))
            n = int(row.get("batch encrypt circuit's N", row.get("multiplication_constraints", "0")))
            q = int(row.get("batch encrypt circuit's Q", row.get("linear_constraints", "0")))
            check(mc in POINTS and bits == 512 and n > 0 and q > 0, "Reference requires a supported MC, 512-bit label and positive N/Q")
            thread_key = next((k for k in ("actual_threads", "threads") if row.get(k)), None)
            threads = int(row[thread_key]) if thread_key else int(provenance.get("threads", declared_threads))
            rep = int(row.get("repeat") or provenance.get("repeat", declared_repeat))
            for key in ("actual_threads", "threads"):
                if row.get(key):
                    check(int(row[key]) == threads, "Contradictory thread fields in reference CSV")
            if "threads" in provenance:
                check(int(provenance["threads"]) == threads, "Reference CSV/provenance thread metadata conflict")
            if "repeat" in provenance:
                check(int(provenance["repeat"]) == rep, "Reference CSV/provenance repeat metadata conflict")
            if "actual_paillier_bits" in provenance:
                check(int(provenance["actual_paillier_bits"]) == bits, "Reference CSV/provenance bit metadata conflict")
            for key in ("actual_bits", "byte length(bit)", "paillier_bits"):
                if row.get(key):
                    check(int(row[key]) == bits, "Contradictory bit fields in reference CSV")
            check(threads > 0 and rep > 0, "Reference thread/repeat labels must be positive")
            source = "csv" if thread_key else ("hash_bound_provenance" if "threads" in provenance else "driver_declared")
            metadata_source = source if (row.get("repeat") or "repeat" in provenance) else "driver_declared"
            try:
                commit = Decimal(row.get("commit time", row.get("commit_seconds", "NaN")))
                prove = Decimal(row.get("prove time", row.get("prove_seconds", "NaN")))
                verify = Decimal(row.get("verify time", row.get("verify_seconds", "NaN")))
            except ArithmeticError as exc:
                raise ValueError("Malformed reference stage time") from exc
            check(all(x.is_finite() and x > 0 for x in (commit, prove, verify)), "Reference stages must be positive finite seconds")
            converted = [float(x) for x in (commit, prove, verify, commit + prove)]
            check(all(math.isfinite(x) and x > 0 for x in converted), "Reference stages cannot be represented as positive finite elapsed seconds")
            verified_points = provenance.get("verified_message_counts", provenance.get("verified_messages", []))
            verification = "unknown"
            if row.get("verified", "").lower() == "false":
                verification = "false"
            elif provenance.get("full_zkp_verified") is True and mc in verified_points:
                check(int(provenance.get("actual_paillier_bits", 0)) == bits, "Verified provenance actual bit count differs from CSV label")
                verification = "true"
            key = (mc, threads, rep, bits)
            check(key not in records, "Duplicate reference MC/threads/repeat/bits key")
            records[key] = {"messages": mc, "reference_paillier_bits": bits, "threads": threads,
                "repeat": rep, "multiplication_constraints": n, "linear_constraints": q,
                "constraints": n + q, "commit_seconds": float(commit), "prove_seconds": float(prove),
                "commit_plus_prove_seconds": float(commit + prove), "verify_seconds": float(verify),
                "verification": verification, "metadata_source": metadata_source,
                "thread_metadata_source": source, "csv_sha256": digest}
    check(records, "Empty reference CSV")
    return records, provenance


def reference_for(records, mc, threads, repeat):
    key = (mc, threads, repeat, 512)
    check(key in records, "No reference row matching MC={}, threads={}, repeat={}, bits=512".format(mc, threads, repeat))
    result = records[key]
    check(result["verification"] != "false", "Reference explicitly reports failed verification")
    return result


def process_timing(case):
    path = case / "process-timing.json"
    note = ("Monotonic process wall time: generator is its Python subprocess; launcher is the whole runtime shell subprocess including checks; "
            "case covers driver preflight/generation/launcher through launcher return, excluding summary writing. These are not pure prover timers.")
    if not path.is_file():
        return dict({key: None for key in PROCESS_TIMINGS}, process_timing_status="not_recorded", process_timing_note=note)
    record = json.loads(path.read_text(encoding="utf-8"))
    values = {key: record[key] for key in PROCESS_TIMINGS}
    check(all(v is None or (not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and v >= 0)
              for v in values.values()), "Invalid process wall time")
    check(values["case_wall_seconds"] is not None, "Case process wall time missing")
    measured_sum = sum(values[key] or 0 for key in PROCESS_TIMINGS[:2])
    check(values["case_wall_seconds"] + 1e-6 >= measured_sum, "Case wall time shorter than measured subprocesses")
    return dict(values, process_timing_status=record["status"], process_timing_note=note)


def validate_case(case, config, reference, mc, repeat):
    inp, run = case / "input", case / "dizk"
    check((run / "SCALE_BENCHMARK_COMPLETE.txt").is_file(), "Scale proof completion marker missing")
    check((run / "exit-code.txt").read_text(encoding="ascii").strip() == "0", "Runtime Java exit code is not zero")
    check(not (inp / ".incomplete").exists(), "Generator input incomplete")
    header = json.loads((inp / "header.json").read_text(encoding="utf-8"))
    snapshot = json.loads((run / "input-files.json").read_text(encoding="utf-8"))
    check(snapshot["header"] == header, "Input snapshot/header differ")
    hashes = {name: sha(inp / name) for name in ("header.json", "constraints.tsv", "assignment.tsv")}
    check(hashes == snapshot["sha256"], "Actual generated input SHA-256 differs from run snapshot")
    bundle = "".join(name + "\0" + hashes[name] + "\n" for name in ("header.json", "constraints.tsv", "assignment.tsv"))
    bundle_hash = hashlib.sha256(bundle.encode("utf-8")).hexdigest()
    check(snapshot["input_bundle_sha256"] == bundle_hash, "Snapshot bundle hash is not reproducible")
    check(header["scope"] == SCOPE and header["same_statement"] is False and header["paillier_bits"] == 0,
          "Input is not explicitly a field-only scale benchmark")
    check(header["format"] == "dizk-r1cs-v2" and str(header["field_modulus"]) == FR, "Input format/BN254b domain mismatch")
    check(header["generator_id"] == GENERATOR, "Different scale generator")
    check(header["reference_csv_sha256"] == config["reference_csv_sha256"] == reference["csv_sha256"], "Reference CSV identity mismatch")
    for key in ("messages", "reference_paillier_bits", "multiplication_constraints", "linear_constraints", "constraints"):
        check(header[key] == reference[key], "Input/reference metadata mismatch: " + key)
    check(header["constraints"] == header["multiplication_constraints"] + header["linear_constraints"], "M must equal N+Q")
    check(header["variables"] == header["constraints"] + 3 and header["primary_inputs"] == 2, "Scale generator variable/public-input shape differs")
    check(header["relation_hash"] == hashes["constraints.tsv"] and header["assignment_hash"] == hashes["assignment.tsv"], "Generated relation/assignment digest mismatch")
    with (run / "results.csv").open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        check(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)), "Malformed DIZK CSV columns")
        rows = list(reader)
    check(len(rows) == 1 and None not in rows[0] and all(v is not None for v in rows[0].values()), "Each invocation must contain exactly one complete DIZK row")
    row = rows[0]
    check(row["verified"] == "true" and row["scope"] == SCOPE and row["same_statement"] == "false", "DIZK proof/scope mismatch")
    check(row["mode"] == "distributed" and row["run_id"] == config["run_id"] + "_MC{}r{}".format(mc, repeat), "Run mode/identity mismatch")
    check(row["curve"] == "BN254b" and row["scalar_field_modulus"] == FR, "Result field mismatch")
    check(row["upstream_commit"] == "29fb0f78964cd6019029d20228ee0bee3184e64d", "Author source version differs")
    check(int(row["threads"]) == config["threads"] == reference["threads"] and int(row["requested_threads"]) == config["threads"], "Thread labels differ")
    check(int(row["partitions"]) == config["partitions"] and int(row["repeat"]) == 1, "Partitions/invocation repeat mismatch")
    check(reference["repeat"] == repeat, "Archived reference repeat mismatch")
    for key in ("messages", "paillier_bits", "reference_paillier_bits", "multiplication_constraints", "linear_constraints", "constraints", "variables", "primary_inputs"):
        check(int(row[key]) == header[key], "CSV/header metadata mismatch: " + key)
    check(row["generator_id"] == GENERATOR and row["relation_hash"] == header["relation_hash"], "Result generator/relation mismatch")
    for name, key in (("header.json", "header_sha256"), ("constraints.tsv", "constraints_sha256"), ("assignment.tsv", "assignment_sha256")):
        check(row[key] == hashes[name], "CSV actual input SHA mismatch: " + name)
    check(row["input_bundle_sha256"] == bundle_hash, "CSV bundle hash mismatch")
    check(len(row["proof_sha256"]) == 64 and all(c in "0123456789abcdef" for c in row["proof_sha256"]), "Concrete proof digest missing")
    times = {key: float(row[key]) for key in TIMINGS}
    check(all(math.isfinite(v) and v >= 0 for v in times.values()), "Nonfinite/negative elapsed time")
    check(all(times[k] > 0 for k in ("setup_seconds", "prove_seconds", "verify_seconds")), "Unmeasured proof stage")
    stage_sum = sum(times[k] for k in ("setup_seconds", "prove_seconds", "verify_seconds"))
    check(abs(stage_sum - times["proof_system_seconds"]) <= max(1e-7, stage_sum * 1e-7), "Proof stages do not sum")
    check(times["total_task_seconds"] + 1e-6 >= stage_sum, "Task sum shorter than proof stages")
    rss_path = case / "dizk-maxrss-kib.txt"
    rss = None
    if rss_path.is_file():
        text = rss_path.read_text(encoding="ascii").strip()
        if text.isdigit() and int(text) > 0:
            rss = int(text)
    result = {"messages": mc, "reference_paillier_bits": 512, "paillier_bits": 0,
        "threads": config["threads"], "partitions": config["partitions"], "repeat": repeat,
        "reference_repeat": reference["repeat"], "dizk_invocation_repeat": 1,
        "multiplication_constraints": header["multiplication_constraints"], "linear_constraints": header["linear_constraints"],
        "constraints": header["constraints"], "variables": header["variables"], "primary_inputs": header["primary_inputs"],
        "reference_csv_sha256": reference["csv_sha256"], "scope": SCOPE, "same_statement": False,
        "scale_only": True, "baseline_kind": "archived_v6_baseline", "generator_id": GENERATOR,
        "v6_verification": reference["verification"], "v6_metadata_source": reference["metadata_source"],
        "v6_thread_metadata_source": reference["thread_metadata_source"],
        "dizk_verified": True, "v6_commit_seconds": reference["commit_seconds"], "v6_prove_seconds": reference["prove_seconds"],
        "v6_commit_plus_prove_seconds": reference["commit_plus_prove_seconds"], "v6_verify_seconds": reference["verify_seconds"],
        "dizk_over_archived_v6_prover_time_ratio": times["prove_seconds"] / reference["commit_plus_prove_seconds"],
        "maxrss_kib": rss, "maxrss_note": "GNU time peak RSS of launched script/Java children; not Java heap or whole-machine memory" if rss else "GNU time unavailable or no valid RSS record; proof timing remains usable",
        "header_sha256": hashes["header.json"], "constraints_sha256": hashes["constraints.tsv"], "assignment_sha256": hashes["assignment.tsv"],
        "input_bundle_sha256": bundle_hash, "proof_sha256": row["proof_sha256"]}
    result.update(times)
    result.update(process_timing(case))
    return result


def summarize(root):
    root = Path(root)
    config = json.loads((root / "run-config.json").read_text(encoding="utf-8"))
    check(config["phase"] == "run" and config["scope"] == SCOPE and config["same_statement"] is False, "Not a scale benchmark run")
    source = root / "v6-reference.csv"
    check(sha(source) == config["reference_csv_sha256"], "Saved reference CSV changed")
    prov = root / "v6-provenance.json"
    if config.get("reference_provenance_sha256"):
        check(prov.is_file() and sha(prov) == config["reference_provenance_sha256"], "Saved reference provenance changed")
    refs, evidence = read_reference(source, prov if prov.is_file() else None,
        config.get("reference_threads", 16), config.get("reference_repeat", 1))
    rows, status = [], []
    for mc in config["messages"]:
        for repeat in range(1, config["repeat"] + 1):
            case = root / "MC{:03d}".format(mc) / "r{:02d}".format(repeat)
            record = {"messages": mc, "repeat": repeat}
            try:
                record["process_timings"] = process_timing(case)
            except (ValueError, KeyError, OSError) as exc:
                record["process_timing_error"] = str(exc)
            if not (case / "dizk" / "SCALE_BENCHMARK_COMPLETE.txt").is_file():
                record.update(status="incomplete", detail="No verified scale completion marker")
            else:
                try:
                    rows.append(validate_case(case, config, reference_for(refs, mc, config["threads"], repeat), mc, repeat))
                    record.update(status="verified_scale_result", detail="Counts and source identity matched; same_statement=false")
                except (ValueError, KeyError, OSError) as exc:
                    record.update(status="invalid", detail=str(exc))
            status.append(record)
    tmp = root / "comparison.csv.tmp"
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({k: (str(v).lower() if isinstance(v, bool) else v) for k, v in row.items()} for row in rows)
    tmp.replace(root / "comparison.csv")
    notes = {"format": "constraint-scale-comparison-summary-v1", "scope": SCOPE,
        "same_statement": False, "scale_only": True, "baseline_kind": "archived_v6_baseline",
        "requested_cases": len(status), "verified_scale_results": len(rows),
        "complete": len(rows) == len(status), "cases": status, "rows": rows,
        "reference_csv_sha256": config["reference_csv_sha256"], "comparison_csv_sha256": sha(root / "comparison.csv"),
        "reference_verification_evidence": evidence,
        "ratio_definition": "DIZK prove seconds / archived v6 (commit + prove) seconds; greater than 1 means DIZK took longer for this scale benchmark",
        "scope_note": "Equal original multiplication/linear constraint counts only. Different statements, operations, integer representations, protocols, security assumptions and environments. This ratio is not a same-Paillier acceleration claim.",
        "timing_note": "Setup, input preparation, Spark/RDD, validation and verify are separate; historical v6 commit+prove uses original CSV stage timers. Task sums are not process wall time. Separate generator/launcher/case monotonic wall timers include subprocess overhead and checks; they are not pure prover timers. Single repeat has no statistical error estimate.",
        "rss_note": "maxrss_kib uses GNU /usr/bin/time %M in KiB for runtime script and its Java children; it is not maximum heap, sum of concurrent processes, or total machine memory. Missing RSS does not invalidate a verified proof."}
    save_json(root / "summary.json", notes)
    return notes


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results", required=True)
    p.add_argument("--require-complete", action="store_true")
    args = p.parse_args()
    result = summarize(Path(args.results).resolve())
    print("Saved {} of {} verified scale rows; same_statement=false".format(result["verified_scale_results"], result["requested_cases"]))
    if args.require_complete and not result["complete"]:
        raise ValueError("Not all requested scale cases have verified matching results; partial summary saved")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        print("SCALE_SUMMARY_REFUSED: " + str(exc), file=sys.stderr)
        sys.exit(2)
