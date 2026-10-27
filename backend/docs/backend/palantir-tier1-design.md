# Palantir专家 第一档设计：Web 系统 Webhook 出站（签名 + 自动重试）

> 2026-10-27（UTC+8），分支 `palantir-t1`（基于 `283bd89` + `5e3282e`）。标注：【仓库】= 本仓库代码现状；【官方】= Palantir 或通用公开规范里有把握的做法；【推断】= 我的设计取舍，未经外部系统方确认。
> 用户已定：不接 ERP、Kafka、外部定时对账；Web 系统对接走 Webhook。第 2 期（执行策略、机器接口、工具导出、动作意图评测）设计见文末 §8，本次不实现。

## 1. 范围

| 出站路径 | 代码 | 本次改动 |
|---|---|---|
| 动作副作用 通知/Webhook | `app/modules/integration/action_effects.py` `dispatch_effect` | 签名 + 自动重试 + 逐次尝试记录 |
| 事件 HTTP 投递 | `app/modules/event/delivery.py` `execute` | 签名 + 自动重试（沿用投递记录） |
| 动作写回提交（外部执行器） | `app/modules/integration/execution.py` `dispatch_one` | **只签名，不自动重发**（见 §4） |
| 适配器查询/探测（GET） | `app/modules/integration/adapters.py` `HttpAdapter.request` | 不签名（缺口，§7） |
| 入站 `POST /internal/v1/events` | `event/ingress*.py` | **不改**（见 §6） |

共用模块：`app/modules/integration/signing.py`（签名、密钥解析、可重试判定、退避）。

## 2. 签名【推断：头名与串格式为本仓库自定，参考 GitHub/Stripe 式 HMAC Webhook 惯例】

- 请求头：
  - `X-Signature-Timestamp: <Unix 秒>`（本次尝试发出时刻，取 `app.core.clock.now()`）。
  - `X-Signature-256: sha256=<hex>`，`hex = HMAC-SHA256(secret, "<timestamp>." + 原始请求体字节)`。
- 请求体：JSON 只序列化一次（`ensure_ascii=False`、`separators=(",", ":")`），以原字节发送（`content=`，`Content-Type: application/json`），签的就是发出去的字节。接收方须**先验签再解析 JSON**。
- 每次尝试重新取时间戳、重新签名；`Idempotency-Key` 跨尝试不变。
- 接收方参考实现：`signing.verify(secret, timestamp, body, header, tolerance_s=300)`（常量时间比较 + 5 分钟时间窗）【推断】。
- 原有头保留：`Accept`（副作用/提交）、`Idempotency-Key`、`X-Ontology-Id`、可选 `Authorization: Bearer`。

### 2.1 密钥存放【仓库 + 推断】

外部系统凭证**本来就不落库**：适配器令牌在 SecretStr 环境变量 `RENGU_INTEGRATION_ADAPTERS`，事件路由令牌在 `RENGU_EVENT_DELIVERY_ROUTES`【仓库】。签名密钥放在同一处，按每个外部系统 / 每条路由各自配置：

- 适配器：`AdapterConfig.signing_secret`（与 `token` 同级）。
- 事件路由：`signing_secret`（生产）、`test_signing_secret`（测试通道）。
- 值可以是明文，或 `fernet$…`——用现有 `app/modules/data/secrets.py` 的 `encrypt_password` 加密（密钥 `RENGU_DS_SECRET_KEY`，MultiFernet 可轮换），发送时 `decrypt_password` 解密【仓库：复用现有加密 helper】。
- 不配：与原来完全一致，不带签名头。
- 配了但不可用（解不开 / 少于 16 字节 / 含控制字符）：**不发送**，绝不退回不签名发送。副作用记 `SIGNING_SECRET_UNDECRYPTABLE|SIGNING_SECRET_INVALID`；事件投递保持待处理、1 分钟后再检查（不耗尝试次数），这两个码也加进人工“补投”允许的原因列表。
- 路由指纹（`route_fingerprint`）排除 `signing_secret`/`test_signing_secret`（同 `token`），轮换密钥不会让进行中的尝试判为“路由绑定已变”。

## 3. 自动重试【推断】

