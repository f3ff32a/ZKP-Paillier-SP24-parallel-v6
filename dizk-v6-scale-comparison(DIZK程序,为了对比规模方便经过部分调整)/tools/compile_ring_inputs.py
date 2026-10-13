#!/usr/bin/env python3
"""Lift the complete v6 composite-ring relation into author BN254b R1CS.

Python 3.6 compatible. All shapes/bounds depend on public data, never a witness.
The author DIZK proof system is used separately; this is an arithmetic adapter.
"""
from __future__ import print_function
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import time
from collections import OrderedDict

FR = 17855808334804902850260923831770255773646114952324966112694569107431857586177
KINDS = {"A": 0, "B": 1, "C": 2}


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def limbs(value, count, width):
    mask = (1 << width) - 1
    out = [(value >> (i * width)) & mask for i in range(count)]
    if value < 0 or value >> (count * width):
        raise ValueError("integer does not fit its public limb bound")
    return out


def read_row(line, expected, modulus, gates):
    parts = line.rstrip("\n\r").split("\t")
    if len(parts) != 3 or int(parts[0]) != expected:
        raise ValueError("linear row has invalid shape/index at " + str(expected))
    k = int(parts[1])
    if not 0 <= k < modulus:
        raise ValueError("noncanonical public K")
    terms = []
    seen = set()
    for item in parts[2].split(",") if parts[2] else []:
        kind, index, coef = item.split(":")
        index, coef = int(index), int(coef)
        if kind not in KINDS or not 0 <= index < gates or not 0 < coef < modulus:
            raise ValueError("invalid/noncanonical native linear term")
        if (kind, index) in seen:
            raise ValueError("duplicate native linear wire")
        seen.add((kind, index))
        signed = coef if coef <= modulus // 2 else coef - modulus
        terms.append((kind, index, signed))
    return k, terms


