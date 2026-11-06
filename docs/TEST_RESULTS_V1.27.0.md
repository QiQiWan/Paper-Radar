# Paper-Radar v1.27.0 验收记录

日期：2026-11-06。基线：v1.26.0 GitHub Auto Release Ready。

## 自动化回归

- `python -m pytest -q --disable-warnings`：**469 passed in 120.81s**，无失败。
- `python -m pytest -q tests/test_v127_ui_redesign.py`：**9 passed**。
- `python -m pytest -q tests/test_v127_ui_redesign.py tests/test_v126_auto_release.py`：**17 passed**。
- `python -m compileall -q paper_radar tools tests`、`node --check paper_radar/static/app.js`、`node --check paper_radar/static/portal.js`：通过。
- `python tools/packaging/release_version.py`：报告 `Paper-Radar v1.27.0`。

## 界面检查

- `python -m tools.v127_ui_acceptance --out .../previews`：本地和公网交互测试通过，未捕获 JavaScript page errors，390px 页面未出现整页横向溢出。公网使用本地模拟 API。
- `python -m tools.v126_offline_ui_check`：创建/修改研究主题、来源选择、导航和移动端布局通过。
- 桌面管理器 Linux/Xvfb 下对模拟控制器进行实际 Tk 渲染检查，简洁与展开状态均保存截图。
- 与 v1.26.0 在 1440px 宽度下做静态初始状态测量：本地首页高度 1264→1077px；设置 3091→658px；论文库 592→457px。仅反映首屏布局紧凑程度。

## 发行包独立检查

- 通过 `tools/package_release.py` 生成以 `Paper-Radar/` 为根目录的可安全解压源码包。
- 从 ZIP 独立解压后执行 17 项界面/发布测试、版本读取、前端语法、Chromium 离线交互：通过。
- 从解压后的源码启动公网 HTTP 服务，以 Python `urllib` 请求 `/`、`/portal.css`、`/portal.js`、`/api/public/health` 均得到 200；健康接口版本为 `1.27.0`。
- 使用真实 HTTP 地址执行旧版 Chromium 端到端测试时，沙盒浏览器返回 `net::ERR_BLOCKED_BY_ADMINISTRATOR`，本环境无法完成此项。模拟 API 交互测试与真实 HTTP 页面冒烟测试已分别执行，但不能代替真实浏览器端到端验收。

## 环境边界

尚未在 Windows、Ubuntu 原生安装包及 macOS 原生 APP 上进行本轮 UI 验收，尚未完成生产 HTTPS 与真实来源 API 的用户旅程检查。当前发布的 ZIP 是源码，GitHub Actions 仍需推送后在线构建各平台安装包。
