#!/usr/bin/env python3
from __future__ import print_function
import json
from pathlib import Path
import tempfile
import unittest
from validate_raw import audit_raw, statement_digest, file_digest


class RawTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.header = {"format": "v6-ring-v1", "modulus": "20449", "paillier_modulus": "143",
                       "scalar_domain_kind": "composite_ring_mod_n_squared", "bits": 8,
                       "gate_count": 1, "linear_count": 1, "native_total_constraints": 2,
                       "messages": 1, "statement_instance_status": "semantic_fixture_only"}
        self.write_header()
        (self.directory / "gates.tsv").write_text("0\t143\t143\t0\n", encoding="utf-8")
        (self.directory / "linear.tsv").write_text("0\t0\tA:0:1,B:0:20448\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_header(self):
        (self.directory / "header.json").write_text(json.dumps(self.header), encoding="utf-8")

    def test_zero_divisor_relation_is_preserved(self):
        report = audit_raw(self.directory)
        self.assertEqual(report["status"], "ORIGINAL_V6_RING_ASSIGNMENT_VALID")
        self.assertEqual(report["native_total_constraints"], 2)

    def test_negative_one_coefficient_canonical(self):
        (self.directory / "linear.tsv").write_text("0\t1\tA:0:1,B:0:20448,C:0:1\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "linear row 0"):
            audit_raw(self.directory)

    def test_bad_product(self):
        (self.directory / "gates.tsv").write_text("0\t143\t143\t1\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "multiplication gate"):
            audit_raw(self.directory)

    def test_noncanonical_value(self):
        (self.directory / "gates.tsv").write_text("0\t20449\t0\t0\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "non-canonical"):
            audit_raw(self.directory)

    def test_missing_gate(self):
        (self.directory / "gates.tsv").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            audit_raw(self.directory)

    def test_missing_linear_row(self):
        (self.directory / "linear.tsv").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            audit_raw(self.directory)

    def test_repeated_wire(self):
        (self.directory / "linear.tsv").write_text("0\t0\tA:0:1,A:0:20448\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            audit_raw(self.directory)

    def test_wrong_public_modulus_bits(self):
        self.header["bits"] = 9
        self.write_header()
        with self.assertRaisesRegex(ValueError, "bit length"):
            audit_raw(self.directory)

    def test_empty_linear_expression(self):
        (self.directory / "linear.tsv").write_text("0\t0\t\n", encoding="utf-8")
        self.assertEqual(audit_raw(self.directory)["linear_nonzero_terms"], 0)

    def test_statement_and_witness_identity_are_bound(self):
        self.header.update({"range_proof_count": 128, "slot_bytes": 4, "slots_per_message": 16,
                            "messages_per_batch": 15, "batch_count": 1})
        self.header["statement_id"] = statement_digest(self.directory, self.header)
        self.header["witness_id"] = file_digest(self.directory / "gates.tsv")
        self.write_header()
        self.assertEqual(audit_raw(self.directory)["statement_id"], self.header["statement_id"])
        (self.directory / "linear.tsv").write_text("0\t0\tA:0:1,B:0:20448,C:0:1\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "statement_id"):
            audit_raw(self.directory)

    def test_wrong_private_witness_identity(self):
        self.header["witness_id"] = "0" * 64
        self.write_header()
        with self.assertRaisesRegex(ValueError, "witness_id"):
            audit_raw(self.directory)


if __name__ == "__main__":
    unittest.main()
