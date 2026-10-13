#!/usr/bin/env python3
"""Artificial fixtures test scale guards and partial persistence; no proofs run."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import run_scale_comparison as driver
import summarize_scale as summary


class ScaleGuards(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="artificial-scale-tests-"))
        self.csv = self.root / "v6-reference.csv"
        with self.csv.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["message count", "byte length(bit)", "batch encrypt circuit's N", "batch encrypt circuit's Q", "commit time", "prove time", "verify time"])
            writer.writerows([[1,512,2,2,2,1,1], [10,512,3,3,2,1,1]])
        self.prov = {"csv_sha256": summary.sha(self.csv), "threads":16, "repeat":1,
                     "actual_paillier_bits":512, "full_zkp_verified":True, "verified_message_counts":[1,10],
                     "verification_evidence":{"kind":"ARTIFICIAL_UNIT_TEST_NOT_A_PROOF"}}
        summary.save_json(self.root / "v6-provenance.json", self.prov)
        self.config = {"phase":"run", "scope":summary.SCOPE, "same_statement":False,
                       "threads":16, "partitions":16, "repeat":1, "messages":[1,10],
                       "run_id":"artificial-unit-test", "reference_csv_sha256":summary.sha(self.csv)}
        summary.save_json(self.root / "run-config.json", self.config)
        self.refs, unused = summary.read_reference(self.csv,self.root / "v6-provenance.json")
        self.case = self.root / "MC001" / "r01"
        self.make_input(self.case,1)
        self.make_result(self.case,1)

    def tearDown(self):
        shutil.rmtree(str(self.root))

    def make_input(self, case, mc):
        inp = case / "input"
        inp.mkdir(parents=True)
        # Deliberately artificial file payload: this tests identity guards only.
        (inp / "constraints.tsv").write_text("ARTIFICIAL_UNIT_TEST_CONSTRAINTS\n",encoding="ascii")
        (inp / "assignment.tsv").write_text("ARTIFICIAL_UNIT_TEST_ASSIGNMENT\n",encoding="ascii")
        ref = summary.reference_for(self.refs,mc,16,1)
        h = {"format":"dizk-r1cs-v2", "field_modulus":summary.FR,
             "scope":summary.SCOPE, "same_statement":False, "paillier_bits":0,
             "reference_paillier_bits":512, "generator_id":summary.GENERATOR,
             "messages":mc, "multiplication_constraints":ref["multiplication_constraints"],
             "linear_constraints":ref["linear_constraints"],"constraints":ref["constraints"],
             "variables":ref["constraints"]+3,"primary_inputs":2,
             "reference_csv_sha256":summary.sha(self.csv),
             "relation_hash":summary.sha(inp / "constraints.tsv"),
             "assignment_hash":summary.sha(inp / "assignment.tsv")}
        summary.save_json(inp / "header.json",h)

    def make_result(self, case, mc):
        run = case / "dizk"
        run.mkdir()
        inp = case / "input"
        h = json.loads((inp / "header.json").read_text())
        hashes={n:summary.sha(inp / n) for n in ("header.json","constraints.tsv","assignment.tsv")}
        bundle="".join(n+"\0"+hashes[n]+"\n" for n in ("header.json","constraints.tsv","assignment.tsv"))
        snapshot={"header":h,"sha256":hashes,"input_bundle_sha256":hashlib.sha256(bundle.encode()).hexdigest()}
        summary.save_json(run / "input-files.json",snapshot)
        row={k:str(h[k]).lower() if isinstance(h[k],bool) else str(h[k]) for k in (
             "scope","same_statement","paillier_bits","reference_paillier_bits","messages",
             "multiplication_constraints","linear_constraints","constraints","variables","primary_inputs","generator_id","relation_hash")}
        row.update(verified="true",mode="distributed",run_id="artificial-unit-test_MC{}r1".format(mc),
            curve="BN254b",scalar_field_modulus=summary.FR,upstream_commit="29fb0f78964cd6019029d20228ee0bee3184e64d",
            threads="16",requested_threads="16",partitions="16",repeat="1",proof_sha256="0"*64,
            header_sha256=hashes["header.json"],constraints_sha256=hashes["constraints.tsv"],
            assignment_sha256=hashes["assignment.tsv"],input_bundle_sha256=snapshot["input_bundle_sha256"],
            input_prepare_seconds="1",spark_start_seconds="1",rdd_materialize_seconds="1",r1cs_validate_seconds="0",
            setup_seconds="2",prove_seconds="4",verify_seconds="1",proof_system_seconds="7",total_task_seconds="10")
        self.save_row(case,row)
        (run / "SCALE_BENCHMARK_COMPLETE.txt").write_text("ARTIFICIAL_UNIT_TEST_ONLY\n")
        (run / "exit-code.txt").write_text("0\n")
        (case / "dizk-maxrss-kib.txt").write_text("12345\n")
        return row

    @staticmethod
    def save_row(case,row):
        with (case / "dizk" / "results.csv").open("w",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=list(row));writer.writeheader();writer.writerow(row)

    def row(self):
        with (self.case / "dizk" / "results.csv").open(newline="",encoding="utf-8") as f:
            return next(csv.DictReader(f))

    def pair(self):
        return summary.validate_case(self.case,self.config,self.refs[(1,16,1,512)],1,1)

    def test_valid_scale_ratio_scope_rss(self):
        row=self.pair()
        self.assertAlmostEqual(row["dizk_over_archived_v6_prover_time_ratio"],4/3)
        self.assertFalse(row["same_statement"])
        self.assertEqual(row["paillier_bits"],0)
        self.assertEqual(row["constraints"],4)
        self.assertEqual(row["maxrss_kib"],12345)

    def test_csv_guards(self):
        initial=self.row()
        for key,value in (("verified","false"),("scope","v6_ring_lifted"),("same_statement","true"),
                          ("constraints","5"),("threads","1"),("reference_paillier_bits","511"),
                          ("repeat","2"),("prove_seconds","nan"),("header_sha256","0"*64),
                          ("upstream_commit","different"),
                          ("generator_id","different"),("proof_system_seconds","999")):
            row=dict(initial);row[key]=value;self.save_row(self.case,row)
            with self.assertRaises(ValueError,msg=key):self.pair()
        self.save_row(self.case,initial)

    def test_changed_input_and_missing_marker(self):
        path=self.case / "input" / "assignment.tsv"
        original=path.read_bytes();path.write_bytes(original+b"changed")
        with self.assertRaises(ValueError):self.pair()
        path.write_bytes(original)
        (self.case / "dizk" / "SCALE_BENCHMARK_COMPLETE.txt").unlink()
        with self.assertRaises(ValueError):self.pair()

    def test_runtime_nonzero_exit_and_process_times(self):
        path=self.case / "dizk" / "exit-code.txt"
        path.write_text("9\n")
        with self.assertRaises(ValueError):self.pair()
        path.write_text("0\n")
        summary.save_json(self.case / "process-timing.json", {
            "generator_wall_seconds":1.0,"dizk_launcher_wall_seconds":12.0,
            "case_wall_seconds":13.2,"status":"launcher_completed"})
        self.assertEqual(self.pair()["dizk_launcher_wall_seconds"],12.0)
        summary.save_json(self.case / "process-timing.json", {
            "generator_wall_seconds":1.0,"dizk_launcher_wall_seconds":12.0,
            "case_wall_seconds":1.0,"status":"launcher_completed"})
        with self.assertRaises(ValueError):self.pair()

    def test_custom_reference_unknown_driver_declared(self):
        refs,unused=summary.read_reference(self.csv)
        self.assertEqual(refs[(1,16,1,512)]["verification"],"unknown")
        self.assertEqual(refs[(1,16,1,512)]["metadata_source"],"driver_declared")
        row=summary.validate_case(self.case,self.config,refs[(1,16,1,512)],1,1)
        self.assertEqual(row["v6_verification"],"unknown")

    def test_reference_mismatch_and_repeat_two_refused(self):
        bad=dict(self.prov,csv_sha256="0"*64)
        summary.save_json(self.root / "bad-provenance.json",bad)
        with self.assertRaises(ValueError):summary.read_reference(self.csv,self.root / "bad-provenance.json")
        with self.assertRaises(ValueError):summary.reference_for(self.refs,1,16,2)
        with self.assertRaises(ValueError):summary.reference_for(self.refs,1,8,1)

    def test_reference_explicit_failed_verification_overrides_provenance(self):
        with self.csv.open(newline="",encoding="utf-8") as f:
            rows=list(csv.DictReader(f));fields=list(rows[0])+["verified"]
        for row in rows:row["verified"]="false"
        with self.csv.open("w",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        summary.save_json(self.root / "v6-provenance.json",dict(self.prov,csv_sha256=summary.sha(self.csv)))
        refs,unused=summary.read_reference(self.csv,self.root / "v6-provenance.json")
        self.assertEqual(refs[(1,16,1,512)]["verification"],"false")
        with self.assertRaises(ValueError):summary.reference_for(refs,1,16,1)

    def test_reference_conflicting_metadata_rejected(self):
        for key,value in (("threads",8),("repeat",2),("actual_paillier_bits",511)):
            # Add explicit CSV labels: verified evidence cannot override conflicts.
            with self.csv.open(newline="",encoding="utf-8") as f:
                rows=list(csv.DictReader(f))
            for row in rows:row.update(threads="16",repeat="1")
            with self.csv.open("w",newline="",encoding="utf-8") as f:
                writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
            prov=dict(self.prov,csv_sha256=summary.sha(self.csv));prov[key]=value
            summary.save_json(self.root / "bad-provenance.json",prov)
            with self.assertRaises(ValueError,msg=key):summary.read_reference(self.csv,self.root / "bad-provenance.json")

    def test_unrepresentable_reference_seconds_rejected(self):
        text=self.csv.read_text(encoding="utf-8")
        for bad in ("1e9999","1e-9999","not-a-number"):
            self.csv.write_text(text.replace(",2,1,1",","+bad+",1,1"),encoding="utf-8")
            with self.assertRaises(ValueError,msg=bad):summary.read_reference(self.csv)

    def test_partial_summary_keeps_success_only(self):
        result=summary.summarize(self.root)
        self.assertFalse(result["complete"])
        self.assertEqual(result["verified_scale_results"],1)
        with (self.root / "comparison.csv").open(newline="") as f:
            row=next(csv.DictReader(f));self.assertEqual(row["same_statement"],"false")
        bad=self.row();bad["verified"]="false";self.save_row(self.case,bad)
        result=summary.summarize(self.root)
        self.assertEqual(result["verified_scale_results"],0)
        self.assertEqual(result["cases"][0]["status"],"invalid")

    def test_plan_writes_only_one_file(self):
        out=self.root / "plan-only"
        argv=["run_scale_comparison.py","--output",str(out),"--points","1,10","--v6-csv",str(self.csv)]
        with mock.patch.object(driver.sys,"argv",argv):driver.main()
        self.assertEqual([p.name for p in out.iterdir()],["scale-plan.json"])
        j=json.loads((out / "scale-plan.json").read_text())
        self.assertEqual(j["heap_gib"],12);self.assertFalse(j["same_statement"])
        self.assertEqual(j["cases"][0]["constraints"],4)

    def test_memory_preflight_uses_available_not_total(self):
        path=self.root / "meminfo"
        path.write_text("MemTotal: 999999999 kB\nMemAvailable: {} kB\n".format(18*1024*1024))
        self.assertEqual(driver.memory_check(12,path)["required_available_bytes"],16*(1<<30))
        path.write_text("MemTotal: 999999999 kB\nMemAvailable: {} kB\n".format(15*1024*1024))
        with self.assertRaises(ValueError):driver.memory_check(12,path)

    def test_old_prepared_adapter_rejected(self):
        adapter=self.root / "adapter";prepared=self.root / "prepared"
        java=adapter / "src/main/java/comparison/DizkComparison.java";java.parent.mkdir(parents=True)
        java.write_text("--constraint-scale constraint_scale_benchmark")
        prepared.mkdir()
        summary.save_json(prepared / "comparison-build-manifest.json",{"adapter_files":{java.relative_to(adapter).as_posix():"oldstrict"}})
        with self.assertRaises(ValueError):driver.verify_adapter_identity(prepared,adapter)

    def test_later_failure_preserves_first_result(self):
        # Existing MC1 is a successfully completed artificial runtime fixture.
        args=argparse.Namespace(repeat=1,threads=16,partitions=16,heap_gb=12,max_constraints=2000000)
        self.case.rename(self.root / "MC001-existing")
        def command_with_first_success(argv,log=None):
            if len(argv)>1 and str(argv[1]).endswith("generate_scale_inputs.py"):
                mc=int(argv[argv.index("--messages")+1])
                if mc==1:
                    dest=self.root / "MC001" / "r01"
                    src=self.root / "MC001-existing"
                    shutil.copytree(str(src / "input"),str(dest / "input"))
                    shutil.copytree(str(src / "dizk"),str(dest / "dizk"))
                    return
                raise subprocess.CalledProcessError(9,["ARTIFICIAL_LATER_FAILURE"])
        with mock.patch.object(driver,"command",side_effect=command_with_first_success), mock.patch.object(driver,"memory_check",return_value={}):
            with self.assertRaises(subprocess.CalledProcessError):driver.run_cases(self.root,self.config,self.refs,args,self.root / "prepared")
        result=json.loads((self.root / "summary.json").read_text())
        self.assertEqual(result["verified_scale_results"],1)
        self.assertTrue((self.root / "RUN_STOPPED.json").is_file())
        failed=json.loads((self.root / "MC010/r01/process-timing.json").read_text())
        self.assertEqual(failed["status"],"failed_during_generator")
        self.assertIsNotNone(failed["generator_wall_seconds"])
        self.assertIsNone(failed["dizk_launcher_wall_seconds"])
        self.assertFalse((self.root / "SCALE_BENCHMARK_COMPLETE.txt").exists())


if __name__=="__main__":unittest.main()
