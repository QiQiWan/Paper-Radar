# arXiv 来源、版本与 PDF 使用说明

本评估集使用真实 arXiv 论文的**固定版本**地址。每篇保存原文 PDF SHA-256，并在采集时进行结构、页数和文本可读性检查。

arXiv 官方说明：论文可以免费下载和阅读，但**不同投稿所适用的再利用与再分发许可不同**；arXiv 非独占发行许可仅授予 arXiv 分发权限，其他机构不因能免费下载而自动获得再次发布原文的权利。公开代码库因此只包含元数据、校验值、短证据索引、独立人工标注表和私人下载脚本，不托管未经逐篇许可核查的 PDF 原文。

- 许可信息：https://info.arxiv.org/help/license/index.html
- API 文档：https://info.arxiv.org/help/api/user-manual.html
- 批量访问说明：https://info.arxiv.org/help/bulk_data.html

下载脚本有 3.2 秒请求间隔，按原始版本 SHA 校验，支持已有文件断点跳过。正式复用前，仍需审核每篇 PDF 页面中标注的许可与文章版本。
