# Paper-Radar v1.8.0 发布检查清单

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
- [ ] GitHub Release 标签为 v1.8.0，附件可下载、可解压。
