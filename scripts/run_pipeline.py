import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from crawl_whitelist import DEFAULT_OUTPUT_DIR, DEFAULT_WHITELIST, dedupe_rows, write_csv
from output_ownership import reserve_output_paths


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_LOG_DIR = "logs"
DEFAULT_RUN_DIR = "pipeline_runs"
CACHE_SCHEMA_VERSION = 1

MODE_DEFAULTS = {
    "quick": {"limit_per_search": 5, "limit_per_site": 3, "limit_total": 30, "detail_links_per_list": 3},
    "full": {"limit_per_search": 20, "limit_per_site": 0, "limit_total": 0, "detail_links_per_list": 5},
}

USABLE_STATUSES = {"matched"}


class EmptySiteOutputError(RuntimeError):
    """A site crawl completed technically but produced no usable detail rows."""


class RunLock:
    def __init__(self, run_dir):
        self.path = Path(run_dir).resolve() / ".run.lock"
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size == 0:
            with open(self.path, "ab") as initializer:
                if initializer.tell() == 0:
                    initializer.write(b"1")
                    initializer.flush()
        self.file = open(self.path, "r+b")
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError(f"Run already active: {self.path.parent}") from exc
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.file is None:
            return
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()
        self.file = None


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


def read_csv(path):
    with open(path, encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_record(path):
    path = Path(path).expanduser().resolve()
    return {"path": str(path), "sha256": sha256_file(path)}


def _is_link_or_junction(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def managed_path(path, root):
    path = Path(path).absolute()
    root = Path(root).absolute()
    try:
        path.relative_to(root)
    except ValueError:
        return False
    current = path
    while current != root.parent:
        if current.exists() and _is_link_or_junction(current):
            return False
        if current == root:
            break
        current = current.parent
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def reusable_artifact(record, expected_path=None, allowed_root=None):
    if not isinstance(record, dict) or not record.get("path") or not record.get("sha256"):
        return False
    path = Path(record["path"])
    if expected_path is not None and path.absolute() != Path(expected_path).absolute():
        return False
    if allowed_root is not None and not managed_path(path, allowed_root):
        return False
    return path.is_file() and sha256_file(path) == record["sha256"]


def normalized_sites(value):
    return sorted({part.strip() for part in (value or "").replace("，", ",").split(",") if part.strip()})


def summary_config_fingerprint(path):
    path = Path(path)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    identity = {key: data.get(key, "") for key in ("provider", "api_url", "model")}
    return config_fingerprint(identity)


def normalized_config(args):
    if args.resume and args.summary_mode == "popup":
        raise ValueError("--summary-mode popup cannot be combined with --resume because runtime AI identity is unknown.")
    defaults = MODE_DEFAULTS[args.mode]
    whitelist = Path(args.whitelist).expanduser().resolve()
    candidate = Path(args.candidate_csv).expanduser().resolve() if args.candidate_csv else None
    summary_config = PROJECT_DIR / "config" / "summary_api.local.json"
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "topic": unicodedata.normalize("NFC", args.topic.strip()),
        "mode": args.mode,
        "whitelist": {"path": str(whitelist), "sha256": sha256_file(whitelist)},
        "candidate_csv": {"path": str(candidate), "sha256": sha256_file(candidate)} if candidate else None,
        "sites": normalized_sites(args.sites),
        "min_score": args.min_score,
        "limit_per_search": args.limit_per_search if args.limit_per_search is not None else defaults["limit_per_search"],
        "limit_per_site": args.limit_per_site if args.limit_per_site is not None else defaults["limit_per_site"],
        "limit_total": args.limit_total if args.limit_total is not None else defaults["limit_total"],
        "detail_links_per_list": args.detail_links_per_list if args.detail_links_per_list is not None else defaults["detail_links_per_list"],
        "date_from": args.date_from,
        "date_to": args.date_to,
        "request_delay": args.request_delay,
        "summary_mode": args.summary_mode,
        "summary_config_fingerprint": summary_config_fingerprint(summary_config) if args.summary_mode == "saved" else None,
        "include_pdfs": args.include_pdfs,
        "drop_review": args.drop_review,
        "matched_only": args.matched_only,
        "ai_filter_top_n": args.ai_filter_top_n,
        "ai_filter_min_score": args.ai_filter_min_score,
        "output_dir": str(Path(args.output_dir).expanduser().resolve()),
        "log_dir": str(Path(args.log_dir).expanduser().resolve()),
    }


def config_fingerprint(config):
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def safe_name(value):
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value).strip("_") or "item"


