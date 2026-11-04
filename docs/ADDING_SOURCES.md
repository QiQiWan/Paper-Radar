# 添加一个检索来源

以 `paper_radar/sources/openalex.py` 为参考，按以下步骤扩展。

1. 新建 `paper_radar/sources/<source_name>.py`。
2. 实现 `name`、`label` 和 `fetch(config, logger, now) -> list[Paper]`。
3. 将来源结果转换为 `models.Paper`。必须提供稳定 `paper_id`、标题、作者、发表日期和来源页面链接。摘要可以为空。
4. 在 `sources/__init__.py` 中导入并调用 `register(YourSource)`。
5. 页面会自动读取检索来源注册表，完成注册后即可在“设置”中选择该来源，无需手动修改前端名单。新增 API Key 时还需在 `sources/credentials.py` 与设置页增加安全保存入口。默认启用 arXiv、OpenAlex、Crossref；具有授权要求的来源应在 `access` 属性中说明并默认关闭。
6. 补充固定样例下的解析、重复查询、空结果和 API 错误测试。

```python
from ..models import Paper

class ExampleSource:
    name = 'example'
    label = '示例学术数据库'
    access = 'free'
    guide_url = 'https://example.org/developer'
    note = '公开元数据。'

    def fetch(self, config, logger=print, now=None) -> list[Paper]:
        # 在这里调用官方 API、处理限流与分页。
        # 每条记录转换成 Paper。
        return []
```

注册：

```python
from .example import ExampleSource
register(ExampleSource)
```

## 字段约定

| 字段 | 含义 |
| --- | --- |
| `paper_id` | 稳定的本地标识，例如 `openalex:W123` |
| `source` | 来源标识，和 `name` 一致 |
| `doi` | 去掉 `https://doi.org/` 的小写 DOI；没有则留空 |
| `url` | 原始论文页面，不要把付费页面伪装为下载链接 |
| `abstract` | 原文摘要，可能为空 |
| `venue` | 期刊或会议名称 |
| `is_oa` | 来源明确标注可免费阅读全文时填 `1` |
| `download_url` | 来源官方元数据给出的直接 PDF HTTPS 链接；否则留空 |
| `oa_license` | 来源声明的开放许可，未知时留空 |

不要绕过订阅登录、验证码和平台反爬限制。严格按官方 API 条款控制访问频率。

引擎出错时，调度器记录失败并继续执行其他来源。所有来源都失败时，整次检索返回失败，避免覆盖上一份报告。


## v1.10.0 多查询适配注意事项

调度器的 `sources/base.py` 根据研究关键词生成查询计划。arXiv 使用组合查询；其他适配器会收到单个关键词及单组的 `max_results`，因此适配器仍应按 `fetch(config, logger, now)` 返回列表，不需要自己遍历所有关键词。查询组数默认最多8组（1—20可配置），调度器在各组之间设置基础间隔。新增来源请核实平台是否允许这种方式、实际限流要求以及是否支持单个查询词，并在适配器里补充额外的速率限制。重要信息请见 [v1.10.0 审查](REVIEW_V1.10.0.md)。
