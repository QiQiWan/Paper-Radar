# 上传 Paper-Radar 到 GitHub

**目标仓库：** https://github.com/QiQiWan/Paper-Radar

1. 下载并解压本版本 GitHub Source ZIP，确认根目录是 `Paper-Radar/`，根目录下应有 `README.md`、`LICENSE`、`.gitignore`、`main.py`、`paper_radar/` 和 `tests/`。
2. 检查 `.gitignore`，并执行 `git status`，确认没有 `settings.json`、`.env`、`data/`、`downloads/`、`reports/`、`logs/`、个人 Zotero 数据库或 PDF。
3. 在仓库根目录执行：

```bash
git init
git branch -M main
git remote add origin https://github.com/QiQiWan/Paper-Radar.git
git add .
git status
git commit -m "Release Paper-Radar v1.12.0"
git push -u origin main
```

如果已经设置过远程，使用 `git remote set-url origin https://github.com/QiQiWan/Paper-Radar.git`。如果远程已经有提交，先拉取并协调差异，避免强推覆盖。

4. 在 **Releases → Draft a new release** 创建标签 `v1.12.0`，上传本地源码 ZIP 与需要提供给用户的附件。公众号下载链接应指向最终 Release 地址，如 `https://github.com/QiQiWan/Paper-Radar/releases/tag/v1.12.0`；发布时点击 **Publish release**，确保是公开的正式 Release（无 Draft/Prerelease 标记）；确认创建后再用于公开文章。**只有 Git Tag 或未发布的草稿不会触发软件内更新提醒**。
5. 在正式发布前确认 MIT License 授权资格和第三方代码来源；外部出版社 API、真实 Zotero 及 Windows 计划任务仍需实机验证。

目前本交付仅生成本地发布包；**没有自动推送 GitHub 或创建 Release**。


## 更新提醒与发行版本一致性（v1.10.0）

发布者每次提升版本时，应同步 `paper_radar/__init__.py`、根目录 `CHANGELOG.md`、`README.md`、使用手册和源码 ZIP 文件名。创建公开正式 Release 后访问 `https://api.github.com/repos/QiQiWan/Paper-Radar/releases/latest`，核验 `tag_name`、`published_at` 与实际版本；同时用安装了旧版的软件检查通知及官方跳转。官方 API 的最新正式版可能仍是旧版本，只有成功**发布**新 Release 才会出现在 API 中。完整发布清单见 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)。

## v1.12.0 发布新增确认

- `requirements.txt` 和 `pyproject.toml` 新增 `pypdf>=5,<7`，并在 `NOTICE.md` 声明第三方许可。
- 发版前用真实可公开使用的 PDF 检查按页索引，但 Release 源码 ZIP **不得包含真实论文 PDF 或自动生成的全文数据库**。
- Release 说明应明确区分“本地 PDF 索引”（无模型费用）和“AI 阅读卡片”（用户确认后可能有模型费用）。
- 更新公众号安装指南与全文证据入口截图，截图中的模拟论文必须注明示例。
- 如果实际 GitHub Release 尚未发布，用户客户端不会提示已经存在 v1.12.0 远程更新。