def run_dir_for(args, fingerprint=""):
    suffix = f"_{fingerprint[:16]}" if fingerprint else ""
    return Path(args.run_dir).expanduser().resolve() / f"{safe_name(args.topic)}_{args.mode}{suffix}"


def execution_id():
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')}_{uuid.uuid4().hex}"


def execution_paths(run_dir, value, output_dir=None, log_dir=None):
    root = (Path(run_dir).resolve() / "executions" / value).resolve()
    output_root = (Path(output_dir).resolve() / value).resolve() if output_dir else root
    log_root = (Path(log_dir).resolve() / value).resolve() if log_dir else root / "logs"
    return {
        "root": root,
        "output_root": output_root,
        "log_root": log_root,
        "candidate": output_root / "candidates.csv",
        "ai_filtered": output_root / "candidates_ai_filtered.csv",
        "merged": output_root / "merged.csv",
        "quality": output_root / "quality.csv",
        "final": output_root / "final.csv",
    }


def execution_site_paths(output_root, log_root, site_name):
    name_hash = hashlib.sha256(site_name.encode("utf-8")).hexdigest()[:12]
    stem = f"{safe_name(site_name)}_{name_hash}"
    return Path(output_root).resolve() / "sites" / f"{stem}.csv", Path(log_root).resolve() / f"{stem}.json"


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
    name_hash = hashlib.sha256(site_name.encode("utf-8")).hexdigest()[:12]
    return run_dir / "sites" / f"{safe_name(site_name)}_{name_hash}.csv"


def candidate_cache_path(run_dir):
    return Path(run_dir) / "cache" / "candidates.csv"


def ai_filtered_cache_path(run_dir):
    return Path(run_dir) / "cache" / "candidates_ai_filtered.csv"


