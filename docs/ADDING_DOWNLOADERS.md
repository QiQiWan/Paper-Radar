# 添加一个下载引擎

下载器与检索来源分开管理。检索来源负责提供 `Paper.download_url`，下载器决定当前论文使用哪一种下载方式。

1. 新建 `paper_radar/downloads/<engine>.py`。
2. 实现 `name`、`supports(paper)` 和 `url_for(paper)`。
3. 在 `downloads/__init__.py` 导入并 `register()`。
4. 统一通过 `downloads/manager.py` 下载，不要自行实现文件写入和 HTTP 跳转。
5. 增加失败、错误 MIME、超大文件、重复下载和本地路径安全测试。

```python
class ExampleOpenPDF:
    name = 'example_pdf'

    def supports(self, paper):
        return paper.source == 'example' and bool(paper.download_url and paper.is_oa)

    def url_for(self, paper):
        return paper.download_url
```

> 注意：注册顺序代表优先级。现有引擎先尝试 arXiv PDF，再尝试开放获取直链。

## 安全与许可

- 管理器仅允许公网 HTTPS URL 和 HTTPS 重定向。
- 每篇默认最多 30 MB，允许用户设置 1—50 MB。
- 下载内容必须以 `%PDF-` 开头；HTML 错误页面不会保存为 PDF。
- 文件先写入 `.part`，完成后原子重命名；失败会删除临时文件。
- 只能用数据库中已有的论文 ID 提交任务，单次最多 20 篇。
- 只处理明确开放获取的全文；没有许可或 PDF 直链时返回“无可下载文件”。

下一步可以扩展：授权的机构仓储 API、失败重试队列、断点续传、PDF 内容索引。引入任何订阅数据库前，需要先核对机构许可。
