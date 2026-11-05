## v1.17.0 新增交付验收

- [ ] 254项旧测试 + 新增专项测试在独立解压源码目录通过，Python/JavaScript/Agent 自检无异常。
- [ ] v1.16账号数据库备份后迁移 `portal_auth` v3；两个账号的历史数据、Session 和工作区不丢失。
- [ ] 测试任务提交后立即可查、刷新页面不丢失，服务中断时只做有界恢复，失败与取消有记录。
- [ ] 测试原子配额：同账号并发提交只有一个活动任务，拒绝提交不扣24小时额度。
- [ ] 检查管理员账号停用时尚未完成任务被请求取消，另一账号不能读取或操作该任务。
- [ ] 测试论文分页与科研比较诊断，未核验导入证据不得显示为已验证结果。
- [ ] 可信 Nginx 覆盖 `X-Real-IP` 并经 HTTPS 代理；不得开放桌面本地服务或任意密钥接口。
- [ ] 确认生产签名密钥仍离线保管，Release 的 ZIP/manifest/signature 由**可信签名私钥**签署后发布。
- [ ] 真实 Windows 自更新、真实公网环境高并发和备份恢复演练未完成时明确标注限制。

---

## v1.16.0 发布前必检

- [ ] `python -m pytest -q`、`python -m paper_radar.agents doctor` 和前端语法检查通过。
- [ ] 对正式密钥进行离线保管，客户端预置的公钥 ID、Base64 与发布者真实私钥对应。
- [ ] ZIP、JSON manifest、SIG 三件套版本、文件名、sha256/size 相同，并已本地验证签名。
- [ ] 使用 `tools/migration_cli.py` 对真实数据库备份并检查状态；升级使用测试副本，验证新版本拒绝旧客户端降级。
- [ ] 在真实 Windows 上运行 `tools/windows_update_acceptance.ps1`，有条件时用测试 Release 验证更新、异常回滚、密钥验证与桌面启动。
- [ ] 公网入口仅绑定 127.0.0.1，Nginx HTTPS/Host/Origin 配置正确；用两个账号测试数据隔离、禁用、CSRF 与页面性能。
- [ ] 服务器账号数据库、日志、运行数据和私钥均未进入源码 ZIP；公网服务维护期间禁止热更新。
- [ ] README、CHANGELOG、Release Notes、账户服务部署文档和安全风险提示已同步。

---

# Paper-Radar 发布检查清单

## v1.15.0 启动更新专用检查

- [ ] 版本在 `__init__.py`、`pyproject.toml`、UI、文档、ZIP 和 Release Tag 中一致。
- [ ] 安装包包含 `startup.py`、`paper_radar/startup_update.py`、两个稳定启动脚本。
- [ ] Release 正式公开，附件名精确为 `Paper-Radar_vX.Y.Z_Source.zip`，GitHub API `assets[].digest` 为 `sha256:`。
- [ ] 断网、404、无摘要、错摘要、过大 ZIP、路径穿越、符号链接均不替换现有程序。
- [ ] 替换程序前设置与 SQLite 数据库备份成功；中断安装和新版启动失败都能回滚。
- [ ] ZIP 不包含 `.paper_radar_updates/`、`data/`、`downloads/`、`reports/`、`settings.json`、密钥、PDF 和日志。
- [ ] 浏览器已经运行时不会热更新；手动 `--no-update` 可以离线运行。
- [ ] Windows/macOS/Linux 首次安装、依赖安装及浏览器启动分别实机验收。


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
- [ ] GitHub Release 标签与当前发行版本一致，附件可下载、可解压。


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


## v1.13.0 Agent 专项检查

- [ ] `python -m paper_radar.agents list` 显示九个 Agent 及四类按需能力。
- [ ] `python -m paper_radar.agents graph` 依赖图无循环且输出所有权无冲突。
- [ ] `tests/test_v113_agents.py` 验证契约、非关键失败降级、关键错误停止与运行轨迹隐私。
- [ ] 设置页“Agent 工作流”在桌面/移动端显示正常，真实 Windows 计划任务仍可运行。
- [ ] `data/agent_traces.sqlite3` 和其他用户数据不会进入源码 Release 包。
- [ ] `paper_radar/__init__.py`、`pyproject.toml`、UI、README 和 CHANGELOG 均为 1.13.0。

## v1.13.1 额外发布检查

- [ ] `python -m paper_radar.agents doctor` 返回 `state=ok`。
- [ ] `python -m pytest tests/test_v131_agent_runtime.py -q` 全部通过。
- [ ] 设置页两个模型费用单价保存/读取正确；未设价格时不显示费用 0 美元。
- [ ] 手动版本号、`pyproject.toml`、页面版本、更新日志一致。
- [ ] 确认没有把个人数据库、下载论文和系统凭据打包进入源码 ZIP。


## v1.14.0 科研实验 Agent 发布检查

- [x] `paper_radar.__version__`、`pyproject.toml`、工作台版本、README、CHANGELOG 均为 `1.14.0`。
- [x] 四阶段独立科研分析工作流注册、依赖校验和 `research-graph` 命令通过回归。
- [x] 对无依据数字、错误引文、错误片段、数据集、基线与实验条件作拒绝测试；模型格式异常不得覆盖原有效记录。
- [x] 模型调用中 PDF 改动会拒绝保存；新导出的当前主题 CSV 不包含失效 PDF 证据。
- [x] 各主题 CSV 仅包含本主题已关联论文；导出单元格防止 CSV 公式注入。
- [x] 默认检索与全文证据旧接口通过回归测试；实验分析由用户主动确认触发。
- [x] Python、JavaScript 检查、模拟前端浏览器检查和全套测试。
- [ ] 使用真实 PDF（多栏、复杂表格、扫描版）开展有人工标签的证据召回评估。
- [ ] Windows 上接通真实云端模型、Zotero 和定时任务，并核实服务商费用与超时行为。
- [ ] 在 GitHub 发布正式 `v1.14.0` Release，附件实际可下载，并从旧版客户端验证提醒。
- [ ] 公众号教程新图片应从真实用户环境截图，明确演示数据边界。
