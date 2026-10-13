#!/usr/bin/env python3
"""Build a read-only v6 native-ring adapter using Ubuntu 18.04 C++/NTL/GMP.

No source file or existing result is modified. No proof or timing result is made.
Python 3.6 standard library only.
"""
from __future__ import print_function
import argparse
import csv
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import platform
import shlex
import shutil
import subprocess
import sys


def digest(path):
    value = hashlib.sha256()
    with open(str(path), "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v6-source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--bits", type=int, default=512)
    parser.add_argument("--messages", type=int, default=1)
    parser.add_argument("--range-proof-count", type=int, default=128)
    parser.add_argument("--slot-bytes", type=int, default=4)
    parser.add_argument("--messages-per-batch", type=int, default=15)
    parser.add_argument("--seed-hex", default="d17e5120a11ce7c0de51206412800415aa")
    parser.add_argument("--cxx", default="g++")
    parser.add_argument("--portable", action="store_true",
                        help="omit -march=native; record this different compile choice in provenance")
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-v6-proof", action="store_true",
                        help="run original v6 commit/prove/verify on the exact exported instance")
    args = parser.parse_args(argv)
    source = args.v6_source.resolve()
    here = Path(__file__).resolve().parent
    app = source / "src" / "cpp" / "app"
    if not (app / "CBatchEnc.cpp").is_file():
        parser.error("--v6-source must contain src/cpp/app/CBatchEnc.cpp")
    if args.threads < 1 or args.messages < 1 or args.repeat < 1 or args.bits < 16 or args.bits % 8:
        parser.error("positive threads/messages and bits multiple of 8 >=16 are required")
    if args.slot_bytes < 1 or (args.bits // 8) % args.slot_bytes:
        parser.error("slot-bytes must divide the key byte length")
    if args.range_proof_count < 1 or args.messages_per_batch < 1:
        parser.error("range-proof-count/messages-per-batch must be positive")
    try:
        seed = bytes.fromhex(args.seed_hex)
        if not seed or len(args.seed_hex) % 2 or not re.match(r"\A[0-9A-Fa-f]+\Z", args.seed_hex):
            raise ValueError()
    except ValueError:
        parser.error("seed-hex must be nonempty, even hexadecimal without spaces")
    # Exclude end-to-end proof runners. All supporting app implementations are linked.
    cpp = sorted(p for p in app.rglob("*.cpp") if p.name not in ("App.cpp", "EndToEnd.cpp"))
    output = args.output.resolve()
    binary = output / "export_native_ring"
    compiler = shutil.which(args.cxx) or args.cxx
    compile_cmd = [compiler, "-std=c++11", "-O3", "-pthread", "-fopenmp"]
    if not args.portable:
        compile_cmd.append("-march=native")
    compile_cmd += ["-I" + str(source / "src" / "cpp"), str(here / "export_native_ring.cpp")]
    compile_cmd += [str(p) for p in cpp]
    compile_cmd += ["-lntl", "-lgmp", "-lm", "-o", str(binary)]
    run_cmd = [str(binary), "--output", str(output), "--bits", str(args.bits),
               "--messages", str(args.messages), "--range-proof-count", str(args.range_proof_count),
               "--slot-bytes", str(args.slot_bytes), "--messages-per-batch", str(args.messages_per_batch),
               "--seed-hex", args.seed_hex, "--repeat", str(args.repeat)]
    if args.run_v6_proof:
        run_cmd.append("--run-v6-proof")
    if args.dry_run:
        print("compile:", " ".join(shlex.quote(x) for x in compile_cmd))
        print("run:", " ".join(shlex.quote(x) for x in run_cmd))
        print("scope: native-ring circuit/assignment export; BN254 conversion is separate")
        print("same-instance native v6 proof:", args.run_v6_proof)
        return 0
    if any((output / name).exists() for name in ("meta.json", "matrix.tsv", "assignment.tsv", "source_manifest.json",
                                               "header.json", "gates.tsv", "linear.tsv")):
        parser.error("output already contains export files; choose a new output directory")
    output.mkdir(parents=True, exist_ok=True)
    sources = [here / "export_native_ring.cpp", here / "build_and_export.py"]
    sources += sorted(p for p in (source / "src" / "cpp").rglob("*") if p.suffix in (".cpp", ".hpp", ".h"))
    entries = [{"path": str(p), "sha256": digest(p), "bytes": p.stat().st_size} for p in sources]
    env = os.environ.copy()
    env.update({"OMP_NUM_THREADS": str(args.threads), "OMP_DYNAMIC": "FALSE", "OMP_NESTED": "FALSE"})
    try:
        subprocess.check_call(compile_cmd)
        with open(str(output / "export.log"), "w", encoding="utf-8") as log:
            subprocess.check_call(run_cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
    except (OSError, subprocess.CalledProcessError) as error:
        print("native-ring export failed:", error, file=sys.stderr)
        return 2
    changed = [entry["path"] for entry in entries if digest(Path(entry["path"])) != entry["sha256"]]
    if changed:
        raise RuntimeError("source changed during export: " + ", ".join(changed))
    # Public statement identity excludes private A/B/C gate values. The sparse W/Kq
    # table and the fixed arithmetic modulus/configuration bind the proved relation.
    with open(str(output / "header.json"), encoding="utf-8") as stream:
        header = json.load(stream)
    identity_header = {key: header[key] for key in ("format", "modulus", "paillier_modulus", "gate_count",
                       "linear_count", "bits", "messages", "range_proof_count", "slot_bytes",
                       "slots_per_message", "messages_per_batch", "batch_count")}
    identity = hashlib.sha256()
    identity.update(b"v6-public-ring-statement-v1\n")
    identity.update(json.dumps(identity_header, sort_keys=True, separators=(",", ":")).encode("ascii"))
    identity.update(b"\n")
    with open(str(output / "linear.tsv"), "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            identity.update(chunk)
    statement_id = identity.hexdigest()
    witness_id = digest(output / "gates.tsv")
    for name in ("header.json", "meta.json", "v6_native_timing.json"):
        path = output / name
        if path.exists():
            with open(str(path), encoding="utf-8") as stream:
                record = json.load(stream)
            record["statement_id"] = statement_id
            record["witness_id"] = witness_id
            record["witness_id_algorithm"] = "SHA256(gates.tsv exact bytes), private original A/B/C assignment"
            record["statement_id_algorithm"] = "SHA256(domain v6-public-ring-statement-v1 LF + canonical selected public header JSON + LF + linear.tsv exact bytes)"
            with open(str(path), "w", encoding="utf-8") as stream:
                json.dump(record, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
    export_names = ["header.json", "gates.tsv", "linear.tsv", "meta.json", "matrix.tsv", "assignment.tsv", "export.log"]
    if args.run_v6_proof:
        export_names.append("v6_native_timing.json")
        with open(str(output / "v6_native_timing.json"), encoding="utf-8") as stream:
            timing = json.load(stream)
        timing["threads_requested"] = args.threads
        with open(str(output / "v6_native_timing.json"), "w", encoding="utf-8") as stream:
            json.dump(timing, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        columns = ("scheme", "messages", "actual_bits", "repeat", "threads_requested", "openmp_max_threads",
                   "statement_id", "witness_id", "payload_fingerprint", "plaintext_bits", "gate_count", "linear_count",
                   "verified", "raw_public_statement_matched", "raw_export_same_circuit_and_witness", "workload",
                   "key_generation_seconds", "all_message_encryption_seconds", "prover_challenge_and_wire_seconds",
                   "value_assignment_seconds", "generator_setup_seconds", "prover_prepare_seconds", "commit_seconds",
                   "proving_seconds", "proof_generation_seconds", "verifier_prepare_seconds", "verify_seconds",
                   "proof_pipeline_sum_seconds", "timing_scope")
        with open(str(output / "v6_native_timing.csv"), "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerow({key: timing[key] for key in columns})
        export_names.append("v6_native_timing.csv")
    manifest = {"format": "v6-native-export-provenance-v1", "utc": datetime.datetime.utcnow().isoformat() + "Z",
                "source_root": str(source), "source_files": entries, "compile_command": compile_cmd,
                "run_command": run_cmd, "omp_num_threads": args.threads,
                "repeat": args.repeat, "host_platform": platform.platform(),
                "python_version": platform.python_version(), "native_binary_sha256": digest(binary),
                "source_files_unchanged": True,
                "scope": "native_ring_circuit_assignment_export_and_same_instance_original_v6_proof" if args.run_v6_proof else "native_ring_circuit_assignment_export_without_proof",
                "statement_id": statement_id, "witness_id": witness_id, "run_v6_proof": args.run_v6_proof,
                "statement_instance_status": "new_deterministic_instance_not_archived_timing_instance",
                "export_files": [{"path": name, "sha256": digest(output / name),
                                  "bytes": (output / name).stat().st_size}
                                 for name in export_names]}
    with open(str(output / "source_manifest.json"), "w", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print("native ring export saved:", output)
    print("BN254 import prohibited until a sound non-native arithmetic translation exists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
