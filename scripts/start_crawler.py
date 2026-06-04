import subprocess
import sys
from pathlib import Path

from crawl_whitelist import DEFAULT_WHITELIST, read_sites


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent


def ask(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def ask_yes_no(prompt, default=True):
    default_text = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{default_text}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "是", "1", "true"}


def choose_mode():
    value = ask("运行模式 quick/full", "quick").lower()
    return "full" if value == "full" else "quick"


def choose_sites(whitelist):
    sites = [site for site in read_sites(whitelist) if site.enabled == "yes"]
    print("\n可选站点：")
    for index, site in enumerate(sites, 1):
        print(f"{index}. {site.site_name}")
    print("直接回车 = 全部启用站点；也可以输入序号，如 1,3,5；或输入站点名，用逗号分隔。")
    value = ask("选择站点", "")
    if not value:
        return ""
    parts = [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
    selected = []
    for part in parts:
        if part.isdigit():
            index = int(part)
            if 1 <= index <= len(sites):
                selected.append(sites[index - 1].site_name)
        else:
            selected.append(part)
    return ",".join(selected)


def main():
    print("公开网站爬虫启动器")
    print("=" * 24)

    topic = ask("主题", "AI")
    mode = choose_mode()
    whitelist = ask("白名单文件", DEFAULT_WHITELIST)
    use_deepseek = ask_yes_no("是否启用 DeepSeek 增强摘要/分类", False)
    summary_mode = "saved" if use_deepseek else "excerpt"
    resume = ask_yes_no("是否启用 resume 断点续跑", True)
    sites = choose_sites(whitelist)

    ai_filter_top_n = 0
    ai_filter_min_score = 60
    if use_deepseek and ask_yes_no("是否启用候选阶段 Top N AI 精筛", False):
        ai_filter_top_n = int(ask("每站进入 AI 精筛的候选数", "5"))
        ai_filter_min_score = int(ask("AI 相关性最低分", "60"))

    command = [
        sys.executable,
        str(SCRIPT_DIR / "run_pipeline.py"),
        "--topic",
        topic,
        "--mode",
        mode,
        "--whitelist",
        whitelist,
        "--summary-mode",
        summary_mode,
    ]
    if resume:
        command.append("--resume")
    if sites:
        command.extend(["--sites", sites])
    if ai_filter_top_n > 0:
        command.extend(["--ai-filter-top-n", str(ai_filter_top_n), "--ai-filter-min-score", str(ai_filter_min_score)])

    print("\n即将运行：")
    print(" ".join(f'"{part}"' if " " in part else part for part in command))
    if not ask_yes_no("确认运行", True):
        print("已取消。")
        return

    completed = subprocess.run(command, cwd=PROJECT_DIR)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
