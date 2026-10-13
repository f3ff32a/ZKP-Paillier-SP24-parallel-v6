#!/usr/bin/env python3
"""Snapshot a strict real R1CS input identity before launching proof generation."""
from __future__ import print_function
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

MODULUS = "17855808334804902850260923831770255773646114952324966112694569107431857586177"
POINTS = (1, 10, 20, 40, 80, 100, 200, 400)


def validate_scale(meta, root):
    """Check the generated chain with constant memory, including all row counts."""
    field = int(MODULUS)
    for key in ("multiplication_constraints", "linear_constraints"):
        if type(meta.get(key)) is not int or meta[key] < 0:
            raise RuntimeError("Scale header requires nonnegative integer " + key)
    total = meta["multiplication_constraints"] + meta["linear_constraints"]
    if (meta["constraints"] != total or meta["variables"] != total + 3
            or meta["primary_inputs"] != 2):
        raise RuntimeError("Scale relation must have M=N+Q, variables=M+3 and primary_inputs=2")
    if (meta.get("reference_paillier_bits") != 512 or type(meta.get("reference_paillier_bits")) is not int
            or meta.get("generator_id") != "field_chain_mix_v1"
            or meta["paillier_bits"] != 0 or meta["messages"] not in POINTS):
        raise RuntimeError("Invalid scale reference/generator/Paillier metadata")
    if not isinstance(meta.get("reference_csv_sha256"), str) or not re.match(r"\A[0-9a-f]{64}\Z", meta["reference_csv_sha256"]):
        raise RuntimeError("Scale reference_csv_sha256 must be a lowercase SHA256")
    public = meta.get("public_values")
    if (not isinstance(public, list) or len(public) != 2 or public[0] != "1"
            or not isinstance(public[1], str) or not re.match(r"\A[1-9][0-9]*\Z", public[1])
            or not 0 < int(public[1]) < field):
        raise RuntimeError("Scale public_values must bind constant 1 and a canonical nonzero field output")
    if not isinstance(meta.get("seed"), str) or not meta["seed"]:
        raise RuntimeError("Missing scale generator seed")

    def witness_line(stream, expected):
        parts = stream.readline().rstrip("\n").split("\t")
        if (len(parts) != 2 or parts[0] != str(expected)
                or not re.match(r"\A[1-9][0-9]*\Z", parts[1])):
            raise RuntimeError("Scale witness row/index is missing, duplicated or noncanonical")
        value = int(parts[1])
        if not 0 < value < field:
            raise RuntimeError("Scale witness must be canonical and nonzero")
        return value

    def combination(text):
        result = []
        for term in text.split(",") if text else []:
            pieces = term.split(":")
            if (len(pieces) != 2 or not re.match(r"\A(?:0|[1-9][0-9]*)\Z", pieces[0])
                    or not re.match(r"\A[1-9][0-9]*\Z", pieces[1])):
                raise RuntimeError("Malformed scale linear combination")
            index, coefficient = int(pieces[0]), int(pieces[1])
            if index >= meta["variables"] or coefficient >= field or any(x[0] == index for x in result):
                raise RuntimeError("Invalid scale LC index/coefficient")
            result.append((index, coefficient))
        return result

    with (root / "assignment.tsv").open("r", encoding="ascii") as assignment, \
            (root / "constraints.tsv").open("r", encoding="ascii") as constraints:
        if witness_line(assignment, 0) != 1:
            raise RuntimeError("Constant index zero must equal one")
        x, y = witness_line(assignment, 2), witness_line(assignment, 3)
        x_index, y_index = 2, 3
        actual_mul = actual_linear = 0
        for row_index in range(total):
            out_index = row_index + 4 if row_index + 1 < total else 1
            z = witness_line(assignment, out_index)
            sides = constraints.readline().rstrip("\n").split("\t")
            if len(sides) != 3:
                raise RuntimeError("Scale constraint row is missing or malformed")
            a, b, c = [combination(side) for side in sides]
            is_mul = ((row_index + 1) * meta["multiplication_constraints"] // total
                      > row_index * meta["multiplication_constraints"] // total)
            if c != [(out_index, 1)]:
                raise RuntimeError("Scale constraint must bind its chain output")
            if is_mul:
                if [i for i, _ in a] != [0, x_index] or [i for i, _ in b] != [0, y_index]:
                    raise RuntimeError("Scale multiplication row does not use both previous chain values")
                actual_mul += 1
            else:
                if [i for i, _ in a] != [0, x_index, y_index] or b != [(0, 1)]:
                    raise RuntimeError("Scale linear row does not use both previous chain values")
                actual_linear += 1
            values = {0: 1, x_index: x, y_index: y}
            av = sum(values[i] * coefficient for i, coefficient in a) % field
            bv = sum(values[i] * coefficient for i, coefficient in b) % field
            if av * bv % field != z:
                raise RuntimeError("Scale chain witness does not satisfy R1CS")
            x, y, x_index, y_index = y, z, y_index, out_index
        if assignment.readline() or constraints.readline():
            raise RuntimeError("Scale input has extra witness/constraint rows")
        if str(y) != public[1]:
            raise RuntimeError("Scale public output differs from assignment index one")
        if (actual_mul, actual_linear) != (meta["multiplication_constraints"], meta["linear_constraints"]):
            raise RuntimeError("Actual scale multiplication/linear counts differ")
    selected = {key: meta[key] for key in (
        "format", "field_modulus", "scope", "same_statement", "paillier_bits", "reference_paillier_bits",
        "messages", "multiplication_constraints", "linear_constraints", "constraints", "variables",
        "primary_inputs", "public_values", "generator_id", "reference_csv_sha256", "seed", "relation_hash")}
    statement = ("constraint-scale-statement-v1\n" + json.dumps(selected, sort_keys=True,
                 separators=(",", ":")) + "\n").encode("utf-8")
    if meta["statement_id"] != hashlib.sha256(statement).hexdigest():
        raise RuntimeError("Scale statement_id does not bind the generated public relation")


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--adapter-self-test", action="store_true")
    modes.add_argument("--constraint-scale", action="store_true")
    args = parser.parse_args()
    root = Path(args.input).resolve()
    if (root / ".incomplete").exists():
        raise RuntimeError("Input compiler has not completed this directory")
    paths = {name: root / name for name in ("header.json", "constraints.tsv", "assignment.tsv")}
    for name, path in paths.items():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("Missing regular input file: " + name)
    meta = json.loads(paths["header.json"].read_text(encoding="utf-8"))
    if meta.get("format") != "dizk-r1cs-v2" or str(meta.get("field_modulus")) != MODULUS:
        raise RuntimeError("Input format/field must match dizk-r1cs-v2 and BN254bFr")
    scope = "constraint_scale_benchmark" if args.constraint_scale else ("arithmetic_self_test" if args.adapter_self_test else "v6_ring_lifted")
    same_statement = not (args.adapter_self_test or args.constraint_scale)
    if meta.get("scope") != scope or meta.get("same_statement") is not same_statement:
        raise RuntimeError("Input must carry scope=" + scope + " and the matching same_statement flag")
    for key in ("variables", "constraints", "primary_inputs", "messages"):
        if type(meta.get(key)) is not int or meta[key] < 1:
            raise RuntimeError("Header requires positive integer " + key)
    if type(meta.get("paillier_bits")) is not int or (meta["paillier_bits"] < (0 if args.constraint_scale else 1)):
        raise RuntimeError("Invalid integer paillier_bits")
    if meta["variables"] < 2 or meta["primary_inputs"] >= meta["variables"]:
        raise RuntimeError("Counts must include constant index zero and at least one private variable")
    if meta["constraints"] + meta["primary_inputs"] > (1 << 50):
        raise RuntimeError("FFT_DOMAIN_LIMIT: constraints + primary_inputs exceeds BN254b 2^50 domain")
    if args.constraint_scale:
        if meta["paillier_bits"] != 0:
            raise RuntimeError("Scale comparison requires paillier_bits=0")
    elif args.adapter_self_test:
        if not 8 <= meta["paillier_bits"] <= 512:
            raise RuntimeError("Arithmetic self-test bits must be between 8 and 512")
    elif meta["paillier_bits"] != 512 or meta["messages"] not in (1, 10, 20, 40, 80, 100, 200, 400):
        raise RuntimeError("Formal comparison requires actual 512-bit Paillier and one requested MC")
    if not args.constraint_scale:
        ring = int(meta.get("original_ring_modulus", "0"))
        paillier_n = int(meta.get("paillier_modulus", "0"))
        if paillier_n <= 0 or paillier_n.bit_length() != meta["paillier_bits"] or paillier_n * paillier_n != ring:
            raise RuntimeError("Original Paillier N/N^2 metadata is inconsistent")
    if not isinstance(meta.get("statement_id"), str) or not meta["statement_id"]:
        raise RuntimeError("Missing statement_id")
    hashes = {name: sha(path) for name, path in paths.items()}
    if meta.get("relation_hash") != hashes["constraints.tsv"]:
        raise RuntimeError("relation_hash must equal exact constraints.tsv SHA-256")
    if "assignment_hash" in meta and meta["assignment_hash"] != hashes["assignment.tsv"]:
        raise RuntimeError("assignment_hash mismatch")
    if args.constraint_scale:
        if meta.get("assignment_hash") != hashes["assignment.tsv"]:
            raise RuntimeError("Scale assignment_hash is required")
        validate_scale(meta, root)
    bundle = "".join(name + "\0" + hashes[name] + "\n" for name in
                     ("header.json", "constraints.tsv", "assignment.tsv"))
    data = {"format": "dizk-input-snapshot-v1", "directory": str(root), "header": meta,
            "sha256": hashes, "input_bundle_sha256": hashlib.sha256(bundle.encode("utf-8")).hexdigest()}
    output = Path(args.output)
    if output.exists():
        raise RuntimeError("Input snapshot output already exists")
    output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Input recorded: " + str(root))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        sys.exit(1)
