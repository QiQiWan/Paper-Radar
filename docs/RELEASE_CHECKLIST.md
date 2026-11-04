# Paper-Radar v1.12.0 发布检查清单

- [ ] 项目名称统一为 Paper-Radar，仓库地址正确。
- [ ] `LICENSE` 与 `NOTICE.md` 中 `@EatRice` 版权表述经过权利人确认。
- [ ] 不包含个人 Zotero 数据库、API Key、机构令牌、PDF、日志、`settings.json`。
- [ ] `python -m pytest -q`、`python -m compileall -q paper_radar main.py` 通过。
- [ ] `node --check paper_radar/static/app.js` 通过。
- [ ] 解压最终 GitHub ZIP，确认 `sources/`、`downloads/`、`reports/` 全部存在。
- [ ] Windows 首次双击启动、手动检索、自动后台任务、Zotero 本地通讯开关实际验证。
- [ ] 真实 arXiv / OpenAlex / Crossref 网络检索与 PDF 下载验证。
- [ ] 出版社来源仅在具备实际授权后启用并验证；不绕过订阅。
- [ ] DeepSeek 真实模型 ID、余额与用量口径在账号侧核对。
- [ ] 公众号文章中的截图注明演示数据，GitHub Release 链接在发布后验证。
- [ ] GitHub Release 标签为 v1.12.0，附件可下载、可解压。


## v1.10.0 必检项

- [ ] `paper_radar.__version__`、`README`、`CHANGELOG`、Release Tag 和源码 ZIP 版本完全一致。
- [ ] 旧版 `settings.json` 可无损升级为带 `topics` 的配置，多主题切换保持各自检索参数，报告分目录生成。
- [ ] 默认主题报告保留在 `reports/`，新增主题报告位于 `reports/themes/<id>/`；删除主题不会删除历史文献或 PDF。
- [ ] 多查询超过3组关键词均能触达适配器，部分失败会保留成功结果和诊断信息；实际 API 限额已核对。
- [ ] 版本检查启动时异步进行、定期检查有冷却时间，断网/404/429不阻塞检索。
- [ ] 只有 **已公开发布的正式 GitHub Release** 触发版本更新提示；创建 Git Tag / Draft / Prerelease 不会触发。
- [ ] 更新提醒链接限定为官方 HTTPS Release 页面，忽略某版后下一版仍可提醒。
- [ ] 在设置中关闭自动检查后，仍可手动检查并显示错误原因。
- [ ] Windows 上检查浏览器 UI、主题选择器、更新横幅与设置控件，窄屏无横向溢出。
- [ ] 更新公众号文章的 **v1.10.0 真实截图**；旧版 v1.9 截图不要冒充新版界面。
- [ ] 公共 Release 附上 [更新指南](UPDATING.md)，明确升级前备份 `settings.json`、`data`、`reports`、`downloads`。

## v1.11.0 专项清单

- [ ] 确认 `paper_radar/__init__.py`、`pyproject.toml`、前端版本、README、CHANGELOG 均为 1.11.0。
- [ ] 全新 Python 环境安装依赖并运行 `pytest`、`compileall` 与 `node --check`。
- [ ] 用 v1.10 数据备份验证默认主题迁移；验证两个主题对同一论文有独立星标与反馈。
- [ ] 在真实 OpenAlex 账户中检查语义额度、权限、失败处理和实际返回论文；默认选项关闭。
- [ ] 真实模型 Key 在用户授权后验证推荐 JSON 与证据质量，不得在日志中显示密钥。
- [ ] 在 Windows 实机测试批量主题任务的计划运行、睡眠恢复和异常情况。
- [ ] UI 桌面与移动端运行；报告中的 AI 理由必须附可核查的题名/摘要原文。
- [ ] 核对源码包没有 `settings.json`、用户数据库、PDF、日志、云端 Token。
- [ ] GitHub 正式 Release 发布后，从旧版客户端验证自动更新提醒只跳转官方发布页。

## v1.12.0 专项发布核验

- [ ] 统一 `paper_radar/__init__.py`、`pyproject.toml`、README、CHANGELOG、源码 ZIP、Release Tag 为 `1.12.0`。
- [ ] 真实 Windows 环境安装 `pypdf>=5,<7`；测试本地2页/多栏/扫描版 PDF 的索引状态与失效反馈。
- [ ] 验证全文的页码、原文引文、模型 JSON 校验；虚构数字、无效片段 ID 和幻觉引文不能进入卡片。
- [ ] 在程序外替换 PDF 后，阅读卡片、全文检索与新导出 Word/Markdown 不再显示旧证据。
- [ ] 模型未配置、API 超时、用户取消同意时，普通检索、下载、报告仍可使用。
- [ ] 用 `python -m paper_radar.evaluation --export-template` 生成模板，填充真实标签后 `--labels` 输出包含标注覆盖率的评价。
- [ ] 对公开/付费资源权限进行检查：仅存储用户本机依法获取的 PDF；不要在 GitHub Release 打包 PDF 和全文 SQLite 数据库。
- [ ] Windows / macOS / Linux 本地界面测试弹窗、翻页、索引进度与页面关闭；移动端验证无横向溢出。
- [ ] 运行 `python -m pytest -q`、`python -m compileall -q main.py paper_radar`、`node --check paper_radar/static/app.js`、源码 ZIP 解压后的回归。
- [ ] 更新图文教程和截图，附新版本完整升级说明；远程创建**正式发布的** GitHub Release 后，再验证旧客户端的版本提醒。
