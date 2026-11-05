# Paper-Radar v1.16.0 发布说明

本次主要包含独立 Ed25519 发布签名、SQLite 数据库版本管理、跨论文研究对比 SynthesisAgent，以及账号注册和受控公网部署入口。

桌面用户可在“研究报告”生成带原始页码与实验条件的多论文比较，系统会将不满足直接比较条件的结果列为人工核验事项。全新公网门户允许用户自主注册、创建主题、检索少量 arXiv 公开论文、上传桌面版实验证据索引 CSV，并生成个人研究比较报告。公网与桌面数据库分开管理，公网 API 不暴露原有 Zotero/本机文件和系统更新功能。

升级需要安装 `requirements.txt` 中的 `cryptography` 包。v1.15.0 首次升级 v1.16.0 需要手动保留个人数据完成安装；v1.16.0 后的自动更新将要求签名三件套。部署公网实例前必须在独立账号/数据目录配置 HTTPS 反向代理，端口由启动脚本指定。

本版自动化测试与模拟网关通过，真实 Windows 更新、真实 Release 和线上 HTTPS 高并发尚待目标环境验收。完整升级与验收方案见 `docs/DESIGN_V1.16.0.md`、`docs/PUBLIC_DEPLOYMENT_V1.16.0.md`、`docs/WINDOWS_UPDATE_ACCEPTANCE_V1.16.0.md`。
