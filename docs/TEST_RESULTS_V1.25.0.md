# Paper-Radar v1.25.0 | 测试与审阅记录

测试基线：v1.24.0 正式源码 ZIP。运行环境：Linux 容器、Python 3.13、pytest 9、Chromium 离线 DOM 模拟；不包含生产 TLS 或 Windows 实机。

## 自动化与集成检查

- `python -m pytest -q`: **423/423 passed (117.03 s)**. After the final public arXiv subprocess security adjustment, **44/44 targeted compatibility and safety tests passed (8.70 s)**.
- v1.25 专项测试：主题配置、工作区 v8 迁移、跨来源故障隔离、DOI 去重、账号隔离、无付费安全重试、证据 CSV 导出及公式转义、待办与精确论文定位、公开来源密钥隔离。
- `python -m compileall -q paper_radar tools`：通过。
- `node --check paper_radar/static/portal.js`：通过。
- `python -m tools.verify_public_deployment --smoke`：通过；模板检查通过，HTTP `health=200`、注册禁止返回 403，版本为 `1.25.0`。
- `python -m tools.v125_offline_ui_check`：使用模拟 API 的 Chromium 页面检查通过；主题创建/编辑、页面切换、待办状态渲染和移动端无横向溢出，未捕获未处理 JS 异常。

## 对外部来源规则的核验

- OpenAlex 官方资料指出公开基础查询可不带 API Key，API Key 可提升额度，更多使用量可能涉及费用。因此公网工作流默认不向来源子进程继承服务器的 `OPENALEX_API_KEY`，如运营方确实需要共享密钥必须显式设置 `PAPERRADAR_PUBLIC_ALLOW_OPENALEX_API_KEY=1` 并自行监控额度。参见 https://help.openalex.org/api/authentication/ 和 https://help.openalex.org/access/pricing/ 。
- Crossref 官方文档说明公开查询无需注册，但有请求速率与并发限制；建议使用有效的 `mailto` 联系方式并根据服务返回的 429/响应头调整请求。当前公网单来源采用串行、3 秒间隔的保守节流，没有在本地测试中验证实时额度。参见 https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/ 。

## 验收边界

- 本轮无法使用当前环境对本地 HTTP 地址执行完整浏览器端到端访问，尝试 Chromium 实际导航时返回 `ERR_BLOCKED_BY_ADMINISTRATOR`；离线注入 DOM 检查仅覆盖页面逻辑和布局。
- 未测试真实 arXiv、OpenAlex、Crossref 访问、目标站点 API 变化、Nginx HTTPS 证书、生产 systemd、多用户压力、Windows 启动/自动更新和生产私钥签名。
- 真实 PDF 实验数值抽取 F1、复杂表格归属正确率和跨论文学术比较准确率仍需有许可的真实论文及专家双人标注。已通过的结构化规则与模拟论文测试不得替代这些指标。

## 审查结论

v1.25.0 应作为**受控部署候选版本**，完成目标服务环境验收后再进行用户推广。升级前必须完整备份，工作区数据库 v7→v8；回滚须同时恢复旧数据库和 PDF 快照。
