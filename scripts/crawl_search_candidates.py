import argparse
import csv
import json
import re
import time
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from crawl_whitelist import (
    DEFAULT_LOG_DIR,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_WHITELIST,
    REQUEST_DELAY_SECONDS,
    USER_AGENT,
    build_log_path,
    can_fetch,
    build_output_path,
    crawl_url,
    dedupe_rows,
    fetch_html,
    filter_matched,
    is_same_or_subdomain,
    matched_keywords,
    parse_date,
    read_sites,
    result_row,
    split_keywords,
    split_site_names,
    write_csv,
    write_task_log,
)
from discover_search_candidates import expand_topic_keywords
from extractors import extract_page
from summarize import PROVIDERS, fallback_summary, set_summary_config, set_summary_mode, summarize_enhancement


OFFICIAL_DETAIL_DOMAINS = {
    "艾瑞咨询": {"report.iresearch.cn": "iresearch.cn"},
}


def read_candidates(path):
    with open(path, encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def site_map_from_whitelist(path):
    return {site.site_name: site for site in read_sites(path)}


def canonical_url(url):
    return re.sub(r"[?#].*$", "", (url or "").strip()).rstrip("/")


def attach_candidate_context(row, candidate, keywords):
    row["candidate_title"] = candidate.get("title", "")
    row["candidate_snippet"] = candidate.get("snippet", "")
    row["score"] = candidate.get("preliminary_score", "")
    row["candidate_reason"] = candidate.get("reason", "")
    row["ai_relevance_score"] = candidate.get("ai_relevance_score", "")
    row["ai_keep"] = candidate.get("ai_keep", "")
    row["ai_filter_reason"] = candidate.get("ai_filter_reason", "")
    row["source_stage"] = row.get("source_stage") or "detail"
    apply_preview_quality_flags(row, candidate)
    if row.get("status") != "unmatched":
        return row
    if len(row.get("content", "")) < 100:
        row["error"] = "详情正文过短，未使用候选摘要兜底"
        return row
    fallback_hits = matched_keywords(
        candidate.get("title", ""),
        candidate.get("snippet", ""),
        keywords,
    )
    if not fallback_hits:
        return row
    row["status"] = "matched"
    row["matched_keywords"] = " ".join(fallback_hits)
    row["error"] = "详情正文未命中，使用搜索候选摘要命中"
    row["source_stage"] = "candidate_snippet_fallback"
    ensure_ai_fields(row, keywords)
    apply_preview_quality_flags(row, candidate)
    return row


def ensure_ai_fields(row, keywords):
    if row.get("status") != "matched":
        return
    if row.get("ai_summary") or row.get("ai_category") or row.get("ai_reason"):
        return
    content = row.get("content", "")
    if not content:
        return
    topic = row.get("topic", "")
    ai_keywords = keywords or split_keywords(topic)
    row["summary"] = row.get("summary") or fallback_summary(" ".join(content.split()))
    ai_result = summarize_enhancement(
        title=row.get("title", ""),
        url=row.get("url", ""),
        content=content,
        topic=topic,
        keywords=ai_keywords,
    )
    row["ai_summary"] = ai_result.get("ai_summary", "")
    row["ai_category"] = ai_result.get("ai_category", "")
    row["ai_reason"] = ai_result.get("ai_reason", "")


def apply_preview_quality_flags(row, candidate):
    reason = candidate.get("reason", "")
    site_name = candidate.get("site_name", "")
    url = candidate.get("candidate_url", "")
    if "public_preview_only" in reason:
        row["content_type"] = "public_preview"
        row["source_stage"] = "candidate_api"
        row["known_limit"] = "PDF全文受限，仅抓公开命中片段和元数据"
    elif site_name == "前瞻网" and "bg.qianzhan.com/report/detail/" in url:
        row["content_type"] = "public_preview"
        row["known_limit"] = "前瞻公开报告简介偏短，完整报告受限"
    elif site_name == "CBNData" and len(row.get("content", "")) < 100:
        row["quality_issue"] = "CBNData详情页正文不可用或过短"
        row["review_required"] = "yes"


def public_preview_candidate_row(site, topic, candidate, keywords):
    text = " ".join([candidate.get("title", ""), candidate.get("snippet", "")]).strip()
    hits = matched_keywords(candidate.get("title", ""), candidate.get("snippet", ""), keywords)
    row = result_row(
        site,
        topic,
        candidate.get("candidate_url", ""),
        "search_candidate",
        title=candidate.get("title", ""),
        content=text,
        status="matched" if hits else "unmatched",
        keywords=hits,
        publish_date=candidate.get("publish_date", ""),
        content_type="public_preview",
        candidate_title=candidate.get("title", ""),
        candidate_snippet=candidate.get("snippet", ""),
        score=candidate.get("preliminary_score", ""),
        source_stage="candidate_api",
        candidate_reason=candidate.get("reason", ""),
        known_limit="PDF全文受限，仅抓公开命中片段和元数据",
        ai_relevance_score=candidate.get("ai_relevance_score", ""),
        ai_keep=candidate.get("ai_keep", ""),
        ai_filter_reason=candidate.get("ai_filter_reason", ""),
    )
    return row


def site_for_url(site, url):
    from crawl_whitelist import url_domain

    mapped_domain = OFFICIAL_DETAIL_DOMAINS.get(site.site_name, {}).get(url_domain(url))
    return replace(site, domain=mapped_domain) if mapped_domain else site


def is_list_candidate(url):
    lowered = (url or "").lower()
    if "/tag/" in lowered or re.search(r"/tag/[^/]+$", lowered):
        return True
    if lowered.endswith(("/industry/", "/industry/xinzhi/", "/industry/wiki/", "/research/maoyi")):
        return True
    if re.search(r"/channel/[^/.]+/?$", lowered):
        return True
    return False


def is_detail_like_url(url):
    lowered = (url or "").lower()
    return bool(
        re.search(r"/(research|news|industry|wiki|channel)/.+\.html(\?|$)", lowered)
        or re.search(r"/(information|report)/\d+(/detail)?(\?|$)", lowered)
    )


def score_link_for_topic(url, text, keywords):
    haystack = f"{text} {url}".lower()
    hits = [keyword for keyword in keywords if keyword.lower() in haystack]
    score = len(hits) * 30
    if is_detail_like_url(url):
        score += 15
    if re.search(r"20\d{2}", url):
        score += 5
    return score


def detail_links_from_list_candidate(site, url, keywords, limit):
    if not can_fetch(url, USER_AGENT):
        return []
    try:
        html = fetch_html(url)
    except Exception:
        return []
    page = extract_page(url, html)
    rows = []
    seen = set()
    for href, text in page.links:
        from urllib.parse import urljoin, urlparse

        absolute = urljoin(url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        clean_url = parsed._replace(fragment="").geturl()
        key = canonical_url(clean_url)
        if key in seen:
            continue
        if not is_same_or_subdomain(clean_url, site.domain):
            continue
        if not is_detail_like_url(clean_url):
            continue
        score = score_link_for_topic(clean_url, text, keywords)
        if score <= 0:
            continue
        seen.add(key)
        rows.append((score, clean_url))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [url for _score, url in rows[:limit]]


def candidate_in_date_range(candidate, date_from=None, date_to=None):
    publish_date = candidate.get("publish_date", "")
    if not publish_date or (date_from is None and date_to is None):
        return True
    try:
        current = date.fromisoformat(publish_date)
    except ValueError:
        return True
    if date_from and current < date_from:
        return False
    if date_to and current > date_to:
        return False
    return True


def filter_candidates(rows, site_names, min_score, date_from=None, date_to=None):
    wanted_sites = set(site_names)
    filtered = []
    for row in rows:
        if row.get("status") != "ok":
            continue
        if not row.get("candidate_url"):
            continue
        if wanted_sites and row.get("site_name") not in wanted_sites:
            continue
        try:
            score = int(row.get("preliminary_score") or 0)
        except ValueError:
            score = 0
        if score < min_score:
            continue
        if not candidate_in_date_range(row, date_from, date_to):
            continue
        row["_score"] = score
        filtered.append(row)
    return filtered


def select_top_candidates(rows, limit_per_site, limit_total):
    by_site = defaultdict(list)
    for row in rows:
        by_site[row["site_name"]].append(row)
    selected = []
    seen_urls = set()
    for site_name in sorted(by_site):
        site_rows = sorted(by_site[site_name], key=lambda row: row["_score"], reverse=True)
        count = 0
        for row in site_rows:
            key = canonical_url(row["candidate_url"])
            if key in seen_urls:
                continue
            seen_urls.add(key)
            selected.append(row)
            count += 1
            if limit_per_site and count >= limit_per_site:
                break
            if limit_total and len(selected) >= limit_total:
                return selected
    return selected


def main():
    parser = argparse.ArgumentParser(description="读取站内搜索候选 CSV，抓取候选详情正文并输出最终 CSV。")
    parser.add_argument("--topic", required=True, help="主题关键词，多个关键词用空格分隔。")
    parser.add_argument("--candidates", required=True, help="discover_search_candidates.py 输出的候选 CSV。")
    parser.add_argument("--whitelist", default=DEFAULT_WHITELIST, help="白名单 Excel 路径。")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="CSV 输出目录。")
    parser.add_argument("--log-dir", default=DEFAULT_LOG_DIR, help="任务日志输出目录。")
    parser.add_argument("--sites", default="", help="按 site_name 精确匹配多个站点，逗号分隔。")
    parser.add_argument("--min-score", type=int, default=30, help="候选最低 preliminary_score。")
    parser.add_argument("--limit-per-site", type=int, default=10, help="每个站点最多抓取 N 条候选，0 表示不限。")
    parser.add_argument("--limit-total", type=int, default=50, help="全局最多抓取 N 条候选，0 表示不限。")
    parser.add_argument("--detail-links-per-list", type=int, default=5, help="候选为标签/栏目页时，最多展开 N 条详情链接。")
    parser.add_argument("--request-delay", type=float, default=REQUEST_DELAY_SECONDS, help="每次请求后的等待秒数，默认沿用全局配置。")
    parser.add_argument("--include-list-candidates", action="store_true", help="候选为标签/栏目页时，也抓取该候选页本身。")
    parser.add_argument(
        "--match-mode",
        choices=["any", "all"],
        default="any",
        help="详情页关键词匹配模式：any 任一关键词命中；all 全部关键词命中。",
    )
    parser.add_argument("--matched-only", action="store_true", help="只输出关键词命中的结果。")
    parser.add_argument("--date-from", default="", help="发布时间起始日期，格式 YYYY-MM-DD。")
    parser.add_argument("--date-to", default="", help="发布时间结束日期，格式 YYYY-MM-DD。")
    parser.add_argument("--include-pdfs", action="store_true", help="允许抓取并提取 PDF 正文。")
    parser.add_argument("--no-dedupe", action="store_true", help="关闭输出 URL 去重。")
    parser.add_argument(
        "--summary-mode",
        choices=["popup", "excerpt", "saved"],
        default="excerpt",
        help="摘要模式：默认 excerpt；popup 表示首次摘要时弹窗填写 API。",
    )
    parser.add_argument("--summary-provider", default="", help="一次性测试供应商预设，例如 DeepSeek V4 Flash。")
    parser.add_argument("--summary-api-url", default="", help="一次性测试完整 chat/completions 地址。")
    parser.add_argument("--summary-api-key", default="", help="一次性测试 API Key；不会写入文件。")
    parser.add_argument("--summary-model", default="", help="一次性测试模型名。")
    args = parser.parse_args()
    started_at = datetime.now()

    if args.summary_api_key or args.summary_provider or args.summary_api_url or args.summary_model:
        provider = PROVIDERS.get(args.summary_provider, {})
        api_url = args.summary_api_url or provider.get("api_url", "")
        model = args.summary_model or provider.get("model", "")
        if not api_url or not args.summary_api_key or not model:
            raise ValueError("使用摘要 API 参数时，需要 summary-provider/summary-api-url、summary-api-key 和 summary-model。")
        set_summary_config(api_url=api_url, api_key=args.summary_api_key, model=model)
        summary_mode = "api-args"
    else:
        set_summary_mode(args.summary_mode)
        summary_mode = args.summary_mode

    date_from = parse_date(args.date_from, "--date-from")
    date_to = parse_date(args.date_to, "--date-to")
    keywords = expand_topic_keywords(args.topic)
    if not keywords:
        keywords = split_keywords(args.topic)
    site_names = split_site_names(args.sites)
    sites_by_name = site_map_from_whitelist(args.whitelist)

    candidate_rows = filter_candidates(read_candidates(args.candidates), site_names, args.min_score, date_from, date_to)
    selected_rows = select_top_candidates(candidate_rows, args.limit_per_site, args.limit_total)

    output_rows = []
    skipped_missing_sites = 0
    expanded_detail_count = 0
    for candidate in selected_rows:
        site = sites_by_name.get(candidate["site_name"])
        if not site or site.enabled != "yes":
            skipped_missing_sites += 1
            continue
        if "public_preview_only" in candidate.get("reason", ""):
            row = public_preview_candidate_row(site, args.topic, candidate, keywords)
            if not args.matched_only or row.get("status") == "matched":
                output_rows.append(row)
            continue
        candidate_url = candidate["candidate_url"]
        if canonical_url(candidate_url) == canonical_url(site.url):
            continue
        urls_to_crawl = []
        source_stage = "detail"
        if is_list_candidate(candidate_url):
            urls_to_crawl.extend(detail_links_from_list_candidate(site, candidate_url, keywords, args.detail_links_per_list))
            expanded_detail_count += len(urls_to_crawl)
            source_stage = "list_expanded"
            if args.include_list_candidates:
                urls_to_crawl.append(candidate_url)
        else:
            urls_to_crawl.append(candidate_url)

        for url in urls_to_crawl:
            result = crawl_url(
                site_for_url(site, url),
                args.topic,
                keywords,
                url,
                "search_candidate",
                match_mode=args.match_mode,
                date_from=date_from,
                date_to=date_to,
                include_pdfs=args.include_pdfs,
            )
            if isinstance(result, tuple):
                row = attach_candidate_context(result[0], candidate, keywords)
            else:
                row = attach_candidate_context(result, candidate, keywords)
            if row.get("source_stage") == "detail":
                row["source_stage"] = source_stage
            output_rows.append(row)
            time.sleep(args.request_delay)

    rows = output_rows
    if args.matched_only:
        rows = list(filter_matched(rows))
    if not args.no_dedupe:
        rows = list(dedupe_rows(rows))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = build_output_path(output_dir, args.topic)
    write_csv(rows, output_path)

    finished_at = datetime.now()
    log_path = build_log_path(args.log_dir, output_path)
    write_task_log(
        log_path,
        {
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="seconds"),
            "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
            "topic": args.topic,
            "keywords": keywords,
            "candidate_path": args.candidates,
            "whitelist": args.whitelist,
            "output_path": str(output_path),
            "candidate_count": len(candidate_rows),
            "selected_count": len(selected_rows),
            "expanded_detail_count": expanded_detail_count,
            "skipped_missing_sites": skipped_missing_sites,
            "matched_only": args.matched_only,
            "match_mode": args.match_mode,
            "min_score": args.min_score,
            "limit_per_site": args.limit_per_site,
            "limit_total": args.limit_total,
            "summary_mode": summary_mode,
            "date_from": args.date_from,
            "date_to": args.date_to,
            "include_pdfs": args.include_pdfs,
            "dedupe": not args.no_dedupe,
            "row_count": len(rows),
            "status_counts": {status: sum(1 for row in rows if row.get("status") == status) for status in sorted({row.get("status") for row in rows})},
            "site_counts": {site: sum(1 for row in rows if row.get("site_name") == site) for site in sorted({row.get("site_name") for row in rows})},
        },
    )

    print(f"主题: {args.topic}")
    print(f"候选输入: {args.candidates}")
    print(f"候选可用数: {len(candidate_rows)}")
    print(f"实际抓取候选数: {len(selected_rows)}")
    print(f"展开详情数: {expanded_detail_count}")
    print(f"输出行数: {len(rows)}")
    print(f"输出: {output_path}")
    print(f"任务日志: {log_path}")


if __name__ == "__main__":
    main()