- 可重试：网络错误/超时（`httpx.TransportError`/`RequestError`）、HTTP 429、5xx。
- 不可重试，立即失败：其它非 2xx（4xx、3xx；不跟随重定向）。
- 退避：第 n 次发送后等 `30s × 2^(n-1)`，上限 15 分钟；`Retry-After` 为秒数时取二者较大（同样封顶 15 分钟）。
- 不在进程内 sleep，全部靠已有调度：
  - **副作用**：同一 outbox 行（`outbox_event`，topic `action.effect`）改 `available_at = now + 退避`、`last_error = EFFECT_RETRY_SCHEDULED`、`payload.state = "retrying"`；`dispatch_effects` 扫描条件扩为 `attempts == 0 OR last_error == EFFECT_RETRY_SCHEDULED`（且 `available_at <= now`）；`dispatch_effect` 本身也检查 `available_at`。由现有 integration worker 每分钟 cron 调用【仓库】。通用 `dispatch_outbox` 是空实现，不会误碰这些行【仓库】。
  - **事件投递**：投递行回到 `pending`，`next_at = now + 退避`，`error = HTTP_RETRY_SCHEDULED`；现有 sweep（`due → prepare → execute`）到点自动重新认领【仓库】。有序路由下，重试中的前序投递仍是 pending，会继续挡住后继（顺序不被打乱）。
- 次数上限（不含首发）：
  - 副作用：外部系统现有字段 `max_retry`（页面“重试”，0–5）；0 = 不自动重试。
  - 事件路由：路由配置 `max_retries`，默认 3，上限 5。本轮自动次数记在 `attempt_started.detail.autoAttempt`；人工重试或预检失败后重新从 1 计。
- 用尽后：
  - 5xx/429：副作用 `failed`（`EXTERNAL_HTTP_<code>`）；事件 `failed`（`HTTP_SUBSCRIBER_REJECTED`，可人工“重试”）。
  - 超时/网络错误：保持“结果未知”（副作用 `unknown`；事件 `unknown`，`HTTP_RESULT_UNKNOWN_REQUIRES_REVIEW`）——对方可能已经收到。
- 先提交认领、再发请求的原则不变：每次发送前 `attempts += 1` 并提交，进程在发送中崩溃不会盲目重发（副作用停在 `CLAIMED_OUTCOME_UNKNOWN`，事件 5 分钟后转未知）【仓库】。
- 行为变化（相对 `283bd89`）：副作用收到 4xx 以前记“结果未知”，现在记“失败”（对方明确拒绝）；2xx 但响应体不合规仍记“结果未知”，不重试。已经发过一次后再次认领时复核失败（定义/授权/地址已变），记 `unknown` 而不是 `rejected`，因为对方可能已收到前一次。

### 3.1 每次尝试的记录（无新迁移）

- 副作用：追加到 outbox 行 `payload.attempts[]`：`{attempt, at, httpStatus, error, retryable, signed, elapsedMs}`；`attempts` 列 = 已发送次数。
- 事件：沿用 `event_delivery_record`：每次 `attempt_started`（含 `routeFingerprint`、`autoAttempt`）+ `attempt_finished`（重试排队时 `state=pending, error=HTTP_RETRY_SCHEDULED, detail={retryable, signed, nextAt}`，`result.httpStatus`）。
- 不加迁移的原因：`writeback_call.kind` 是带 CHECK 的枚举列，加类型要迁移，且多处查询按 `kind == write` 取单条记录，混入副作用尝试有回归风险；现有 JSON/记录表已能完整留痕。`alembic heads` = `notify_0022`，本次未新增迁移。

## 4. 为什么写回提交（execution.py）只签名不重发

`dispatch_one` 是业务写入：结果未知时转 `reconcile_task`，设计明确“只对账，不重发业务写入”【仓库】。所以这里只加签名（密钥不可用时按配置错误失败，不发送），不加自动重试。注意 `frontend/docs/backend/action-event-api.md` §2 表里写的“明确失败用同一幂等键自动重试 maxRetry 次”与现有代码不符（代码只发 1 次）——本次未改，作为已知差异记录。

## 5. 手工处置

- 事件投递：已有 `POST /event-deliveries/{id}:dispose`（received/skip/retry/supplement）【仓库】，自动重试用尽后照常进入异常收件箱。
- 动作副作用：**没有**人工处置/重试入口。要做需新增权限点、版本号/审计列（outbox 行没有）和页面，超出“小改”，记为缺口。

## 6. 入站（不改）

`POST /internal/v1/events` 仍用按本体的静态 Bearer 令牌（`RENGU_INTERNAL_EVENT_TOKEN(S)`）【仓库】。后续由本体小白改为 `require_api_scope("events:ingest")`（服务账号 + 授权范围，审计记调用方），过渡期保留静态令牌。

## 7. 缺口 / 未做

1. 动作副作用无人工处置/重试入口（§5）。
2. 副作用的逐次尝试只在 outbox 行 JSON 里，外部系统“调用记录”页看不到。
3. 适配器 GET 查询/探测不签名。
4. 前端未改：外部系统页（`ExternalSystems.vue`）表单本来没有凭证字段，密钥只能走环境配置。
5. 事件路由 `max_retries` 只在环境 JSON 里配，页面不展示。
6. 预检失败（配置抖动）会重置本轮自动次数（§3），可能多重试一轮。
7. 签名头名、签名串格式、5 分钟时间窗都需与接入方确认。

