# Public Web Crawler Skill

A Codex Skill for crawling whitelisted public Chinese websites by topic.

It discovers search candidates, fetches public detail pages, evaluates content quality, and outputs CSV files. It also supports optional DeepSeek-based summaries, classification, and Top N candidate filtering.

## What It Does

- Crawl public whitelisted websites by topic.
- Run `quick` or `full` modes.
- Resume interrupted jobs.
- Output candidate, final, and quality CSV files.
- Mark known limits such as paid reports, public previews, or restricted PDFs.
- Optionally use DeepSeek or another OpenAI-compatible API for summaries and classification.

## What It Does Not Do

- Does not bypass login, CAPTCHA, paywalls, 403 restrictions, or access controls.
- Does not bypass explicit `robots.txt` restrictions.
- Does not crawl private or non-whitelisted data.
- Does not include any API key in this repository.

## Install

Clone or copy this skill folder, then install dependencies:

```powershell
python -m pip install -r requirements.txt
python scripts/check_environment.py
```

## Basic Usage

Interactive launcher:

```powershell
python scripts/start_crawler.py
```

Basic quick mode, no API cost:

```powershell
python scripts/run_pipeline.py --topic "AI" --mode quick --whitelist 白名单网站模板_栏目优化.xlsx --summary-mode excerpt --resume
```

Basic full mode, no API cost:

```powershell
python scripts/run_pipeline.py --topic "AI" --mode full --whitelist 白名单网站模板_栏目优化.xlsx --summary-mode excerpt --resume
```

## DeepSeek Enhanced Mode

Create a local config file from the example:

```text
config/summary_api.example.json -> config/summary_api.local.json
```

Then fill in your own API key.

Enhanced quick mode:

```powershell
python scripts/run_pipeline.py --topic "AI" --mode quick --whitelist 白名单网站模板_栏目优化.xlsx --summary-mode saved --resume
```

Enhanced quick mode with AI candidate filtering:

```powershell
python scripts/run_pipeline.py --topic "AI" --mode quick --whitelist 白名单网站模板_栏目优化.xlsx --summary-mode saved --resume --ai-filter-top-n 5 --ai-filter-min-score 60
```

## Outputs

Main output:

```text
outputs/*_final.csv
```

Supporting outputs:

```text
outputs/*_搜索候选.csv
outputs/*_quality.csv
pipeline_runs/<topic>_<mode>/manifest.json
```

Sample files:

```text
examples/sample_final.csv
examples/sample_quality.csv
```

## Key CSV Fields

| Field | Meaning |
| --- | --- |
| `summary` | Local excerpt, no API call |
| `ai_summary` | DeepSeek summary |
| `ai_category` | DeepSeek category |
| `ai_reason` | DeepSeek reasoning |
| `ai_relevance_score` | AI candidate relevance score |
| `ai_keep` | AI keep/drop decision |
| `ai_filter_reason` | AI candidate filtering reason |
| `content_type` | `html` or `public_preview` |
| `known_limit` | Known limitation |
| `quality_issue` | Real extraction or quality issue |
| `review_required` | Manual review flag |

## Skill Package

This repository is structured as a Codex Skill package:

```text
SKILL.md
agents/openai.yaml
scripts/
references/
examples/
config/summary_api.example.json
requirements.txt
白名单网站模板_栏目优化.xlsx
```

To install into another Codex environment, copy this repository folder to the target skills directory, for example:

```text
~/.codex/skills/public-web-crawler/
```

Then run:

```powershell
python -m pip install -r requirements.txt
python scripts/check_environment.py
```

## Notes

- `config/summary_api.local.json` is intentionally ignored.
- `outputs/`, `logs/`, and `pipeline_runs/` are intentionally ignored.
- The default whitelist can be edited or replaced as needed.
