# Paper-Radar v1.16.0 测试与验收记录

**测试日期：** 2026-11-04  
**源码版本：** 1.16.0  
**测试平台：** Linux Python / Chromium（Playwright 离线接口模拟）  
**回归结果：** 252/254 项 Python 测试通过（新增签名、迁移、门户与 UI 隐藏行为测试）。

## 一、可复现命令

```bash
python -m pip install -r requirements.txt
python -m pip install pytest
python -m pytest -q
python -m compileall -q paper_radar startup.py main.py tools
node --check paper_radar/static/app.js
node --check paper_radar/static/portal.js
python -m paper_radar.agents doctor
python tools/package_release.py --out dist/Paper-Radar_v1.16.0_Source.zip
```

## 二、主要通过项目

| 测试模块 | 验证范围 | 结果 |
| --- | --- | --- |
| 旧版兼容 | 原 230 项检索、Zotero、Agent、全文实验分析、报告和旧更新器回归 | 通过 |
| Ed25519 签名 | 原样签名成功；改清单、改签名、改摘要、换 Key、重放版本失败 | 通过 |
| Release 元数据 | 正确匹配 ZIP + JSON + SIG，缺少附件拒绝自动安装 | 通过 |
| SQLite 迁移 | 重复迁移幂等、未来数据库版本拒绝、迁移校验历史拒绝篡改 | 通过 |
| 多账号 | 用户独立主题和论文、CSRF、Host/Origin、修改密码及会话失效 | 通过 |
| 公网 API | 本地机密和文件/系统操作接口未开放，管理员启停账户 | 通过 |
| 实验综合 | 同数据集、同指标、同条件聚合；不符则拒绝比较、数值不自动演算 | 通过 |
| CSV 导入 | 来源标记 `user_import_unverified`、数量与文件大小限制 | 通过 |
| 自动更新防护 | 公网服务运行标记阻止桌面更新器热替换文件 | 通过 |
| 公网端口配置 | Windows/Linux 启动参数，自定义端口的 Nginx 配置生成与非法域名拦截 | 通过 |
| 前端桌面/手机 | 1440px 桌面与 390px 手机的登录/注册/登录工作台；显示隐藏及响应宽度 | 离线模拟通过 |

## 三、独立包验收

最终源码 ZIP 应在一个全新目录解压后再次运行全部测试、编译检查和 Agent 诊断。正式签名 JSON 和 SIG 必须使用**最终 ZIP 字节**生成；任何文件变化都必须重新打包并重新签名。发布包中不得出现个人账号库、PDF、API Key、签名私钥或运行日志。

## 四、明确未完成的真实环境验收

1. **Windows 真机自动更新与回滚：未完成。** 当前没有 Windows 机器。仓库含 `tools/windows_update_acceptance.ps1`，需要在用户真实 Windows 电脑执行并保留结果；有条件时需要完整升级/断网/损坏签名/失败回滚实操。
2. **真实 GitHub Release 在线下载安装：未完成。** 当前未推送仓库或建立正式 Release。以上签名基于本地发布资产完成验证。
3. **真实公网 HTTPS/Nginx/Systemd、域名与高并发压力：未完成。** 目前仅完成本地 HTTP 受控功能与离线浏览器模拟接口的前端测试。
4. **邮件验证、密码找回、第三方机器人对抗、跨进程可靠队列、灾备：未实现。** 对不受控的公众流量应先行补强。
5. **实际跨论文科研有效性：未完成大样本人工评估。** SynthesisAgent 仅以已有证据生成可核查比较候选，尚不具备自动发表结论的可信度。

**结论：** 本版适合个人本地升级、隔离环境部署测试以及受控用户小规模试用；生产公网公开注册与 Windows 自动更新仍有明确的上线验收门槛。