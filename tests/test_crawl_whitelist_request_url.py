import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from crawl_whitelist import request_safe_url


class RequestSafeUrlTest(unittest.TestCase):
    def test_chinese_query_is_percent_encoded_for_urllib(self):
        url = "https://www.huaon.com/search?cid=zixun&word=空调行业发展"

        safe = request_safe_url(url)

        self.assertEqual(
            "https://www.huaon.com/search?cid=zixun&word=%E7%A9%BA%E8%B0%83%E8%A1%8C%E4%B8%9A%E5%8F%91%E5%B1%95",
            safe,
        )


if __name__ == "__main__":
    unittest.main()
