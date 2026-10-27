# Palantir专家 交接（动作类型 / 事件类型 / 外部系统）

> 2026-10-27（UTC+8）重写。分支 `palantir-t1`（基于 `283bd89`，先 cherry-pick PDE 的测试夹具修复 `5e3282e` → `98d127a`）。
> 接口细节见 `frontend/docs/backend/action-event-api.md`（§14 = 本次 Webhook 签名/重试合同）；设计见 `backend/docs/backend/palantir-tier1-design.md`。
> 用户已定：**不接 ERP、Kafka，不做外部定时对账**；Web 系统对接走 Webhook。标注：【仓库】代码现状，【推断】我的取舍。

## 1. 本次完成：第一档 Webhook 出站

| 项 | 状态 | 位置 |
|---|---|---|
| HMAC-SHA256 签名（`X-Signature-256` / `X-Signature-Timestamp`，头名【推断】） | 完成 | `integration/signing.py`；副作用 `integration/action_effects.py`；事件投递 `event/delivery.py`；写回提交 `integration/execution.py` |
| 签名密钥按外部系统 / 路由配置，可用 `fernet$` 加密（复用 `data/secrets.py`） | 完成 | `AdapterConfig.signing_secret`（`integration/adapters.py`）；路由 `signing_secret` / `test_signing_secret` |
| 未配密钥 = 原行为；密钥无效 = 不发送 | 完成 | 同上；新错误码加入投递“补投”允许列表 `event/exceptions.py` |
| 自动重试（超时/网络/429/5xx，指数退避，次数有上限，同一幂等键） | 完成：副作用、事件投递；写回提交**不重发**（只对账【仓库】） | 副作用走 outbox `available_at`；投递走 `pending + next_at` |
| 4xx 不重试、立即失败 | 完成 | 副作用 4xx 由“结果未知”改为“失败”（行为变化） |
| 每次尝试留痕 | 完成（无迁移） | outbox `payload.attempts[]`；`event_delivery_record` |
| 测试 | 新文件 `backend/tests/test_palantir_webhook.py`（12 条，本机 mock HTTP） | 见 §4 |
| 前端 | 未改 | 外部系统页没有凭证编辑表单，密钥只走环境配置 |

## 2. 怎么配（运维）

```json
// RENGU_INTEGRATION_ADAPTERS
{"1/WMS": {"submit_path": "/exec", "effect_paths": {"webhook": "/hooks/effect"},
           "token": "…", "signing_secret": "fernet$…"}}
// RENGU_EVENT_DELIVERY_ROUTES
{"1/order_status_changed/仓储系统": {"url": "https://…", "test_url": "https://…", "idempotent": true,
  "user": "U010001", "signing_secret": "fernet$…", "test_signing_secret": "…", "max_retries": 3}}
```

副作用的重试次数取外部系统页的“重试”（`maxRetry`，0 = 不自动重试）。密钥加密：`python -c "from app.modules.data.secrets import encrypt_password; print(encrypt_password('…'))"`（需同一 `RENGU_DS_SECRET_KEY`）。

## 3. 入站（本次不改，交接给本体小白）

- 现状【仓库】：`POST /internal/v1/events` 用按本体的静态 Bearer 令牌（`RENGU_INTERNAL_EVENT_TOKEN(S)`），无签名、审计不记调用方。
- 计划：本体小白在做 `require_api_scope("events:ingest")`（服务账号 + 授权范围）。接入后入站改为按 scope 鉴权并记调用方；**过渡期保留静态令牌**，两者都接受，确认所有接入方切换后再下线静态令牌。本模块不需要改出站代码配合。

## 4. 测试

- `backend/tests/test_palantir_webhook.py`：签名正确性与验签、无密钥不签名、无效密钥不发送、503/网络断开/超时后重试并成功、400 不重试、重试用尽的终态、幂等键跨尝试不变、逐次尝试记录（副作用 JSON、投递记录表）、密钥轮换不改路由指纹。
- 跑法：`RENGU_TEST_DB=rengu_test_palantir_t1 python -m pytest tests/test_palantir_webhook.py`（私有库，本机 127.0.0.1 mock 服务，不连外网、不起 worker）。
- 副作用测试把授权/映射复核 `recheck_effect` 替换成固定结果，只验发送与重试编排；完整动作闭环、发布写回 + 409 回滚的回归按 PDE 安排由合并后统一测试覆盖，本次不写。
- 已知（非本次引入）：`tests/test_event_delivery_boundary.py::test_explicit_test_endpoint_and_stable_subscription_idempotency` 在基线上就失败（`delivery_key` 读 `event.ontology_id`，测试的 SimpleNamespace 没给；来自 `ea83447`）。

## 5. 仍未做 / 缺口

1. 动作副作用没有人工处置/重试入口（需权限点、outbox 行版本/审计和页面，超出小改）。
2. 副作用逐次尝试不在外部系统“调用记录”页展示（只在 outbox JSON）。
3. 适配器 GET 查询/探测不签名。
4. 事件路由 `max_retries`、签名密钥只能改环境配置，页面不展示。
5. 文档 §2 表里写“写回提交明确失败时自动重试 maxRetry 次”，代码只发一次（本次保持不重发），需 PDE 定以哪个为准。
6. 签名头名、签名串、5 分钟时间窗需与接入方确认【推断】。
7. 旧遗留：静态①两标签页并发编辑提示（部分）、静态③事件行级范围（等定义）、编号锁去锁反证、`VITE_PALANTIR_CR_WRITE` 默认打开（等决定）。
8. 第 2 期（执行策略、机器接口、AI 工具导出、动作意图评测）：只设计，见设计文档 §8。

## 6. 划掉的项（不再做）

ERP 连接器、Kafka、外部定时对账任务扩展（`reconcile_task`/`query_by_key`/`on_reconciled` 保持原样不扩展）、对外部业务系统的真实写回验证。
