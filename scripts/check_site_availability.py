import argparse
import csv
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError

from crawl_whitelist import DEFAULT_OUTPUT_DIR, DEFAULT_WHITELIST, USER_AGENT, can_fetch, fetch_html, read_sites, split_site_names
from discover_search_candidates import (
    API_SEARCH_SITES,
    build_search_urls,
    expand_topic_keywords,
    fetch_json,
    templates_for_site,
    url_domain,
)


STATUS_ORDER = {
    "crawlable": 1,
    "disabled_but_accessible": 2,
    "robots_blocked": 3,
    "login_or_paid": 4,
    "dynamic_only": 5,
    "no_search_template": 6,
    "access_failed": 7,
}


def classify_note(note):
    text = note or ""
    if "公开" in text and ("收费" in text or "完整报告" in text):
        return "public_preview"
    if "登录" in text or "收费" in text:
        return "login_or_paid"
    if "动态" in text:
        return "dynamic_only"
    if "DNS" in text or "解析失败" in text or "无法访问" in text:
        return "access_failed"
    if "robots" in text:
        return "robots_blocked"
    return ""


def check_home(site):
    robots_allowed = can_fetch(site.url, USER_AGENT)
    if not robots_allowed:
        return "robots_blocked", "robots.txt 不允许访问白名单入口"
    try:
        html = fetch_html(site.url)
    except HTTPError as exc:
        return "access_failed", f"HTTP {exc.code}"
    except (URLError, TimeoutError, ValueError) as exc:
        return "access_failed", str(exc)
    if site.domain != "qianzhan.com" and any(marker in html for marker in ["请登录", "验证码", "扫码登录", "Access Denied", "403 Forbidden"]):
        return "login_or_paid", "首页疑似登录/验证限制"
    return "home_accessible", f"首页可访问，HTML长度={len(html)}"


def api_search_available(site):
    domain = url_domain(site.url) or site.domain
    return any(domain == api_domain or domain.endswith("." + api_domain) for api_domain in API_SEARCH_SITES)


def check_search(site, topic):
    if api_search_available(site):
        return "api_search", "已配置公开搜索 API"
    templates = templates_for_site(site)
    if not templates:
        return "no_search_template", "缺少搜索 URL 模板"
    query = expand_topic_keywords(topic)[0] if topic else "AI"
    search_url = build_search_urls(site, [query])[0][1]
    if not can_fetch(search_url, USER_AGENT):
        return "robots_blocked", f"robots.txt 不允许访问搜索入口: {search_url}"
    try:
        html = fetch_html(search_url)
    except HTTPError as exc:
        return "access_failed", f"搜索入口 HTTP {exc.code}: {search_url}"
    except (URLError, TimeoutError, ValueError) as exc:
        return "access_failed", f"搜索入口访问失败: {exc}"
    if site.domain != "qianzhan.com" and any(marker in html for marker in ["请登录", "验证码", "扫码登录", "Access Denied", "403 Forbidden"]):
        return "login_or_paid", f"搜索入口疑似登录/验证限制: {search_url}"
    if topic and topic not in html and "AI" not in html and "人工智能" not in html:
        return "dynamic_only", f"搜索入口可访问但静态 HTML 未见主题内容: {search_url}"
    return "search_accessible", f"搜索入口可访问: {search_url}"


def recommend(site, home_status, search_status, note_status):
    if site.enabled != "yes":
        return "keep_disabled", "白名单已禁用；保留来源记录，不进入自动抓取"
    if site.domain == "djyanbao.com" and search_status == "api_search":
        return "crawl_public_preview", "公开搜索 API 可用；PDF 全文直连受限，基础模式抓候选、命中片段和元数据"
    if home_status == "robots_blocked" or search_status == "robots_blocked":
        return "disable_or_skip", "robots 限制；基础模式跳过"
    if note_status == "public_preview" and search_status in {"api_search", "search_accessible"}:
        return "crawl_public_preview", "公开资讯/报告简介可抓，完整报告收费不抓"
    if note_status == "login_or_paid" or home_status == "login_or_paid" or search_status == "login_or_paid":
        return "disable_or_public_url_only", "登录/收费/验证限制；仅支持人工公开 URL 复测"
    if home_status == "access_failed":
        return "confirm_url", "入口访问失败；需人工确认域名或新栏目"
    if search_status in {"api_search", "search_accessible"}:
        return "crawlable", "可进入搜索候选抓取"
    if search_status == "dynamic_only":
        return "review_api", "需确认是否有稳定公开 API；基础模式暂不依赖动态页"
    if search_status == "no_search_template":
        return "add_search_template", "需补搜索 URL 模板或公开 API"
    return "review", "需人工复核"


def output_path(output_dir, topic):
    safe_topic = "".join(char if char.isalnum() or char in "-_" else "_" for char in topic).strip("_") or "site"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(output_dir) / f"{timestamp}_{safe_topic}_site_availability.csv"


def main():
    parser = argparse.ArgumentParser(description="Check whitelist site access, robots, and search availability.")
    parser.add_argument("--whitelist", default=DEFAULT_WHITELIST, help="Whitelist Excel path.")
    parser.add_argument("--topic", default="AI", help="Topic used to probe search pages.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="CSV output directory.")
    parser.add_argument("--sites", default="", help="Optional comma-separated site_name filter.")
    args = parser.parse_args()

    sites = read_sites(args.whitelist)
    wanted = split_site_names(args.sites)
    if wanted:
        wanted_set = set(wanted)
        sites = [site for site in sites if site.site_name in wanted_set]

    rows = []
    for site in sites:
        note_status = classify_note(site.note)
        home_status, home_detail = check_home(site)
        search_status, search_detail = check_search(site, args.topic)
        recommendation, recommendation_detail = recommend(site, home_status, search_status, note_status)
        rows.append(
            {
                "site_name": site.site_name,
                "url": site.url,
                "domain": site.domain,
                "enabled": site.enabled,
                "category": site.category,
                "home_status": home_status,
                "home_detail": home_detail,
                "search_status": search_status,
                "search_detail": search_detail,
                "note_status": note_status,
                "recommendation": recommendation,
                "recommendation_detail": recommendation_detail,
                "note": site.note,
            }
        )

    rows.sort(key=lambda row: (STATUS_ORDER.get(row["recommendation"], 99), row["site_name"]))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_path(output_dir, args.topic)
    fields = [
        "site_name",
        "url",
        "domain",
        "enabled",
        "category",
        "home_status",
        "home_detail",
        "search_status",
        "search_detail",
        "note_status",
        "recommendation",
        "recommendation_detail",
        "note",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"站点数: {len(rows)}")
    print(f"输出: {path}")
    for row in rows:
        print(f"{row['site_name']}: {row['recommendation']} - {row['recommendation_detail']}")


if __name__ == "__main__":
    main()
