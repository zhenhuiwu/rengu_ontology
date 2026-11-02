# Ollama 模型配置后端与运行时

新增 provider=openai/ollama，旧数据库 JSON 和环境配置省略该字段时仍为 openai。两用途 qa/ontology 继续独立保存、乐观版本检查与管理权限。保存不请求模型、不改原有配置，未新增依赖或数据库结构。

Ollama 采用已安装的 OpenAI SDK，根地址与 `/v1` 地址归一到 `/v1`，默认 `http://localhost:11434/v1`。默认 authMode=none，不要求或发送 Authorization。显式选择 key 可用于受鉴权代理；旧 key 只在同 provider、同源站、原 key 模式下保留，换 provider/源站不能继承云密钥。历史 JSON 无 provider 时保持旧行为；环境回退同样限定 provider 与源站，避免数据库配置换源后借用环境凭据。

GET `/api/admin/v1/settings/models` 与 PUT `/{usage}` 沿用原字段，并新增 provider。新增 POST `/api/admin/v1/settings/models/{usage}/connection`，管理权限，接受未保存配置及可选 apiKey，不要求 lockVersion。model 可省略、null 或空字符串，便于先获取目录；保存仍必须填写有效 model。响应为 `ok/provider/baseUrl/modelOptions[{value,label}]/selectedModelAvailable/error`；未选模型时 selectedModelAvailable=null。配置/权限错误仍按现有 HTTP 错误；远端连接、HTTP 状态、TLS 或格式问题返回 ok=false 和固定清理后的原因，不返回响应正文或密钥。

检测仅 GET 归一 Base URL 下 `/models`，不跟随重定向、保留 TLS 校验，目录响应最大 1 MiB，不执行 chat、模型加载、pull 或任何业务动作。Ollama 官方确认 [OpenAI 兼容 `/v1/chat/completions` 与 `/v1/models`](https://docs.ollama.com/api/openai-compatibility)，另有原生 [GET `/api/tags`](https://docs.ollama.com/api/tags)；此实现复用兼容目录路径，避免维护两套探测契约。模型目录成功仅代表目录读取成功，不能声称 SQL 质量、JSON 能力、上下文容量或推理已验收。

环境可选 QA_LLM_PROVIDER/AUTO_ONTOLOGY_LLM_PROVIDER=ollama；对应 MODEL、BASE_URL、TIMEOUT_S 沿用现有变量。明确选择 ollama 时环境回退启用无鉴权模式，不依赖 DASHSCOPE_API_KEY；部署者应显式填实际准备好的模型。未安装服务或模型时由运维处理，本任务未安装、启动或拉取任何服务/模型，也未覆盖本地 `.env`。

一个离线脚本 `cd backend; .venv/Scripts/python.exe -m scripts.check_ollama_model_settings` 验证旧配置默认、URL/提供方/认证、无 key 配置、跨提供方/源站不复用 key、真实客户端在 MockTransport 上生成正确的目录 GET 与两种用途兼容请求且不发送 Authorization。全部请求仅内存传输，无数据库、真实网络或推理。未运行 pytest/后台全量/集成迁移测试。真实界面和本地 Ollama 实连结果由根代理另行补验。

随后窄审补齐自动本体 SDK 的原异常/资源边界：沿用单次调用 async context 关闭客户端，缺失/None/空 choices、缺 message 或非字符串 content 均转 LlmError，按原服务降级路径处理。既有同一离线脚本增加这些异常与关闭断言并通过；仅本轮 4 个产品 Python 文件及该脚本经过已安装 Ruff 的导入修正、format 和 check，最终全部通过，未格式化其他文件或运行后台测试。
