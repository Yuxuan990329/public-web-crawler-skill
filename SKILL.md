---
name: public-web-crawler
description: Crawl whitelisted public Chinese websites by topic, discover search candidates, fetch public detail pages, evaluate quality, and output CSV files. Use when the user asks to crawl public website content by topic, run quick/full crawler modes, use DeepSeek for summaries/classification or Top N candidate filtering, resume failed crawler jobs, inspect crawler CSV results, or package/run this public website crawler skill.
---

# Public Web Crawler

## Core Rule

Use this skill for topic-based crawling from the bundled whitelist. The executable capability lives in `scripts/`; this `SKILL.md` is the workflow and boundary guide.

Do not bypass login, CAPTCHA, paywalls, access controls, or explicit `robots.txt` restrictions. Treat restricted sources as skipped, public preview, or known-limit results.

## First Choice

For a user-facing run, prefer the interactive launcher:

```powershell
python scripts/start_crawler.py
```

For deterministic runs, use:

```powershell
python scripts/run_pipeline.py
```

Use `scripts/crawl_whitelist.py` only as a fallback for direct whitelist URL crawling. The main workflow is search-candidate discovery followed by detail crawling.

## Modes

Choose the mode before running:

```text
basic quick  = quick validation, no DeepSeek, no API fee
basic full   = full crawl, no DeepSeek, no API fee
enhanced quick = quick crawl with DeepSeek summary/classification, API fee
enhanced full  = full crawl with DeepSeek, higher API fee, require explicit user confirmation
```

If the user gives a topic but no mode, ask them to choose from those four options.

## Commands

Basic quick:

```powershell
python scripts/run_pipeline.py --topic "AI" --mode quick --whitelist 白名单网站模板_栏目优化.xlsx --summary-mode excerpt --resume
```

Basic full:

```powershell
python scripts/run_pipeline.py --topic "AI" --mode full --whitelist 白名单网站模板_栏目优化.xlsx --summary-mode excerpt --resume
```

DeepSeek enhanced quick:

```powershell
python scripts/run_pipeline.py --topic "AI" --mode quick --whitelist 白名单网站模板_栏目优化.xlsx --summary-mode saved --resume
```

DeepSeek candidate filtering before detail crawl:

```powershell
python scripts/run_pipeline.py --topic "AI" --mode quick --whitelist 白名单网站模板_栏目优化.xlsx --summary-mode saved --resume --ai-filter-top-n 5 --ai-filter-min-score 60
```

Change API config at runtime:

```powershell
python scripts/run_pipeline.py --topic "AI" --mode quick --whitelist 白名单网站模板_栏目优化.xlsx --summary-mode popup --resume
```

Resume a failed full run:

```powershell
python scripts/run_pipeline.py --topic "AI" --mode full --whitelist 白名单网站模板_栏目优化.xlsx --summary-mode excerpt --resume
```

## Install And Check

When this skill folder is copied to a new machine, install dependencies from the skill root:

```powershell
python -m pip install -r requirements.txt
python scripts/check_environment.py
```

The skill ships with `config/summary_api.example.json`. For DeepSeek mode, create:

```text
config/summary_api.local.json
```

Use `summary_api.local.json` for personal machines only. Do not expose API keys in replies, logs, or shared packages.

## Output

Prioritize the final CSV:

```text
outputs/*_final.csv
```

Useful supporting outputs:

```text
outputs/*_搜索候选.csv
outputs/*_quality.csv
pipeline_runs/<topic>_<mode>/manifest.json
pipeline_runs/<topic>_<mode>/sites/*.csv
```

Report at least:

```text
mode
whether DeepSeek was called
final CSV path
row count
pass/review distribution
site distribution
known_limit count
quality_issue count
```

## Important Fields

```text
summary              local excerpt, no API call
ai_summary           DeepSeek summary
ai_category          DeepSeek category
ai_reason            DeepSeek summary/classification reason
ai_relevance_score   DeepSeek candidate relevance score
ai_keep              DeepSeek candidate keep decision
ai_filter_reason     DeepSeek candidate filtering reason
content_type         html or public_preview
known_limit          known limitation such as paid report or PDF 403
quality_issue        real extraction or quality problem
review_required      whether manual review is needed
```

## Site Strategy

```text
Stable full text: 华经情报网, 智研咨询, 艾瑞咨询, 国家统计局
Observe: CBNData
Public preview: 前瞻网
Discovery/metadata only: 洞见研报
Skip: login, paid, CAPTCHA, robots-restricted sites
```

## Validation

After code changes, run:

```powershell
python -m py_compile scripts\start_crawler.py scripts\ai_filter_candidates.py scripts\run_pipeline.py scripts\crawl_whitelist.py scripts\crawl_search_candidates.py
python scripts/check_environment.py
```

For a quick live validation without DeepSeek:

```powershell
python scripts/run_pipeline.py --topic "空调 行业动态" --mode quick --whitelist 白名单网站模板_栏目优化.xlsx --summary-mode excerpt --resume --request-delay 0.1
```

For a quick DeepSeek validation:

```powershell
python scripts/run_pipeline.py --topic "空调 行业动态" --mode quick --whitelist 白名单网站模板_栏目优化.xlsx --summary-mode saved --resume --ai-filter-top-n 5 --ai-filter-min-score 60 --request-delay 0.1
```

## References

Read these only when needed:

```text
references/使用说明.md              user-facing usage details
references/迁移安装说明.md          copy/install guidance for other machines
references/CBNData观察说明.md       CBNData quality and downgrade policy
```
