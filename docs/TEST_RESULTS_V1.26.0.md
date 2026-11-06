# Paper-Radar v1.26.0 测试与验收边界

## 核心结论

**离线源码回归及当前 Linux 环境的图形管理器/HTTP 运行验证已执行。** 由于当前环境缺少 PyInstaller 及 Windows/macOS 原生系统，所有原生可执行安装包的实际构建、签名和真机运行仍待完成。

| 测试 | 结果 | 证据或限制 |
|---|---|---|
| 全量 pytest 回归 | 452/452 passed (116.59 s) | Full source regression after frozen-worker compatibility and native runner updates |
| v1.26 新增专项 | 29/29 passed | Includes source cache, cross-process supervisor, live HTTP safe stop, private manager token and frozen worker subcommand |
| Linux 源码模式桌面管理器 | Passed (source mode) | Real localhost HTTP launch, second-manager reattach and authorized stop; Tk window rendered with Xvfb |
| 页面交互 | 通过，离线模拟 API | Chromium 测试创建和编辑主题、切换界面、手机视口无横向溢出、未捕获 JS 控制台异常 |
| 静态检查 | 通过 | Python `compileall`、Node.js `--check`、GitHub Actions YAML 解析 |
| 真实论文 PDF 评估 | 未测量 | 当前无 3 篇固定版本原始 PDF；没有模型预测 JSON。 |
| 三平台原生包 | 未构建、未真机验收 | PyInstaller 未安装；没有 Windows/macOS/Ubuntu 原生发行包及签名文件 |

## 桌面管理相关安全性

本地服务器只监听 `127.0.0.1`，工作台管理接口使用独立的当前用户私有密钥认证；健康检查仅输出密钥指纹。对未授权停止请求返回 HTTP 403，同一用户可再次打开管理窗口并通过授权接口停止空闲服务。执行中的科研任务拒绝关闭；未经本机认证的公网调用无法直接停止服务。该机制尚未经历 Windows ACL 与 macOS 权限实机验证。

## 科研内容准确性

测试使用真实论文身份及人工整理的 6 条种子证据，却未在当前容器中重新读取三篇 PDF 原文，因此有关页码、表格与数值的“真实 PDF 自动抽取指标”均为**无数据**。已有 5 条不允许进行定量比较的配对，确认规则当前未放行这些例子；可比正例为 0，真实可比召回率无统计依据。

## 构建验收命令

```bash
python -m pytest -q
python -m pytest -q tests/test_v126_desktop_and_sources.py
python -m compileall -q paper_radar tools
node --check paper_radar/static/app.js
node --check paper_radar/static/portal.js
python -m paper_radar.desktop_app --doctor-output ./doctor.json
# 在各自原生操作系统上执行；需先安装 PyInstaller
python tools/packaging/build_desktop.py
```

CI 为每个 OS 产物预留相同的 doctor 自检，但完整真实功能、签名/公证及系统安装后回滚仍需发布者操作。
