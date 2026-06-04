# CBNData 观察说明

## 当前结论

CBNData 已能进入 full 结果，但页面质量需要继续观察，不能简单等同于稳定全文源。

当前可能出现三类页面：

| 类型 | 表现 | 当前处理 |
| --- | --- | --- |
| 真实正文页 | 正文较长，能通过 `window.__INITIAL_STATE__` 或页面正文提取 | `content_type=html` |
| 摘要/短介绍页 | 只有标题和少量介绍文字 | 标记 `quality_issue`，必要时后续降级 |
| 异常/空正文页 | 详情页正文不可用或过短 | `review_required=yes` |

## 当前提取策略

CBNData 专用提取器按顺序尝试：

1. `window.__INITIAL_STATE__`
2. 递归查找 `content/body/articleContent/detail/summary/description`
3. `meta description`
4. 通用 HTML 正文提取

## 后续判断标准

建议后续按 full 输出继续抽样：

- 正文长度大于 500 字，且包含连续段落：可视为正文页。
- 正文少于 100 字：视为摘要页或异常页。
- 出现登录、验证码、无正文：进入 review。

## 是否降级

如果后续 CBNData 大量页面都只有摘要，建议整体降级为：

```text
content_type = public_preview
known_limit = CBNData公开页面多为摘要，完整正文不稳定
```

当前暂不整体降级，继续观察。
