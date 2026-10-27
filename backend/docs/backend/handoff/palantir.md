# Palantir专家 交接（动作类型 / 事件类型 / 外部系统）

> 2026-10-27（UTC+8）重写。分支 `palantir-t1`（基于 `283bd89`，先 cherry-pick PDE 的测试夹具修复 `5e3282e` → `98d127a`）。
> 2026-10-27（UTC+8）追加：分支 `palantir-t2`（基于 `bc32ce6`）= 执行策略 + 机器接口 + 入站服务账号，见 §0。
> 2026-10-27（UTC+8）追加：分支 `palantir-t3`（基于 `c55f370`）= 机器接口第 3 期（对象查询 / 函数调用 / AI 工具清单 / 评测集），见 §0.1。
> 接口细节见 `frontend/docs/backend/action-event-api.md`（§14 = Webhook 签名/重试合同；§15 = 执行策略/机器接口/入站；§16 = 机器接口第 3 期）；设计见 `backend/docs/backend/palantir-tier1-design.md`。
> 用户已定：**不接 ERP、Kafka，不做外部定时对账**；Web 系统对接走 Webhook。标注：【仓库】代码现状，【推断】我的取舍。

## 0. palantir-t2：执行策略 / 机器接口 / 入站服务账号（无迁移）

| 项 | 状态 | 位置 |
|---|---|---|
| 动作类型执行策略（suggest / confirm / auto + 调用方 + 上限，超限转暂存） | 完成；默认 confirm、不限调用方 = 原行为 | `action/policy.py`；变更单 `policy` 段 `action/writeback.py`、`action/sections.py`；分流 `dynamic/service.py` `stage()` / `apply_policy()` |
| 机器接口 `:execute`（Idempotency-Key）、`GET /machine/action-runs/{id}` | 完成，`require_api_scope("actions:execute", action_type="{type_id}")` | `dynamic/router.py`、`dynamic/schemas.py` `MachineExecuteIn` |
| 入站 `POST /internal/v1/events` 接受 `events:ingest` 密钥 + 事件类型白名单，记调用方；静态令牌保留 | 完成 | `event/ingress_schema.py` `authenticate`、`event/ingress.py` |
| 服务账号能过对象动作授权与提交条件检查（按绑定角色） | 完成（小改他人模块） | `integration/action_edits.py` `object_operation`、`action/checker.py` |
| 文档 | 写回提交“只发一次、带签名、不重发、只对账”已统一；§15 新合同 | `frontend/docs/backend/action-event-api.md` |
| 测试 | `tests/test_palantir_policy.py`（20）、`tests/test_palantir_machine.py`（4） | 跑法须带 `-p tests.use_ops_support` |

取舍【推断】：策略存版本定义 JSON、额度按时间线日志计数，避免迁移；服务账号调用在额度内也转人工（执行链按在用用户解析执行人，代执行需 runAs 设计）；服务账号发起的记录不指定确认人。

## 0.1 palantir-t3：机器接口第 3 期（无迁移）

| 项 | 状态 | 位置 |
|---|---|---|
| `POST /machine/objects:query`（`objects:read`），复用问数检索与行级范围 | 完成 | `machine/router.py`（用 `qa/explore_api.py` 的 `_bind` / `_facets` / `_item`） |
| `POST /machine/functions/{id}:invoke`（`functions:invoke`），只允许已发布 | 完成 | `machine/router.py` → `functions/runtime.py` |
| `GET /machine/tools?format=mcp\|openai`（`meta:read`） | 完成，静态能力目录的薄转换 + 密钥白名单 | `machine/tools.py` → `static/capabilities.py`（未改） |
| 执行响应 `checks` / `edits` / `retryable`；`params` 可传对象 | 完成 | `dynamic/router.py` `_machine_out`、`dynamic/schemas.py` |
| 动作意图评测集（20 条） | 完成 | `tests/fixtures/palantir_action_intents.yaml` |
| 文档 | §16 | `frontend/docs/backend/action-event-api.md` |
| 测试 | `tests/test_palantir_machine_tools.py` | 跑法须带 `-p tests.use_ops_support` |

取舍【推断】：对象查询没直接调 `explore_search`，因为它按 `effective_viewer` 解析会话用户，服务账号过不去，所以复刻其核心（同一 `bd.stmt(user)` 行范围）；`checks` 只能表达“全部通过”（不通过在暂存时就 422）；`edits` 是声明不是实际值。

需要静态能力目录（`static/capabilities.py`，未改）补的钩子：① 事件类型段（现由 `machine/tools.py` 自己读）；② 动作参数的规则 / 枚举选项与描述模板（现枚举参数导出为字符串）；③ 按段计算（现在工具清单也要付指标就绪度、规则、行范围的计算成本）。

## 1. 第一档 Webhook 出站（palantir-t1，已合入）

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

## 3. 入站

- 现状【仓库】（palantir-t2）：`POST /internal/v1/events` 接受 API 密钥（scope `events:ingest`，事件类型白名单按类型编码或 API 名匹配，`created_by = api:{服务账号编号或密钥前缀}`），同时保留按本体的静态令牌（`RENGU_INTERNAL_EVENT_TOKEN(S)`，`created_by = internal:event`）。
- 待定【推断】：确认所有接入方切换到密钥后再下线静态令牌；入站仍无签名校验。

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
5. 签名头名、签名串、5 分钟时间窗需与接入方确认【推断】。
6. 旧遗留：静态①两标签页并发编辑提示（部分）、静态③事件行级范围（等定义）、编号锁去锁反证、`VITE_PALANTIR_CR_WRITE` 默认打开（等决定）。
7. 执行策略缺口（t2）：评测门槛 `evalPassedAt` 未接；`objects` 只是 ID 列表（非行范围）；规则/告警/问数/推演来源不自动执行；服务账号不自动执行（缺 runAs）；`suggest` 未在试运行/选项接口展示；无前端策略编辑器。
8. 机器接口缺口（t3 后）：函数无按密钥白名单；`suggest` 动作不导出、无试运行工具；清单无对象查询工具；只读角色用户的密钥不能调 `/machine` POST（`auth.py` 纯读判定未改）；对象查询依赖问数模块私有函数；静态能力目录钩子见 §0.1。

## 6. 划掉的项（不再做）

ERP 连接器、Kafka、外部定时对账任务扩展（`reconcile_task`/`query_by_key`/`on_reconciled` 保持原样不扩展）、对外部业务系统的真实写回验证。
