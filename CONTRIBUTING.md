# 参与开发

欢迎通过 Issue 报告缺陷，或通过 Pull Request 提交修改。请在问题描述中给出操作系统、版本号、复现步骤和必要的脱敏日志。

提交代码前使用 Python 3.10 或更新版本安装依赖并运行：

```bash
python -m pip install -r requirements.txt pytest
python -m tools.packaging.feature_parity
python -m pytest -q
```

新增检索来源、Agent、数据迁移、页面或 API 时，应增加对应测试，并确保旧任务和数据接口继续可用。详细架构与发行流程见 [维护指南](docs/MAINTAINERS.md)。

请勿提交用户设置、API Key、数据库、PDF、日志或其他个人研究资料。贡献内容应有可追溯的数据来源，并遵守适用的第三方许可证。
