#!/usr/bin/env python3
"""Evidence pairing guards: all data below is explicitly artificial unit-test data."""
from __future__ import print_function
import json
import csv
from pathlib import Path
import shutil
import tempfile
import unittest
from summarize import pair, sha


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.case = Path(tempfile.mkdtemp(prefix="dizk-pair-test-"))
        raw, lifted, run = [self.case / x for x in ("native", "lifted", "dizk")]
        for path in (raw, lifted, run):
            path.mkdir()
        self.config = {"threads": 16, "run_id": "artificial-unit-test"}
        (raw / "gates.tsv").write_text("ARTIFICIAL_UNIT_TEST\n", encoding="ascii")
        (raw / "linear.tsv").write_text("0\t1\tARTIFICIAL_UNIT_TEST\n", encoding="ascii")
        (raw / "header.json").write_text(json.dumps({"statement_id": "artificial-statement", "witness_id": sha(raw / "gates.tsv")}), encoding="utf-8")
        self.native = {"verified": True, "raw_public_statement_matched": True,
                       "raw_export_same_circuit_and_witness": True, "statement_id": "artificial-statement",
                       "witness_id": sha(raw / "gates.tsv"), "actual_bits": 512, "messages": 1,
                       "threads_requested": 16, "openmp_max_threads": 16, "repeat": 1,
                       "proof_generation_seconds": 2, "verify_seconds": 1, "generator_setup_seconds": 1}
        self.save_native()
        (lifted / "assignment.tsv").write_text("ARTIFICIAL_UNIT_TEST\n", encoding="ascii")
        (lifted / "constraints.tsv").write_text("ARTIFICIAL_UNIT_TEST\n", encoding="ascii")
        self.header = {"same_statement": True, "statement_id": "artificial-statement",
                       "source_gate_witness_sha256": sha(raw / "gates.tsv"), "source_header_sha256": sha(raw / "header.json"),
                       "source_linear_sha256": sha(raw / "linear.tsv"), "relation_hash": sha(lifted / "constraints.tsv"),
                       "original_gates": 1, "original_linear_rows": 1, "constraints": 1, "variables": 2,
                       "compiler_wall_seconds": 1, "public_k_values": ["1"]}
        (lifted / "header.json").write_text(json.dumps(self.header), encoding="utf-8")
        hashes = {name: sha(lifted / name) for name in ("header.json", "assignment.tsv", "constraints.tsv")}
        (run / "input-files.json").write_text(json.dumps({"sha256": hashes, "input_bundle_sha256": "artificial-bundle"}), encoding="utf-8")
        (run / "COMPLETE.txt").write_text("ARTIFICIAL_UNIT_TEST\n", encoding="ascii")
        self.row = {"verified": "true", "same_statement": "true", "scope": "v6_ring_lifted",
                    "header_sha256": hashes["header.json"], "constraints_sha256": hashes["constraints.tsv"],
                    "assignment_sha256": hashes["assignment.tsv"], "relation_hash": self.header["relation_hash"],
                    "threads": "16", "paillier_bits": "512", "messages": "1", "repeat": "1",
                    "prove_seconds": "4", "verify_seconds": "2", "setup_seconds": "3",
                    "input_bundle_sha256": "artificial-bundle", "run_id": "artificial-unit-test_MC1r1", "mode": "distributed"}
        self.save_row()

    def tearDown(self):
        shutil.rmtree(str(self.case))
    def save_native(self):
        (self.case / "native" / "v6_native_timing.json").write_text(json.dumps(self.native), encoding="utf-8")
    def save_row(self):
        with (self.case / "dizk" / "results.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(self.row)); writer.writeheader(); writer.writerow(self.row)
    def test_valid_identity(self):
        result = pair(self.case, self.config, 1, 1)
        self.assertEqual(result["dizk_over_v6_prover_time_ratio"], 2)
    def test_native_false_or_wrong_statement(self):
        for key, value in (("verified", False), ("statement_id", "historical"), ("witness_id", "different")):
            original = self.native[key]; self.native[key] = value; self.save_native()
            with self.assertRaises(ValueError):
                pair(self.case, self.config, 1, 1)
            self.native[key] = original
    def test_dizk_false_synthetic_or_nan(self):
        for key, value in (("verified", "false"), ("scope", "synthetic_smoke"), ("prove_seconds", "nan"), ("threads", "1")):
            original = self.row[key]; self.row[key] = value; self.save_row()
            with self.assertRaises(ValueError):
                pair(self.case, self.config, 1, 1)
            self.row[key] = original
    def test_incomplete_and_changed_input(self):
        marker = self.case / "lifted" / ".incomplete"
        marker.write_text("incomplete", encoding="ascii")
        with self.assertRaises(ValueError):
            pair(self.case, self.config, 1, 1)
        marker.unlink()
        (self.case / "lifted" / "assignment.tsv").write_text("changed", encoding="ascii")
        with self.assertRaises(ValueError):
            pair(self.case, self.config, 1, 1)


if __name__ == "__main__":
    unittest.main()
