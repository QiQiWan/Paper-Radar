# Paper-Radar 版权与第三方说明

Paper-Radar · Copyright (c) 2026 @EatRice。软件代码按仓库内 [MIT License](LICENSE) 发布；正式公开前应由代码权利人确认授权资格及引用代码的许可遵守情况。部分方案参考了现有公开文献追踪工具的工作流程，保留必要的来源说明。

arXiv、OpenAlex、Crossref、Springer Nature、Elsevier、Zotero、DeepSeek、OpenAI、Anthropic、火山引擎等名称属于各自权利人。Paper-Radar 是独立开源项目，上述平台未为本项目背书。第三方服务条款、API 数据和访问限额由其官方决定。

下载与引用论文时，应保留原作者信息、DOI、正式页面及授权信息。已开放阅读的论文不当然允许以任何方式重新分发。本项目不会规避订阅、校园网身份验证或付费墙。

参考：
- https://info.arxiv.org/help/api/index.html
- https://docs.openalex.org/
- https://www.crossref.org/documentation/retrieve-metadata/rest-api/
- https://dev.springernature.com/
- https://dev.elsevier.com/
- https://www.zotero.org/support/dev/web_api/v3/start

### v1.12.0 本地 PDF 解析依赖

`pypdf` 为独立的第三方开源 Python 库，采用 BSD-3-Clause 许可证，项目地址：<https://github.com/py-pdf/pypdf>；Paper-Radar 将其作为本地 PDF 文本层解析依赖。源码发行包不包含或转售第三方论文 PDF，使用者应遵守各篇论文、出版社和数据提供方适用的使用许可。发布打包的可执行版本前，需再次收集和附加所有实际分发依赖的第三方许可证。
