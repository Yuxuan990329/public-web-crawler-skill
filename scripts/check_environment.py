import importlib.util
import json
import sys
from pathlib import Path

from crawler_defaults import DEFAULT_WHITELIST


PROJECT_DIR = Path(__file__).resolve().parent.parent
REQUIRED_FILES = [
    "SKILL.md",
    "requirements.txt",
    DEFAULT_WHITELIST,
    "scripts/run_pipeline.py",
    "scripts/start_crawler.py",
    "scripts/crawl_search_candidates.py",
    "scripts/discover_search_candidates.py",
    "scripts/evaluate_output_quality.py",
]
REQUIRED_MODULES = ["openpyxl", "pypdf"]
LOCAL_API_CONFIG = PROJECT_DIR / "config" / "summary_api.local.json"


def has_module(name):
    return importlib.util.find_spec(name) is not None


def check_api_config():
    if not LOCAL_API_CONFIG.exists():
        return "未配置", "未找到 config/summary_api.local.json，基础模式可运行，增强模式需要配置 API。"
    try:
        data = json.loads(LOCAL_API_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "异常", "config/summary_api.local.json 不是有效 JSON。"
    missing = [key for key in ("api_url", "api_key", "model") if not str(data.get(key) or "").strip()]
    if missing:
        return "异常", f"缺少字段: {', '.join(missing)}"
    model = str(data.get("model") or "").strip()
    return "已配置", f"增强模式 API 已配置，模型: {model}。"


def main():
    print("公开网站爬虫环境检查")
    print("=" * 28)
    print(f"Python: {sys.version.split()[0]}")
    print(f"项目目录: {PROJECT_DIR}")

    failed = False
    print("\n文件检查:")
    for relative_path in REQUIRED_FILES:
        path = PROJECT_DIR / relative_path
        status = "OK" if path.exists() else "缺失"
        print(f"- {relative_path}: {status}")
        if not path.exists():
            failed = True

    print("\n依赖检查:")
    for module_name in REQUIRED_MODULES:
        status = "OK" if has_module(module_name) else "未安装"
        print(f"- {module_name}: {status}")
        if status != "OK":
            failed = True

    api_status, api_message = check_api_config()
    print("\nAPI 配置:")
    print(f"- {api_status}: {api_message}")

    print("\n建议:")
    if failed:
        print("- 先运行: python -m pip install -r requirements.txt")
        print("- 确认白名单 Excel 和 scripts 目录已随项目一起复制。")
    else:
        print("- 基础模式可运行。")
    if api_status != "已配置":
        print("- 如需 DeepSeek 增强模式，复制 config/summary_api.example.json 为 config/summary_api.local.json 后填写 API。")
    else:
        print("- DeepSeek 增强模式可运行。")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
