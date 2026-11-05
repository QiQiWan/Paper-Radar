# v1.16.0 数据库迁移、备份与恢复

## 数据分类及版本

`paper_radar/migrations.py` 为数据库维护一张 `schema_migrations`，字段包括数据库种类 `kind`、连续整数 `version`、迁移名称、SQL SHA-256 校验和时间。三类数据库分开管理：

| Kind | 数据库 | 已有迁移版本 | 说明 |
| --- | --- | ---: | --- |
| `research` | 本地桌面 `data/papers.sqlite3` | 2 | 对已有旧表保持兼容，记录主题索引以及跨论文综合分析运行历史 |
| `portal_auth` | 公网 `storage/portal/accounts.sqlite3` | 2 | 账号、scrypt 密码摘要、会话、限流记录及用户名大小写唯一性 |
| `portal_workspace` | 公网 `storage/portal/users/<随机账号ID>/research.sqlite3` | 1 | 每账号研究主题、论文和导入证据 |

迁移在 SQLite `BEGIN IMMEDIATE` 单事务中执行，步骤失败回滚；先检查历史版本连续性、checksum 和新版兼容性。旧研究库进入原有初始化逻辑前先检查是否存在高于本程序支持的版本，防止静默降级污染资料。

## 操作与验收

先停止服务，确保数据库已关闭或能进行 SQLite 在线备份。执行：

```bash
python tools/migration_cli.py --help
python tools/migration_cli.py --database data/papers.sqlite3 --kind research status
python tools/migration_cli.py --database data/papers.sqlite3 --kind research backup
python tools/migration_cli.py --database data/papers.sqlite3 --kind research apply
```

公网账号库可以将 `--database` 改为 `storage/portal/accounts.sqlite3 --kind portal_auth`。每个工作区需指定其独立路径和 `--kind portal_workspace`。**切勿用不同 kind 的迁移覆盖同一文件**。备份不能只复制运行中 SQLite 主文件，需使用 SQLite Backup API 或先安全停止服务；同时保存 `-wal/-shm` 场景需要注意事务一致性。

手动恢复时先停止全部进程，保留故障后的数据库只读快照，再用经过校验的备份替换，并检查登录及主题论文关联。系统不自动合并恢复后产生的新数据。数据库迁移与代码版本变更是不同维度；仅凭源码版本号无法判断能否安全回滚数据结构。

## 后续增强

建议加入迁移前数据库完整性检查、针对真实用户规模的迁移时延测试、数据库加密/备份策略与升级前后的业务抽样对账。生产部署必须先在备份副本试运行迁移。
