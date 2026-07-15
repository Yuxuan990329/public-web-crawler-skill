import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from discover_search_candidates import build_search_queries, expand_topic_keywords, score_candidate


class CandidateScoringRegressionTest(unittest.TestCase):
    def test_topic_yields_business_core_term_without_run_labels(self):
        topic = "空调行业发展与市场变化 修复后复测"

        self.assertEqual(["空调"], expand_topic_keywords(topic))
        self.assertEqual(["空调"], build_search_queries(topic))

    def test_explicit_queries_stay_separate_from_business_keywords(self):
        topic = "空调行业发展与市场变化 回归测试"

        self.assertEqual(["中央空调", "家用空调"], build_search_queries(topic, "中央空调 家用空调 修复后复测"))
        self.assertEqual(["空调"], expand_topic_keywords(topic))

    def test_ai_expansion_is_preserved(self):
        keywords = expand_topic_keywords("人工智能行业趋势 回归测试")

        self.assertIn("人工智能", keywords)
        self.assertIn("AI", keywords)
        self.assertIn("大模型", keywords)

    def test_explicit_space_separated_keywords_are_preserved(self):
        self.assertEqual(["空调", "家电"], expand_topic_keywords("空调 家电"))

    def test_business_evidence_gate_for_current_samples(self):
        cases = [
            (
                "洞见空调报告",
                "https://www.djyanbao.com/report/detail?id=12345",
                "2025年中国空调行业发展研究报告",
                "制冷设备市场规模与竞争格局",
                True,
            ),
            (
                "无关修复报告",
                "https://www.djyanbao.com/report/detail?id=67890",
                "城市更新修复行业研究报告",
                "公共设施修复与维护市场",
                False,
            ),
            (
                "前瞻羊毛问答",
                "https://t.qianzhan.com/caijing/detail/240101-abcd.html",
                "羊毛衫应该怎样清洗和保养？",
                "生活消费问答",
                False,
            ),
            (
                "搜索页",
                "https://www.huaon.com/search?cid=zixun&word=空调",
                "空调行业资讯",
                "空调市场发展",
                False,
            ),
            (
                "首页",
                "https://bg.qianzhan.com/",
                "空调行业发展研究报告",
                "",
                False,
            ),
        ]

        for name, url, title, snippet, should_pass in cases:
            with self.subTest(name=name):
                score, hits, _positives, negatives = score_candidate(url, title, ["空调"], snippet)
                if should_pass:
                    self.assertGreaterEqual(score, 30)
                    self.assertEqual(["空调"], hits)
                else:
                    self.assertLess(score, 30)
                if name == "搜索页":
                    self.assertIn("search", negatives)
                if name == "首页":
                    self.assertIn("root", negatives)

    def test_unrelated_detail_url_cannot_reach_threshold_from_shape_alone(self):
        score, hits, positives, _negatives = score_candidate(
            "https://example.com/research/report/detail/2026/01",
            "城市更新与建筑修复报告",
            ["空调"],
        )

        self.assertEqual([], hits)
        self.assertIn("report", positives)
        self.assertIn("detail", positives)
        self.assertLess(score, 30)

    def test_search_page_context_is_not_business_evidence_for_html_links(self):
        score, hits, _positives, _negatives = score_candidate(
            "https://example.com/report/detail/123",
            "城市更新研究报告",
            ["空调"],
            "搜索结果页周边文本包含空调，但当前链接标题无关",
        )

        self.assertEqual([], hits)
        self.assertLess(score, 30)

    def test_structured_api_snippet_alone_is_not_business_evidence(self):
        score, hits, _positives, _negatives = score_candidate(
            "https://example.com/report/detail/123",
            "家用电器行业报告",
            ["空调"],
            "报告覆盖空调市场规模与竞争格局",
        )

        self.assertEqual([], hits)
        self.assertLess(score, 30)


if __name__ == "__main__":
    unittest.main()
