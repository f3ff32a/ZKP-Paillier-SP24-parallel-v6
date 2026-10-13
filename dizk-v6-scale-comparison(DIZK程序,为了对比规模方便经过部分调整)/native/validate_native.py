#!/usr/bin/env python3
"""Check a native-ring assignment with exact Python integers and reject BN254 reduction.

Python 3.6 standard library only. Reads TSV in constraint order without loading matrices.
"""
from __future__ import print_function
import argparse
import hashlib
import json
from pathlib import Path
import sys

# Final DIZK BN254b target, solely for the incompatible-domain diagnostic.
# Original native-ring arithmetic always uses the exported N² instead.
BN254B_FR = 17855808334804902850260923831770255773646114952324966112694569107431857586177
BN254_FR = BN254B_FR  # Compatibility alias for callers of the diagnostic checker.


def sha256(path):
    result = hashlib.sha256()
    with open(str(path), "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def check_export(directory, require_bn254=False):
    directory = Path(directory)
    with open(str(directory / "meta.json"), encoding="utf-8") as stream:
        meta = json.load(stream)
    if meta.get("format") != "v6-native-ring-v1":
        raise ValueError("expected v6-native-ring-v1, not a DIZK field input")
    if meta.get("scalar_domain_kind") != "composite_ring_mod_n_squared":
        raise ValueError("unrecognized scalar-domain declaration")
    p, n = int(meta["ring_modulus"]), int(meta["paillier_public_key"])
    if n <= 1 or p != n * n:
        raise ValueError("ring modulus must equal public N squared")
    if meta["paillier_bits"] != n.bit_length():
        raise ValueError("actual public-key bit length does not match metadata")
    count, public, constraints = int(meta["num_variables"]), int(meta["num_public_inputs"]), int(meta["num_constraints"])
    gates, linear = int(meta["num_multiplication_constraints"]), int(meta["num_linear_constraints"])
    if constraints != gates + linear or public != linear + 1 or count != linear + 1 + 3 * gates:
        raise ValueError("dimension/constant/public-prefix metadata mismatch")
    if count < 1 or not 1 <= public <= count:
        raise ValueError("invalid variable/public-input dimensions")
    expected_offsets = {"A": public, "B": public + gates, "C": public + 2 * gates}
    if meta.get("private_assignment_offsets") != expected_offsets:
        raise ValueError("private A/B/C offsets do not match the declared public prefix")
    if meta.get("bn254_direct_import_allowed") is not False:
        raise ValueError("native-ring export must explicitly disallow BN254 direct import")
    manifest_path = directory / "source_manifest.json"
    integrity = "no_manifest"
    if manifest_path.exists():
        with open(str(manifest_path), encoding="utf-8") as stream:
            manifest = json.load(stream)
        for entry in manifest.get("export_files", []):
            path = directory / entry["path"]
            if path.resolve().parent != directory.resolve():
                raise ValueError("manifest export file must be directly inside input directory")
            if sha256(path) != entry["sha256"] or path.stat().st_size != int(entry["bytes"]):
                raise ValueError("export integrity failure: " + entry["path"])
        integrity = "export_hashes_verified"
    # There is no ring homomorphism Z_(N²) -> F_r preserving 1 when r does not divide N².
    if require_bn254:
        return {"status": "DOMAIN_MISMATCH", "native_ring_modulus": str(p),
                "bn254_diagnostic_curve": "BN254b",
                "bn254_scalar_field_modulus": str(BN254_FR), "native_ring_modulus_bits": p.bit_length(),
                "bn254_scalar_field_bits": BN254_FR.bit_length(),
                "bn254_modulus_role": "diagnostic_target_scalar_field_only_not_the_native_ring_modulus",
                "reason": "N_squared_is_composite_and_not_BN254b_Fr; sound non-native arithmetic translation required",
                "direct_mod_reduction_permitted": False, "integrity": integrity,
                "assignment_checked": False}
    assignment = []
    with open(str(directory / "assignment.tsv"), encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            pieces = line.rstrip("\n\r").split("\t")
            if len(pieces) != 2:
                raise ValueError("assignment line %d must have two TSV columns" % line_number)
            index, value = int(pieces[0]), int(pieces[1])
            if index != len(assignment) or not 0 <= value < p:
                raise ValueError("assignment index/canonical value invalid at line %d" % line_number)
            assignment.append(value)
    if len(assignment) != count or assignment[0] != 1:
        raise ValueError("assignment dimensions or constant 1 invalid")
    current, accum, checked, terms = 0, {"A": 0, "B": 0, "C": 0}, 0, 0
    seen = set()

    def finish(row, value):
        if (value["A"] * value["B"] - value["C"]) % p:
            raise ValueError("unsatisfied native-ring constraint %d" % row)

    with open(str(directory / "matrix.tsv"), encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            pieces = line.rstrip("\n\r").split("\t")
            if len(pieces) != 4:
                raise ValueError("matrix line %d must have four TSV columns" % line_number)
            row, side, index, coefficient = int(pieces[0]), pieces[1], int(pieces[2]), int(pieces[3])
            if row < current or row >= constraints or side not in accum or not 0 <= index < count:
                raise ValueError("matrix row/side/index invalid at line %d" % line_number)
            if not 0 < coefficient < p:
                raise ValueError("matrix coefficient must be nonzero and canonical")
            while current < row:
                if not seen:
                    raise ValueError("missing native-ring constraint row %d" % current)
                finish(current, accum)
                checked += 1
                current += 1
                accum, seen = {"A": 0, "B": 0, "C": 0}, set()
            if (side, index) in seen:
                raise ValueError("duplicate term at matrix line %d" % line_number)
            seen.add((side, index))
            accum[side] = (accum[side] + coefficient * assignment[index]) % p
            terms += 1
    while current < constraints:
        if not seen:
            raise ValueError("missing native-ring constraint row %d" % current)
        finish(current, accum)
        checked += 1
        current += 1
        accum = {"A": 0, "B": 0, "C": 0}
        seen = set()
    return {"status": "NATIVE_RING_VALID_BN254_INCOMPATIBLE", "assignment_checked": True,
            "native_ring_modulus": str(p), "actual_paillier_bits": n.bit_length(),
            "native_ring_modulus_bits": p.bit_length(), "constraints_checked": checked,
            "matrix_nonzero_terms": terms, "num_variables": count, "num_public_inputs": public,
            "bn254_direct_import_allowed": False, "integrity": integrity,
            "statement_instance_status": meta.get("statement_instance_status", "unspecified")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-bn254", action="store_true",
                        help="reject this ring export before reading witness; never reduce modulo BN254")
    args = parser.parse_args(argv)
    try:
        report = check_export(args.input, args.require_bn254)
        code = 2 if report["status"] == "DOMAIN_MISMATCH" else 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        report, code = {"status": "INVALID_NATIVE_EXPORT", "error": str(error)}, 2
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.report:
        with open(str(args.report), "w", encoding="utf-8") as stream:
            stream.write(text + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
