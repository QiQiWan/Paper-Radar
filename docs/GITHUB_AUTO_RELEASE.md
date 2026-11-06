# Paper-Radar GitHub Actions 自动构建与 Release 发布指南

本仓库支持 **main 分支推送新版本后自动构建 4 组原生桌面包，并自动上传到 GitHub Releases**。版本发布不再需要手工进入 Actions 下载并逐个上传安装包。

## 1. 发布条件

1. 使用 `python tools/packaging/bump_version.py 1.27.0` 一次性更新程序、Agent、桌面标识和项目版本；也可以手动同步修改这些版本标记。
2. 代码与 `.github/workflows/desktop-release.yml` 都在 `main` 分支。
3. 将代码推送到 `main`（或在 `main` 上手动运行工作流）。Workflow 检查版本一致性；如果对应 `vX.Y.Z` Release 已存在，则直接跳过打包/发布，不覆盖旧产物。如果标签存在但没有 Release，则失败并要求人工确认。
4. 通过完整 Python 回归、Agent doctor、前端 JS 语法检查、四系统原生构建和打包后的 `--doctor-output` 启动检查后，才进入 Release 阶段。某一平台失败，整个 Release 不会发布。
5. Release 阶段独立核对 **6 个文件 + 6 份 SHA-256**，先创建 Draft、上传全部产物，再从 Draft 发布成可见的 **Prerelease**，Tag 自动创建并绑定本次推送的 commit SHA。

**注意：无需单独手工打标签。** 修改源码的版本号、提交并推送后，将自动生成 `v1.27.0` 这样的 Tag。连续向 `main` 推送而不升级版本号，只会执行常规 `ci.yml` 测试，不会重复创建 Release。

## 2. 你需要在 GitHub 设置中确认的选项

- `Settings → Actions → General`：允许 Actions 运行，并确保仓库或组织策略允许工作流使用 `contents: write` 发布 Release。工作流内只给最后的 `publish` job 写权限。
- 建议为 `main` 设置分支保护：PR 审核、CI 成功才能合并，避免未经测试的代码自动公开发布。
- 无需新增 Personal Access Token。使用 GitHub Actions 自动提供的 `GITHUB_TOKEN`。
- 如果希望保护公开发布的安全性，后续需为 Windows 配置 Authenticode 签名证书、为 macOS 配置 Developer ID 证书与 Apple 公证凭据；目前源代码**不含签名/公证任务**。

## 3. 自动发布的文件

| 文件名 | 用途 |
| --- | --- |
| `Paper-Radar_Windows_x64.zip` | Windows：解压整个目录后双击 `Paper-Radar.exe` |
| `Paper-Radar_Ubuntu_portable_amd64.zip` | Ubuntu：便携式目录（保留执行权限，启动 `Paper-Radar`） |
| `Paper-Radar_X.Y.Z_amd64.deb` | Ubuntu：可在系统软件安装器中安装 |
| `Paper-Radar_macOS_arm64.zip` | macOS Apple Silicon：`Paper-Radar.app` |
| `Paper-Radar_macOS_intel.zip` | macOS Intel：`Paper-Radar.app` |
| `Paper-Radar_vX.Y.Z_Source.zip` | 可追溯源码/旧源码自动更新格式 |

每份安装包均有独立 `*.sha256` 附件，总共 **12 个 Release 附件**。源码自动更新器默认只选择符合安全校验规则的正式非预发布版本；这一阶段的自动 Prerelease 不会无意触发已安装客户端的自动更新。

## 4. 为什么默认自动发布为 Prerelease

目前尚无 Windows/macOS 代码签名和 Apple 公证。Unsigned Windows 可执行程序可能触发 SmartScreen，Unsigned macOS `.app` 可能被 Gatekeeper 阻止。针对普通用户发布正式版前应完成签名、公证、三平台实机安装/启动/升级/卸载和数据保留测试。**不要通过教用户关闭系统安全保护来替代签名。**

GitHub Actions 的 `--doctor-output` 是冻结后可执行程序的启动自检；它不等同于实际 GUI 双击测试。发布人员仍需要在真实机器上审阅下载产物。确认签名与全面验收闭环后，可再为工作流加入正式版自动发布通道。

## 5. 一次更新的例子

本地开发机器完成 v1.27.0 改动后：

```bash
# 自动更新五处当前版本号，然后运行测试
python tools/packaging/bump_version.py 1.27.0
python -m pytest -q
# 提交并推送到 main
git add .
git commit -m "Release v1.27.0"
git push origin main
```

随后打开 `https://github.com/QiQiWan/Paper-Radar/actions/workflows/desktop-release.yml` 查看 `prepare → test → build/source → publish` 的执行结果。全部通过后，文件会出现在 `https://github.com/QiQiWan/Paper-Radar/releases`。失败请打开对应 job 日志，不会自动发布不完整的安装包。

## 6. 当前仓库同步情况

自动发布工作流建立在完整 v1.26.0 源码包上。若远端 `main` 仍是 v1.21.0，必须先把完整的新源码（包括 `tools/packaging`、`tests`、`requirements.txt`、`paper_radar`、`.github`）同步到远端。仅上传 YAML 文件到旧版仓库会因缺少打包器而失败。
