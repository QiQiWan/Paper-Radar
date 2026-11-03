# 开发与调试

Paper-Radar v1.8.0 使用 Python 3.10+ 和标准库本地 HTTP 服务。主要模块见 [ARCHITECTURE.md](ARCHITECTURE.md)，扩展检索器见 [ADDING_SOURCES.md](ADDING_SOURCES.md)，PDF 下载器见 [ADDING_DOWNLOADERS.md](ADDING_DOWNLOADERS.md)。

```bash
python -m pip install -r requirements.txt
python -m pip install pytest
python -m pytest -q
python -m compileall -q paper_radar main.py
node --check paper_radar/static/app.js
python main.py --demo
python main.py --serve
```

本地 HTTP 不对公网开放，修改接口须校验 `X-Local-Token`，下载器严格校验目标 URL 与本地路径。敏感凭据通过系统 keyring 或显式环境变量设置；日志不得记录密钥。修改发布逻辑后，使用 `tools/package_release.py` 创建源码 ZIP，并从干净目录解压重新运行测试。

测试主要采用 Mock API、Mock Zotero、内建 HTTP 服务和本地浏览器前端模拟。真实机构出版社权限及 Windows 定时任务需要具备相应条件的实际电脑验收。
