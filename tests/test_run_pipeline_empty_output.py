import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from run_pipeline import EmptySiteOutputError, build_parser, crawl_one_site, main, require_nonempty_site_output


class EmptySiteOutputRegressionTest(unittest.TestCase):
    def test_pipeline_detail_crawl_keeps_diagnostic_rows_by_default(self):
        args = build_parser().parse_args([
            "--topic", "诊断测试",
            "--candidate-csv", "candidates.csv",
            "--output-dir", "outputs",
        ])
        captured = {}

        def fake_run_step(command):
            captured["command"] = command
            output = Path(command[command.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("status\nmatched\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as temp_dir, patch("run_pipeline.run_step", side_effect=fake_run_step):
            crawl_one_site(
                args, Path("candidates.csv"), "测试站点", 0, 0, 5,
                Path(temp_dir) / "detail.csv", Path(temp_dir) / "detail.json",
            )

        self.assertNotIn("--matched-only", captured["command"])

    def test_pipeline_can_request_matched_only_detail_rows(self):
        args = build_parser().parse_args([
            "--topic", "诊断测试",
            "--candidate-csv", "candidates.csv",
            "--output-dir", "outputs",
            "--matched-only",
        ])
        captured = {}

        def fake_run_step(command):
            captured["command"] = command
            output = Path(command[command.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("status\nmatched\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as temp_dir, patch("run_pipeline.run_step", side_effect=fake_run_step):
            crawl_one_site(
                args, Path("candidates.csv"), "测试站点", 0, 0, 5,
                Path(temp_dir) / "detail.csv", Path(temp_dir) / "detail.json",
            )

        self.assertIn("--matched-only", captured["command"])

    def test_header_only_site_output_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "empty.csv"
            with open(output, "w", newline="", encoding="utf-8-sig") as file:
                csv.DictWriter(file, fieldnames=["title", "url"]).writeheader()

            with self.assertRaises(EmptySiteOutputError):
                require_nonempty_site_output(output, "测试站点")

    def test_diagnostic_only_site_output_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "diagnostic.csv"
            with open(output, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=["title", "url", "status"])
                writer.writeheader()
                writer.writerow({"title": "未命中页", "url": "https://example.com/detail", "status": "unmatched"})

            with self.assertRaisesRegex(EmptySiteOutputError, "zero usable rows"):
                require_nonempty_site_output(output, "测试站点")

    def test_resume_records_header_only_output_as_no_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            candidate_path = temp_path / "candidates.csv"
            empty_output = temp_path / "empty.csv"
            with open(candidate_path, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=["status", "site_name", "preliminary_score", "candidate_url"])
                writer.writeheader()
                writer.writerow({
                    "status": "ok",
                    "site_name": "测试站点",
                    "preliminary_score": "100",
                    "candidate_url": "https://example.com/detail",
                })
            with open(empty_output, "w", newline="", encoding="utf-8-sig") as file:
                csv.DictWriter(file, fieldnames=["title", "url"]).writeheader()

            argv = [
                "run_pipeline.py",
                "--topic", "回归主题",
                "--candidate-csv", str(candidate_path),
                "--output-dir", str(temp_path),
                "--run-dir", str(temp_path / "runs"),
                "--resume",
            ]
            with patch.object(sys, "argv", argv), patch("run_pipeline.crawl_one_site", return_value=empty_output):
                with self.assertRaisesRegex(RuntimeError, "No usable per-site outputs"):
                    main()

            manifest_path = next((temp_path / "runs").glob("回归主题_full_*/manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("no_results", manifest["sites"]["测试站点"]["status"])
            self.assertEqual(["测试站点"], manifest["no_result_sites"])
            self.assertEqual("failed_no_usable_rows", manifest["run_status"])

    def test_resume_rejects_empty_final_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            candidate_path = temp_path / "candidates.csv"
            detail_output = temp_path / "detail.csv"
            with open(candidate_path, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=["status", "site_name", "preliminary_score", "candidate_url"])
                writer.writeheader()
                writer.writerow({
                    "status": "ok",
                    "site_name": "测试站点",
                    "preliminary_score": "100",
                    "candidate_url": "https://example.com/detail",
                })
            with open(detail_output, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=["url", "site_name", "status"])
                writer.writeheader()
                writer.writerow({"url": "https://example.com/detail", "site_name": "测试站点", "status": "matched"})

            argv = [
                "run_pipeline.py",
                "--topic", "最终空结果回归",
                "--candidate-csv", str(candidate_path),
                "--output-dir", str(temp_path),
                "--run-dir", str(temp_path / "runs"),
                "--resume",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch("run_pipeline.crawl_one_site", return_value=detail_output),
                patch("run_pipeline.run_step"),
                patch("run_pipeline.enrich_quality", return_value=[]),
            ):
                with self.assertRaisesRegex(RuntimeError, "Final output contains zero usable rows"):
                    main()

            manifest_path = next((temp_path / "runs").glob("最终空结果回归_full_*/manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("failed_no_usable_rows", manifest["run_status"])

    def test_resume_records_candidate_score_diagnostics_when_none_qualify(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            candidate_path = temp_path / "candidates.csv"
            with open(candidate_path, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=["status", "site_name", "preliminary_score", "candidate_url"])
                writer.writeheader()
                writer.writerow({
                    "status": "ok",
                    "site_name": "测试站点",
                    "preliminary_score": "13",
                    "candidate_url": "https://example.com/report/detail",
                })
                writer.writerow({
                    "status": "skipped",
                    "site_name": "受限站点",
                    "preliminary_score": "0",
                    "candidate_url": "",
                })

            argv = [
                "run_pipeline.py",
                "--topic", "候选门槛回归",
                "--candidate-csv", str(candidate_path),
                "--output-dir", str(temp_path),
                "--run-dir", str(temp_path / "runs"),
                "--resume",
            ]
            with patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(RuntimeError, "No crawlable candidates"):
                    main()

            manifest_path = next((temp_path / "runs").glob("候选门槛回归_full_*/manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("failed_no_qualified_candidates", manifest["run_status"])
            self.assertEqual(2, manifest["candidate_diagnostics"]["candidate_row_count"])
            self.assertEqual(1, manifest["candidate_diagnostics"]["ok_candidate_count"])
            self.assertEqual(0, manifest["candidate_diagnostics"]["qualified_candidate_count"])
            self.assertEqual(13, manifest["candidate_diagnostics"]["max_score"])
            self.assertEqual({"13": 1}, manifest["candidate_diagnostics"]["score_distribution"])


if __name__ == "__main__":
    unittest.main()
