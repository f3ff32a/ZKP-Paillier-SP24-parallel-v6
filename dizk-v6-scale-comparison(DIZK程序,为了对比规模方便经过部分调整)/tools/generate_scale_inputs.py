#!/usr/bin/env python3
"""Stream a sparse BN254b chain with requested multiplication/linear counts.

This is a different relation from Paillier: matching counts does not match
computation, topology, proof protocol, or cryptographic security parameters.
Python 3.6 standard library only; existing directories are never overwritten.
"""
from __future__ import print_function
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

FIELD = 17855808334804902850260923831770255773646114952324966112694569107431857586177
GENERATOR_ID = "field_chain_mix_v1"
DEFAULT_SEED = "constraint-scale-default-v1"
POINTS = (1, 10, 20, 40, 80, 100, 200, 400)


def file_sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            value.update(chunk)
    return value.hexdigest()


def generate(output, messages, multiplication_constraints, linear_constraints,
             reference_csv_sha256, seed=DEFAULT_SEED):
    """Return the completed header after streaming TSV files into a new directory."""
    for name, value in (("messages", messages), ("multiplication_constraints", multiplication_constraints),
                        ("linear_constraints", linear_constraints)):
        if type(value) is not int or value < (1 if name == "messages" else 0):
            raise ValueError(name + " must be an integer in the permitted range")
    if messages not in POINTS:
        raise ValueError("messages must be one of 1,10,20,40,80,100,200,400")
    total = multiplication_constraints + linear_constraints
    if total < 1 or total + 2 > (1 << 50):
        raise ValueError("Constraint total must be positive and fit BN254b FFT domain")
    if not isinstance(reference_csv_sha256, str) or not re.match(r"\A[0-9a-f]{64}\Z", reference_csv_sha256):
        raise ValueError("reference_csv_sha256 must be a lowercase 64-character SHA256")
    if not isinstance(seed, str) or not seed:
        raise ValueError("seed must be a nonempty string")
    requested_output = Path(output)
    if requested_output.exists() or requested_output.is_symlink():
        raise ValueError("Output directory already exists; choose a new --output")
    output = requested_output.resolve()
    if output.exists() or output.is_symlink():
        raise ValueError("Output directory already exists; choose a new --output")
    output.mkdir(parents=True, exist_ok=False)
    marker = output / ".incomplete"
    marker.write_text("Generation incomplete; do not prove this input.\n", encoding="ascii")
    seed_material = json.dumps({"seed": seed, "messages": messages,
                               "multiplication_constraints": multiplication_constraints,
                               "linear_constraints": linear_constraints,
                               "reference_csv_sha256": reference_csv_sha256},
                              sort_keys=True, separators=(",", ":")).encode("utf-8")
    x = int.from_bytes(hashlib.sha256(b"scale-private-x\0" + seed_material).digest(), "big") % (FIELD - 1) + 1
    y = int.from_bytes(hashlib.sha256(b"scale-private-y\0" + seed_material).digest(), "big") % (FIELD - 1) + 1
    x_index, y_index = 2, 3
    seen_mul = seen_linear = 0
    with (output / "constraints.tsv").open("w", encoding="ascii", newline="\n", buffering=1048576) as constraints, \
            (output / "assignment.tsv").open("w", encoding="ascii", newline="\n", buffering=1048576) as assignment:
        # Index 1 is appended after its final value is known; both import paths use
        # explicit indices rather than requiring sorted assignment rows.
        assignment.write("0\t1\n2\t{}\n3\t{}\n".format(x, y))
        for index in range(total):
            z_index = index + 4 if index + 1 < total else 1
            a, b = 2 + index % 5, 3 + index % 7
            d, e = 1 + index % 11, 2 + index % 13
            is_mul = ((index + 1) * multiplication_constraints // total
                      > index * multiplication_constraints // total)
            if is_mul:
                if (a * x + d) % FIELD == 0:
                    d += 1
                if (b * y + e) % FIELD == 0:
                    e += 1
                z = ((a * x + d) * (b * y + e)) % FIELD
                left = "0:{},{}:{}".format(d, x_index, a)
                right = "0:{},{}:{}".format(e, y_index, b)
                seen_mul += 1
            else:
                z = (a * x + b * y + d) % FIELD
                if z == 0:
                    d += 1
                    z = 1
                left = "0:{},{}:{},{}:{}".format(d, x_index, a, y_index, b)
                right = "0:1"
                seen_linear += 1
            if z == 0:
                raise RuntimeError("Nonzero chain invariant failed")
            constraints.write(left + "\t" + right + "\t{}:1\n".format(z_index))
            assignment.write("{}\t{}\n".format(z_index, z))
            x, y, x_index, y_index = y, z, y_index, z_index
    if (seen_mul, seen_linear) != (multiplication_constraints, linear_constraints):
        raise RuntimeError("Generated constraint counts disagree")
    header = {
        "format": "dizk-r1cs-v2", "field_modulus": str(FIELD),
        "scope": "constraint_scale_benchmark", "same_statement": False,
        "paillier_bits": 0, "reference_paillier_bits": 512, "messages": messages,
        "multiplication_constraints": seen_mul, "linear_constraints": seen_linear,
        "constraints": total, "variables": total + 3, "primary_inputs": 2,
        "public_values": ["1", str(y)], "generator_id": GENERATOR_ID,
        "reference_csv_sha256": reference_csv_sha256, "seed": seed,
        "relation_hash": file_sha(output / "constraints.tsv"),
        "assignment_hash": file_sha(output / "assignment.tsv"),
        "assignment_order": "0,2,3,4..M+2,1; explicit indices, unique and complete",
        "relation_notice": "Counts match the reference row only. This sparse prime-field chain is not the Paillier computation, topology or cryptographic protocol.",
    }
    statement_meta = {key: header[key] for key in (
        "format", "field_modulus", "scope", "same_statement", "paillier_bits",
        "reference_paillier_bits", "messages", "multiplication_constraints", "linear_constraints",
        "constraints", "variables", "primary_inputs", "public_values", "generator_id",
        "reference_csv_sha256", "seed", "relation_hash")}
    statement_bytes = ("constraint-scale-statement-v1\n" + json.dumps(statement_meta,
                       sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    header["statement_id"] = hashlib.sha256(statement_bytes).hexdigest()
    header["statement_id_algorithm"] = "SHA256(constraint-scale-statement-v1 LF + sorted compact public metadata JSON including relation_hash + LF)"
    (output / "header.json").write_text(json.dumps(header, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    marker.unlink()
    return header


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--messages", required=True, type=int)
    parser.add_argument("--multiplication-constraints", required=True, type=int)
    parser.add_argument("--linear-constraints", required=True, type=int)
    parser.add_argument("--reference-csv-sha256", required=True)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    args = parser.parse_args()
    header = generate(args.output, args.messages, args.multiplication_constraints,
                      args.linear_constraints, args.reference_csv_sha256, args.seed)
    print(json.dumps(header, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print("SCALE_GENERATION_FAILED: " + str(exc), file=sys.stderr)
        sys.exit(2)