def update_managed_cache(source, target, run_dir):
    source = Path(source).resolve()
    target = Path(target).absolute()
    if not managed_path(target, run_dir):
        raise RuntimeError(f"Managed cache path escaped or traversed a link: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if source != target.resolve():
        shutil.copy2(source, target)
    return target


def reusable_site_output(path, entry, fingerprint, candidate_sha256):
    return (
        isinstance(entry, dict)
        and entry.get("status") == "completed"
        and entry.get("config_fingerprint") == fingerprint
        and entry.get("candidate_sha256") == candidate_sha256
        and reusable_artifact(entry.get("artifact"), expected_path=path, allowed_root=Path(path).parent.parent)
        and Path(entry["artifact"]["path"]).resolve() == Path(path).resolve()
        and any(row.get("status") in USABLE_STATUSES for row in read_csv(path))
    )


def require_nonempty_site_output(path, site_name):
    rows = read_csv(path)
    usable_count = sum(row.get("status") in USABLE_STATUSES for row in rows)
    if usable_count == 0:
        raise EmptySiteOutputError(
            f"Site '{site_name}' detail crawl returned zero usable rows "
            f"({len(rows)} diagnostic rows): {path}"
        )
    return usable_count


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


def candidate_score_diagnostics(candidate_path, min_score, sites=""):
    wanted = {name.strip() for name in (sites or "").replace("，", ",").split(",") if name.strip()}
    rows = read_csv(candidate_path)
    scores = []
    qualified_count = 0
    for row in rows:
        if row.get("status") != "ok":
            continue
        if wanted and row.get("site_name") not in wanted:
            continue
        try:
            score = int(row.get("preliminary_score") or 0)
        except ValueError:
            score = 0
        scores.append(score)
        if score >= min_score:
            qualified_count += 1
    return {
        "candidate_row_count": len(rows),
        "ok_candidate_count": len(scores),
        "qualified_candidate_count": qualified_count,
        "min_score": min_score,
        "max_score": max(scores) if scores else None,
        "score_distribution": dict(sorted(Counter(scores).items(), reverse=True)),
    }


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
        if row.get("status") not in USABLE_STATUSES:
            continue
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
    parser.add_argument("--mode", choices=["quick", "full"], default="full")
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
    parser.add_argument("--matched-only", action="store_true", help="Only keep keyword-matched detail rows. Default keeps diagnostic rows.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-dir", default=DEFAULT_RUN_DIR)
    parser.add_argument("--candidate-csv", default="")
    parser.add_argument("--ai-filter-top-n", type=int, default=0)
    parser.add_argument("--ai-filter-min-score", type=int, default=60)
    return parser


def discover_candidates(args, limit_per_search, manifest, assigned_output, cache_path=None):
    if args.candidate_csv:
        return Path(args.candidate_csv).expanduser().resolve()
    if args.resume and cache_path and reusable_artifact(
        manifest.get("candidate_artifact"), expected_path=cache_path, allowed_root=Path(cache_path).parent.parent
    ):
        candidate_path = Path(manifest["candidate_artifact"]["path"])
        print(f"复用候选输出: {candidate_path}")
        return candidate_path

    candidate_path = Path(assigned_output).expanduser().resolve()
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(SCRIPT_DIR / "discover_search_candidates.py"),
        "--topic",
        args.topic,
        "--whitelist",
        args.whitelist,
        "--output",
        str(candidate_path),
        "--limit-per-search",
        str(limit_per_search),
    ]
    if args.sites:
        command.extend(["--sites", args.sites])
    run_step(command)
    if not candidate_path.is_file():
        raise FileNotFoundError(f"Assigned candidate output was not created: {candidate_path}")
    return candidate_path


def maybe_ai_filter_candidates(args, candidate_path, manifest, assigned_output, cache_path=None):
    if args.ai_filter_top_n <= 0:
        return candidate_path
    if args.summary_mode == "excerpt":
        raise ValueError("--ai-filter-top-n requires --summary-mode saved or popup.")
    candidate_sha256 = sha256_file(candidate_path)
    if (
        args.resume
        and cache_path
        and reusable_artifact(
            manifest.get("ai_filtered_artifact"), expected_path=cache_path, allowed_root=Path(cache_path).parent.parent
        )
        and manifest.get("ai_filtered_input_sha256") == candidate_sha256
    ):
        filtered_path = Path(manifest["ai_filtered_artifact"]["path"])
        print(f"复用AI精筛候选输出: {filtered_path}")
        return filtered_path

    filtered_path = Path(assigned_output).expanduser().resolve()
    filtered_path.parent.mkdir(parents=True, exist_ok=True)
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
    if not filtered_path.is_file():
        raise FileNotFoundError(f"Assigned AI-filtered output was not created: {filtered_path}")
    return filtered_path


