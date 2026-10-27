# Palantir专家 交接（动作类型 / 事件类型 / 外部系统）

> 2026-10-27（UTC+8）重写。分支 `palantir-t1`（基于 `283bd89`，先 cherry-pick PDE 的测试夹具修复 `5e3282e` → `98d127a`）。
> 2026-10-27（UTC+8）追加：分支 `palantir-t2`（基于 `bc32ce6`）= 执行策略 + 机器接口 + 入站服务账号，见 §0。
> 2026-10-27（UTC+8）追加：分支 `palantir-t3`（基于 `c55f370`）= 机器接口第 3 期（对象查询 / 函数调用 / AI 工具清单 / 评测集），见 §0.1。
> 2026-10-27（UTC+8）追加：分支 `palantir-t5`（基于 `343ba5f`）= 服务账号自动执行（执行主体 `api:{编号}`）+ 工具清单改用能力目录，见 §0.3。
> 接口细节见 `frontend/docs/backend/action-event-api.md`（§14 = Webhook 签名/重试合同；§15 = 执行策略/机器接口/入站；§16 = 机器接口第 3 期）；设计见 `backend/docs/backend/palantir-tier1-design.md`。
> 用户已定：**不接 ERP、Kafka，不做外部定时对账**；Web 系统对接走 Webhook。标注：【仓库】代码现状，【推断】我的取舍。

## 未完成清单（第一档收尾，交 Codex 接手，2026-10-27 UTC+8，基于 master `e657b08`）

**待老板定**
- 审批触发提交时，提交条件改由确认人复核（palantir-t7 已生效，`action/checker.py`）。若否决，改回按审批人检查并调整 `tests/test_palantir_mainline.py`。

**已知失败（非本人改动引入，master 上就有）**
- `tests/test_palantir_webhook.py` 在 `-p tests.use_ops_support` 下 8 条失败：外网拦截把 127.0.0.1 本地 mock 接收端也拦了。修法建议：拦截放行回环地址，或该文件不加载 `use_ops_support`。
- `tests/test_event_delivery_boundary.py::test_explicit_test_endpoint_and_stable_subscription_idempotency`：`delivery_key` 读 `event.ontology_id`，测试的 SimpleNamespace 没给（见 §4）。

**演示环境（不在代码里，需部署时配）**
- `RENGU_INTEGRATION_ADAPTERS` 加 `1/EXT-WEBHOOK`：`submit_path`、`actions:["request_replenish"]`、`signing_secret`；`RENGU_INTEGRATION_REAL_CONNECT=true`；接收端须 HTTPS 并回 `{"status":"SUCCESS","receiptId":…}`；派发任务 `dispatch_queued` 要在跑。
- 时间线文字显示种子配置里的 `POST /praybill/save`，仅外观问题。

**前端适配（动态本体负责人已提出）**
- 人工复核项 `humanChecks` 尚未在建议面板和启动确认框里显示（接口格式见 action-event-api §18）。

**延后功能（按约定本档不做）**
- 下线旧的入站静态令牌，加入站签名校验（等接入方都换成 API 密钥）。
- 事件载荷参数 `enum`（等静态在事件类型定义里加上）。
- 其余缺口见 §5（副作用人工重试入口、调用记录页展示、策略前端编辑器、评测门槛 `evalPassedAt`、函数按密钥白名单等）。

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

## 0.2 palantir-t4 补丁（基于 `492d746`，无迁移）

- 入站只收 外部系统 来源（PDE 裁定；同步 / 动作 / 规则 → 403 `EVENT_SOURCE_NOT_EXTERNAL`），静态令牌与密钥一致；内部 `receive(request=None)` 不受影响：`event/ingress.py`。工具清单事件工具同步收窄：`machine/tools.py` `INGESTIBLE`。
- `枚举列表` 参数逐项校验：`action/checker.py`。
- 未做（延后）：工具清单改用能力目录的参数 `enum` 与事件类型节（static-t3 已提供），`machine/tools.py` 仍自己读事件类型、自己解析枚举。
- 测试：`tests/test_palantir_machine.py` 新增 2 条。

