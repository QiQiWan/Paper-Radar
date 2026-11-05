# v1.16.0 起：发布者独立签名的三件套发布流程

正式更新必须在 `https://github.com/QiQiWan/Paper-Radar` 发布公开、非预发布 Release，Tag 形如 `v1.16.0`。每个 Release 同时上传：

1. `Paper-Radar_v1.16.0_Source.zip`：固定根目录 `Paper-Radar/` 的发布源码 ZIP；
2. `Paper-Radar_v1.16.0_release.json`：含仓库、版本、文件名、SHA-256、归档大小和启动协议的签名清单；
3. `Paper-Radar_v1.16.0_release.sig`：对上述清单原始字节计算的 Ed25519 独立签名。

实际发布时，将文件名中的版本号替换为新版本。源码中 `paper_radar/release_public_keys.json` 为客户端固定公钥信任库，签名私钥只能放在发布者离线受控位置，**不得上传仓库、Release 附件、公网服务器或交付 ZIP**。

准备工作（建议在发布者自己的离线电脑生成正式密钥）：

```bash
python tools/sign_release.py keygen --output /secure/offline/paperradar_ed25519.pem
```

命令输出 public key ID 与 Base64 公钥。将此公钥填写到**首发时**客户端 `paper_radar/release_public_keys.json` 的 `keys` 列表中（先完成源码编译测试，之后再生成压缩包）。该步骤建立信任根；后续更换私钥应通过已经受信任的版本完成公钥轮换，不能只在 GitHub 替换公钥。

生成签名资产：

```bash
python tools/package_release.py --out dist/Paper-Radar_v1.16.0_Source.zip
python tools/sign_release.py sign --source dist/Paper-Radar_v1.16.0_Source.zip --version 1.16.0 --key /secure/offline/paperradar_ed25519.pem --output dist
```

用 `paper_radar.release_signatures.verify_manifest(...)` 离线验证签名与 ZIP 哈希，再上传三个附件。GitHub Release API 的 `digest` 只提供传输一致性信息，独立 Ed25519 验证提供单独的发布者信任边界。

**首次迁移限制：** v1.15.0 启动器没有固定公钥信任，只能手动升级到具备信任根的 v1.16.0，之后同协议的版本才可通过签名自动更新。v1.16.0 的自动安装禁止覆盖正在运行的公网服务，必须停机、备份并在维护窗口升级。

**预先发布的本次示范签名**使用了当前生成的临时开发密钥，仅用于验证流程。正式对公众发布前应当在发布者控制的离线机器生成独立私钥、重新配置信任公钥、重建 ZIP 并重新签名。

---

## 旧版发布说明（历史）

# Paper-Radar 正式发布及自动更新附件说明

## 固定仓库与版本

项目仓库：https://github.com/QiQiWan/Paper-Radar 。升级检查固定使用 GitHub 的公开正式 Release API。发布者需确保 `paper_radar/__init__.py`、`pyproject.toml`、工作台版本、更新日志和 Release Tag（例如 `v1.15.0`）一致。

## 发布步骤

1. 核对代码来源、许可证、版本号；执行 `python -m pytest -q`、`node --check paper_radar/static/app.js`、`python -m paper_radar.agents doctor`。
2. 执行 `python tools/package_release.py --out dist/Paper-Radar_v1.15.0_Source.zip`。此脚本排除 `.paper_radar_updates/`、数据库、PDF、设置、日志、密钥与虚拟环境，生成 ZIP 和本地 `.zip.sha256`。
3. 确认 ZIP 经过独立解压与自动更新归档校验，输出只包含程序与公开文档。
4. 推送经审核的源码至仓库，创建 `v1.15.0` 标签及**已发布的正式 Release**（不选择 Draft 或 Prerelease）。
5. 上传附件 `Paper-Radar_v1.15.0_Source.zip`。**附件名称和 ZIP 内的版本必须精确一致**。不要依赖 GitHub 的 Source code (zip) 自动附件。
6. 使用公开 GitHub Release API 复查 `tag_name`、`assets[].name`、`assets[].size`、`assets[].digest` 是否符合。GitHub 文档提供 `sha256:` 摘要；若缺失，客户端会拒绝自动安装。
7. 使用真实低版本客户端做升级演练：联网自动更新、断网启动、损坏 ZIP 回退、用户资料保留、启动失败回滚。Windows、macOS、Linux 分别验收。

## 上线风险与恢复

启动器只信任上述仓库；HTTPS、GitHub 元数据与 SHA-256 可减少传输损坏和来源混淆。发布者账号若被攻破，仍可能推送恶意代码；正式使用应保护账号、启用双重验证和 Tag/Release 审批制度。企业场景可进一步引入独立签名与更新批准策略。

v1.14.0 及更旧的启动器不能自动安装，用户需手动更新到 v1.15.0。v1.15.0 启动协议为 1，未来发行版应保持兼容，若破坏兼容应人工升级。

## 发布 v1.17.0 的具体附件

先运行 `python tools/package_release.py --out /your/output/Paper-Radar_v1.17.0_Source.zip`，使用已受信任的离线 Ed25519 私钥执行：

```bash
python tools/sign_release.py sign \
  --source /your/output/Paper-Radar_v1.17.0_Source.zip \
  --version 1.17.0 \
  --key /secure/offline/paperradar_ed25519.pem \
  --output /your/output
```

核对 `release_public_keys.json` 内的公钥 ID 与签名私钥派生公钥匹配。上传 `Paper-Radar_v1.17.0_Source.zip`、`Paper-Radar_v1.17.0_release.json` 和 `Paper-Radar_v1.17.0_release.sig` 到对应公开正式 Release。不要将私钥导入公网部署服务器。本次迭代仅生成本地源码，正式签名需要由持有私钥的发布者完成。
