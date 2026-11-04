# AGENTS.md

本仓库是仁谷本体平台（FastAPI + PostgreSQL + Vue）。AI 代理在接入或修改前，先读 [docs/AI-INTEGRATION.md](docs/AI-INTEGRATION.md)。

## 必守边界

- 运行时只通过 HTTP API 或 MCP 访问本体；不要直连业务库、平台库或源库取数。
- 所有业务请求显式带 `X-Ontology-Id`；机器调用用 Bearer API Key，不用 `X-Demo-As` 或预览参数。
- 动作执行必须带 `Idempotency-Key`，并以 `runId` 查询最终状态；`结果未知` 停止并人工对账。
- 事件写入用 `eventId` 幂等；`payload` 严格按能力目录 schema，不把数值写成字符串。
- 对象、指标、动作、事件、参数编码从 `GET /api/ontology/v1/meta/capabilities` 获取，不猜。
- 不把 API Key、数据源密码、内部事件令牌写入前端、日志、URL 或提交到仓库。
- 定义或发布变化后重新读取能力目录和 `releases/current`；不要用旧快照重放事件。

## 常用入口

| 用途 | 入口 |
|---|---|
| 集成总指南 | `docs/AI-INTEGRATION.md` |
| MCP 客户端模板 | `docs/ai/mcp-client.example.json` |
| 后端运行 | `backend/README.md` |
| CDC 专题 | `docs/26-10-31-cdc-deployment-ai-guide.md` |
| 能力目录 | `GET /api/ontology/v1/meta/capabilities` |
| MCP | `POST /api/qa/v1/mcp` |
