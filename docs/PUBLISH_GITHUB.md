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
git commit -m "Release Paper-Radar v1.8.0"
git push -u origin main
```

如果已经设置过远程，使用 `git remote set-url origin https://github.com/QiQiWan/Paper-Radar.git`。如果远程已经有提交，先拉取并协调差异，避免强推覆盖。

4. 在 **Releases → Draft a new release** 创建标签 `v1.8.0`，上传本地源码 ZIP 与需要提供给用户的附件。公众号下载链接应指向最终 Release 地址，如 `https://github.com/QiQiWan/Paper-Radar/releases/tag/v1.8.0`；确认创建后再用于公开文章。
5. 在正式发布前确认 MIT License 授权资格和第三方代码来源；外部出版社 API、真实 Zotero 及 Windows 计划任务仍需实机验证。

目前本交付仅生成本地发布包；**没有自动推送 GitHub 或创建 Release**。
