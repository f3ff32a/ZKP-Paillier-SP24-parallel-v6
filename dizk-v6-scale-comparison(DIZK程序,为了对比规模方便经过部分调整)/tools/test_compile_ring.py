#!/usr/bin/env python3
"""Independent relation checks and real modular-arithmetic self-test fixtures."""
from __future__ import print_function
import argparse
import json
from pathlib import Path
import random
import shutil
import tempfile
import unittest
from compile_ring_inputs import FR, compile_relation, inspect, sha_file


def write_raw(root, n, gates, rows, bits=None):
    root.mkdir(parents=True)
    m = n * n
    header = {"format": "v6-ring-v1", "scope": "arithmetic_self_test", "modulus": str(m),
              "paillier_modulus": str(n), "gate_count": len(gates), "linear_count": len(rows),
              "messages": 1, "bits": bits or n.bit_length(),
              "fixture_notice": "Arithmetic adapter self-test only; not the v6 CBatchEnc experimental workload"}
    (root / "header.json").write_text(json.dumps(header, indent=2) + "\n", encoding="utf-8")
    with (root / "gates.tsv").open("w", encoding="ascii", newline="\n") as f:
        for i, values in enumerate(gates):
            f.write("\t".join(str(x) for x in (i,) + tuple(values)) + "\n")
    with (root / "linear.tsv").open("w", encoding="ascii", newline="\n") as f:
        for i, (value, terms) in enumerate(rows):
            f.write(str(i) + "\t" + str(value % m) + "\t" + ",".join(
                "{}:{}:{}".format(kind, index, coef % m) for kind, index, coef in terms if coef % m) + "\n")


def verify_files(root, override=None):
    meta = json.loads((root / "header.json").read_text(encoding="utf-8"))
    witness = []
    with (root / "assignment.tsv").open("r", encoding="ascii") as f:
        for i, line in enumerate(f):
            index, value = (int(x) for x in line.split("\t"))
            if index != i or not 0 <= value < FR:
                raise ValueError("invalid assignment")
            witness.append(value)
    if len(witness) != meta["variables"] or witness[0] != 1:
        raise ValueError("assignment count/constant mismatch")
    if override:
        witness[override[0]] = override[1]
    def evaluate(side):
        value = 0
        for entry in side.split(",") if side else []:
            index, coefficient = (int(x) for x in entry.split(":"))
            if not 0 <= index < len(witness) or not 0 <= coefficient < FR:
                raise ValueError("LC outside field/assignment")
            value = (value + coefficient * witness[index]) % FR
        return value
    rows = 0
    with (root / "constraints.tsv").open("r", encoding="ascii") as f:
        for rows, line in enumerate(f, 1):
            sides = line.rstrip("\n\r").split("\t")
            if len(sides) != 3 or (evaluate(sides[0]) * evaluate(sides[1]) - evaluate(sides[2])) % FR:
                raise ValueError("unsatisfied R1CS at row " + str(rows - 1))
    if rows != meta["constraints"]:
        raise ValueError("R1CS row count mismatch")
    if not override and (sha_file(root / "constraints.tsv") != meta["relation_hash"] or
                         sha_file(root / "assignment.tsv") != meta["assignment_hash"]):
        raise ValueError("file digest mismatch")
    return meta


def paillier_fixture(root):
    # Two actual small primes. This fixture proves encryption r^N*(1+Nm) mod N^2.
    n, message, r = 251 * 241, 17, 7
    modulus = n * n
    gates, rows = [], []
    def gate(a, b):
        index = len(gates)
        gates.append((a, b, a * b % modulus))
        return index, gates[-1][2]
    def bind(kind, index, value):
        rows.append((value, [(kind, index, 1)]))
    def link(kind, index, prior):
        rows.append((0, [(kind, index, 1), ("C", prior, -1)]))
    previous, result = None, 1
    for bit in bin(n)[2:]:
        current, result = gate(result, result)
        if previous is None:
            bind("A", current, 1); bind("B", current, 1)
        else:
            link("A", current, previous); link("B", current, previous)
        previous = current
        if bit == "1":
            current, result = gate(result, r)
            link("A", current, previous)
            # The randomizer is private and shared, rather than a public value.
            random_gate = next((i for i in range(current) if gates[i][1] == r), None)
            if random_gate is not None:
                rows.append((0, [("B", current, 1), ("B", random_gate, -1)]))
            previous = current
    mn_gate, mn = gate(message, n)
    bind("B", mn_gate, n)
    final, cipher = gate(1 + mn, result)
    rows.append((1, [("A", final, 1), ("C", mn_gate, -1)]))
    link("B", final, previous)
    bind("C", final, cipher)
    assert cipher == ((1 + n * message) * pow(r, n, modulus)) % modulus
    write_raw(root, n, gates, rows)


