# Paper-Radar v1.18.0 验收记录

日期：2026-11-04。测试基于本地 Linux/Python，公网服务使用同进程 HTTP 服务器与模拟学术文献源、合成 PDF；不等同于生产服务器渗透测试。

## 一、自动化回归

- 全量命令：`python -m pytest -q`
- 结果：**293 passed**（保留原 v1.17.0 的 282 项测试，加上新增 11 项专项测试）。
- 版本检查：`paper_radar.__version__` 与 `pyproject.toml` 均为 `1.18.0`。
- 语法：`python -m compileall -q paper_radar tools main.py`、`node --check paper_radar/static/portal.js`、`node --check paper_radar/static/app.js` 均通过。
- Agent 自检：`python -m paper_radar.agents doctor` 返回 `state=ok`，注册 16 个 Agent。

## 二、重点测试

- 两个独立账号完整科研任务运行：元数据→PDF→逐页解析→AI 原文实验记录→比较报告；另一个账号不能读取。
- 模型编造数字/引文被拒绝；PDF 内容变更后旧证据不再出现在新报告。
- 幂等分析和模型结果缓存；账号数据库与工作区迁移；独立队列类型及任务历史。
- 旧版 CSV 导入继续显示 `user_import_unverified`，不会被误认为 PDF 直接核验。
- 单目录只启动一套公网服务，Origin/CSRF 及相关访问控制由旧版和新版集成测试覆盖。
- 独立新工具：安全 HTTP 检查与 SQLite 在线备份/文件哈希验收。

## 三、浏览器审查

- 使用真实前端 HTML/CSS/JS 和模拟数据接口运行 Chromium，无 JS 运行异常。
- 桌面宽度 1440px、移动端宽度 390px，页面无横向溢出；注册登录及研究工作流入口、上传与全文操作按钮均显示。
- 浏览器通过模拟接口验证界面，不能替代真实 Nginx 公开域名端到端验收。

## 四、明确未完成的真实环境验收

1. 真实互联网 arXiv 下载及所有 API 速率、响应、授权、费用检查。
2. 非合成真实科研 PDF 的解析质量，复杂表格、公式、扫描文本及恶意文档压力测试。
3. 生产 HTTPS/Nginx/HSTS、WAF、验证码、邮件确认和流量保护。
4. 正式生产签名发布以及 Windows 启动更新和跨版本回滚实机演练。
5. 真实数据库异地备份恢复、断电恢复、并发性能、长时多账号稳定运行。

结论：**完成了当前运行环境内的功能和基础安全回归。公网生产部署仍需完成上述现场验收后逐步开放。**

## 五、独立源码包复测与打包问题修复

最终源码 ZIP 曾在首次独立解压时发现排除规则过宽，误排除了 Python 源码包 `paper_radar/downloads/` 和 `paper_radar/reports/`。已修复仅排除项目根级运行数据目录的规则，并重新生成 ZIP。

修订后的独立解压源码通过两组完整互斥测试：**51 项公网/多用户核心测试 + 242 项其余回归测试 = 293 项全部通过**。源码包与交付包均通过 ZIP CRC 完整性检查，并扫描排除账号数据库、个人文献、密钥和运行日志。

同时以实际前端 HTML/CSS/JS 搭配模拟业务 API 在 Chromium 验证登录、研究工作台与论文操作按钮：桌面 1440px、手机 390px 均无横向溢出和 JavaScript 运行异常。此项仍需真实公网端到端验收。
