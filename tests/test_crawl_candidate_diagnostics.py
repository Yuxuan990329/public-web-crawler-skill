import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from crawl_search_candidates import enforce_list_detail_title_evidence, main, result_row, score_link_for_topic, select_top_candidates
from crawl_whitelist import canonical_url
from discover_search_candidates import expand_topic_keywords, score_candidate


class CrawlCandidateDiagnosticsTest(unittest.TestCase):
    def test_list_expanded_detail_requires_business_evidence_in_final_title(self):
        unrelated = {
            "title": "智能冰箱行业研究报告",
            "status": "matched",
            "matched_keywords": "空调",
            "source_stage": "list_expanded",
        }
        related = {
            "title": "中央空调行业研究报告",
            "status": "matched",
            "matched_keywords": "空调",
            "source_stage": "list_expanded",
        }

        self.assertEqual("unmatched", enforce_list_detail_title_evidence(unrelated, ["空调"])["status"])
        self.assertEqual("matched", enforce_list_detail_title_evidence(related, ["空调"])["status"])

    def test_list_detail_shape_cannot_replace_business_evidence(self):
        unrelated_score = score_link_for_topic(
            "https://example.com/channel/fridge/123.html",
            "智能冰箱行业报告",
            ["空调"],
        )
        related_score = score_link_for_topic(
            "https://example.com/channel/fridge/456.html",
            "中央空调行业报告",
            ["空调"],
        )

        self.assertEqual(0, unrelated_score)
        self.assertGreater(related_score, 0)

    def test_identity_query_params_are_preserved_during_deduplication(self):
        rows = [
            {"site_name": "洞见研报", "candidate_url": "https://example.com/report/detail?id=1", "_score": 40},
            {"site_name": "洞见研报", "candidate_url": "https://example.com/report/detail?id=2", "_score": 40},
        ]

        selected = select_top_candidates(rows, limit_per_site=0, limit_total=0)

        self.assertEqual(2, len(selected))
        self.assertNotEqual(canonical_url(rows[0]["candidate_url"]), canonical_url(rows[1]["candidate_url"]))

    def test_tracking_query_params_do_not_create_duplicate_urls(self):
        plain = "https://example.com/report/detail?id=1"
        tracked = "https://example.com/report/detail?utm_source=test&id=1#section"

        self.assertEqual(canonical_url(plain), canonical_url(tracked))

    def test_regression_marker_is_not_used_as_business_keyword(self):
        self.assertEqual(["空调"], expand_topic_keywords("空调行业发展与市场变化 回归测试"))

    def test_root_and_search_pages_do_not_reach_detail_threshold_by_title_only(self):
        keywords = ["空调"]
        root_score, _hits, _positives, root_negatives = score_candidate(
            "https://example.com/",
            "空调行业发展与市场变化_搜索_测试站",
            keywords,
        )
        search_score, _hits, _positives, search_negatives = score_candidate(
            "https://example.com/search?word=空调行业发展与市场变化",
            "空调行业发展与市场变化_搜索_测试站",
            keywords,
        )

        self.assertLess(root_score, 30)
        self.assertIn("root", root_negatives)
        self.assertLess(search_score, 30)
        self.assertIn("search", search_negatives)

    def test_matched_only_empty_output_keeps_pre_filter_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "outputs"
            log_dir = temp_path / "logs"
            whitelist = temp_path / "whitelist.xlsx"
            candidates = temp_path / "candidates.csv"

            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "whitelist"
            sheet.append(["site_name", "url", "domain", "category", "page_type", "enabled", "max_detail_pages", "note"])
            sheet.append(["测试站点", "https://example.com", "example.com", "测试", "page", "yes", 5, ""])
            workbook.save(whitelist)

            with open(candidates, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=[
                        "site_name",
                        "category",
                        "query",
                        "search_url",
                        "candidate_url",
                        "title",
                        "snippet",
                        "publish_date",
                        "matched_keywords",
                        "preliminary_score",
                        "status",
                        "reason",
                        "error",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "site_name": "测试站点",
                        "category": "测试",
                        "query": "空调行业发展与市场变化",
                        "search_url": "https://example.com/search",
                        "candidate_url": "https://example.com/detail",
                        "title": "候选标题",
                        "snippet": "候选摘要",
                        "publish_date": "",
                        "matched_keywords": "空调行业发展与市场变化",
                        "preliminary_score": "100",
                        "status": "ok",
                        "reason": "",
                        "error": "",
                    }
                )

            def fake_crawl_url(site, topic, keywords, url, source_type, **_kwargs):
                return (
                    result_row(
                        site,
                        topic,
                        url,
                        source_type,
                        title="无关详情",
                        content="这是一段没有业务关键词的公开正文。",
                        status="unmatched",
                    ),
                    [],
                )

            argv = [
                "crawl_search_candidates.py",
                "--topic",
                "空调行业发展与市场变化 回归测试",
                "--candidates",
                str(candidates),
                "--whitelist",
                str(whitelist),
                "--output-dir",
                str(output_dir),
                "--log-dir",
                str(log_dir),
                "--sites",
                "测试站点",
                "--matched-only",
                "--summary-mode",
                "excerpt",
            ]
            with patch.object(sys, "argv", argv), patch("crawl_search_candidates.crawl_url", side_effect=fake_crawl_url):
                main()

            log_path = next(log_dir.glob("*_task_log.json"))
            payload = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(1, payload["pre_filter_row_count"])
            self.assertEqual({"unmatched": 1}, payload["pre_filter_status_counts"])
            self.assertEqual(0, payload["row_count"])


if __name__ == "__main__":
    unittest.main()
