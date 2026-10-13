#!/usr/bin/env python3
"""Semantic regression fixtures only: not cryptographic performance measurements."""
from __future__ import print_function
import json
from pathlib import Path
import tempfile
import unittest
from validate_native import BN254B_FR, check_export


def fixture(directory):
    # Genuine composite modulus: N=11*13; N*N=0 (mod N²), but not in BN254.
    meta = {"format": "v6-native-ring-v1", "scalar_domain_kind": "composite_ring_mod_n_squared",
            "paillier_public_key": "143", "ring_modulus": "20449", "paillier_bits": 8,
            "num_variables": 5, "num_public_inputs": 2, "num_constraints": 2,
            "num_multiplication_constraints": 1, "num_linear_constraints": 1,
            "private_assignment_offsets": {"A": 2, "B": 3, "C": 4},
            "bn254_direct_import_allowed": False, "statement_instance_status": "semantic_fixture_only"}
    (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (directory / "assignment.tsv").write_text("0\t1\n1\t0\n2\t143\n3\t143\n4\t0\n", encoding="utf-8")
    (directory / "matrix.tsv").write_text("0\tA\t2\t1\n0\tB\t3\t1\n0\tC\t4\t1\n"
                                         "1\tA\t4\t1\n1\tA\t1\t20448\n1\tB\t0\t1\n", encoding="utf-8")
    return meta


class RingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.meta = fixture(self.directory)

    def tearDown(self):
        self.temp.cleanup()

    def test_true_ring_relation(self):
        report = check_export(self.directory)
        self.assertTrue(report["assignment_checked"])
        self.assertEqual(report["constraints_checked"], 2)

    def test_bn254_reduction_changes_relation(self):
        self.assertEqual((143 * 143) % 20449, 0)
        self.assertNotEqual((143 * 143) % BN254B_FR, 0)
        report = check_export(self.directory, require_bn254=True)
        self.assertEqual(report["status"], "DOMAIN_MISMATCH")
        self.assertEqual(report["bn254_diagnostic_curve"], "BN254b")
        self.assertEqual(int(report["bn254_scalar_field_modulus"]), BN254B_FR)
        self.assertFalse(report["assignment_checked"])

    def test_corrupt_witness(self):
        text = (self.directory / "assignment.tsv").read_text(encoding="utf-8")
        (self.directory / "assignment.tsv").write_text(text.replace("4\t0", "4\t1"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unsatisfied"):
            check_export(self.directory)

    def test_public_value_changes_statement(self):
        text = (self.directory / "assignment.tsv").read_text(encoding="utf-8")
        (self.directory / "assignment.tsv").write_text(text.replace("1\t0", "1\t1"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "constraint 1"):
            check_export(self.directory)

    def test_wrong_ring_modulus(self):
        self.meta["ring_modulus"] = "20450"
        (self.directory / "meta.json").write_text(json.dumps(self.meta), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "N squared"):
            check_export(self.directory)

    def test_duplicate_matrix_term(self):
        text = (self.directory / "matrix.tsv").read_text(encoding="utf-8")
        (self.directory / "matrix.tsv").write_text("0\tA\t2\t1\n" + text, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            check_export(self.directory)

    def test_incorrect_actual_bits(self):
        self.meta["paillier_bits"] = 9
        (self.directory / "meta.json").write_text(json.dumps(self.meta), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "bit length"):
            check_export(self.directory)

    def test_private_public_partition(self):
        self.meta["private_assignment_offsets"]["A"] = 1
        (self.directory / "meta.json").write_text(json.dumps(self.meta), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "offsets"):
            check_export(self.directory)

    def test_missing_constraint_cannot_pass_as_zero_relation(self):
        lines = (self.directory / "matrix.tsv").read_text(encoding="utf-8").splitlines(True)
        (self.directory / "matrix.tsv").write_text("".join(lines[3:]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing native-ring constraint"):
            check_export(self.directory)


if __name__ == "__main__":
    unittest.main()
