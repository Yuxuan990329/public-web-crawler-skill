import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from run_pipeline import (
    artifact_record,
    build_parser,
    config_fingerprint,
    normalized_config,
    reusable_artifact,
    reusable_site_output,
    run_dir_for,
    summary_config_fingerprint,
)
import run_pipeline


class PipelineIdentityTest(unittest.TestCase):
    def make_args(self, whitelist, candidate_csv="", *extra):
        argv = ["--topic", " 空调行业 ", "--whitelist", str(whitelist), "--candidate-csv", str(candidate_csv)]
        argv.extend(extra)
        return build_parser().parse_args(argv)

    def test_behavior_and_input_changes_alter_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            whitelist = root / "whitelist.xlsx"
            candidates = root / "candidates.csv"
            whitelist.write_bytes(b"whitelist-v1")
            candidates.write_bytes(b"candidate-v1")
            base = self.make_args(whitelist, candidates)
            base_fingerprint = config_fingerprint(normalized_config(base))

            variants = [
                ["--date-from", "2026-01-01"],
                ["--min-score", "31"],
                ["--sites", "A,B"],
                ["--summary-mode", "saved"],
                ["--ai-filter-top-n", "5"],
                ["--ai-filter-min-score", "70"],
                ["--request-delay", "0.3"],
                ["--include-pdfs"],
                ["--matched-only"],
                ["--drop-review"],
            ]
            for extra in variants:
                with self.subTest(extra=extra):
                    changed = self.make_args(whitelist, candidates, *extra)
                    self.assertNotEqual(base_fingerprint, config_fingerprint(normalized_config(changed)))

            whitelist.write_bytes(b"whitelist-v2")
            self.assertNotEqual(base_fingerprint, config_fingerprint(normalized_config(base)))
            whitelist.write_bytes(b"whitelist-v1")
            candidates.write_bytes(b"candidate-v2")
            self.assertNotEqual(base_fingerprint, config_fingerprint(normalized_config(base)))

    def test_semantically_equal_sites_have_same_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            whitelist = Path(temp_dir) / "whitelist.xlsx"
            whitelist.write_bytes(b"same")
            first = self.make_args(whitelist, "", "--sites", "B,A,A")
            second = self.make_args(whitelist, "", "--sites", " A， B ")
            self.assertEqual(config_fingerprint(normalized_config(first)), config_fingerprint(normalized_config(second)))

    def test_artifact_hash_and_site_identity_are_required_for_reuse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate = root / "candidate.csv"
            site = root / "site.csv"
            candidate.write_text("status\nmatched\n", encoding="utf-8")
            site.write_text("status\nmatched\n", encoding="utf-8")
            candidate_record = artifact_record(candidate)
            site_record = artifact_record(site)
            self.assertTrue(reusable_artifact(candidate_record))
            self.assertTrue(reusable_site_output(site, {
                "status": "completed",
                "config_fingerprint": "fingerprint",
                "candidate_sha256": candidate_record["sha256"],
                "artifact": site_record,
            }, "fingerprint", candidate_record["sha256"]))

            candidate.write_text("status\nunmatched\n", encoding="utf-8")
            self.assertFalse(reusable_artifact(candidate_record))
            self.assertFalse(reusable_site_output(site, {
                "status": "completed",
                "config_fingerprint": "fingerprint",
                "candidate_sha256": candidate_record["sha256"],
                "artifact": site_record,
            }, "fingerprint", "changed"))

    def test_popup_resume_is_rejected_before_cache_use(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            whitelist = Path(temp_dir) / "whitelist.xlsx"
            whitelist.write_bytes(b"same")
            args = self.make_args(whitelist, "", "--summary-mode", "popup", "--resume")
            with self.assertRaises(ValueError):
                normalized_config(args)

    def test_resume_mismatch_fails_before_any_subprocess(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            whitelist = root / "whitelist.xlsx"
            candidates = root / "candidates.csv"
            run_dir = root / "runs"
            whitelist.write_bytes(b"whitelist")
            candidates.write_text("status\nmatched\n", encoding="utf-8")
            argv = [
                "run_pipeline.py", "--topic", "空调行业", "--whitelist", str(whitelist),
                "--candidate-csv", str(candidates), "--run-dir", str(run_dir), "--resume",
            ]
            parsed = build_parser().parse_args(argv[1:])
            fingerprint = config_fingerprint(normalized_config(parsed))
            manifest_dir = run_dir / f"空调行业_full_{fingerprint[:16]}"
            manifest_dir.mkdir(parents=True)
            (manifest_dir / "manifest.json").write_text(json.dumps({
                "schema_version": 1,
                "config_fingerprint": "stale",
                "normalized_config": {},
            }), encoding="utf-8")
            with patch.object(sys, "argv", argv), patch("run_pipeline.run_step") as run_step:
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    run_pipeline.main()
            run_step.assert_not_called()

    def test_managed_artifact_cannot_be_redirected_to_external_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = root / "run" / "cache" / "candidates.csv"
            external = root / "external.csv"
            external.write_text("status\nmatched\n", encoding="utf-8")
            record = artifact_record(external)
            self.assertFalse(reusable_artifact(record, expected_path=managed, allowed_root=root / "run"))

    def test_saved_config_identity_excludes_key_but_tracks_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "summary.json"
            config.write_text(json.dumps({"api_url": "https://api.example/v1", "api_key": "key-a", "model": "m1"}), encoding="utf-8")
            first = summary_config_fingerprint(config)
            config.write_text(json.dumps({"api_url": "https://api.example/v1", "api_key": "key-b", "model": "m1"}), encoding="utf-8")
            self.assertEqual(first, summary_config_fingerprint(config))
            config.write_text(json.dumps({"api_url": "https://api.example/v1", "api_key": "key-b", "model": "m2"}), encoding="utf-8")
            self.assertNotEqual(first, summary_config_fingerprint(config))

    def test_new_execution_clears_old_completed_status_before_work(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            whitelist = root / "whitelist.xlsx"
            candidates = root / "candidates.csv"
            whitelist.write_bytes(b"whitelist")
            candidates.write_text("status,site_name,preliminary_score\nok,测试站,100\n", encoding="utf-8")
            argv = [
                "run_pipeline.py", "--topic", "空调", "--whitelist", str(whitelist),
                "--candidate-csv", str(candidates), "--run-dir", str(root / "runs"),
                "--output-dir", str(root / "outputs"), "--log-dir", str(root / "logs"), "--resume",
            ]
            parsed = build_parser().parse_args(argv[1:])
            config = normalized_config(parsed)
            fingerprint = config_fingerprint(config)
            run_dir = run_dir_for(parsed, fingerprint)
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(json.dumps({
                "schema_version": 1,
                "normalized_config": config,
                "config_fingerprint": fingerprint,
                "execution_id": "OLD",
                "run_status": "completed",
                "final_path": "OLD_FINAL",
                "merged_path": "OLD_MERGED",
                "quality_path": "OLD_QUALITY",
                "sites": {},
            }), encoding="utf-8")
            with (
                patch.object(sys, "argv", argv),
                patch("run_pipeline.candidate_sites", return_value=["测试站"]),
                patch("run_pipeline.crawl_one_site", side_effect=KeyboardInterrupt()),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    run_pipeline.main()
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("running", manifest["run_status"])
            self.assertNotEqual("OLD", manifest["execution_id"])
            self.assertNotIn("final_path", manifest)
            self.assertEqual("OLD_FINAL", manifest["last_success"]["final_path"])


if __name__ == "__main__":
    unittest.main()
