import argparse

from summarize import PROVIDERS, set_summary_config, set_summary_mode, summarize


def main():
    parser = argparse.ArgumentParser(description="测试摘要 API 是否可用。")
    parser.add_argument("--provider", default="", help="供应商预设名称，例如 DeepSeek V4 Flash。")
    parser.add_argument("--api-url", default="", help="完整 chat/completions 地址。")
    parser.add_argument("--api-key", default="", help="一次性测试 API Key；不会写入文件。")
    parser.add_argument("--model", default="", help="模型名。")
    args = parser.parse_args()

    if args.provider or args.api_key or args.api_url or args.model:
        provider = PROVIDERS.get(args.provider, {})
        api_url = args.api_url or provider.get("api_url", "")
        model = args.model or provider.get("model", "")
        if not api_url or not args.api_key or not model:
            raise ValueError("使用命令参数测试时，需要 provider/api-url、api-key 和 model。")
        set_summary_config(api_url=api_url, api_key=args.api_key, model=model)
    else:
        set_summary_mode("popup")

    result = summarize(
        title="摘要 API 连通性测试",
        url="local-test",
        topic="人工智能 政策",
        keywords=["人工智能", "政策"],
        content=(
            "国家持续推进人工智能产业发展，鼓励关键技术攻关、行业应用落地和数据要素流通。"
            "相关政策强调加强算力基础设施建设，推动人工智能与制造、消费、公共服务等领域融合。"
        ),
    )
    print("摘要结果:")
    print(result)


if __name__ == "__main__":
    main()
