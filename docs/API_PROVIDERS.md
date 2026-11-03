# 模型 API：先从 DeepSeek 开始

模型用于在已有论文标题和摘要基础上生成简短中文速览。初次使用可以先不启用，基础检索、Zotero 和 Word/Markdown 报告不依赖模型服务。

## DeepSeek 配置

1. 打开 https://platform.deepseek.com/，注册 API 平台账号，按平台要求开通额度。
2. 创建 API Key。注意这是 API 平台的密钥，请勿复制到公众号文章或 GitHub。
3. Paper-Radar 左侧进入 **设置 → 中文速览**，勾选启用，选择 **DeepSeek**。
4. 接口地址保持 `https://api.deepseek.com`；模型预置 `deepseek-flash`。如果平台控制台提供不同可用模型，以实际权限为准。
5. 粘贴 API Key，点击 **保存并测试连接**。
6. 设置“每次 AI 速览”的上限，建议先用少量论文验证效果与成本。

启用后，程序将当前待处理论文标题和摘要发给所选服务商，返回100–160字左右的中文速览。速览供快速筛选，论文贡献和数值结论请以原文为准。连接失败时基础检索和 Word 输出仍可继续。

## 其他快捷供应商

| 供应商 | 入口 | 需要注意 |
| --- | --- | --- |
| 火山引擎豆包 | https://console.volcengine.com/ark | 取得 API Key，填写开通后提供的模型 ID / Endpoint ID |
| OpenAI / ChatGPT API | https://platform.openai.com/ | 使用 API 平台账号与 API Key；ChatGPT 会员费用不含 API 用量 |
| Claude (Anthropic) | https://platform.claude.com/ | 原生 Messages API，填写官方模型 ID |
| SiliconFlow | https://cloud.siliconflow.cn/ | API Key 与模型名按控制台授权 |
| 阿里云通义千问 | https://bailian.console.aliyun.com/ | 百炼 OpenAI 兼容接口，选择已开通模型 |
| 智谱 GLM | https://open.bigmodel.cn/ | 根据平台选择模型及接口权限 |
| Google Gemini | https://aistudio.google.com/ | 使用兼容接口及具有权限的模型名 |
| 自定义 | 服务商文档 | HTTPS、兼容 OpenAI Chat Completions 的服务可手工填写 |

## 用量显示的含义

设置页显示 Paper-Radar 本机成功调用的请求数、输入 Token、输出 Token。由服务商响应中的 `usage` 汇总，不能反映其他工具的调用，也不等于本月应付款项。

- **DeepSeek：** 可点击“刷新当前供应商”尝试查询官方 `user/balance` 余额，仍建议以官方账单为准。
- **其他供应商：** 软件提供当前服务商控制台链接，在官方控制台查询总用量与费用。部分平台的账号级用量接口需要额外管理员权限，目前不自动读取。
- **已配置供应商：** 设置页展示本机凭据管理器中是否保存相应 API Key；不会显示密钥明文。

密钥默认保存于操作系统凭据管理器，不进入 `settings.json` 或公开源码包。若用环境变量配置，请只在私人环境中设置，且 `.env.example` 是示例文件，程序不会自动加载 `.env`。

## 排错

401/403：检查密钥、服务商和模型权限。402：检查余额。404：确认接口 URL/模型名称。429：控制调用频率、稍后重试。网络超时：先核对网络和服务商区域地址。Claude 使用官方 `/v1/messages` 协议，其他主要采用兼容 Chat Completions 接口。