def inspect(root, width):
    header_path = root / "header.json"
    meta = json.loads(header_path.read_text(encoding="utf-8"))
    if meta.get("format") != "v6-ring-v1":
        raise ValueError("expected genuine v6-ring-v1 input")
    m, n = int(meta["modulus"]), int(meta["paillier_modulus"])
    if n <= 2 or m != n * n:
        raise ValueError("public ring modulus must be exactly N squared")
    if int(meta["bits"]) != n.bit_length():
        raise ValueError("Paillier bit count must describe the actual N")
    gates, rows = int(meta["gate_count"]), int(meta["linear_count"])
    if gates < 1 or rows < 1:
        raise ValueError("empty original relation")
    k = m.bit_length()
    length = (k + width - 1) // width
    # Public inputs may be deduplicated because their values and row mapping are public.
    public_values = OrderedDict()
    terms_count = 0
    row_count = 0
    linear_estimate = 0
    with (root / "linear.tsv").open("r", encoding="ascii") as f:
        for row_count, line in enumerate(f, 1):
            value, terms = read_row(line, row_count - 1, m, gates)
            if value not in public_values:
                public_values[value] = len(public_values)
            terms_count += len(terms)
            qbits = max(1, (2 * sum(abs(t[2]) for t in terms) + 4).bit_length())
            qlength = (qbits + width - 1) // width
            offset = 2 * sum(-t[2] for t in terms if t[2] < 0) + 2
            columns = max(length + qlength, 2 * length,
                          (offset * m).bit_length() // width + 1)
            bounds = [0] * columns
            x_bounds = [(1 << min(width, k - i * width)) - 1 for i in range(length)]
            m_digits = limbs(m, length, width)
            for kind, index, coefficient in terms:
                digits = limbs(abs(coefficient), (abs(coefficient).bit_length() + width - 1) // width, width)
                for i, bound in enumerate(x_bounds):
                    for j, digit in enumerate(digits):
                        bounds[i + j] += bound * digit
            for i in range(qlength):
                q_bound = (1 << min(width, qbits - i * width)) - 1
                for j, digit in enumerate(m_digits):
                    bounds[i + j] += q_bound * digit
            constants = limbs(offset * m, columns, width)
            for i in range(columns):
                bounds[i] += constants[i] + (x_bounds[i] if i < length else 0)
            cbound = (max(bounds) + (1 << width) - 2) // ((1 << width) - 1) + 1
            carry_bits = max(2, cbound.bit_length() + 2)
            linear_estimate += qbits + qlength + columns + (columns - 1) * (carry_bits + 1)
    if row_count != rows:
        raise ValueError("native linear row count mismatch")
    public = 1 + len(public_values) * length
    qlength = (k + width) // width
    columns = length + qlength
    x_bounds = [(1 << min(width, k - i * width)) - 1 for i in range(length)]
    gate_bounds = [0] * columns
    for i, a in enumerate(x_bounds):
        for j, b in enumerate(x_bounds):
            gate_bounds[i + j] += a * b
    for i in range(qlength):
        bound = (1 << min(width, k + 1 - i * width)) - 1
        for j, digit in enumerate(limbs(m, length, width)):
            gate_bounds[i + j] += bound * digit
    for i, bound in enumerate(x_bounds):
        gate_bounds[i] += bound
    cbound = (max(gate_bounds) + (1 << width) - 2) // ((1 << width) - 1) + 1
    carry_bits = max(2, cbound.bit_length() + 2)
    per_gate = (3 * (k + length) + k + 1 + qlength + length * length
                + columns + (columns - 1) * (carry_bits + 1))
    estimated = gates * per_gate + linear_estimate + len(public_values) * (k + length)
    statement = hashlib.sha256()
    public_header_keys = ("format", "modulus", "paillier_modulus", "gate_count", "linear_count", "bits", "messages",
                          "range_proof_count", "slot_bytes", "slots_per_message", "messages_per_batch", "batch_count")
    if meta.get("scope") == "arithmetic_self_test":
        selected = {key: meta[key] for key in public_header_keys if key in meta}
        statement.update(b"arithmetic-self-test-statement-v1\n")
    else:
        selected = {key: meta[key] for key in public_header_keys}
        statement.update(b"v6-public-ring-statement-v1\n")
    statement.update(json.dumps(selected, sort_keys=True, separators=(",", ":")).encode("ascii"))
    statement.update(b"\n")
    # Coefficients, row order and all K are public. The witness is not hashed here.
    with (root / "linear.tsv").open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            statement.update(chunk)
    if "statement_id_algorithm" in meta and meta.get("statement_id") != statement.hexdigest():
        raise ValueError("original public statement hash mismatch")
    return meta, public_values, {
        "format": "dizk-resource-estimate-v1", "estimate_only": True,
        "statement_id": statement.hexdigest(), "original_gates": gates,
        "original_linear_rows": rows, "original_linear_terms": terms_count,
        "ring_bits": k, "limb_bits": width, "public_inputs": public,
        "curve": "BN254b", "field_modulus": str(FR), "fft_domain_max": 1 << 50,
        "estimated_constraints_per_original_gate": per_gate,
        "estimated_constraints_upper": estimated,
        "estimated_text_disk_gb_range": [round(estimated * 60 / 1e9, 2), round(estimated * 200 / 1e9, 2)],
        "estimated_java_memory_gb_range": [round(estimated * 160 / 1e9, 2), round(estimated * 700 / 1e9, 2)],
        "resource_estimate_note": "Conservative constraint estimate; byte/heap ranges are planning heuristics, not measurements. Spark can require more memory or spill. Run --estimate-only on the actual exported relation first.",
    }


class Writer(object):
    def __init__(self, root):
        self.assignment = (root / "assignment.tsv").open("w", encoding="ascii", newline="\n", buffering=1048576)
        self.constraints = (root / "constraints.tsv").open("w", encoding="ascii", newline="\n", buffering=1048576)
        self.variables = 0
        self.rows = 0
        self.max_integer_equation_bound = 0
        self.one = self.var(1, 1)

    def var(self, value, bound):
        if not 0 <= value <= bound < FR:
            raise ValueError("variable witness outside its declared integer bound")
        v = (self.variables, 1, value, bound)
        self.assignment.write(str(self.variables) + "\t" + str(value) + "\n")
        self.variables += 1
        return v

    @staticmethod
    def scale(v, coefficient):
        return (v[0], v[1] * coefficient, v[2], v[3])

    @staticmethod
    def normalize(terms):
        combined = {}
        for index, coefficient, value, bound in terms:
            if index in combined:
                old = combined[index]
                if old[1:] != (value, bound):
                    raise ValueError("inconsistent duplicate variable metadata")
                combined[index] = (old[0] + coefficient, value, bound)
            else:
                combined[index] = (coefficient, value, bound)
        return [(i, c, v, b) for i, (c, v, b) in sorted(combined.items()) if c]

    def emit(self, a, b, c):
        sides = [self.normalize(side) for side in (a, b, c)]
        evaluations = [sum(t[1] * t[2] for t in side) % FR for side in sides]
        if (evaluations[0] * evaluations[1] - evaluations[2]) % FR:
            raise ValueError("generated R1CS witness failed at row " + str(self.rows))
        self.constraints.write("\t".join(",".join(str(t[0]) + ":" + str(t[1] % FR) for t in side if t[1] % FR)
                                           for side in sides) + "\n")
        self.rows += 1

    def zero(self, terms, constant=0):
        terms = list(terms)
        if constant:
            terms.append(self.scale(self.one, constant))
        terms = self.normalize(terms)
        bound = sum(abs(t[1]) * t[3] for t in terms)
        self.max_integer_equation_bound = max(self.max_integer_equation_bound, bound)
        if bound >= FR:
            raise ValueError("integer column may wrap BN254; reduce limb bits")
        if sum(t[1] * t[2] for t in terms) != 0:
            raise ValueError("generated integer equality is false")
        self.emit(terms, [self.one], [])

    def range(self, v, bits):
        if v[3] != (1 << bits) - 1:
            raise ValueError("range bound does not match bits")
        parts = []
        for bit in range(bits):
            b = self.var((v[2] >> bit) & 1, 1)
            self.emit([b], [b, self.scale(self.one, -1)], [])
            parts.append(self.scale(b, -(1 << bit)))
        self.zero([v] + parts)

    def integer(self, value, bits, width):
        count = (bits + width - 1) // width
        parts = []
        for i, digit in enumerate(limbs(value, count, width)):
            b = min(width, bits - i * width)
            v = self.var(digit, (1 << b) - 1)
            self.range(v, b)
            parts.append(v)
        return parts

    def column_equations(self, columns, constants, width):
        base = 1 << width
        columns = [self.normalize(c) for c in columns]
        # |d_t| <= D and |carry_t| <= D/(B-1) by the radix recurrence.
        d_bound = max(sum(abs(t[1]) * t[3] for t in c) + abs(z) for c, z in zip(columns, constants))
        carry_bound = (d_bound + base - 2) // (base - 1) + 1
        carry_bits = max(2, carry_bound.bit_length() + 2)
        offset = 1 << (carry_bits - 1)
        previous = None
        carry_value = 0
        for i, (terms, constant) in enumerate(zip(columns, constants)):
            value = sum(t[1] * t[2] for t in terms) + constant + carry_value
            if value % base:
                raise ValueError("invalid integer quotient/carry at column " + str(i))
            next_value = value // base
            last = i == len(columns) - 1
            if last and next_value != 0:
                raise ValueError("nonzero final integer carry")
            following = None if last else self.var(next_value + offset, (1 << carry_bits) - 1)
            if following is not None:
                self.range(following, carry_bits)
            equation = list(terms)
            adjusted = constant
            if previous is not None:
                equation.append(previous)
                adjusted -= offset
            if following is not None:
                equation.append(self.scale(following, -base))
                adjusted += base * offset
            self.zero(equation, adjusted)
            previous, carry_value = following, next_value

    def close(self):
        self.assignment.close()
        self.constraints.close()


def compile_relation(input_dir, output_dir, width=64, progress=True):
    start = time.monotonic()
    source_hashes = {name: sha_file(input_dir / name) for name in ("header.json", "gates.tsv", "linear.tsv")}
    source, public_values, estimate = inspect(input_dir, width)
    if any(sha_file(input_dir / name) != digest for name, digest in source_hashes.items()):
        raise ValueError("native source input changed while inspecting the relation")
    if output_dir.exists():
        raise ValueError("output already exists; preserve prior experiment")
    output_dir.mkdir(parents=True)
    marker = output_dir / ".incomplete"
    marker.write_text("Compilation is incomplete; do not prove this input.\n", encoding="ascii")
    (output_dir / "estimate.json").write_text(json.dumps(estimate, indent=2) + "\n", encoding="utf-8")
    m, k = int(source["modulus"]), int(source["modulus"]).bit_length()
    length = (k + width - 1) // width
    m_digits = limbs(m, length, width)
    gates = int(source["gate_count"])
    record = struct.Struct("<QQ")
    wire_path = output_dir / "wiremap.bin"
    w = Writer(output_dir)
    try:
        public_wires = {}
        for value in public_values:
            public_wires[value] = [w.var(digit, (1 << min(width, k - i * width)) - 1)
                                   for i, digit in enumerate(limbs(value, length, width))]
        primary = w.variables
        for parts in public_wires.values():
            for i, v in enumerate(parts):
                w.range(v, min(width, k - i * width))
        with wire_path.open("w+b", buffering=1048576) as mapping:
            count = 0
            with (input_dir / "gates.tsv").open("r", encoding="ascii") as f:
                for count, line in enumerate(f, 1):
                    if count > gates:
                        raise ValueError("extra native gate rows")
                    data = line.rstrip("\n\r").split("\t")
                    if len(data) != 4 or int(data[0]) != count - 1:
                        raise ValueError("native gate index/shape mismatch")
                    values = [int(x) for x in data[1:]]
                    if not all(0 <= x < m for x in values) or values[0] * values[1] % m != values[2]:
                        raise ValueError("native multiplication witness invalid")
                    abc = [w.integer(x, k, width) for x in values]
                    for parts in abc:
                        for v in parts:
                            mapping.write(record.pack(v[0], v[2]))
                    quotient = (values[0] * values[1] - values[2]) // m
                    q = w.integer(quotient, k + 1, width)
                    columns = [[] for _ in range(length + len(q))]
                    for i, a in enumerate(abc[0]):
                        for j, b in enumerate(abc[1]):
                            bound = a[3] * b[3]
                            if bound >= FR:
                                raise ValueError("limb product can wrap field")
                            product = w.var(a[2] * b[2], bound)
                            w.emit([a], [b], [product])
                            columns[i + j].append(product)
                    for i, v in enumerate(q):
                        for j, digit in enumerate(m_digits):
                            if digit:
                                columns[i + j].append(w.scale(v, -digit))
                    for i, v in enumerate(abc[2]):
                        columns[i].append(w.scale(v, -1))
                    w.column_equations(columns, [0] * len(columns), width)
                    if progress and count % 1000 == 0:
                        print("Lifted gates {}/{}; constraints {}; elapsed {:.1f}s".format(count, gates, w.rows, time.monotonic() - start), flush=True)
            if count != gates:
                raise ValueError("native gate count mismatch")
            mapping.flush()

            def get_wire(kind, index):
                mapping.seek(((index * 3 + KINDS[kind]) * length) * record.size)
                raw = mapping.read(length * record.size)
                if len(raw) != length * record.size:
                    raise ValueError("truncated wire index")
                return [(index_v, 1, value, (1 << min(width, k - i * width)) - 1)
                        for i, (index_v, value) in enumerate(record.iter_unpack(raw))]

            with (input_dir / "linear.tsv").open("r", encoding="ascii") as f:
                row_count = 0
                for row_count, line in enumerate(f, 1):
                    if row_count > int(source["linear_count"]):
                        raise ValueError("extra native linear rows")
                    value, terms = read_row(line, row_count - 1, m, gates)
                    wires = [(coefficient, get_wire(kind, index)) for kind, index, coefficient in terms]
                    total = sum(coef * sum(v[2] << (i * width) for i, v in enumerate(parts)) for coef, parts in wires)
                    if (total - value) % m:
                        raise ValueError("native linear witness invalid at row " + str(row_count - 1))
                    q = (total - value) // m
                    offset = 2 * sum(-t[2] for t in terms if t[2] < 0) + 2
                    qbits = max(1, (2 * sum(abs(t[2]) for t in terms) + 4).bit_length())
                    quotient = w.integer(q + offset, qbits, width)
                    coefficient_digits = [(1 if c > 0 else -1,
                                           limbs(abs(c), (abs(c).bit_length() + width - 1) // width, width), parts)
                                          for c, parts in wires]
                    constant_number = offset * m
                    const_count = max(1, (constant_number.bit_length() + width - 1) // width)
                    columns_count = max(length + len(quotient), const_count,
                                        max([length + len(d) for s, d, parts in coefficient_digits] or [length]))
                    columns = [[] for _ in range(columns_count)]
                    constants = limbs(constant_number, columns_count, width)
                    for sign, digits, parts in coefficient_digits:
                        for i, v in enumerate(parts):
                            for j, digit in enumerate(digits):
                                if digit:
                                    columns[i + j].append(w.scale(v, sign * digit))
                    for i, v in enumerate(quotient):
                        for j, digit in enumerate(m_digits):
                            if digit:
                                columns[i + j].append(w.scale(v, -digit))
                    for i, v in enumerate(public_wires[value]):
                        columns[i].append(w.scale(v, -1))
                    w.column_equations(columns, constants, width)
                    if progress and row_count % 1000 == 0:
                        print("Lifted linear rows {}/{}; constraints {}; elapsed {:.1f}s".format(row_count, source["linear_count"], w.rows, time.monotonic() - start), flush=True)
            if row_count != int(source["linear_count"]):
                raise ValueError("native linear count mismatch")
        w.close()
        if any(sha_file(input_dir / name) != digest for name, digest in source_hashes.items()):
            raise ValueError("native source input changed during compilation")
        header = {
            "format": "dizk-r1cs-v2", "field_modulus": str(FR),
            "scope": "arithmetic_self_test" if source.get("scope") == "arithmetic_self_test" else "v6_ring_lifted",
            "same_statement": source.get("scope") != "arithmetic_self_test",
            "variables": w.variables, "constraints": w.rows, "primary_inputs": primary,
            "messages": int(source["messages"]), "paillier_bits": int(source["bits"]),
            "original_ring_modulus": str(m), "paillier_modulus": source["paillier_modulus"],
            "statement_id": estimate["statement_id"],
            "relation_hash": sha_file(output_dir / "constraints.tsv"),
            "assignment_hash": sha_file(output_dir / "assignment.tsv"),
            "limb_bits": width, "original_gates": gates,
            "original_linear_rows": int(source["linear_count"]),
            "max_integer_equation_bound": str(w.max_integer_equation_bound),
            "compiler_wall_seconds": time.monotonic() - start,
            "public_input_order": "constant 1, then distinct original public K in first-row occurrence order, little-endian limbs",
            "public_k_values": [str(x) for x in public_values],
            "source_header_sha256": source_hashes["header.json"],
            "source_gate_witness_sha256": source_hashes["gates.tsv"],
            "source_linear_sha256": source_hashes["linear.tsv"],
            "source_metadata": source,
            "security_scope": "Same original modular gate/linear relation by integer lifting. Adapter is experimental and has not undergone a cryptographic security audit. Different proof system and security parameters.",
        }
        (output_dir / "header.json").write_text(json.dumps(header, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        wire_path.unlink()
        marker.unlink()
        return header
    finally:
        w.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--output")
    p.add_argument("--limb-bits", type=int, default=64)
    p.add_argument("--estimate-only", action="store_true")
    args = p.parse_args()
    if not 8 <= args.limb_bits <= 64:
        p.error("limb bits must be in [8,64]")
    root = Path(args.input).resolve()
    if args.estimate_only:
        meta, public, report = inspect(root, args.limb_bits)
        print(json.dumps(report, indent=2))
        if args.output:
            path = Path(args.output)
            if path.exists():
                raise ValueError("estimate output already exists")
            path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    else:
        if not args.output:
            p.error("--output is required for compilation")
        header = compile_relation(root, Path(args.output).resolve(), args.limb_bits)
        print(json.dumps({key: header[key] for key in ("variables", "constraints", "primary_inputs", "statement_id", "compiler_wall_seconds")}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        sys.exit(1)
