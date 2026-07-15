import sys
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from run_pipeline import (
    RunLock,
    build_parser,
    crawl_one_site,
    discover_candidates,
    execution_id,
    execution_paths,
    execution_site_paths,
)
from output_ownership import reserve_output_paths
import run_pipeline


class PipelineOutputOwnershipTest(unittest.TestCase):
    def test_execution_ids_and_all_paths_are_unique_and_absolute(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            first_id = execution_id()
            second_id = execution_id()
            output_dir = Path(temp_dir) / "outputs"
            log_dir = Path(temp_dir) / "logs"
            first = execution_paths(run_dir, first_id, output_dir, log_dir)
            second = execution_paths(run_dir, second_id, output_dir, log_dir)
            self.assertNotEqual(first_id, second_id)
            self.assertTrue(all(path.is_absolute() for path in first.values()))
            self.assertTrue(set(first.values()).isdisjoint(second.values()))
            self.assertTrue(first["final"].is_relative_to(output_dir))
            self.assertTrue(first["log_root"].is_relative_to(log_dir))

    def test_discovery_returns_only_parent_assigned_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            whitelist = root / "whitelist.xlsx"
            whitelist.write_bytes(b"whitelist")
            args = build_parser().parse_args(["--topic", "空调", "--whitelist", str(whitelist)])
            expected = root / "execution" / "candidates.csv"

            def fake_run(command):
                output = Path(command[command.index("--output") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("status\nok\n", encoding="utf-8")
                (root / "newer_foreign_候选.csv").write_text("foreign", encoding="utf-8")

            with patch("run_pipeline.run_step", side_effect=fake_run):
                result = discover_candidates(args, 20, {}, expected)
            self.assertEqual(expected.resolve(), result.resolve())

    def test_detail_returns_only_parent_assigned_output_and_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            whitelist = root / "whitelist.xlsx"
            whitelist.write_bytes(b"whitelist")
            args = build_parser().parse_args(["--topic", "空调", "--whitelist", str(whitelist)])
            output = root / "execution" / "sites" / "site.csv"
            log = root / "execution" / "logs" / "site.json"

            def fake_run(command):
                assigned = Path(command[command.index("--output") + 1])
                assigned.parent.mkdir(parents=True, exist_ok=True)
                assigned.write_text("status\nmatched\n", encoding="utf-8")
                self.assertEqual(str(log.resolve()), command[command.index("--log-output") + 1])

            with patch("run_pipeline.run_step", side_effect=fake_run):
                result = crawl_one_site(args, root / "candidates.csv", "站点/A", 0, 0, 5, output, log)
            self.assertEqual(output.resolve(), result.resolve())

    def test_same_run_lock_rejects_second_owner_and_releases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            with RunLock(run_dir):
                with self.assertRaises(RuntimeError):
                    with RunLock(run_dir):
                        pass
            with RunLock(run_dir):
                pass

    def test_lock_releases_on_exception_and_different_runs_do_not_conflict(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "run-a"
            second = Path(temp_dir) / "run-b"
            with self.assertRaises(RuntimeError):
                with RunLock(first):
                    raise RuntimeError("boom")
            with RunLock(first), RunLock(second):
                pass

    def test_site_hash_prevents_safe_name_collision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir) / "outputs"
            log_root = Path(temp_dir) / "logs"
            first = execution_site_paths(output_root, log_root, "A/B")
            second = execution_site_paths(output_root, log_root, "A?B")
            self.assertNotEqual(first, second)

    def test_atomic_reservation_rolls_back_when_any_path_conflicts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "output.csv"
            log = root / "log.json"
            log.write_text("sentinel", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                reserve_output_paths([output, log])
            self.assertFalse(output.exists())
            self.assertEqual("sentinel", log.read_text(encoding="utf-8"))

    def test_quality_and_final_use_explicit_execution_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            whitelist = root / "whitelist.xlsx"
            candidates = root / "candidates.csv"
            detail = root / "detail.csv"
            whitelist.write_bytes(b"whitelist")
            candidates.write_text("status,site_name,preliminary_score\nok,测试站,100\n", encoding="utf-8")
            detail.write_text("url,site_name,status\nhttps://example.com/1,测试站,matched\n", encoding="utf-8")
            commands = []

            def fake_run(command):
                commands.append(command)
                if "evaluate_output_quality.py" in str(command[1]):
                    output = Path(command[command.index("--output") + 1])
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with open(output, "w", newline="", encoding="utf-8-sig") as file:
                        writer = csv.DictWriter(file, fieldnames=["quality", "issue", "url"])
                        writer.writeheader()
                        writer.writerow({"quality": "pass", "issue": "", "url": "https://example.com/1"})

            argv = [
                "run_pipeline.py", "--topic", "空调", "--whitelist", str(whitelist),
                "--candidate-csv", str(candidates), "--run-dir", str(root / "runs"),
                "--output-dir", str(root / "outputs"), "--log-dir", str(root / "logs"),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch("run_pipeline.crawl_one_site", return_value=detail),
                patch("run_pipeline.run_step", side_effect=fake_run),
            ):
                run_pipeline.main()

            quality_command = next(command for command in commands if "evaluate_output_quality.py" in str(command[1]))
            quality_path = Path(quality_command[quality_command.index("--output") + 1])
            self.assertTrue(quality_path.is_relative_to(root / "outputs"))
            finals = list((root / "outputs").glob("*/final.csv"))
            self.assertEqual(1, len(finals))


if __name__ == "__main__":
    unittest.main()
