import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from summarize import REQUEST_TIMEOUT_SECONDS, get_summary_config, set_summary_mode


def read_csv(path):
    with open(path, encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def write_csv(rows, path):
    if not rows:
        return
    fields = list(rows[0].keys())
    for field in ["ai_relevance_score", "ai_keep", "ai_filter_reason"]:
        if field not in fields:
            fields.append(field)
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def safe_name(value):
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value).strip("_") or "candidates"


def output_path(input_path, topic):
    return input_path.with_name(input_path.stem + f"_{safe_name(topic)}_ai_filtered.csv")


def candidate_relevance(config, topic, row):
    prompt = (
        "请判断候选网页是否值得进入详情抓取，严格输出 JSON，不要输出 Markdown。\n"
        "JSON 字段：ai_relevance_score, ai_keep, ai_filter_reason。\n"
        "ai_relevance_score：0-100 的整数，表示与用户主题的相关性。\n"
        "ai_keep：true/false，只有主题相关、不是明显导航页/噪音页时才为 true。\n"
        "ai_filter_reason：一句中文说明，指出保留或剔除原因。\n\n"
        f"用户主题：{topic}\n"
        f"站点：{row.get('site_name', '')}\n"
        f"标题：{row.get('title', '')}\n"
        f"摘要：{row.get('snippet', '')}\n"
        f"候选URL：{row.get('candidate_url', '')}\n"
        f"基础分：{row.get('preliminary_score', '')}\n"
        f"基础原因：{row.get('reason', '')}"
    )
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": "你是严谨的网页候选筛选助手，只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    request = Request(
        config.api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
        raw = data["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
        parsed = json.loads(raw)
        return {
            "ai_relevance_score": str(int(parsed.get("ai_relevance_score") or 0)),
            "ai_keep": "yes" if bool(parsed.get("ai_keep")) else "no",
            "ai_filter_reason": str(parsed.get("ai_filter_reason") or "").strip(),
        }
    except (HTTPError, URLError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "ai_relevance_score": "0",
            "ai_keep": "no",
            "ai_filter_reason": f"AI筛选失败：{exc}",
        }


def select_rows(rows, top_n_per_site):
    by_site = defaultdict(list)
    for row in rows:
        if row.get("status") != "ok":
            continue
        try:
            row["_score"] = int(row.get("preliminary_score") or 0)
        except ValueError:
            row["_score"] = 0
        by_site[row.get("site_name", "")].append(row)

    selected = []
    for site_name in sorted(by_site):
        site_rows = sorted(by_site[site_name], key=lambda item: item["_score"], reverse=True)
        selected.extend(site_rows[:top_n_per_site])
    return selected


def main():
    parser = argparse.ArgumentParser(description="Use an LLM to pre-filter search candidates before detail crawling.")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--summary-mode", choices=["saved", "popup"], default="saved")
    parser.add_argument("--top-n-per-site", type=int, default=5)
    parser.add_argument("--min-ai-score", type=int, default=60)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    set_summary_mode(args.summary_mode)
    config = get_summary_config()
    input_path = Path(args.candidates)
    rows = read_csv(input_path)
    selected_keys = {id(row) for row in select_rows(rows, args.top_n_per_site)}

    output_rows = []
    evaluated_count = 0
    kept_count = 0
    for row in rows:
        if id(row) not in selected_keys:
            row["ai_relevance_score"] = ""
            row["ai_keep"] = "no"
            row["ai_filter_reason"] = "未进入AI精筛范围"
            row["status"] = "skipped"
            row["error"] = row.get("error", "") or "未进入AI精筛范围"
            output_rows.append(row)
            continue
        result = candidate_relevance(config, args.topic, row)
        row.update(result)
        evaluated_count += 1
        if result["ai_keep"] == "yes" and int(result["ai_relevance_score"] or 0) >= args.min_ai_score:
            kept_count += 1
            output_rows.append(row)
        else:
            row["status"] = "skipped"
            row["error"] = row.get("error", "") or "AI精筛剔除"
            output_rows.append(row)

    path = Path(args.output) if args.output else output_path(input_path, args.topic)
    write_csv(output_rows, path)
    print(f"输入候选: {input_path}")
    print(f"输出候选: {path}")
    print(f"AI评估候选数: {evaluated_count}")
    print(f"AI保留候选数: {kept_count}")


if __name__ == "__main__":
    main()
