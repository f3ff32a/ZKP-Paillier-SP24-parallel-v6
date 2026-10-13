#!/usr/bin/env python3
"""Meaningful arithmetic/count/identity guards; CSV fixtures are not proof data."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tracemalloc
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "tools" / "generate_scale_inputs.py"
CHECK_INPUT = ROOT / "runtime" / "tools" / "check_input.py"
CHECK_RESULTS = ROOT / "runtime" / "tools" / "check_results.py"


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


generator = module(GENERATOR, "scale_generator_test")
results = module(CHECK_RESULTS, "scale_results_test")
REFERENCE = "a" * 64


def run(path, args):
    return subprocess.run([sys.executable, str(path)] + [str(x) for x in args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


class ScaleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="scale-runtime-tests-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def make(self, name="input", mul=7, linear=9):
        path = self.root / name
        header = generator.generate(path, 1, mul, linear, REFERENCE)
        return path, header

    def preflight(self, path, suffix="snapshot", extra=None):
        return run(CHECK_INPUT, ["--input", path, "--output", self.root / (suffix + ".json")]
                   + (["--constraint-scale"] if extra is None else extra))

    def rewrite_header(self, path, header):
        (path / "header.json").write_text(json.dumps(header), encoding="utf-8")

    def test_actual_math_counts_all_variables_and_public_output(self):
        for mul, linear in [(1, 0), (0, 1), (7, 9), (37, 11), (1, 29), (29, 1)]:
            path, h = self.make("input-{}-{}".format(mul, linear), mul, linear)
            field = int(h["field_modulus"])
            witness = {}
            for line in (path / "assignment.tsv").read_text().splitlines():
                index, value = [int(x) for x in line.split("\t")]
                self.assertNotIn(index, witness)
                self.assertTrue(0 < value < field)
                witness[index] = value
            self.assertEqual(set(witness), set(range(h["variables"])))
            self.assertEqual([str(witness[0]), str(witness[1])], h["public_values"])
            involved = set()
            actual_mul = actual_linear = 0
            for line in (path / "constraints.tsv").read_text().splitlines():
                sides = line.split("\t")
                self.assertEqual(len(sides), 3)
                values = []
                for side in sides:
                    value = 0
                    for term in side.split(","):
                        index, coefficient = [int(x) for x in term.split(":")]
                        self.assertTrue(coefficient > 0)
                        involved.add(index)
                        value += witness[index] * coefficient
                    values.append(value % field)
                self.assertEqual(values[0] * values[1] % field, values[2])
                if sides[1] == "0:1":
                    actual_linear += 1
                else:
                    actual_mul += 1
            self.assertEqual((actual_mul, actual_linear), (mul, linear))
            self.assertEqual(involved, set(witness))
            self.assertFalse(h["same_statement"])
            self.assertEqual(h["paillier_bits"], 0)
            self.assertEqual(self.preflight(path, "check-{}-{}".format(mul, linear)).returncode, 0)

    def test_reproducible_and_refuses_existing_output(self):
        first, h1 = self.make("first")
        second, h2 = self.make("second")
        self.assertEqual(h1, h2)
        for filename in ["constraints.tsv", "assignment.tsv", "header.json"]:
            self.assertEqual((first / filename).read_bytes(), (second / filename).read_bytes())
        original = (first / "constraints.tsv").read_bytes()
        with self.assertRaises(ValueError):
            generator.generate(first, 1, 7, 9, REFERENCE)
        self.assertEqual((first / "constraints.tsv").read_bytes(), original)

    def test_generation_is_streaming_low_peak(self):
        tracemalloc.start()
        path, h = self.make("streaming", 10000, 10000)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertLess(peak, 12 * 1024 * 1024)
        self.assertGreater((path / "assignment.tsv").stat().st_size, 1000000)
        self.assertEqual(h["constraints"], 20000)

    def test_scope_modes_are_isolated(self):
        path, h = self.make()
        self.assertNotEqual(self.preflight(path, "strict", []).returncode, 0)
        self.assertNotEqual(self.preflight(path, "arith", ["--adapter-self-test"]).returncode, 0)
        h["same_statement"] = True
        self.rewrite_header(path, h)
        self.assertNotEqual(self.preflight(path, "falseclaim").returncode, 0)

    def test_public_and_hash_and_math_tampering_are_rejected(self):
        path, h = self.make("public")
        h["public_values"][1] = str((int(h["public_values"][1]) + 1) % generator.FIELD or 1)
        self.rewrite_header(path, h)
        self.assertNotEqual(self.preflight(path, "public-out").returncode, 0)
        path, h = self.make("hash")
        with (path / "constraints.tsv").open("a") as stream:
            stream.write("0:1\t0:1\t0:1\n")
        self.assertNotEqual(self.preflight(path, "hash-out").returncode, 0)
        path, h = self.make("math")
        lines = (path / "assignment.tsv").read_text().splitlines()
        lines[1] = "2\t{}".format(int(lines[1].split("\t")[1]) + 1)
        (path / "assignment.tsv").write_text("\n".join(lines) + "\n")
        # A changed hash alone cannot make an invalid mathematical assignment pass.
        h["assignment_hash"] = generator.file_sha(path / "assignment.tsv")
        self.rewrite_header(path, h)
        self.assertNotEqual(self.preflight(path, "math-out").returncode, 0)

    def test_count_and_generator_metadata_tampering_are_rejected(self):
        for key, value in [("multiplication_constraints", 8), ("variables", 999),
                           ("generator_id", "padding"), ("reference_paillier_bits", 256),
                           ("reference_csv_sha256", "wrong"), ("paillier_bits", 512)]:
            path, h = self.make("tamper-" + key)
            h[key] = value
            self.rewrite_header(path, h)
            self.assertNotEqual(self.preflight(path, "out-" + key).returncode, 0)

    def csv_fixture(self):
        path, h = self.make()
        self.assertEqual(self.preflight(path).returncode, 0)
        snapshot = json.loads((self.root / "snapshot.json").read_text())
        row = {key: "0" for key in results.REQUIRED + results.SCALE_REQUIRED}
        row.update(run_id="checker-fixture-not-measurement", scope=h["scope"], same_statement="false",
                   mode="distributed", curve="BN254b", scalar_field_modulus=h["field_modulus"],
                   requested_threads="2", threads="2", partitions="2", repeat="1", verified="true",
                   upstream_commit=results.COMMIT, input_bundle_sha256=snapshot["input_bundle_sha256"],
                   header_sha256=snapshot["sha256"]["header.json"],
                   constraints_sha256=snapshot["sha256"]["constraints.tsv"],
                   assignment_sha256=snapshot["sha256"]["assignment.tsv"], relation_hash=h["relation_hash"],
                   proof_sha256="b" * 64, max_heap_bytes="1024", setup_seconds="1", prove_seconds="2",
                   verify_seconds="3", proof_system_seconds="6", total_task_seconds="8")
        for key in ["paillier_bits", "messages", "constraints", "variables", "primary_inputs"] + results.SCALE_REQUIRED:
            row[key] = str(h[key])
        return path, h, row

    def check_csv(self, row, extra=None):
        output = self.root / "checker-fixture-not-measurement.csv"
        with output.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=results.REQUIRED + results.SCALE_REQUIRED)
            writer.writeheader()
            writer.writerow(row)
        return run(CHECK_RESULTS, ["--csv", output, "--run-id", "checker-fixture-not-measurement",
                   "--mode", "distributed", "--threads", 2, "--partitions", 2, "--repeat", 1,
                   "--input-record", self.root / "snapshot.json"]
                   + (["--constraint-scale"] if extra is None else extra))

    def test_result_checker_accepts_scale_and_rejects_mixing(self):
        _, _, row = self.csv_fixture()
        self.assertEqual(self.check_csv(row).returncode, 0)
        self.assertNotEqual(self.check_csv(row, []).returncode, 0)
        self.assertNotEqual(self.check_csv(row, ["--adapter-self-test"]).returncode, 0)
        for key, value in [("same_statement", "true"), ("verified", "false"),
                           ("multiplication_constraints", "999"), ("generator_id", "padding"),
                           ("reference_paillier_bits", "256"), ("header_sha256", "c" * 64),
                           ("constraints", "17"), ("prove_seconds", "nan")]:
            bad = dict(row)
            bad[key] = value
            self.assertNotEqual(self.check_csv(bad).returncode, 0)

    def test_post_run_input_change_is_rejected(self):
        path, _, row = self.csv_fixture()
        with (path / "assignment.tsv").open("a") as stream:
            stream.write("999\t1\n")
        self.assertNotEqual(self.check_csv(row).returncode, 0)

    def test_reject_invalid_generation_request(self):
        for messages, mul, linear, sha in [(2, 1, 1, REFERENCE), (1, 0, 0, REFERENCE),
                                          (1, -1, 2, REFERENCE), (1, 1, 1, "X" * 64)]:
            with self.assertRaises(ValueError):
                generator.generate(self.root / "invalid", messages, mul, linear, sha)
            self.assertFalse((self.root / "invalid").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
