# Paper-Radar v1.26.0 — 三平台桌面版安装与运行说明

本指南面向不使用命令行的科研人员。桌面软件由一个**运行管理窗口**和一个**本机浏览器研究工作台**组成。运行管理窗口负责启动与停止本地服务、打开研究工作台、显示科研任务进度、状态及日志；所有论文、检索配置和报告保存在当前系统用户的个人数据目录。

## 1. 下载哪个安装包

| 操作系统 | 预期原生交付物 | 安装后使用方式 | 本轮实际验收 |
|---|---|---|---|
| Windows 10/11 x64 | `Paper-Radar_Windows_x64.zip`（包含 `Paper-Radar.exe` 和运行库） | 解压整个文件夹，双击 `Paper-Radar.exe` | 构建脚本和运行参数已实现；**Windows 原生构建、签名及实机 GUI 尚未验收** |
| Ubuntu 22.04+ amd64 | `Paper-Radar_1.26.0_amd64.deb` 或便携版 ZIP | 双击 `.deb` 安装后，从应用菜单点击 Paper-Radar | Linux Python 源码模式启动和 HTTP 管理已通过；**Deb 构建与安装实机尚未验收** |
| macOS Apple Silicon / Intel | `Paper-Radar.app`（按架构分开构建后压缩成 ZIP） | 解压后移动到“应用程序”，打开 Paper-Radar | 构建脚本已实现；**macOS 原生构建、公证与实机 GUI 尚未验收** |

**目前交付的是已测试的 v1.26.0 源码和三平台原生构建工作流。** 当前执行环境缺少 PyInstaller、Windows/macOS 运行环境及原生签名凭据，本轮没有生成经过验证的 Windows EXE、Ubuntu DEB、macOS APP。GitHub Actions 构建完成后，仍需由发布者进行原生安装与运行验收。请勿把源码 ZIP 当成无 Python 依赖的安装包。

## 2. 双击启动后看到什么

1. 打开 **Paper-Radar 运行管理** 窗口；程序会自动寻找当前系统用户拥有的本地服务，如没有则自动启动。
2. 本地服务通过健康检查后自动打开默认浏览器中的研究工作台。服务绑定 `127.0.0.1`，不会向局域网或公网开放。
3. 在工作台创建研究主题，设置关键词和来源，点击“开始文献追踪”。进行联网检索时显示来源成功、超时与缓存状态。
4. 运行管理窗口实时显示“正在启动 / 运行正常 / 未运行”、当前任务阶段、进度百分比及最近日志；可按需打开浏览器、重启或停止。
5. 关闭运行管理窗口**不会强制中断正在进行的科研任务**。下次打开管理器能识别同一用户的服务并重新接管。后台服务在一段时间无访问且无科研任务时自动退出。

正在执行任务时会拒绝停止或重启，避免未保存的结果丢失。遇到服务无法启动，可查看运行管理器的日志区域。

## 3. 文件保存在哪里

| 系统 | 默认用户数据目录 |
|---|---|
| Windows | `%LOCALAPPDATA%\Paper-Radar` |
| macOS | `~/Library/Application Support/Paper-Radar` |
| Ubuntu | `~/.local/share/paper-radar`（尊重 `XDG_DATA_HOME`） |

其中包含设置、SQLite 数据库、已下载 PDF、报告和 `logs/desktop_server.log`。安装程序所在目录只读取代码和资源文件，不写科研数据。可以通过系统备份工具备份这个目录；**卸载软件前应自行备份。**

## 4. 从旧源码版本升级

旧源码模式数据通常在旧 Paper-Radar 文件夹的 `settings.json`、`data/`、`reports/`、`downloads/`。首次换用打包桌面版时，新版本使用上表的个人数据目录，**不会自动猜测和搬迁旧数据**。建议停掉旧服务、备份整套旧文件，检查无任务在运行，再按以下顺序迁移：

1. 备份旧软件目录，至少保存 `settings.json`、`data`、`reports`、`downloads`；另外确认有无自定义 `output_dir` 与 Zotero 路径。
2. 打开新版运行管理器一次，查看个人数据路径，再停止服务。若默认没有旧数据，退出程序后将这些文件夹合并复制到个人数据目录，务必保留原始备份。
3. 重新打开软件，在论文库和报告中核对总数、主题和历史记录；如有自定义报告目录、Zotero 位置或模型密钥，请按系统提示重新检查。
4. 不要将个人数据库、密钥及 PDF 放回只读程序目录，也不要用旧代码直接打开新版迁移后的数据库。

公网多账号服务仍按原 `PAPERRADAR_PORTAL_DATA` 运维，桌面数据与公网数据默认隔离。**v1.26.0 未改变公网工作区 v8 迁移版本。**

## 5. 给发布者的原生打包步骤

CI 文件：`.github/workflows/desktop-release.yml`。**在 main 分支推送新的应用版本后，自动构建 Windows、Ubuntu、macOS ARM、macOS Intel，并在四组构建、回归和附件校验全部通过后，自动创建一个 GitHub Prerelease**。已有版本号不会重复发布。开发者也可在 Actions 页面手动运行该工作流。请阅读 [完整发布配置](GITHUB_AUTO_RELEASE.md)，目前产物尚未签名或公证。

本地复现（仅开发与发布人员）：

```bash
python -m pip install -r requirements.txt pyinstaller pytest
python tools/packaging/build_desktop.py
```

Ubuntu 可继续执行 `sh tools/packaging/build_ubuntu_deb.sh`；Windows 构建还会生成用于后台任务和 PDF/检索子进程的 `Paper-Radar-Service.exe`，**必须与主 EXE 及 `_internal` 文件夹共同分发**。

发布前还必须：Windows Authenticode 签名、macOS Developer ID 签名与公证、对应平台打开 GUI 并跑真实本地服务及保存测试、病毒/恶意软件误报排查、升级与卸载用户数据保留测试。Unsigned macOS app 可能受到 Gatekeeper 阻拦；Windows 下载程序可能触发 SmartScreen。不要指导普通用户关闭系统防护作为常规安装方法。

## 6. 发布验收清单

- 双击启动：无需管理员权限和预装 Python；首次启动自动创建当前用户数据目录。
- 打开浏览器：本地健康检查报告与软件版本一致，服务仅监听 `127.0.0.1`。
- 再次打开：自动接管**同一系统用户**的服务；不能关闭另一用户的服务。
- 关闭/重启：空闲时安全退出；有任务时显示拒绝操作的原因；不存在孤儿进程。
- 检索：断网时可反馈并允许恢复；缓存命中/过期必须区分，不能将旧结果显示为最新。
- 保存：重启与升级后主题、论文、阅读状态、报告和 PDF 仍在；个人数据不写入应用安装目录。
- 子进程：arXiv、OpenAlex、Crossref 的来源子进程和隔离 PDF 解析子进程在原生包内都能正确启动与超时退出。
- 安全：没有打包私钥、Token、个人 PDF、用户数据库；脚本不能绕过系统签名与公证。

上述是**目标验收清单**，不能据此推定 Windows/macOS/Ubuntu 原生包已在本轮完成真机验收。
