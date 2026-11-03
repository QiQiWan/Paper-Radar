# Paper-Radar 正式发布前需要补充的真实截图

此表用于替换教程中的模拟界面图片。优先完成 A 组；B 组用于完整操作手册。需要截图时尽量使用同一研究主题。截图中应隐藏 API Key、机构令牌、Zotero 路径中的用户名、论文库里的未公开资料和个人信息。

| 组别 | 建议文件名 | 真实截图内容 | 截图方式与必要信息 |
| --- | --- | --- | --- |
| A1 | `A01_home_first_run.png` | Windows 实机打开后的 Paper-Radar 工作台 | 保留左侧导航、关键词框、主按钮和浏览器地址 `127.0.0.1`；不显示个人路径 |
| A2 | `A02_search_running.png` | 检索正在运行 | 顶部显示正在处理的阶段、进度条、数量；可展开1-3行不含密钥的日志 |
| A3 | `A03_search_completed.png` | 正常完成一次真实检索 | 同屏包含任务成功、当前主题命中篇数和“查看研究报告” |
| A4 | `A04_library_actual.png` | 实际论文库 | 尽量包含2-3篇真实论文、中文简述、星标、摘要展开、分组折叠 |
| A5 | `A05_report_markdown_preview.png` | Markdown 预览弹框 | 预览本期综述和优先阅读开头；保持标题和复制按钮清晰 |
| A6 | `A06_report_word.png` | 正式 Word 报告 | 截取本期概览、综述、优先阅读；标题和内容均来自真实检索 |
| A7 | `A07_zotero_permission.png` | Zotero 设置 → 高级 → 杂项 | 勾选“允许此计算机上的其他应用程序与 Zotero 通讯”，避免显示下方用户数据路径；当前用户提供的 macOS 图可用 |
| A8 | `A08_zotero_collection.png` | Zotero 收藏夹连接与关键词分析 | 连接成功、选中具体收藏夹、提取的检索词及论文数量；隐藏敏感目录 |
| B1 | `B01_python_install.png` | Windows Python 安装界面或 `python --version` | 让初次安装的读者知道版本要求；不要截环境变量隐私 |
| B2 | `B02_start_bat.png` | `start_windows.bat` 首次启动日志 | 显示 Python 环境安装成功和工作台本地地址；不需全屏命令行 |
| B3 | `B03_advanced_sources.png` | 首页展开“检索范围和选项” | 看清 arXiv/OpenAlex/Crossref 和30篇及排序方式 |
| B4 | `B04_pdf_download_status.png` | 下载页 | 成功和失败各展示一条，出现重新下载按钮 |
| B5 | `B05_deepseek_setup.png` | DeepSeek 配置成功状态 | 拍服务商、接口、模型和“测试通过”；API Key 输入框必须打码 |
| B6 | `B06_scheduled_task.png` | Windows 每日定时任务已开启 | 展示执行时间、下次运行和运行状态；如拍任务计划程序，仅保留任务名 |
| B7 | `B07_publisher_access.png` | Springer/Elsevier 选项打开后的授权提醒 | 展示打开高级来源时的提示与官方申请入口；不要显示任何 Key |
| B8 | `B08_source_failure.png` | 单个来源失败而其他成功 | 展示不含敏感 URL 的错误信息与继续完成的结果 |
| B9 | `B09_report_files.png` | `reports/` 文件夹 | 展示 Word、Markdown、CSV 三种文件的输出名称和修改时间 |
| B10 | `B10_darkmode.png` | 深色模式首页 | 与 A1 同尺寸，便于展示深浅色切换 |

建议图片宽度在 1400～1800 px；正文里只展示相关操作区域，保留完整按钮和操作反馈。公众号单张图片尽量控制在 2 MB 内。Word 中的图按“图1—图N”顺序编号。

**最需要补充的证据：** A2、A3、A4、A5、A6。当前压缩包已附同一前端代码生成的演示界面截图，它们能说明界面结构，但无法证明外部 API、Zotero 和后台任务已经在你的电脑上真实跑通。

如果暂时没有真实运行截图，可保留“界面演示”标记发布操作说明；涉及实际检索效果和耗时的部分暂不使用虚构数字。