## 8. 第 2 期设计（不在本次实现）

> 以下沿用 2026-10-27 只读草稿（基于 `4a1293b`），本节内的“§1–§5”指 8.1–8.5。

### 8.0 现状（设计的起点）

- 动作流程【仓库】：`POST /action-runs:stage`（待确认）→ `:confirm`（审批中；若动作配置 `approvalConfigured && approvalRequired=false` 则直接执行）→ `:approve` → 执行 → 成功 / 失败 / 结果未知；另有 `:withdraw`、`:resubmit`、`:reconcile`（`dynamic/router.py:258-309`、`dynamic/service.py:784-809`）。
- 只读试运行【仓库】：`POST /action-types/{id}:dry-run`（`action/router.py:45`）。
- API 密钥【仓库】：只有“元数据读取”、仅 GET，权限 = 创建时快照 ∩ 绑定用户当前权限，不带行数据（`admin/api_keys.py:226-264`）。
- 能力目录 `/meta`【仓库】：还没有统一接口，各模块零散 `/xxx/meta`；由静态新做。

### 8.1 动作类型「执行策略」

#### 8.1.1 字段（动作定义新增 `policy` 段，经变更单发布，写回挂 `action/writeback.py` 新段）【推断】

```json
{
  "mode": "suggest | confirm | auto",          // 只给建议 / 人工确认 / 在上限内自动执行
  "callers": ["人", "服务账号", "AI"],           // 允许的调用方类型
  "limits": {
    "dailyCount": 50,                           // 每本体每自然日（Asia/Shanghai）最多自动执行次数
    "perCallerDailyCount": 20,                  // 每个服务账号 / AI 身份每日上限
    "amount": {"param": "qty", "max": 500},     // 金额或数量上限：指定参数 API + 单次上限
    "objects": {"kind": "门店", "filter": "..."}  // 允许对象：对象类型 + 行范围表达式（复用 row_filter 语法）
  },
  "overflow": "stage"                           // 超限一律转暂存（固定值，不提供“拒绝”以外的放宽）
}
```

- 默认值：存量动作 `mode=confirm`、`callers=["人"]`，行为与现在一致；**不回填、不改历史**。
- `auto` 必须同时满足：动作意图评测达标（§4，记录在定义里 `evalPassedAt`/`evalRunId`，由评测结果写入，人不可手填）+ 老板批准。未达标时发布校验 409 拒绝。
- 与现有 `approvalRequired` 的关系：`policy` 管“谁能不经人确认发起”，`approvalRequired` 仍管“确认后要不要审批”，两者正交。`auto` 等价于“系统代为确认 + 免审”，因此 `auto` 要求 `approvalRequired=false`，否则发布校验报错。

#### 8.1.2 执行分流（接现有接口，不另起流程）【推断】

调用方统一走 `POST /actions/{code}:execute`（机器接口，§2）或人工入口：

1. 解析调用方（人 / 服务账号 / AI）→ 不在 `callers` 里 → 403 `CALLER_NOT_ALLOWED`。
2. 先跑现有 dry-run 校验（提交条件、参数、行权限）；失败直接返回校验结果，不建记录。
3. 按 `mode`：
   - `suggest`：只返回建议 + 试运行结果，**不建 ActionRun**；决策记录由动态的决策接口写。
   - `confirm`：调用现有 `stage`，返回待确认记录，由人在动作中心 `:confirm` / `:approve`。
   - `auto`：在同一事务里检查 `limits`（计数用 `pg_advisory_xact_lock` + 新计数表，按 Asia/Shanghai 自然日）；未超限 → `stage` 后由系统身份 `confirm`，时间线记“按执行策略自动执行（调用方 X，额度 n/N）”；**超限 / 不在允许对象内 → 照常 stage 成待确认**，返回 `status=待确认, overflowReason`。
4. 所有分支写审计：调用方类型、服务账号编号、策略版本（动作类型版本号）、额度快照。

#### 8.1.3 参考【官方】

- Palantir 动作类型自带“提交条件（Submission criteria）”，执行前校验，失败不执行。
- Palantir Actions API 支持“仅校验不执行”的调用模式，以及批量执行。
- Palantir Automate 可以在条件满足时自动执行动作，也可以只发通知让人处理。

### 8.2 机器接口（给程序 / AI / 问数用）

前缀 `/api/ontology/v1/machine`【推断】，鉴权：`Authorization: Bearer rg_…`（服务账号密钥，由本体小白扩展 `authenticate_key` 的 scope，如 `对象读取`、`函数调用`、`动作执行`）。我只读取服务账号上下文：`callerKind`、`accountId`、`allowedActionTypes`、行范围。

