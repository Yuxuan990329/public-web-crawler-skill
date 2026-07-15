import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from crawl_search_candidates import attach_candidate_context, main, result_row, validate_candidate_source
from crawl_whitelist import Site
from discover_search_candidates import expand_topic_keywords, score_candidate


class Stage1EvidenceBoundaryTest(unittest.TestCase):
    def test_unmatched_detail_is_never_upgraded_by_candidate_summary(self):
        site = Site("测试站", "https://example.com", "example.com", "测试", "page", "yes", 5, "")
        row = result_row(
            site,
            "空调行业发展",
            "https://example.com/detail/1",
            "search_candidate",
            title="与主题无关的详情",
            content="这是一篇长度足够的无关正文，用于验证候选摘要不能覆盖详情正文的语义结论。" * 4,
            status="unmatched",
        )
        candidate = {
            "title": "空调行业发展报告",
            "snippet": "空调市场规模与竞争格局",
            "preliminary_score": "48",
            "reason": "api=test",
        }

        result = attach_candidate_context(row, candidate, ["空调"])

        self.assertEqual("unmatched", result["status"])
        self.assertEqual("candidate_preview_only", result["source_stage"])
        self.assertEqual("yes", result["review_required"])
        self.assertIn("候选摘要", result["quality_issue"])

    def test_scope_terms_cannot_become_independent_admission_evidence(self):
        keywords = expand_topic_keywords("空调 行业动态")
        score, hits, _positives, _negatives = score_candidate(
            "https://example.com/news/2026/steel.html",
            "钢铁行业动态",
            keywords,
        )

        self.assertEqual(["空调"], keywords)
        self.assertEqual([], hits)
        self.assertLess(score, 30)

    def test_preview_rejects_external_candidate_before_robots_check(self):
        site = Site("洞见研报", "https://www.djyanbao.com", "djyanbao.com", "报告", "page", "yes", 5, "")
        with patch("crawl_search_candidates.can_fetch") as can_fetch:
            error = validate_candidate_source(site, "https://evil.example/report/1")

        self.assertEqual("URL 不匹配白名单域名", error)
        can_fetch.assert_not_called()

    def test_public_preview_external_url_is_written_as_skipped_not_matched(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            whitelist = temp_path / "whitelist.xlsx"
            candidates = temp_path / "candidates.csv"
            output_dir = temp_path / "outputs"
            log_dir = temp_path / "logs"

            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "whitelist"
            sheet.append(["site_name", "url", "domain", "category", "page_type", "enabled", "max_detail_pages", "note"])
            sheet.append(["洞见研报", "https://www.djyanbao.com", "djyanbao.com", "报告", "page", "yes", 5, ""])
            workbook.save(whitelist)

            fieldnames = [
                "site_name", "category", "query", "search_url", "candidate_url", "title", "snippet",
                "publish_date", "matched_keywords", "preliminary_score", "status", "reason", "error",
            ]
            with open(candidates, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerow({
                    "site_name": "洞见研报",
                    "category": "报告",
                    "query": "空调",
                    "search_url": "https://www.djyanbao.com/report/search?q=空调",
                    "candidate_url": "https://evil.example/report/1",
                    "title": "空调行业报告",
                    "snippet": "外域伪造公开摘要",
                    "publish_date": "2026-07-01",
                    "matched_keywords": "空调",
                    "preliminary_score": "48",
                    "status": "ok",
                    "reason": "api=djyanbao; public_preview_only",
                    "error": "",
                })

            argv = [
                "crawl_search_candidates.py", "--topic", "空调", "--candidates", str(candidates),
                "--whitelist", str(whitelist), "--output-dir", str(output_dir), "--log-dir", str(log_dir),
                "--sites", "洞见研报", "--summary-mode", "excerpt",
            ]
            with patch.object(sys, "argv", argv):
                main()

            with next(output_dir.glob("*.csv")).open(encoding="utf-8-sig") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(1, len(rows))
            self.assertEqual("skipped", rows[0]["status"])
            self.assertEqual("URL 不匹配白名单域名", rows[0]["error"])


if __name__ == "__main__":
    unittest.main()
