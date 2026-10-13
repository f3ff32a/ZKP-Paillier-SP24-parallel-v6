#!/usr/bin/env python3
"""Read-only exact integer audit of v6-ring-v1 header/gates/linear TSV exports.

This validates original modular constraints, not a proof system or BN254 conversion.
Python 3.6 standard library only.
"""
from __future__ import print_function
import argparse
import hashlib
import json
from pathlib import Path
import sys

STATEMENT_HEADER_KEYS = ("format", "modulus", "paillier_modulus", "gate_count", "linear_count", "bits",
                         "messages", "range_proof_count", "slot_bytes", "slots_per_message",
                         "messages_per_batch", "batch_count")


def statement_digest(directory, header):
    public_header = {key: header[key] for key in STATEMENT_HEADER_KEYS}
    identity = hashlib.sha256()
    identity.update(b"v6-public-ring-statement-v1\n")
    identity.update(json.dumps(public_header, sort_keys=True, separators=(",", ":")).encode("ascii"))
    identity.update(b"\n")
    with open(str(Path(directory) / "linear.tsv"), "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            identity.update(chunk)
    return identity.hexdigest()


def file_digest(path):
    result = hashlib.sha256()
    with open(str(path), "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def load_header(directory):
    directory = Path(directory)
    with open(str(directory / "header.json"), encoding="utf-8") as stream:
        header = json.load(stream)
    if header.get("format") != "v6-ring-v1":
        raise ValueError("expected format v6-ring-v1")
    p, n = int(header["modulus"]), int(header["paillier_modulus"])
    if n <= 1 or p != n * n:
        raise ValueError("modulus must equal paillier_modulus squared")
    if int(header["bits"]) != n.bit_length():
        raise ValueError("bits must be the actual public modulus bit length")
    if header.get("scalar_domain_kind") != "composite_ring_mod_n_squared":
        raise ValueError("scalar-domain declaration must identify the composite ring")
    gates, linear = int(header["gate_count"]), int(header["linear_count"])
    if gates < 1 or linear < 1:
        raise ValueError("gate_count/linear_count must be positive")
    if "native_total_constraints" in header and int(header["native_total_constraints"]) != gates + linear:
        raise ValueError("native_total_constraints must be gates plus linear rows")
    if "single_encryption_gate_count" in header:
        messages, range_count, batches = (int(header[name]) for name in ("messages", "range_proof_count", "batch_count"))
        slots = int(header["slots_per_message"])
        total_encryptions = messages + range_count + batches
        if gates != int(header["single_encryption_gate_count"]) * total_encryptions + messages * slots:
            raise ValueError("full CBatchEnc multiplication count does not match its configuration")
        expected_linear = (int(header["single_encryption_linear_count"]) * total_encryptions +
                           2 * messages * slots + messages + range_count + batches)
        if linear != expected_linear:
            raise ValueError("full CBatchEnc linear count does not match its configuration")
        if int(header["ciphertext_count"]) != total_encryptions:
            raise ValueError("ciphertext_count must include normal, range-mask and packed messages")
        for key, count in (("normal", messages), ("range_masks", range_count), ("packed", batches)):
            values = header["ciphertexts"][key]
            if len(values) != count or any(not 0 <= int(value) < p for value in values):
                raise ValueError("invalid public ciphertext group " + key)
    return header


def audit_raw(directory):
    directory = Path(directory)
    header = load_header(directory)
    p, n = int(header["modulus"]), int(header["paillier_modulus"])
    statement_id, witness_id = header.get("statement_id"), file_digest(directory / "gates.tsv")
    if statement_id is not None and statement_id != statement_digest(directory, header):
        raise ValueError("public statement_id does not match modulus/configuration/linear.tsv")
    if header.get("witness_id") is not None and header["witness_id"] != witness_id:
        raise ValueError("private witness_id does not match gates.tsv")
    gate_count, linear_count = int(header["gate_count"]), int(header["linear_count"])
    values = {"A": [], "B": [], "C": []}
    with open(str(directory / "gates.tsv"), encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            fields = line.rstrip("\n\r").split("\t")
            if len(fields) != 4:
                raise ValueError("gates.tsv line %d needs four columns" % line_number)
            index, a, b, c = [int(value) for value in fields]
            if index != len(values["A"]) or index >= gate_count:
                raise ValueError("non-contiguous/out-of-range gate index at line %d" % line_number)
            if any(not 0 <= value < p for value in (a, b, c)):
                raise ValueError("non-canonical gate value at gate %d" % index)
            if (a * b - c) % p:
                raise ValueError("unsatisfied original multiplication gate %d" % index)
            for name, value in zip(("A", "B", "C"), (a, b, c)):
                values[name].append(value)
    if len(values["A"]) != gate_count:
        raise ValueError("gates.tsv is incomplete")
    public_k_digest = hashlib.sha256()
    terms, checked_linear = 0, 0
    with open(str(directory / "linear.tsv"), encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            fields = line.rstrip("\n\r").split("\t")
            if len(fields) != 3:
                raise ValueError("linear.tsv line %d needs three columns (the term column may be empty)" % line_number)
            row, k = int(fields[0]), int(fields[1])
            if row != checked_linear or row >= linear_count:
                raise ValueError("non-contiguous/out-of-range linear row at line %d" % line_number)
            if not 0 <= k < p:
                raise ValueError("non-canonical public K at linear row %d" % row)
            seen, total = set(), 0
            for term in (fields[2].split(",") if fields[2] else []):
                pieces = term.split(":")
                if len(pieces) != 3:
                    raise ValueError("invalid term at linear row %d" % row)
                kind, index, coefficient = pieces[0], int(pieces[1]), int(pieces[2])
                if kind not in values or not 0 <= index < gate_count or not 0 < coefficient < p:
                    raise ValueError("invalid term kind/index/coefficient at linear row %d" % row)
                if (kind, index) in seen:
                    raise ValueError("duplicate wire term at linear row %d" % row)
                seen.add((kind, index))
                total = (total + coefficient * values[kind][index]) % p
                terms += 1
            if total != k:
                raise ValueError("unsatisfied original linear row %d" % row)
            public_k_digest.update((str(k) + "\n").encode("ascii"))
            checked_linear += 1
    if checked_linear != linear_count:
        raise ValueError("linear.tsv is incomplete")
    return {"status": "ORIGINAL_V6_RING_ASSIGNMENT_VALID", "format": "v6-ring-v1",
            "native_modulus": str(p), "paillier_modulus": str(n),
            "actual_paillier_bits": n.bit_length(), "modulus_bits": p.bit_length(),
            "multiplication_constraints_checked": gate_count,
            "linear_constraints_checked": checked_linear, "native_total_constraints": gate_count + checked_linear,
            "linear_nonzero_terms": terms, "public_Kq_sha256": public_k_digest.hexdigest(),
            "raw_files": {name: file_digest(directory / name) for name in ("header.json", "gates.tsv", "linear.tsv")},
            "statement_id": statement_id, "witness_id": witness_id,
            "public_witness_partition": "all Kq public; all A/B/C private",
            "public_statement_parameter_scope": "N and sparse W coefficients fixed by the exported public statement",
            "same_archived_timing_instance": False,
            "statement_instance_status": header.get("statement_instance_status", "unspecified"),
            "bn254_direct_reduction_allowed": False,
            "validation_scope": "exact original modular arithmetic only; not a cryptographic proof verification"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report, code = audit_raw(args.input), 0
    except (OSError, KeyError, ValueError, TypeError) as error:
        report, code = {"status": "INVALID_V6_RING_EXPORT", "error": str(error)}, 2
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        with open(str(args.report), "w", encoding="utf-8") as stream:
            stream.write(text + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