| 接口 | 作用 | 复用 |
|---|---|---|
| `POST /machine/objects:query` | `{objectType, filter, fields, page, size≤100}` 查对象，按服务账号行范围过滤 | 现有对象搜索 / `row_filter` |
| `POST /machine/functions/{code}:call` | 按已发布函数版本调用，`{version, input}` | `functions/router.py:52` 的运行时；版本必须固定 |
| `POST /machine/actions/{code}:dry-run` | 只校验，返回条件逐条结果、预计修改 | `action/router.py:45` |
| `POST /machine/actions/{code}:execute` | 按 §1.2 分流；必须带 `Idempotency-Key` | `dynamic` 的 `stage/confirm` |
| `GET  /machine/action-runs/{id}` | 查执行结果 | `dynamic/router.py:279` |

统一响应：
```json
{"ok": true, "status": "建议|待确认|执行中|成功|失败|结果未知",
 "runId": "AR-…", "checks": [{"name": "库存低于安全线", "passed": true, "value": 12}],
 "edits": [{"objectType": "门店商品", "id": "…", "field": "补货量", "before": 0, "after": 40}],
 "overflowReason": null, "policyVersion": 3, "traceId": "…"}
```
错误沿用现有 `AppError` 结构（`code`、`message`、`details.fields`），加 `retryable` 布尔。

边界：不允许 `x-demo-as` / `previewAs`（同现有密钥规则）；同一 `Idempotency-Key` + 相同内容返回原记录（复用 stage 幂等）；不提供批量执行（第一档不做）。

参考【官方】：Palantir OSDK 按本体生成带类型的 SDK，覆盖对象查询、动作执行和函数调用，第三方应用用 OAuth 客户端和授权范围（scopes）访问。

### 8.3 AI 工具导出

- 来源：静态的能力目录（基于 `/meta` 新做）。我负责把其中“动作 + 函数”转成工具说明；对象查询工具由静态目录直接给。
- 生成规则【推断】：每个 `callers` 含 `AI` 且已发布的动作类型 → 两个工具 `{code}__dry_run`、`{code}__execute`；参数 JSON Schema 来自动作参数（类型、必填、枚举、对象引用 → `{objectType, id}`）；`description` = 动作说明 + 提交条件的自然语言 + 执行策略（如“超过 500 件会转人工确认”）。函数同理，固定版本号。
- 只导出当前服务账号可用的子集（按 `allowedActionTypes` 过滤），不导出未发布 / 已退役 / `suggest` 以外不允许 AI 的动作。
- 输出接口：`GET /machine/tools?format=openai|mcp`；MCP 包装（server 名、传输方式）和问数一起定。
- 参考【官方】：Palantir AIP（AIP Logic、Agent Studio）可以把本体动作、函数、对象查询配置为模型可调用的工具。

### 8.4 动作意图评测题（放进问数调优题库）

格式【推断】，与问数现有题目同表，`kind=action_intent`：
```json
{
  "id": "AI-0001", "kind": "action_intent", "ontologyId": 1,
  "question": "SO20261025… 这单的门店库存不够，帮我补 40 件",
  "context": {"objectType": "门店商品", "objectId": "…"},
  "expect": {
    "action": "replenish_stock",          // 期望动作；null 表示应拒绝 / 不做动作
    "params": {"qty": 40},                // 必须完全一致的参数
    "paramsLoose": ["note"],              // 只要求存在、不比值的参数
    "behavior": "execute|clarify|refuse"  // 期望行为：执行 / 反问 / 拒绝
  },
  "tags": ["数量", "单对象"]
}
```
- 打分：动作选对率、参数全对率、反问/拒答正确率，**按动作类型分别统计**。
- 门槛【推断】：某动作的题数 ≥20 且选对率、参数全对率都 ≥95%、错误执行 0 次，才允许该动作 `mode=auto`；分数回落低于门槛自动降回 `confirm` 并通知负责人。
- 评测只跑 dry-run，不真实执行。
- 参考【官方】：Palantir AIP Evals 用测试用例集给 AIP 逻辑打分，上线前、修改后都要跑。

### 8.5 依赖与顺序

1. 本体小白：服务账号 scope 扩展（对象读取 / 函数调用 / 动作执行）和调用方审计。
2. 我：`policy` 段 + 写回 + 发布校验 → 执行分流 → 机器接口。
3. 静态：能力目录 → 我：工具导出 → 问数：MCP 包装 + 动作意图题。
4. 动态：决策接口调用 `machine/actions/{code}:dry-run`，`suggest` 结果写决策记录。

需要的新表 / 迁移（开工前两位非作者评审）：`action_policy_counter`（按日 / 按调用方计数），`policy` 段存在动作定义现有 JSON 列还是新表，开工时再定。
