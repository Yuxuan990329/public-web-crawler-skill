import argparse
import csv
from collections import Counter
from pathlib import Path

from output_ownership import reserve_output_paths


NOISE_MARKERS = [
    "请登录",
    "验证码",
    "扫码登录",
    "Access Denied",
    "403 Forbidden",
    "RSS",
]


def score_row(row):
    content = row.get("content", "")
    title = row.get("title", "")
    status = row.get("status", "")
    site_name = row.get("site_name", "")
    extractor = row.get("extractor", "")
    content_type = row.get("content_type", "")
    errors = []

    if status == "matched" and content_type == "public_preview":
        if len(content) < 50:
            errors.append("公开预览内容过短")
    elif status == "matched" and len(content) < 200:
        if site_name == "CBNData":
            errors.append("CBNData详情页正文不可用或过短")
        else:
            errors.append("matched正文过短")

    if len(content) > 20000 and extractor != "stats.gov.cn":
        errors.append("正文过长需检查")
    if not title and status in {"matched", "unmatched"}:
        errors.append("缺少标题")
    for marker in NOISE_MARKERS:
        if marker in content:
            errors.append(f"疑似噪音:{marker}")
            break
    return "pass" if not errors else "review", "；".join(errors)


def main():
    parser = argparse.ArgumentParser(description="Evaluate crawler CSV output quality.")
    parser.add_argument("csv_path", help="Crawler output CSV path.")
    parser.add_argument("--output", default="", help="Quality CSV output path.")
    args = parser.parse_args()

    assigned_output = reserve_output_paths([args.output])[0] if args.output else None

    input_path = Path(args.csv_path)
    rows = list(csv.DictReader(open(input_path, encoding="utf-8-sig")))
    status_counter = Counter(row.get("status", "") for row in rows)
    site_counter = Counter(row.get("site_name", "") for row in rows)
    extractor_counter = Counter(row.get("extractor", "") for row in rows)

    evaluated = []
    quality_counter = Counter()
    for row in rows:
        quality, issue = score_row(row)
        quality_counter[quality] += 1
        evaluated.append(
            {
                "quality": quality,
                "issue": issue,
                "review_required": "yes" if quality == "review" else "",
                "status": row.get("status", ""),
                "site_name": row.get("site_name", ""),
                "extractor": row.get("extractor", ""),
                "source_type": row.get("source_type", ""),
                "category": row.get("category", ""),
                "publish_date": row.get("publish_date", ""),
                "content_type": row.get("content_type", ""),
                "title": row.get("title", ""),
                "url": row.get("url", ""),
                "content_length": len(row.get("content", "")),
                "matched_keywords": row.get("matched_keywords", ""),
            }
        )

    output_path = assigned_output if assigned_output else input_path.with_name(input_path.stem + "_quality.csv")
    fields = [
        "quality",
        "issue",
        "review_required",
        "status",
        "site_name",
        "extractor",
        "source_type",
        "category",
        "publish_date",
        "content_type",
        "title",
        "url",
        "content_length",
        "matched_keywords",
    ]
    with open(output_path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(evaluated)

    print(f"输入: {input_path}")
    print(f"输出: {output_path}")
    print(f"总行数: {len(rows)}")
    print(f"状态分布: {dict(status_counter)}")
    print(f"质量分布: {dict(quality_counter)}")
    print(f"站点分布: {dict(site_counter)}")
    print(f"提取器分布: {dict(extractor_counter)}")


if __name__ == "__main__":
    main()
