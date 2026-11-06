# Paper-Radar · GitHub 多平台自动发布实施与复审

## 结论

可在 `main` 推送新的版本号后，自动完成四平台构建、完整回归、产物校验与 GitHub Release 的创建。本补丁基于 v1.26.0 源码，不改变项目版本号、用户数据库或研究功能。当前仅完成了 **工作流静态检查与本地离线单元测试**；尚未推送到 GitHub，也未进行 GitHub-hosted Runner 四平台实际打包测试，更未生成签名/公证安装包。

## 原工作流存在的缺口

1. `desktop-release.yml` 仅将结果保存为 Actions Artifacts，未下载、校验、汇总上传到 Release。
2. ARM 和 Intel 的 macOS ZIP 使用相同名称 `Paper-Radar_macOS.zip`，合并文件时存在重名覆盖风险。
3. 原工作流针对 `v1.26.*` 标签，需先手工创建标签，未满足仅推送代码就发布的要求。
4. 版本号在历史测试和应用界面中存在硬编码，升级版本会引发与实际功能无关的 CI 失败。
5. 缺少统一检查 6 个发行资产及哈希的发布门禁。

## 已实现的调整

- 新的 `prepare` job 从 `paper_radar/__init__.py` 读取版本并验证 `pyproject.toml`、Agent catalog、网页版本一致；已有发布版本跳过，新标签残留 Draft 或孤立标签则明确报错。
- `test` job 完整运行 `pytest`、Agent doctor、Python 编译与两份 JS 语法检查。
- `build` 矩阵原生构建 Windows x64、Ubuntu amd64、macOS ARM64、macOS Intel，运行可执行文件 `--doctor-output`，并生成唯一文件名和 SHA-256。
- `source` job 创建具有原有自动更新兼容命名的源码包及校验文件。
- `publish` job 仅在所有依赖成功后启用 `contents: write`，检查 6 个软件包/源码包和 6 个 SHA-256，先创建 Draft 并附加全部文件，再发布为可见 Prerelease。失败时不会推送不完整的公开版本。
- 新增 `python tools/packaging/bump_version.py 1.27.0`，批量更新 5 处版本标记，并将旧测试中与固定版本号耦合的断言改为一致性检查。

## 安全与后续验收

默认发布为 **Prerelease**：Windows 和 macOS 安装包尚未完成发布者代码签名；macOS 尚未公证，普通用户可能被系统拦截。当前不会触发针对稳定版本的自动升级。发布正式 Release 还需提供签名凭据、原生安装/升级/卸载测试、用户数据留存验收。

GitHub 仓库当前 `main` 分支上是 v1.21.0，须先推送完整 v1.26.0 代码与本工作流。仅将 YAML 文件复制到旧仓库会因缺少打包器而失败。推送后可在 Actions 页面查看 `Desktop build and automatic GitHub Release`，在 Releases 页面查看对应 `v1.26.0` 预发布。

## 本地验证范围

新增针对性测试涵盖版本号一致性、版本升级脚本、六种资产与哈希检查、内容被篡改、文件重命名、路径穿越及工作流结构。源码包生成后需要再次执行安全解压与 `testzip()` 校验。原生 CI 环境实际构建和本地签名验证不在本轮执行范围内。