def crawl_one_site(args, candidate_path, site_name, limit_per_site, limit_total, detail_links_per_list, output_path, log_path):
    output_path = Path(output_path).expanduser().resolve()
    log_path = Path(log_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(SCRIPT_DIR / "crawl_search_candidates.py"),
        "--topic",
        args.topic,
        "--candidates",
        str(candidate_path),
        "--whitelist",
        args.whitelist,
        "--output",
        str(output_path),
        "--log-output",
        str(log_path),
        "--sites",
        site_name,
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
    if args.matched_only:
        command.append("--matched-only")
    run_step(command)
    if not output_path.is_file():
        raise FileNotFoundError(f"Assigned detail output was not created: {output_path}")
    return output_path


def run_locked(args, config, fingerprint, run_dir, paths, value):
    defaults = MODE_DEFAULTS[args.mode]
    limit_per_search = args.limit_per_search if args.limit_per_search is not None else defaults["limit_per_search"]
    limit_per_site = args.limit_per_site if args.limit_per_site is not None else defaults["limit_per_site"]
    limit_total = args.limit_total if args.limit_total is not None else defaults["limit_total"]
    detail_links_per_list = args.detail_links_per_list if args.detail_links_per_list is not None else defaults["detail_links_per_list"]

    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["output_root"].mkdir(parents=True, exist_ok=True)
    paths["log_root"].mkdir(parents=True, exist_ok=True)
    for key in ("candidate", "ai_filtered", "merged", "quality", "final"):
        if paths[key].exists():
            raise FileExistsError(f"Execution output already exists: {paths[key]}")
    manifest = load_manifest(run_dir) if args.resume else {}
    if args.resume and manifest and (
        manifest.get("schema_version") != CACHE_SCHEMA_VERSION
        or manifest.get("config_fingerprint") != fingerprint
        or manifest.get("normalized_config") != config
    ):
        raise RuntimeError("Resume configuration does not match the existing manifest; cached artifacts will not be reused.")
    if manifest.get("run_status") == "completed" and manifest.get("final_path"):
        manifest["last_success"] = {
            "execution_id": manifest.get("execution_id", ""),
            "final_path": manifest.get("final_path", ""),
            "merged_path": manifest.get("merged_path", ""),
            "quality_path": manifest.get("quality_path", ""),
            "finished_at": manifest.get("finished_at", manifest.get("updated_at", "")),
        }
    for key in ("error", "merged_path", "quality_path", "final_path", "finished_at"):
        manifest.pop(key, None)
    manifest.update({
        "schema_version": CACHE_SCHEMA_VERSION,
        "normalized_config": config,
        "config_fingerprint": fingerprint,
        "execution_id": value,
        "execution_dir": str(paths["root"]),
        "output_execution_dir": str(paths["output_root"]),
        "log_execution_dir": str(paths["log_root"]),
        "run_status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "topic": args.topic,
        "mode": args.mode,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "sites": manifest.get("sites", {}),
    })
    if args.resume:
        save_manifest(run_dir, manifest)

    candidate_cache = candidate_cache_path(run_dir)
    candidate_path = discover_candidates(args, limit_per_search, manifest, paths["candidate"], candidate_cache)
    manifest["candidate_path"] = str(candidate_path)
    if args.resume and not args.candidate_csv:
        cached_candidate = update_managed_cache(candidate_path, candidate_cache, run_dir)
        manifest["candidate_artifact"] = artifact_record(cached_candidate)
    else:
        manifest["candidate_artifact"] = artifact_record(candidate_path)
    ai_cache = ai_filtered_cache_path(run_dir)
    candidate_path = maybe_ai_filter_candidates(args, candidate_path, manifest, paths["ai_filtered"], ai_cache)
    if args.resume and args.ai_filter_top_n > 0:
        cached_ai = update_managed_cache(candidate_path, ai_cache, run_dir)
        manifest["ai_filtered_candidate_path"] = str(candidate_path)
        manifest["ai_filtered_artifact"] = artifact_record(cached_ai)
        manifest["ai_filtered_input_sha256"] = manifest["candidate_artifact"]["sha256"]
    effective_candidate_sha256 = sha256_file(candidate_path)
    manifest["candidate_diagnostics"] = candidate_score_diagnostics(candidate_path, args.min_score, args.sites)
    if args.resume:
        save_manifest(run_dir, manifest)

    site_names = candidate_sites(candidate_path, args.min_score, args.sites)
    if not site_names:
        message = "No crawlable candidates matched the current score and site filters."
        if args.resume:
            manifest.update({
                "run_status": "failed_no_qualified_candidates",
                "error": message,
                "finished_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            })
            save_manifest(run_dir, manifest)
        raise RuntimeError(message)

    detail_paths = []
    failed_sites = []
    no_result_sites = []
    for site_name in site_names:
        resumable_path = site_output_path(run_dir, site_name)
        site_entry = manifest.get("sites", {}).get(site_name, {})
        if args.resume and reusable_site_output(resumable_path, site_entry, fingerprint, effective_candidate_sha256):
            print(f"跳过已完成站点: {site_name} -> {resumable_path}")
            detail_paths.append(resumable_path)
            continue
        try:
            assigned_output, assigned_log = execution_site_paths(paths["output_root"], paths["log_root"], site_name)
            detail_path = crawl_one_site(
                args,
                candidate_path,
                site_name,
                limit_per_site,
                limit_total,
                detail_links_per_list,
                assigned_output,
                assigned_log,
            )
            require_nonempty_site_output(detail_path, site_name)
            if args.resume:
                if not managed_path(resumable_path, run_dir):
                    raise RuntimeError(f"Managed site cache path escaped or traversed a link: {resumable_path}")
                resumable_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(detail_path, resumable_path)
                manifest.setdefault("sites", {})[site_name] = {
                    "status": "completed",
                    "output_path": str(resumable_path),
                    "source_output_path": str(detail_path),
                    "config_fingerprint": fingerprint,
                    "candidate_sha256": effective_candidate_sha256,
                    "artifact": artifact_record(resumable_path),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
                save_manifest(run_dir, manifest)
                detail_path = resumable_path
            detail_paths.append(detail_path)
        except Exception as exc:
            is_no_result = isinstance(exc, EmptySiteOutputError)
            if is_no_result:
                no_result_sites.append(site_name)
            else:
                failed_sites.append(site_name)
            if not args.resume:
                raise
            manifest.setdefault("sites", {})[site_name] = {
                "status": "no_results" if is_no_result else "failed",
                "error": str(exc),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            save_manifest(run_dir, manifest)
            print(f"站点失败，已记录并继续: {site_name} - {exc}")

    if args.resume:
        manifest.update({
            "failed_sites": failed_sites,
            "no_result_sites": no_result_sites,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
        save_manifest(run_dir, manifest)

    if not detail_paths:
        message = "No usable per-site outputs were produced; cannot merge."
        if args.resume:
            manifest.update({
                "run_status": "failed_no_usable_rows",
                "error": message,
                "finished_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            })
            save_manifest(run_dir, manifest)
        raise RuntimeError(message)

    merged_path = paths["merged"]
    reserve_output_paths([merged_path])
    merge_outputs(detail_paths, merged_path)
    quality_path = paths["quality"]
    run_step([
        sys.executable,
        str(SCRIPT_DIR / "evaluate_output_quality.py"),
        str(merged_path),
        "--output",
        str(quality_path),
    ])
    final_path = paths["final"]
    reserve_output_paths([final_path])
    final_rows = enrich_quality(merged_path, quality_path, final_path, drop_review=args.drop_review)

    if args.resume:
        manifest.update({
            "merged_path": str(merged_path),
            "quality_path": str(quality_path),
            "final_path": str(final_path),
            "failed_sites": failed_sites,
            "no_result_sites": no_result_sites,
            "run_status": "completed" if final_rows else "failed_no_usable_rows",
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
        save_manifest(run_dir, manifest)

    if not final_rows:
        raise RuntimeError("Final output contains zero usable rows; run is not successful.")

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


def main():
    args = build_parser().parse_args()
    args.whitelist = str(Path(args.whitelist).expanduser().resolve())
    args.output_dir = str(Path(args.output_dir).expanduser().resolve())
    args.log_dir = str(Path(args.log_dir).expanduser().resolve())
    args.run_dir = str(Path(args.run_dir).expanduser().resolve())
    if args.candidate_csv:
        args.candidate_csv = str(Path(args.candidate_csv).expanduser().resolve())
    config = normalized_config(args)
    fingerprint = config_fingerprint(config)
    run_dir = run_dir_for(args, fingerprint)
    value = execution_id()
    paths = execution_paths(run_dir, value, args.output_dir, args.log_dir)
    with RunLock(run_dir):
        return run_locked(args, config, fingerprint, run_dir, paths, value)


if __name__ == "__main__":
    main()
