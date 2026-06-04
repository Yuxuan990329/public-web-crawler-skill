import json
from dataclasses import dataclass
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, Toplevel, messagebox, ttk
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REQUEST_TIMEOUT_SECONDS = 60
_SUMMARY_CONFIG = None
PROJECT_DIR = Path(__file__).resolve().parent.parent
LOCAL_CONFIG_PATH = PROJECT_DIR / "config" / "summary_api.local.json"

PROVIDERS = {
    "DeepSeek V4 Flash": {
        "api_url": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-v4-flash",
    },
    "DeepSeek V4 Pro": {
        "api_url": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-v4-pro",
    },
    "豆包 / 火山方舟": {
        "api_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "model": "",
    },
    "OpenAI": {
        "api_url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o-mini",
    },
    "自定义 OpenAI-compatible": {
        "api_url": "",
        "model": "",
    },
}


@dataclass
class SummaryConfig:
    use_api: bool
    api_url: str
    api_key: str
    model: str


def summarize(title, url, content, topic, keywords):
    """Return a short Chinese summary.

    The API credentials are requested once per run through a popup and are not
    saved to disk. If the popup is cancelled or the API call fails, the function
    falls back to a simple text excerpt.
    """
    text = " ".join((content or "").split())
    if not text:
        return ""

    config = get_summary_config()
    if not config.use_api:
        return fallback_summary(text)

    try:
        return summarize_with_openai_compatible_api(
            config=config,
            title=title,
            url=url,
            content=text,
            topic=topic,
            keywords=keywords,
        )
    except Exception as exc:
        return f"{fallback_summary(text)} [摘要 API 调用失败：{exc}]"


def summarize_enhancement(title, url, content, topic, keywords):
    text = " ".join((content or "").split())
    if not text:
        return {"ai_summary": "", "ai_category": "", "ai_reason": ""}

    config = get_summary_config()
    if not config.use_api:
        return {"ai_summary": "", "ai_category": "", "ai_reason": ""}

    try:
        return analyze_with_openai_compatible_api(
            config=config,
            title=title,
            url=url,
            content=text,
            topic=topic,
            keywords=keywords,
        )
    except Exception as exc:
        return {
            "ai_summary": "",
            "ai_category": "",
            "ai_reason": f"摘要 API 调用失败：{exc}",
        }


def set_summary_mode(mode):
    """Set summary behavior for the current process.

    mode=popup asks for API credentials on first matched page.
    mode=excerpt always uses the local excerpt fallback.
    mode=saved uses config/summary_api.local.json without showing a popup.
    """
    global _SUMMARY_CONFIG
    if mode == "excerpt":
        _SUMMARY_CONFIG = SummaryConfig(use_api=False, api_url="", api_key="", model="")
    elif mode == "popup":
        _SUMMARY_CONFIG = None
    elif mode == "saved":
        config = load_local_summary_config()
        if not config:
            raise ValueError("未找到可用的本地摘要 API 配置：config/summary_api.local.json")
        _SUMMARY_CONFIG = config
    else:
        raise ValueError(f"未知摘要模式: {mode}")


def set_summary_config(api_url, api_key, model):
    """Set one-time API config for the current process without saving it."""
    global _SUMMARY_CONFIG
    _SUMMARY_CONFIG = SummaryConfig(
        use_api=True,
        api_url=api_url,
        api_key=api_key,
        model=model,
    )


def fallback_summary(text):
    return text[:200]


def get_summary_config():
    global _SUMMARY_CONFIG
    if _SUMMARY_CONFIG is None:
        _SUMMARY_CONFIG = ask_summary_config()
    return _SUMMARY_CONFIG


