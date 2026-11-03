# 文献检索来源与访问权限

Paper-Radar 将来源分为三类：可直接调用的公开来源、需要注册 API Key 的来源、需要 API Key 与机构授权的来源。**授权来源默认关闭。**

| 来源 | 默认为 | 配置项 | 下载说明 |
| --- | --- | --- | --- |
| arXiv | 开启 | 无 | 可按公开 PDF 地址下载 |
| OpenAlex | 开启 | 如受额度限制配置 API Key | 只下载明确标为开放获取的 PDF |
| Crossref | 开启 | 无 | 一般提供 DOI/题录，未必有 PDF |
| Springer Nature Meta API | 关闭 | Springer Developer Portal API Key | 当前只读取题录；付费全文需授权 |
| Elsevier Scopus API | 关闭 | Elsevier API Key、接口权利，可能需要机构 IP/VPN 或 Insttoken | 当前只读取题录 |
| Elsevier ScienceDirect Search | 关闭 | Elsevier API Key，需 ScienceDirect API 权限，可能需要机构访问环境 | 当前只读取题录 |

## 在界面启用出版社来源

在 **设置 → 出版社检索授权** 分别保存 Springer API Key 或 Elsevier API Key；若你的机构发放 Insttoken，可在 Elsevier 区域填写。返回工作台勾选相应来源。没有相关权限时保持关闭，不影响公开来源检索。密钥保存在系统凭据管理器，软件不会请求学校统一认证账号密码。

**机构访问必须通过合法授权。** 校园网、图书馆代理或 VPN 可能满足机构 IP 识别条件，但无法代替 API Key，也无法保证具备程序化下载权。论文页可在浏览器打开；如需使用订阅 PDF，遵守学校图书馆与出版方条款，当前程序不提供出版社付费全文批量下载。

## 各来源的限制

arXiv 偏重预印本，可检索到尚未正式出版的论文。OpenAlex 提供广泛学术索引，部分记录的摘要、期刊元数据或 OA 链接可能缺失。Crossref 适合 DOI 和出版信息校验，题录质量因出版方提交而异。Springer 的 Meta API 返回元数据，不能据此推断拥有全文权利。Scopus 和 ScienceDirect 使用不同的 Elsevier Search API，两者可以独立选择，权限可能不同。

多个来源的论文优先按 DOI 合并，并保留现有本地星标、阅读状态与下载记录。不同来源的题录可能存在延迟、缺字段、重复或版本差异。系统会按当前关键词计算匹配度后统一排序，默认选择30篇。

## 申请入口与官方文档

- arXiv API：https://info.arxiv.org/help/api/index.html
- OpenAlex API：https://docs.openalex.org/
- Crossref REST API：https://www.crossref.org/documentation/retrieve-metadata/rest-api/
- Springer Nature Developer Portal：https://dev.springernature.com/；Meta API 文档：https://dev.springernature.com/docs/api-endpoints/meta-api/
- Elsevier Developer Portal：https://dev.elsevier.com/；认证：https://dev.elsevier.com/tecdoc_api_authentication.html
- ScienceDirect Search API：https://dev.elsevier.com/documentation/SCIDIRSearchAPI.wadl

出版社可能调整订阅政策、免费额度和接口，出现 HTTP 401、403、429 应先确认当前官方规定。**当前实现尚未在真实出版社授权账户下做端到端验证。**
