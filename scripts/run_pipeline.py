import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from crawl_whitelist import DEFAULT_OUTPUT_DIR, DEFAULT_WHITELIST, dedupe_rows, write_csv


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_LOG_DIR = "logs"
DEFAULT_RUN_DIR = "pipeline_runs"

MODE_DEFAULTS = {
    "quick": {"limit_per_search": 5, "limit_per_site": 3, "limit_total": 30, "detail_links_per_list": 3},
    "full": {"limit_per_search": 20, "limit_per_site": 0, "limit_total": 0, "detail_links_per_list": 5},
}


def run_step(command):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        command,
        cwd=PROJECT_DIR,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(completed.stdout or "")
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(str(part) for part in command)}")


def newest_csv(output_dir, since, name_contains="", exclude_parts=None):
    exclude_parts = exclude_parts or []
    candidates = []
    for path in Path(output_dir).glob("*.csv"):
        if datetime.fromtimestamp(path.stat().st_mtime) < since:
            continue
        if name_contains and name_contains not in path.name:
            continue
        if any(part in path.stem for part in exclude_parts):
            continue
        candidates.append(path)
    if not candidates:
        raise FileNotFoundError(f"No output CSV found in {output_dir}.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def newest_candidate_csv(output_dir, since):
    return newest_csv(output_dir, since, name_contains="候选")


def newest_detail_csv(output_dir, since):
    return newest_csv(
        output_dir,
        since,
        exclude_parts=["候选", "quality", "merged", "final", "site_availability"],
    )


def read_csv(path):
    with open(path, encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def safe_name(value):
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value).strip("_") or "item"


def run_dir_for(args):
    return Path(args.run_dir) / f"{safe_name(args.topic)}_{args.mode}"


def manifest_path(run_dir):
    return run_dir / "manifest.json"


def load_manifest(run_dir):
    path = manifest_path(run_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_manifest(run_dir, data):
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path(run_dir).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def site_output_path(run_dir, site_name):
    return run_dir / "sites" / f"{safe_name(site_name)}.csv"


def reusable_site_output(path):
    return path.exists() and path.stat().st_size > 0 and len(read_csv(path)) > 0


def candidate_sites(candidate_path, min_score, sites):
    wanted = {name.strip() for name in (sites or "").replace("，", ",").split(",") if name.strip()}
    names = set()
    for row in read_csv(candidate_path):
        if row.get("status") != "ok":
            continue
        if wanted and row.get("site_name") not in wanted:
            continue
        try:
            score = int(row.get("preliminary_score") or 0)
        except ValueError:
            score = 0
        if score >= min_score:
            names.add(row.get("site_name", ""))
    return sorted(name for name in names if name)


def merge_outputs(paths, output_path):
    rows = []
    for path in paths:
        rows.extend(read_csv(path))
    rows = list(dedupe_rows(rows))
    write_csv(rows, output_path)
    return rows


def quality_output_for(csv_path):
    return csv_path.with_name(csv_path.stem + "_quality.csv")


def enrich_quality(result_path, quality_path, final_path, drop_review=False):
    quality_by_url = {}
    for row in read_csv(quality_path):
        issue = row.get("issue", "")
        quality_by_url[row.get("url", "")] = issue if row.get("quality") == "review" else ""

    final_rows = []
    for row in read_csv(result_path):
        row["quality_issue"] = row.get("quality_issue", "") or quality_by_url.get(row.get("url", ""), "")
        row["review_required"] = "yes" if quality_by_url.get(row.get("url", "")) else row.get("review_required", "")
        if drop_review and row["review_required"] == "yes":
            continue
        final_rows.append(row)
    write_csv(final_rows, final_path)
    return final_rows


def build_parser():
    parser = argparse.ArgumentParser(description="Run candidate discovery, per-site detail crawl, merge, and quality evaluation.")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--mode", choices=["quick", "full"], default="quick")
    parser.add_argument("--whitelist", default=DEFAULT_WHITELIST)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
    parser.add_argument("--sites", default="")
    parser.add_argument("--min-score", type=int, default=30)
    parser.add_argument("--limit-per-search", type=int, default=None)
    parser.add_argument("--limit-per-site", type=int, default=None)
    parser.add_argument("--limit-total", type=int, default=None)
    parser.add_argument("--detail-links-per-list", type=int, default=None)
    parser.add_argument("--date-from", default="")
    parser.add_argument("--date-to", default="")
    parser.add_argument("--request-delay", type=float, default=0.2)
    parser.add_argument("--summary-mode", choices=["excerpt", "popup", "saved"], default="excerpt")
    parser.add_argument("--include-pdfs", action="store_true")
    parser.add_argument("--drop-review", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-dir", default=DEFAULT_RUN_DIR)
    parser.add_argument("--candidate-csv", default="")
    parser.add_argument("--ai-filter-top-n", type=int, default=0)
    parser.add_argument("--ai-filter-min-score", type=int, default=60)
    return parser


def discover_candidates(args, limit_per_search, run_dir, manifest, started_at):
    if args.candidate_csv:
        return Path(args.candidate_csv)
    if args.resume and manifest.get("candidate_path") and Path(manifest["candidate_path"]).exists():
        candidate_path = Path(manifest["candidate_path"])
        print(f"复用候选输出: {candidate_path}")
        return candidate_path

    command = [
        sys.executable,
        str(SCRIPT_DIR / "discover_search_candidates.py"),
        "--topic",
        args.topic,
        "--whitelist",
        args.whitelist,
        "--output-dir",
        args.output_dir,
        "--limit-per-search",
        str(limit_per_search),
    ]
    if args.sites:
        command.extend(["--sites", args.sites])
    run_step(command)
    candidate_path = newest_candidate_csv(args.output_dir, started_at)
    if args.resume:
        manifest["candidate_path"] = str(candidate_path)
        save_manifest(run_dir, manifest)
    return candidate_path


def maybe_ai_filter_candidates(args, candidate_path, run_dir, manifest):
    if args.ai_filter_top_n <= 0:
        return candidate_path
    if args.summary_mode == "excerpt":
        raise ValueError("--ai-filter-top-n requires --summary-mode saved or popup.")
    if args.resume and manifest.get("ai_filtered_candidate_path") and Path(manifest["ai_filtered_candidate_path"]).exists():
        filtered_path = Path(manifest["ai_filtered_candidate_path"])
        print(f"复用AI精筛候选输出: {filtered_path}")
        return filtered_path

    filtered_path = run_dir / f"{safe_name(args.topic)}_{args.mode}_ai_filtered_candidates.csv"
    command = [
        sys.executable,
        str(SCRIPT_DIR / "ai_filter_candidates.py"),
        "--topic",
        args.topic,
        "--candidates",
        str(candidate_path),
        "--summary-mode",
        args.summary_mode,
        "--top-n-per-site",
        str(args.ai_filter_top_n),
        "--min-ai-score",
        str(args.ai_filter_min_score),
        "--output",
        str(filtered_path),
    ]
    run_step(command)
    if args.resume:
        manifest["ai_filtered_candidate_path"] = str(filtered_path)
        save_manifest(run_dir, manifest)
    return filtered_path


def crawl_one_site(args, candidate_path, site_name, limit_per_site, limit_total, detail_links_per_list):
    before_site = datetime.now()
    command = [
        sys.executable,
        str(SCRIPT_DIR / "crawl_search_candidates.py"),
        "--topic",
        args.topic,
        "--candidates",
        str(candidate_path),
        "--whitelist",
        args.whitelist,
        "--output-dir",
        args.output_dir,
        "--log-dir",
        args.log_dir,
        "--sites",
        site_name,
        "--matched-only",
        "--min-score",
        str(args.min_score),
        "--limit-per-site",
        str(limit_per_site),
        "--limit-total",
        str(limit_total),
        "--detail-links-per-list",
        str(detail_links_per_list),
        "--summary-mode",
        args.summary_mode,
        "--request-delay",
        str(args.request_delay),
    ]
    if args.date_from:
        command.extend(["--date-from", args.date_from])
    if args.date_to:
        command.extend(["--date-to", args.date_to])
    if args.include_pdfs:
        command.append("--include-pdfs")
    run_step(command)
    return newest_detail_csv(args.output_dir, before_site)


def main():
    args = build_parser().parse_args()
    defaults = MODE_DEFAULTS[args.mode]
    limit_per_search = args.limit_per_search if args.limit_per_search is not None else defaults["limit_per_search"]
    limit_per_site = args.limit_per_site if args.limit_per_site is not None else defaults["limit_per_site"]
    limit_total = args.limit_total if args.limit_total is not None else defaults["limit_total"]
    detail_links_per_list = args.detail_links_per_list if args.detail_links_per_list is not None else defaults["detail_links_per_list"]

    started_at = datetime.now()
    run_dir = run_dir_for(args)
    manifest = load_manifest(run_dir) if args.resume else {}
    manifest.update({
        "topic": args.topic,
        "mode": args.mode,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "sites": manifest.get("sites", {}),
    })

    candidate_path = discover_candidates(args, limit_per_search, run_dir, manifest, started_at)
    manifest["candidate_path"] = str(candidate_path)
    candidate_path = maybe_ai_filter_candidates(args, candidate_path, run_dir, manifest)
    if args.resume:
        save_manifest(run_dir, manifest)

    site_names = candidate_sites(candidate_path, args.min_score, args.sites)
    if not site_names:
        raise RuntimeError("No crawlable candidates matched the current score and site filters.")

    detail_paths = []
    failed_sites = []
    for site_name in site_names:
        resumable_path = site_output_path(run_dir, site_name)
        if args.resume and reusable_site_output(resumable_path):
            print(f"跳过已完成站点: {site_name} -> {resumable_path}")
            detail_paths.append(resumable_path)
            continue
        try:
            detail_path = crawl_one_site(args, candidate_path, site_name, limit_per_site, limit_total, detail_links_per_list)
            if args.resume:
                resumable_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(detail_path, resumable_path)
                manifest.setdefault("sites", {})[site_name] = {
                    "status": "completed",
                    "output_path": str(resumable_path),
                    "source_output_path": str(detail_path),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
                save_manifest(run_dir, manifest)
                detail_path = resumable_path
            detail_paths.append(detail_path)
        except Exception as exc:
            failed_sites.append(site_name)
            if not args.resume:
                raise
            manifest.setdefault("sites", {})[site_name] = {
                "status": "failed",
                "error": str(exc),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            save_manifest(run_dir, manifest)
            print(f"站点失败，已记录并继续: {site_name} - {exc}")

    if not detail_paths:
        raise RuntimeError("No per-site outputs were completed; cannot merge.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    merged_path = Path(args.output_dir) / f"{timestamp}_{safe_name(args.topic)}_{args.mode}_merged.csv"
    merge_outputs(detail_paths, merged_path)
    run_step([sys.executable, str(SCRIPT_DIR / "evaluate_output_quality.py"), str(merged_path)])
    quality_path = quality_output_for(merged_path)
    final_path = merged_path.with_name(merged_path.stem.replace("_merged", "_final") + ".csv")
    final_rows = enrich_quality(merged_path, quality_path, final_path, drop_review=args.drop_review)

    if args.resume:
        manifest.update({
            "merged_path": str(merged_path),
            "quality_path": str(quality_path),
            "final_path": str(final_path),
            "failed_sites": failed_sites,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
        save_manifest(run_dir, manifest)

    site_counts = Counter(row.get("site_name", "") for row in final_rows)
    status_counts = Counter(row.get("status", "") for row in final_rows)
    print(f"候选输出: {candidate_path}")
    print(f"合并输出: {merged_path}")
    print(f"质量输出: {quality_path}")
    print(f"最终输出: {final_path}")
    print(f"最终行数: {len(final_rows)}")
    print(f"状态分布: {dict(status_counts)}")
    print(f"站点分布: {dict(site_counts)}")
    if failed_sites:
        print(f"失败站点: {', '.join(failed_sites)}")


if __name__ == "__main__":
    main()
