# GitHub 不能识别桌面构建工作流：安装和检查

Paper-Radar 使用两个工作流：

- `.github/workflows/ci.yml`：常规测试。
- `.github/workflows/desktop-release.yml`：`main` 分支新版本推送后，执行测试、Windows/Ubuntu/macOS 分别构建，再自动创建 GitHub Prerelease。

**关键：GitHub 只能识别仓库根目录下 `.github/workflows/*.yml` 文件。** `.github` 以句点开头，部分文件管理器默认隐藏。即使源码 ZIP 中包含工作流，只要上传时遗漏隐藏目录，GitHub Actions 就不会显示。

## 选择正确的压缩包

- `Paper-Radar_v1.28.0_Source.zip` 是兼容 Paper-Radar **源码自动更新器**的包。内部有 `Paper-Radar/` 目录。
- `Paper-Radar_v1.28.0_GitHub_Repository_Root.zip` 专供同步已有 GitHub 仓库。打开后**第一层就是 `.github/`、`paper_radar/`、`tools/` 等**，不会再嵌套一层 `Paper-Radar/`。
- `Paper-Radar_v1.28.0_Workflows_Only.zip` 仅包含 `.github/workflows/`，用于在已有 v1.28.0 源码仓库中补齐工作流。

**不要在 GitHub 上传 ZIP 文件本身并期望 GitHub 自动解包，也不要把 `Paper-Radar/` 文件夹作为仓库根目录中的下一级文件夹。**

## 本地同步完整源码（推荐）

在已克隆的仓库目录中操作；先备份或提交未保存的修改。

**Windows PowerShell**（假设压缩包放在下载目录）：

```powershell
cd C:\path\to\Paper-Radar
Expand-Archive -Path "$HOME\Downloads\Paper-Radar_v1.28.0_GitHub_Repository_Root.zip" -DestinationPath . -Force
Get-ChildItem -Force .github\workflows

git add -A
git status --short
git ls-files --cached .github/workflows
git commit -m "Release v1.28.0 with desktop Actions"
git push origin main
```

**Ubuntu / macOS**：

```bash
cd /path/to/Paper-Radar
unzip -o ~/Downloads/Paper-Radar_v1.28.0_GitHub_Repository_Root.zip -d .
ls -la .github/workflows

git add -A
git status --short
git ls-files --cached .github/workflows
git commit -m "Release v1.28.0 with desktop Actions"
git push origin main
```

以上命令中的路径和版本号按实际文件调整。保持现有 `.git` 目录，不要覆盖本机 `data/`、`.env`、`settings.json` 等私有配置；发行包本身已排除这些文件。

## 只补齐工作流

已经上传了完整源码但 GitHub 没有 Actions 工作流时，解压 `Workflows_Only.zip` 到**仓库根目录**，然后执行：

```bash
git add .github/workflows/ci.yml .github/workflows/desktop-release.yml
git commit -m "Restore GitHub Actions workflows"
git push origin main
```

请确认 GitHub 网页端仓库 Code 栏目下能够看到 `.github/workflows/desktop-release.yml`。随后打开 `https://github.com/QiQiWan/Paper-Radar/actions`，若仓库的 Actions 权限已开启，就能看到工作流。首次引入工作流会依照当前 main 分支源码版本尝试自动构建；为了发布 v1.28.0，应确保其完整代码已经同时同步。

## 更新前本地验证

```bash
python tools/packaging/check_github_workflows.py
python tools/packaging/check_github_workflows.py --archive Paper-Radar_v1.28.0_GitHub_Repository_Root.zip --layout git-root
```

GitHub 仓库还需要在 **Settings → Actions → General** 允许 Actions，并赋予最后一个发布任务所需的仓库写入权限（工作流的 `publish` job 自带 `contents: write`）。如果仍未显示，先用 GitHub 网页直接打开 `.github/workflows/desktop-release.yml`，检查文件名、位置、是否在默认分支及 Actions 设置。

**发布安全说明**：当前流程自动发布的是未签名的 Prerelease；Windows 与 macOS 原生应用尚需签名、公证和真机验收。自动发布是否成功须以 GitHub Actions 实际运行结果为准。
