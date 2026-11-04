# Paper-Radar v1.10.0 发布检查清单

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
- [ ] GitHub Release 标签为 v1.10.0，附件可下载、可解压。


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