def ask_summary_config():
    root = Tk()
    root.withdraw()
    saved_config = load_local_summary_config()
    if saved_config and messagebox.askyesno(
        "摘要 API 配置",
        f"检测到已保存配置：{saved_config.model}\n本次运行是否直接使用？\n\n选择“否”可以修改 API URL / Key / 模型。",
    ):
        root.destroy()
        return saved_config

    dialog = Toplevel(root)
    dialog.title("填写摘要 API")
    dialog.resizable(False, False)
    dialog.grab_set()

    use_api_var = BooleanVar(value=True)
    provider_var = StringVar(value="DeepSeek V4 Flash")
    api_url_var = StringVar(value=saved_config.api_url if saved_config else PROVIDERS["DeepSeek V4 Flash"]["api_url"])
    api_key_var = StringVar(value=saved_config.api_key if saved_config else "")
    model_var = StringVar(value=saved_config.model if saved_config else PROVIDERS["DeepSeek V4 Flash"]["model"])
    cancelled = {"value": True}

    frame = ttk.Frame(dialog, padding=16)
    frame.grid(row=0, column=0, sticky="nsew")

    ttk.Checkbutton(frame, text="本次运行使用模型 API 生成摘要", variable=use_api_var).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
    )

    ttk.Label(frame, text="供应商预设").grid(row=1, column=0, sticky="w", pady=4)
    provider_box = ttk.Combobox(
        frame,
        textvariable=provider_var,
        values=list(PROVIDERS.keys()),
        state="readonly",
        width=55,
    )
    provider_box.grid(row=1, column=1, sticky="ew", pady=4)

    ttk.Label(frame, text="API URL").grid(row=2, column=0, sticky="w", pady=4)
    ttk.Entry(frame, textvariable=api_url_var, width=58).grid(row=2, column=1, sticky="ew", pady=4)

    ttk.Label(frame, text="API Key / 密码").grid(row=3, column=0, sticky="w", pady=4)
    ttk.Entry(frame, textvariable=api_key_var, width=58, show="*").grid(row=3, column=1, sticky="ew", pady=4)

    ttk.Label(frame, text="模型名").grid(row=4, column=0, sticky="w", pady=4)
    ttk.Entry(frame, textvariable=model_var, width=58).grid(row=4, column=1, sticky="ew", pady=4)

    hint = "这里填写完整 chat/completions 地址，不是 SDK base_url。预设可手动修改；取消或不填密钥时使用正文前 200 字。"
    ttk.Label(frame, text=hint, foreground="#555555", wraplength=520).grid(
        row=5, column=0, columnspan=2, sticky="w", pady=(8, 12)
    )

    buttons = ttk.Frame(frame)
    buttons.grid(row=6, column=0, columnspan=2, sticky="e")

    def apply_provider(_event=None):
        preset = PROVIDERS.get(provider_var.get(), {})
        api_url_var.set(preset.get("api_url", ""))
        model_var.set(preset.get("model", ""))

    provider_box.bind("<<ComboboxSelected>>", apply_provider)

    def submit():
        if use_api_var.get() and (
            not api_url_var.get().strip()
            or not api_key_var.get().strip()
            or not model_var.get().strip()
        ):
            messagebox.showwarning("缺少信息", "如果使用模型 API，请填写 API URL、API Key / 密码和模型名。")
            return
        cancelled["value"] = False
        dialog.destroy()

    def cancel():
        cancelled["value"] = True
        dialog.destroy()

    ttk.Button(buttons, text="取消，使用截取摘要", command=cancel).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(buttons, text="确定", command=submit).grid(row=0, column=1)

    dialog.protocol("WM_DELETE_WINDOW", cancel)
    dialog.update_idletasks()
    width = dialog.winfo_width()
    height = dialog.winfo_height()
    x = (dialog.winfo_screenwidth() // 2) - (width // 2)
    y = (dialog.winfo_screenheight() // 2) - (height // 2)
    dialog.geometry(f"+{x}+{y}")

    root.wait_window(dialog)
    root.destroy()

    if cancelled["value"]:
        return SummaryConfig(use_api=False, api_url="", api_key="", model="")

    config = SummaryConfig(
        use_api=use_api_var.get(),
        api_url=api_url_var.get().strip(),
        api_key=api_key_var.get().strip(),
        model=model_var.get().strip(),
    )
    if config.use_api:
        save_local_summary_config(config)
    return config


def load_local_summary_config():
    if not LOCAL_CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    api_url = str(data.get("api_url") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    model = str(data.get("model") or "").strip()
    if not api_url or not api_key or not model:
        return None
    return SummaryConfig(use_api=True, api_url=api_url, api_key=api_key, model=model)


def save_local_summary_config(config):
    LOCAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_CONFIG_PATH.write_text(
        json.dumps(
            {
                "api_url": config.api_url,
                "api_key": config.api_key,
                "model": config.model,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def summarize_with_openai_compatible_api(config, title, url, content, topic, keywords):
    prompt = (
        "请根据用户主题，概括以下网页正文中与主题直接相关的核心信息。\n"
        "要求：中文，100-200字，保留关键数据、政策名称、机构名称和时间信息。\n"
        "如果正文只命中部分关键词，但缺少用户主题中的关键含义，请明确说明缺少哪部分信息，不要泛泛总结全文。\n"
        "如果正文与用户主题不相关，请只输出：本网页内容未涉及{topic}相关信息。\n\n"
        f"用户主题：{topic}\n"
        f"关键词：{'、'.join(keywords)}\n"
        f"标题：{title}\n"
        f"来源：{url}\n\n"
        f"正文：{content[:6000]}"
    )
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": "你是严谨的信息摘要助手，只输出摘要正文。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
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
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:200]
        raise RuntimeError(f"HTTP {exc.code} {detail}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("响应格式不是 chat/completions") from exc
def analyze_with_openai_compatible_api(config, title, url, content, topic, keywords):
    prompt = (
        "请根据用户主题分析网页正文，严格输出 JSON，不要输出 Markdown。\n"
        "JSON 字段：ai_summary, ai_category, ai_reason。\n"
        "ai_summary：中文 100-200 字，只概括与用户主题直接相关的信息，保留关键数据、机构、时间。\n"
        "ai_category：从 政策/产业数据/企业动态/研究报告/市场观点/技术趋势/其他 中选择一个。\n"
        "ai_reason：用 1-2 句话说明为什么保留这条结果，指出关键词命中位置或核心相关性；如果相关性弱，也要说明。\n\n"
        f"用户主题：{topic}\n"
        f"关键词：{'、'.join(keywords)}\n"
        f"标题：{title}\n"
        f"来源：{url}\n\n"
        f"正文：{content[:6000]}"
    )
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": "你是严谨的信息筛选助手，只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
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
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:200]
        raise RuntimeError(f"HTTP {exc.code} {detail}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc

    try:
        raw = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("响应格式不是 chat/completions") from exc

    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.removeprefix("json").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI 返回内容不是 JSON: {raw[:120]}") from exc

    return {
        "ai_summary": str(parsed.get("ai_summary") or "").strip(),
        "ai_category": str(parsed.get("ai_category") or "").strip(),
        "ai_reason": str(parsed.get("ai_reason") or "").strip(),
    }