## 0.3 palantir-t5：服务账号自动执行 / 工具清单改用能力目录（基于 `343ba5f`，无迁移）

| 项 | 状态 | 位置 |
|---|---|---|
| 服务账号经 `:execute` 在 auto 策略、调用方允许、上限内**真实执行**；超限仍转暂存（待确认）；幂等不变 | 完成；去掉 t2 的“服务账号一律转人工”分支和 `SERVICE_ACCOUNT_TEXT` | `dynamic/service.py` `apply_policy()`、`action/policy.py` |
| 执行主体：`ServiceAccount` 没有 runAs 用户字段【仓库，`admin/models.py`】，故用系统主体 `api:{服务账号编号}`：写入 `action_run.created_by`、外部提交调用账本 `writeback_call.actor`、副作用 outbox `actor`；自动执行日志备注带“执行主体 api:…”；确认人留空（不冒充真人） | 完成 | 新增 `machine/principal.py`（`actor_of` / `resolve_actor`） |
| 后台执行链按主体重建身份：`api:` → 读服务账号（未吊销）+ 绑定且已绑定到本空间的角色（功能权限 = 角色 func，行范围 = 角色行范围）；其余仍走 `resolve_user`（在用用户） | 完成 | `integration/execution.py`（排队写回）、`integration/action_effects.py`（副作用复核）、`integration/emitter.py`（动作事件 / 编辑授权） |
| 工具清单：动作参数 / 函数入参用能力目录的 `enum`；事件工具取目录 `events` 节，只导出 `ingest.accepting`，密钥看 `ingest.keyAllowed`，接口地址取 `ingest`；删除自读事件类型与自解析枚举 | 完成 | `machine/tools.py` |
| 测试 | `test_palantir_machine.py` 改写服务账号用例 + 新增主体解析用例；`test_palantir_machine_tools.py` 新增目录 enum / 事件节用例 | 跑法须带 `-p tests.use_ops_support` |

取舍【推断】：后台重建的服务账号身份用**角色**权限，不再叠加密钥的 scope / 功能快照收窄（后台拿不到是哪把密钥；入口处已按密钥校验过）；服务账号吊销或角色解绑后，排队中的执行在后台 401 失败，不会以别人身份继续。`machine.tools.INGESTIBLE` 保留，因为 `static/capabilities.py` `_ingest` 引用它计算 `accepting`。

需要的钩子（未改他人文件）：
- 本体小白：如要“绑定真人代执行”，需在 `ServiceAccount` 加 runAs 用户字段（迁移在其模块）；`admin/api_keys.py` 里服务账号 `CurrentUser` 的构造是私有逻辑，`machine/principal.resolve_actor` 复刻了同样的形状（编号、名称、角色、`is_service_account`），建议抽一个公开函数共用。
- 静态：`INGESTIBLE` 由能力目录反向引用 `machine.tools`，可考虑挪到 `event` 模块做公共常量；事件载荷字段目前不带 `enum`（工具侧已兼容，有就用）。

## 0.4 palantir-t7：外部 Webhook 动作的声明效果 / 人工核对条件（基于 master `40562d7`，含 xiaobai-p5，无迁移）

