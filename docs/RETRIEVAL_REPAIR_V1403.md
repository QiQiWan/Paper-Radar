# v1.40.3 文献检索修复与验证说明

## 修复范围

根据 v1.40.2 的 2026-11-08 诊断包，本次检索中 arXiv、OpenAlex、Crossref 和 OpenAlex Semantic 都出现 `SOURCE_CHILD_PROTOCOL`，OpenReview 三组请求正常返回 HTTP 200 但未命中论文。部分来源的子进程退出码为 0，说明请求结束与主程序解析结果之间存在问题。

代码复现确认：在 Windows 常用的 CP936 输出环境下，子进程按本地编码输出中文论文标题，主程序按 UTF-8 解码，导致合法 JSON 被错误标记为协议故障。包含部分数学符号的文本也可能使旧版子进程产生 `UnicodeEncodeError`，退出码为 1。旧版把这些情况合并为同一诊断错误。

v1.40.3 修改子进程为固定 UTF-8 JSON 字节流（兼容 ASCII 转义），把来源适配器输出的普通文本与进程通信隔离；完善 Windows `--windowed` 打包模式的标准管道回退。父进程先判断异常退出，再验证输出编码、JSON 格式及论文记录结构，并记录无敏感内容的阶段、输出字节数和错误码。致命的子进程错误不会对同一来源继续无意义重试其他关键词，其他来源继续检索。固定 DNS、证书和代理故障也会快速报告原始错误分类，避免多次等待后只显示总超时。

## 更新流程（源码版）

1. 完全退出旧版 Paper-Radar、本地 Web 服务与后台搜索任务。
2. 备份 `%LOCALAPPDATA%\Paper-Radar`，特别是 `settings.json`、`data`、`logs`；不要用新版仓库覆盖用户数据目录。
3. 完整解压 v1.40.3 的项目根目录；不要只复制 `source_process.py`，其依赖诊断和 supervisor 的配套修改。
4. 在项目根目录使用原有 Python 环境运行 `python -m pip install -r requirements.txt`（已安装依赖通常无需重装），然后按原有方式启动本地界面。
5. 在「设置 → 文献来源授权」可选配置 OpenAlex 免费 API Key。配置到系统凭据管理器；OpenAlex 日常小量检索可免密，额度受官方政策限制。语义发现应考虑额外额度开销。

## 验收顺序

### A. 离线检索协议

在项目根目录执行：

```powershell
python -m pytest -q tests/test_v1403_retrieval_protocol.py
```

测试覆盖 CP936、CP1252、中文和特殊符号、混入输出、输出为空、旧协议兼容性、子进程异常、其他来源继续执行、隐私字段剔除。

### B. 真实来源子进程检索

**这一步会主动连接公开 API**。可在项目根目录双击 `start_windows.bat --smoke`，或执行：

```powershell
python -m paper_radar.sources.smoke --live --timeout 25 --output retrieval_smoke.json
```

此命令只使用固定的公开测试词 `machine learning` 和 30 天窗口，不读取个人主题和配置。每个来源最多执行 25 秒，`retrieval_smoke.json` 包含分类错误、请求阶段和耗时，不记录检索到的题名、用户关键词、URL 参数、请求头或 API Key。网络故障只影响对应来源；有一个来源成功时退出码为 0。**成功但 0 篇**说明接口访问和进程解析通过，需进一步检查时间窗口、关键词或来源覆盖范围。

### C. 用户常规检索

在本地界面使用实际研究主题进行检索，检查「任务中心 → 检索诊断」：

- 检索完成：来源 `ok=true`，返回数量可以为 0；在论文库核对新论文数。
- `SOURCE_CHILD_ENCODING`：旧编码问题或混合安装；核对版本并彻底重启所有进程。
- `SOURCE_CHILD_EMPTY`：子进程没有传回任何字节，重点检查 PyInstaller、杀毒软件和进程启动路径。
- `SOURCE_CHILD_PROTOCOL`：子进程返回结构损坏；查看新增 `stage` 和 stdout/stderr 字节数。
- `RATE_LIMIT`/`AUTH_REQUIRED`：检查来源官方额度与凭据。OpenAlex 可在设置中填写免费 Key。
- `DNS_ERROR`/`PROXY_ERROR`/`TLS_ERROR`：检查网络、系统代理、DNS 和证书。
- `SOURCE_TIMEOUT`：公共来源超时；稍后重试或调整检索预算。

下载诊断包后可重复定位问题。诊断不会保存请求原文；如需追踪实际 API 侧错误，须在本机安全环境中分析或使用服务商控制台。

## 验证边界

上述离线测试验证的是代码通信、解析和失败隔离。公开服务是否能从受影响的 Windows 设备及其当前代理正常访问，必须以 B、C 两步的实际结果为准；本次未取得该 Windows 机器的修复后网络日志，不应认定所有来源已经实机验证通过。
