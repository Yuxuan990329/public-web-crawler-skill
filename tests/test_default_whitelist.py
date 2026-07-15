import sys
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_DIR / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import check_environment
import check_site_availability
import crawl_search_candidates
import crawl_whitelist
import discover_search_candidates
import run_pipeline
import start_crawler
from crawler_defaults import DEFAULT_WHITELIST


class DefaultWhitelistRegressionTest(unittest.TestCase):
    def test_runtime_parsers_and_environment_share_existing_default(self):
        expected = (PROJECT_DIR / "白名单网站模板_栏目优化.xlsx").resolve()

        self.assertEqual(expected, Path(DEFAULT_WHITELIST))
        self.assertTrue(expected.is_file())
        self.assertEqual(DEFAULT_WHITELIST, crawl_whitelist.DEFAULT_WHITELIST)
        self.assertEqual(DEFAULT_WHITELIST, crawl_search_candidates.DEFAULT_WHITELIST)
        self.assertEqual(DEFAULT_WHITELIST, discover_search_candidates.DEFAULT_WHITELIST)
        self.assertEqual(DEFAULT_WHITELIST, check_site_availability.DEFAULT_WHITELIST)
        self.assertEqual(DEFAULT_WHITELIST, start_crawler.DEFAULT_WHITELIST)
        with tempfile.TemporaryDirectory() as external_cwd, chdir(external_cwd):
            pipeline_default = run_pipeline.build_parser().parse_args(["--topic", "测试"]).whitelist
            self.assertEqual(DEFAULT_WHITELIST, pipeline_default)
            self.assertTrue(Path(pipeline_default).is_file())
        self.assertIn(DEFAULT_WHITELIST, check_environment.REQUIRED_FILES)

    def test_launcher_reads_real_default_before_cancel_without_starting_process(self):
        answers = iter(["", "", "", "", "", "", "n"])

        with tempfile.TemporaryDirectory() as external_cwd:
            with (
                chdir(external_cwd),
                patch("builtins.input", side_effect=lambda _prompt: next(answers)),
                patch("start_crawler.read_sites", wraps=start_crawler.read_sites) as read_sites,
                patch("start_crawler.subprocess.run") as run_process,
            ):
                start_crawler.main()

        read_sites.assert_called_once_with(DEFAULT_WHITELIST)
        run_process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