| 项 | 状态 | 位置 |
|---|---|---|
| 执行通道是外部系统且其 `category = Webhook` 时，自然语言（非有界 JSON）的编辑规则 / 外部副作用 = **声明效果**：本地不执行、不报 503；随签名提交载荷 `declaredEdits`（每项 `status: "external/declared"`）交外部系统承担 | 完成 | `integration/action_edits.py`（`external_webhook` / `declared_text` / `declared_writeback`，`business_edits` 跳过）、`integration/action_effects.py`（`local_effects`）、`integration/execution.py`（载荷） |
| 同一动作下的自然语言参数规则 / 提交条件（受限解释器无法判定）= **确认人人工核对**：不阻断暂存与试运行，`dryRun.humanChecks` 列出，暂存时间线记“需确认人人工核对的提交条件（N 项）”；能判定为不通过的照旧 422 | 完成 | `action/checker.py` `run()`、`dynamic/service.py` `stage()` |
| auto 策略下含人工核对项 → 不自动执行，转待确认（`overflowReason`） | 完成 | `dynamic/service.py` `apply_policy()` |
| 机器接口响应 `edits[].status = external/declared`、`humanChecks` | 完成 | `dynamic/router.py` `_machine_out` |
| 声明编辑不再要求本地对象绑定（发起可用性、执行记录参数可见性） | 完成 | `dynamic/action_options.py`、`dynamic/run_visibility.py` |
| 审批后提交复核：提交条件按**确认人**身份复核（原先按审批人，审批人不满足“执行人角色 / 告警处理权限”导致 422）；审批人自身的功能 / 对象 / 操作权限仍按审批人校验；确认人无法解析时保持原行为 | 完成 | `integration/execution.py` `criteria_user()` |
| 无外部 Webhook 通道的动作：自然语言编辑仍 503、不可校验条件仍失败；有界 JSON 编辑在任何通道上都严格校验（不变） | 不变 | — |
| 测试 | 新增 `tests/test_palantir_mainline.py`：直接暂存主线、告警发起主线（AL-20261025-0139 → `:start-action` 直接进审批中 → U010812 审批 → 签名 Webhook → 已对账）、严格/声明对照、auto 转确认 | 跑法须带 `-p tests.use_ops_support` |

演示运行所需配置【仓库】：种子与数据**不改**；只需部署环境的 `RENGU_INTEGRATION_ADAPTERS` 含 `1/EXT-WEBHOOK`：`submit_path`、`actions: ["request_replenish"]`、`signing_secret`（可 `fernet$…`），`RENGU_INTEGRATION_REAL_CONNECT=true`，并把 EXT-WEBHOOK 的 `endpoint` 指向真实可达的 HTTPS 接收端（种子值 `https://127.0.0.1:8443`，适配器只接受 HTTPS）；外部接收端须返回 `{"status":"SUCCESS","receiptId":"…"}`。

需要的钩子（未改他人文件）：
- 本体小白：`tests/test_demo_mainline_e2e.py::test_mainline_stops_before_webhook` 断言“暂存 503、接收端零请求、试运行不通过”，本修复后按设计会失败，需改为走通断言（可参考 `tests/test_palantir_mainline.py`）。
- 静态：经变更单发布的编辑规则仍须为有界 JSON（`action/writeback.py` `edits` 校验，见 API 文档 §18）；商品等对象如要**本地**执行编辑，需在 `WRITABLE` 白名单登记可写列（当前仅门店 / 订单）。
- 时间线“调用 …：POST /praybill/save”取自动作配置的 `endpoint` 文字，实际提交路径是适配器 `submit_path`（演示为 `/hook`）；如需一致请改动作配置（种子，未改）。

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
7. 执行策略缺口（t2）：评测门槛 `evalPassedAt` 未接；`objects` 只是 ID 列表（非行范围）；规则/告警/问数/推演来源不自动执行；服务账号已可自动执行（t5，系统主体 `api:{编号}`，无真人 runAs）；`suggest` 未在试运行/选项接口展示；无前端策略编辑器。
8. 机器接口缺口（t3 后）：函数无按密钥白名单；`suggest` 动作不导出、无试运行工具；清单无对象查询工具；只读角色用户的密钥不能调 `/machine` POST（`auth.py` 纯读判定未改）；对象查询依赖问数模块私有函数；静态能力目录钩子见 §0.1（事件段、参数 enum 已由 static-t3/t4 提供并在 t5 接上，按段计算仍未做）。

## 6. 划掉的项（不再做）

ERP 连接器、Kafka、外部定时对账任务扩展（`reconcile_task`/`query_by_key`/`on_reconciled` 保持原样不扩展）、对外部业务系统的真实写回验证。
