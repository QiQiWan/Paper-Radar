# Paper-Radar v1.18.0 发布说明

本版为公网科研工作流实质性升级：多账号主题内完成文献发现、受控 arXiv PDF 下载或上传、独立全文解析、可选经用户明确授权的大模型实验提取、原文引文和 PDF 哈希校验、SynthesisAgent 综合比较。新增持久化多类型任务、子阶段状态、账号隔离、来源限制、服务器安全验收脚本和 SQLite/PDF 一致性备份工具。

旧桌面版功能保持，原16个 Agent 不变。公网 `portal_auth` 数据库迁移至 v4，`portal_workspace` 迁移至 v2。首次运行前应停机备份，保留个人数据目录。

此版本没有经过真实公网生产 TLS/Nginx、Windows 安装回滚、真实云模型费用或高负载验收。完整代码 ZIP 尚需由持有生产签名私钥的发布者签署 GitHub 正式 Release，不能作为旧客户端已验证自动更新的发布包。

完整用法：`docs/PUBLIC_DEPLOYMENT_V1.18.0.md`。安全设计与限制：`docs/DESIGN_V1.18.0.md`。