def generate_fixtures(destination):
    destination.mkdir(parents=True, exist_ok=True)
    paillier_fixture(destination / "toy_paillier_raw")
    compile_relation(destination / "toy_paillier_raw", destination / "toy_paillier_lifted", width=16, progress=False)
    # A public 512-bit integer N. Compiler test only, no claim of a Paillier key.
    n = (1 << 511) + (1 << 255) + 12345
    m = n * n
    a, b = m - 2, m - 3
    c = a * b % m
    rows = [(a, [("A", 0, 1)]), (b, [("B", 0, 1)]), (c, [("C", 0, 1)]),
            ((m - 1) * a + 9 * b, [("A", 0, m - 1), ("B", 0, 9)])]
    write_raw(destination / "ring512_raw", n, [(a, b, c)], rows)
    compile_relation(destination / "ring512_raw", destination / "ring512_lifted", progress=False)
    for name in ("toy_paillier_lifted", "ring512_lifted"):
        meta = verify_files(destination / name)
        print(name, "variables", meta["variables"], "constraints", meta["constraints"], "verified independently")


class LiftingTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="dizk-lift-test-"))
    def tearDown(self):
        shutil.rmtree(str(self.directory))

    def test_random_composite_ring_relations(self):
        rng = random.Random(20261013)
        for test in range(12):
            n = rng.choice((15, 77, 323, 65537))
            m = n * n
            gates = []
            for i in range(3):
                a, b = rng.randrange(m), rng.randrange(m)
                gates.append((a, b, a * b % m))
            rows = []
            for row in range(4):
                terms = [(kind, i, rng.randrange(1, m)) for i in range(3) for kind in ("A", "B", "C")]
                value = sum(coef * gates[i][{"A": 0, "B": 1, "C": 2}[kind]] for kind, i, coef in terms) % m
                rows.append((value, terms))
            raw, lifted = self.directory / ("raw" + str(test)), self.directory / ("lifted" + str(test))
            write_raw(raw, n, gates, rows)
            meta = compile_relation(raw, lifted, width=8 if test % 2 else 16, progress=False)
            self.assertEqual(verify_files(lifted)["constraints"], meta["constraints"])
            self.assertLess(int(meta["max_integer_equation_bound"]), FR)

    def test_tampered_witness_rejected(self):
        raw = self.directory / "raw"
        write_raw(raw, 77, [(3, 4, 12)], [(12, [("C", 0, 1)])])
        lifted = self.directory / "lifted"
        meta = compile_relation(raw, lifted, width=8, progress=False)
        # First private variable is a helper bit of a public K limb.
        with self.assertRaises(ValueError):
            verify_files(lifted, (meta["primary_inputs"], 2))
        (raw / "gates.tsv").write_text("0\t3\t4\t13\n", encoding="ascii")
        with self.assertRaises(ValueError):
            compile_relation(raw, self.directory / "bad")
        self.assertTrue((self.directory / "bad" / ".incomplete").exists())
        self.assertFalse((self.directory / "bad" / "header.json").exists())

    def test_linear_mismatch_and_no_overwrite(self):
        raw = self.directory / "raw"
        write_raw(raw, 15, [(3, 4, 12)], [(13, [("C", 0, 1)])])
        with self.assertRaises(ValueError):
            compile_relation(raw, self.directory / "bad")
        with self.assertRaises(ValueError):
            compile_relation(raw, self.directory / "bad")

    def test_fixed_shape_independent_of_private_witness(self):
        shapes = []
        relations = []
        for label, a, b in (("a", 3, 4), ("b", 7, 9)):
            raw, lifted = self.directory / (label + "raw"), self.directory / label
            write_raw(raw, 77, [(a, b, a * b)], [(0, [("A", 0, 1), ("A", 0, -1)])])
            # Use a valid distinct-wire identity; duplicate native terms are forbidden.
            (raw / "linear.tsv").write_text("0\t0\t\n", encoding="ascii")
            meta = compile_relation(raw, lifted, width=8, progress=False)
            shapes.append((meta["variables"], meta["constraints"]))
            relations.append(meta["relation_hash"])
        self.assertEqual(shapes[0], shapes[1])
        self.assertEqual(relations[0], relations[1])

    def test_real_paillier_and_full_1024_bit_arithmetic(self):
        generate_fixtures(self.directory / "fixtures")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures")
    args, remaining = parser.parse_known_args()
    if args.fixtures:
        generate_fixtures(Path(args.fixtures))
    else:
        unittest.main(argv=[__file__] + remaining)
