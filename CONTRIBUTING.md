# 参与开发

欢迎通过 Issue 反馈问题或提交 Pull Request。

开发环境：Python 3.10+。安装依赖后，执行：

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

本地测试中使用模拟论文及模拟 API 响应，不要把 API Key、`data/`、`logs/`、`reports/`、`settings.json` 提交到公开仓库。

保留第三方引用和数据来源信息。贡献代码时请确认你有权以仓库许可证提供。


## Agent 模块贡献（v1.13.0）

请优先在 `paper_radar/agents/stages/` 独立修改对应 Agent，保持 `AgentSpec` 的输入输出合同，增加对应的 `tests/test_v113_agents.py` 或 `tests/test_agent_<name>.py`。必要的依赖变化须同步修改 `agents/catalog.py`，并以完整 `python -m pytest -q` 检查旧 API 兼容。详情见 [Agent 独立研发清单](docs/agents/README.md)。
