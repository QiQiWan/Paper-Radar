# Paper-Radar v1.12.0 测试记录

版本：1.12.0｜2026-11-04

## 自动化命令

```bash
PYTHONPATH=. python -m pytest -q
python -m compileall -q main.py paper_radar
node --check paper_radar/static/app.js
python -m paper_radar.evaluation --help
```

**完整自动化：155 项通过。** 涵盖 v1.11 历史143项与新增12项，含本地 PDF 索引、真实 pypdf 文本层解析、跨主题搜索、文件指纹缓存及失效、AI 引文校验、模型调用模拟、人工标签离线评价、标注模板导出、真实 HTTP Handler GET/POST 与 CSRF 保护、Word/Markdown 证据渲染及替换文件后失效。

Python 编译与 JavaScript 语法检查通过。HTTP 合约测试使用临时监听的真实 `ThreadingHTTPServer` 和隔离 SQLite，不访问外部服务。假数据 PDF 由 pypdf 写出，测试的是正文文本层和页码，不包含扫描、表格及复杂数学公式。

## 浏览器界面检查

使用本机 `/usr/bin/chromium` 和 Playwright 加载**项目真实 HTML、CSS、JavaScript**，以模拟后端响应运行：论文库进入、全文证据弹窗、证据页码显示、原文检索、关闭弹窗。桌面1400×1000和移动端390×844均通过。验证 `document.documentElement.scrollWidth <= window.innerWidth`，两档没有全局横向溢出；无 JS 页面异常。对应截图存放在交付包 `qa/` 下（模拟论文，不是实际学术成果）。

当前环境阻止浏览器通过普通地址导航访问本地/测试域名，因此上述浏览器检查使用 `page.set_content` 与 API 模拟；**本地 HTTP 接口则由独立的真实 Handler 集成测试验证**。本结果不代表 Windows 上真实服务的端到端 GUI 验收。

## 独立源码打包检查

- 最终 ZIP 使用仓库 `tools/package_release.py`，排除 `settings.json`、密钥、数据库、真实 PDF、`downloads/`、`logs/`、`reports/` 和构建缓存。
- 在干净目录解压再次运行完整测试及 `node --check`，核对版本标签、依赖和 ZIP 完整性。
- 本次功能基于本地与模拟模型测试；没有实际请求或消费云端模型额度，也没有使用出版社付费全文。

## 尚待用户实机验收

1. Windows 双击 `start_windows.bat` 完成安装，真实浏览器访问本机工作台。
2. 真实开放获取 PDF：双栏、表格、扫描版、公式与超过100页的边界行为。
3. 配置真实模型凭据后，确认原文发送授权、费用、引文质量和超时降级。
4. Zotero Local API 兼容性和 Windows 定时任务。
5. GitHub 创建公开正式 Release 后，从旧版客户端检查更新提醒。
