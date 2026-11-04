# 本体平台 AI 集成指南

> 面向 AI 代理、自动化脚本和外部软件开发者。目标：把一个已有软件的数据、事件和可执行动作接入本体平台，并让 AI 通过机器 API 或 MCP 稳定使用。
>
> 适用范围：本仓库的 FastAPI 后端（默认 `http://127.0.0.1:8000`）。所有路径都以 `/api/<module>/v1` 开头，MCP 和内部事件入口除外。

---

## 0. 给 AI 的硬规则（先读这一段）

1. **不要直接连接业务数据库或平台数据库来读数据。** 运行时只走本指南中的 HTTP API 或 MCP。平台只查询已经登记、发布、绑定并授权的对象。
2. **每个业务请求都必须带 `X-Ontology-Id`。** 它是最小工作空间边界；省略时默认 `1`，但生产集成必须显式传。
3. **机器调用使用 Bearer API Key。** 人类浏览器使用登录 Cookie；API Key 形如 `rg_live_...` / `rg_test_...`，一个密钥只应授予完成本集成所需的最小 scope。
4. **动作执行必须带 `Idempotency-Key`。** 同一个键重复提交只应产生一个执行记录。不要把“已提交”当成“已成功”，必须查询 `runId`。
5. **MCP 的动作工具只按动作策略执行。** `auto` 可能直接执行，`confirm` 只生成待人工确认的记录，`suggest` 不导出。问数（`qa_ask`）永远只给建议，不执行。
6. **事件写入必须使用 `eventId` 幂等。** 同一 `eventId` 加同一信封只会入站一次；信封内容不同会返回 `409 EVENT_ID_CONFLICT`。
7. **不要猜对象、指标、动作、事件或参数的编码。** 先调用能力目录 `GET /api/ontology/v1/meta/capabilities`，使用其中返回的 `id` / `api` / `params` / `payload`。
8. **定义变更后重新发现能力。** 保存定义会立即生效，但正式发布（formal release）和事件投递有版本保护；变更后重新调用能力目录和 `releases/current`。
9. **不要用轮询扫描全表当实时同步。** 实时读取使用受控绑定/CDC；没有 CDC 的数据源先做平台本地镜像并登记受控绑定。
10. **结果未知时停止并人工对账。** 外部系统超时或 `结果未知` 不得盲目重试；用动作详情/对账接口确认。

---

## 1. 系统模型

一次集成涉及以下对象：

| 概念 | 含义 | 对 AI 的接口 |
|---|---|---|
| 工作空间 / 本体 | 一个独立的业务语义空间，数字编号如 `6`；名称“1030”不是编号 | `X-Ontology-Id` |
| 数据连接 | 一个只读源库连接（达梦、PostgreSQL、MySQL、金仓等） | `/api/data/v1/datasources` |
| 对象类型 | 业务实体（如平台实例、探测事件） | capabilities `objects[]` |
| 属性 | 对象字段，带 API 名、类型、主键/标题/维度标记 | capabilities `objects[].properties[]` |
| 关系类型 | 对象之间的有向关系 | `/api/ontology/v1/link-types` / capabilities |
| 受控绑定 | 平台可查询的物理表授权；分平台本地表和达梦外部只读绑定 | `/api/data/v1/governed-bindings`、`/external-read-bindings` |
| CDC | 达梦捕获、MySQL已提交binlog到平台镜像；视图只支持已验证身份的镜像 | `/api/data/v1/external-read-bindings/{code}/cdc/*` |
| 指标 | 固定版本聚合及组合/窗口程序；无时间指标支持当前快照计算 | capabilities `metrics[]` |
| 函数 | 本地受限计算或部署授权的远程纯计算，版本与合同固定 | capabilities `functions[]` |
| 动作类型 | 可对某对象执行的业务动作，带参数、策略、外部写回 | capabilities `actions[]` |
| 动作执行 | 一次动作的持久记录（暂存/执行中/成功/失败/结果未知/已对账） | `/machine/actions/{id}:execute`、`/machine/action-runs/{runId}` |
| 事件类型 | 外部软件产生的事件定义，带载荷 schema、去重、订阅 | capabilities `events[]` |
| 事件入站 | 生产方推送事件 | `POST /internal/v1/events` |
| 订阅 / 规则 | 事件触发规则、告警或动作 | capabilities `rules[]` |
| 正式发布 | 对已发布定义做完整校验并记录快照 | `/api/ontology/v1/releases/current`、`/releases:formal` |

集成有四个独立通道：

```text
软件数据库 ──只读连接──> 对象定义 ──受控绑定/CDC──> 平台可查询
软件业务 API <──动作写回──── 动作类型 <──人/规则/AI── 动作执行
软件事件   ──HTTP 入站──> 事件类型 ──订阅/规则──> 告警/动作
AI / 自动化 ──MCP 或机器 API──> 对象查询 / 指标 / 问数 / 动作 / 事件 / 函数
```

---

## 2. 接入前准备

### 2.1 环境与工作空间

- 后端：`http://<host>:8000`；交互式接口文档：`/docs`；前端开发代理：`http://127.0.0.1:5280`。
- 健康检查：`GET /healthz`、`GET /readyz`。
- 查询当前工作空间编号：

```http
GET /api/ontology/v1/workspaces/current
X-Ontology-Id: 6
```

返回的 `id` 是数字编号，集成时写入 `X-Ontology-Id`。工作空间名称、页面上的“1030”等不是 API 编号。

### 2.2 权限

- 工作空间成员角色：`owner` / `editor` / `viewer`。
- 数据源、对象、绑定、动作、事件和发布需要在对应页面有功能权限；所有写操作还需要工作空间非只读。
- 机器调用使用 API Key。API Key 绑定一个用户或服务账号，实际权限是“密钥 scope”与“绑定角色权限”的交集；行级范围也按绑定角色执行。

### 2.3 创建 API Key

只允许管理员在工作空间内创建。`POST /api/admin/v1/api-keys`：

```json
{
  "name": "erp-integration",
  "env": "生产",
  "scopes": [
    "meta:read",
    "objects:read",
    "actions:dry-run",
    "actions:execute",
    "events:ingest",
    "functions:invoke",
    "dynamic:read"
  ],
  "binding": { "kind": "服务账号", "serviceAccountId": "SA-..." },
  "lifeDays": 90,
  "actionTypes": ["dispatch_destroyer"],
  "eventTypes": ["SubmarineDetected"],
  "functionTypes": ["FN-1030-001"],
  "ratePerMinute": 120,
  "quotaPerDay": 10000,
  "ipAllow": ["10.0.0.0/8"],
  "note": "ERP 只读数据 + 动作执行 + 事件上报"
}
```

要点：

- `name` 只能是小写字母、数字、连字符，3–40 位。
- `env` 只能是 `生产` 或 `测试`；分别生成 `rg_live_` / `rg_test_` 前缀。
- `lifeDays` 只能是 `0 / 30 / 90 / 180 / 365`。
- `actionTypes` / `eventTypes` / `functionTypes` 为空列表表示“全部拒绝”，不传表示“该 scope 下全部允许”。动作/事件/函数白名单分别要求 `actions:*` / `events:ingest` / `functions:invoke` scope。
- 响应中的 `secret` 只返回一次；服务端只能保存哈希。丢失后只能轮换或重新签发。
- 生产集成建议使用服务账号，不要用个人账号密钥。

### 2.4 发现能力目录

```http
GET /api/ontology/v1/meta/capabilities
Authorization: Bearer rg_live_...
X-Ontology-Id: 6
```

返回：

```json
{
  "notes": ["..."],
  "omitted": [],
  "objects": [
    {
      "id": "PltPlatform",
      "api": "PltPlatform",
      "name": "平台实例",
      "rowAccess": "all",
      "properties": [
        {"api":"platform_id","name":"平台编号","type":"string","pk":true,"title":false},
        {"api":"platform_name","name":"平台名称","type":"string","pk":false,"title":true}
      ]
    }
  ],
  "metrics": [
    {"id":"first_detect_km","name":"目标发现距离","version":"v3","dims":["推演阶段","手段"],"trialReady":true}
  ],
  "actions": [
    {
      "id":"ddg_turn",
      "name":"驱逐舰转向",
      "object":"PltPlatform",
      "params":[{"api":"ddg_id","name":"驱逐舰编号","type":"字符串","required":true}],
      "policy":{"mode":"auto"},
      "machine":{
        "execute":{"method":"POST","path":"/api/ontology/v1/machine/actions/ddg_turn:execute","scope":"actions:execute","idempotencyHeader":"Idempotency-Key"}
      }
    }
  ],
  "events": [
    {
      "id":"SubmarineDetected",
      "name":"潜艇目标发现",
      "payload":[{"api":"detect_id","name":"事件编号","type":"字符串","required":true}],
      "ingest":{"method":"POST","path":"/internal/v1/events","scope":"events:ingest","accepting":true}
    }
  ],
  "functions": [
    {"id":"FN-1030-001","name":"cross_locate","inputs":[{"name":"bearing_mdeg","type":"Any"}]}
  ],
  "rules": [
    {"id":"R-001","name":"阶段门控：确认后前出","trigger":"事件","actionType":"dispatch_destroyer"}
  ]
}
```

约定：

- 对象用 `objects[].id` 作为 `objectType`。
- 指标用 `metrics[].id`；`trialReady:false` 的指标不能直接查询。
- 动作参数来自 `actions[].params`，动作目标对象类型来自 `actions[].object`。
- 事件只导出 `ingest.accepting:true` 的外来源类型；载荷字段和必填项来自 `events[].payload`。
- 若某分节无权限，目录里可能没有该键或出现在 `omitted`；不要假设所有分节都存在。

`GET /api/ontology/v1/meta/capabilities` 需要 `meta:read`。对象查询还需要 `objects:read`；动作/事件/函数分别需要对应 scope。

---

## 3. 五分钟路径：让 AI 能查你的软件数据

### 3.1 新建数据连接

先测试未保存的连接参数：

```http
POST /api/data/v1/datasources:test
Content-Type: application/json

{
  "type": "达梦 DM8",
  "host": "192.168.3.19",
  "port": 5236,
  "database": "smrw",
  "user": "SYSDBA",
  "password": "...",
  "compatMode": "Oracle",
  "ssl": false
}
```

支持类型：`PostgreSQL`、`达梦 DM8`、`人大金仓 KingbaseES`、`MySQL`、`ERP`。达梦默认 `ssl:false`，其余类型默认 `ssl:true`。

保存连接：

```http
POST /api/data/v1/datasources
X-Ontology-Id: 6
Content-Type: application/json

{
  "name": "ERP 生产库",
  "type": "达梦 DM8",
  "host": "192.168.3.19",
  "port": 5236,
  "database": "smrw",
  "user": "SYSDBA",
  "password": "...",
  "mode": "实时（CDC）",
  "owner": "王敏",
  "ssl": false
}
```

`mode` 可选：`定时增量`、`定时全量`、`实时（CDC）`、`手动`。达梦以外的数据源如果启用了镜像定时同步，只有工作空间 owner 能操作。

刷新库表目录：

```http
POST /api/data/v1/datasources/{dsId}/tables:refresh
```

或测试已保存连接：`POST /api/data/v1/datasources/{dsId}:test`。

### 3.2 预览并导入对象

```http
POST /api/data/v1/datasources/{dsId}/import-preview
Content-Type: application/json

{
  "schema": "smrw",
  "tables": ["PLT_PLATFORM"],
  "sampleValues": true
}
```

返回 `fingerprint`、`proposal.objectTypes[]`（表、主键、属性）和 `proposal.linkTypes[]`（根据外键生成的关系）。`sampleValues:true` 会只读采样低基数字段，用来建议维度和筛选，不会写源库。多表导入时，`objectTypes` 必须覆盖预览中的每一张表。

正式导入并发布：

```http
POST /api/data/v1/datasources/{dsId}/import-to-cr
Content-Type: application/json

{
  "schema": "smrw",
  "tables": ["PLT_PLATFORM"],
  "sampleValues": true,
  "fingerprint": "<预览返回的 64 位指纹>",
  "objectTypes": [
    {
      "table": "PLT_PLATFORM",
      "id": "PltPlatform",
      "name": "平台实例",
      "api": "PltPlatform",
      "owner": "王敏",
      "pk": "PLATFORM_ID",
      "titleField": "PLATFORM_NAME"
    }
  ],
  "includeLinks": false,
  "publish": true,
  "releaseLockVersion": 318,
  "title": "接入 ERP 平台实例",
  "reason": "AI 集成初始化"
}
```

- `publish:true` 会保存并生成一个新的工作版本，`releaseLockVersion` 必须等于 `GET /api/ontology/v1/releases/current` 的 `lockVersion`，否则 `409 VERSION_CONFLICT`。
- `publish:false` 只生成变更单草稿，需要之后在“变更与发布”里处理。
- `objectTypes[].id/api` 必须唯一，且不能与现有对象或未发布变更单冲突。
- 导入只生成对象/属性/关系定义，不会自动授予平台查询权限。还需要 3.3 的受控绑定。

### 3.3 受控绑定与实时同步

平台运行时只查询“受控表”。两条路径：

#### A. 达梦 DM8：外部只读绑定 + 触发器 CDC（推荐实时）

对象必须已发布，`source_table` 形如 `smrw.PLT_PLATFORM`，且绑定唯一达梦数据源。

1. 保存只读绑定：

```http
PUT /api/data/v1/external-read-bindings/PltPlatform
Content-Type: application/json

{
  "version": 0,
  "readColumns": ["PLATFORM_ID", "PLATFORM_NAME", "SIDE", "STATUS", "LON", "LAT"]
}
```

`version` 从 `GET /api/data/v1/external-read-bindings/PltPlatform` 读取：首次为 `0`。读取列必须来自对象的公开属性映射，且包含主键。

2. 探测、预览安装计划、安装、启用：

```http
GET  /api/data/v1/external-read-bindings/PltPlatform/cdc
POST /api/data/v1/external-read-bindings/PltPlatform/cdc/probe
POST /api/data/v1/external-read-bindings/PltPlatform/cdc/plan
     {"version": 1}
POST /api/data/v1/external-read-bindings/PltPlatform/cdc/install
     {"version": 1, "planDigest": "<plan 返回的 64 位摘要>"}
POST /api/data/v1/external-read-bindings/PltPlatform/cdc/enable
     {"version": 1, "reinitialize": true}
```

- 安装会向达梦源库安装私有触发器/日志对象，需要源库 DDL 权限；这是“需要源端写”的唯一场景，平台不写业务表。
- `enable` 会建立初始快照，随后进入 `catching_up`，再转为实时消费。
- 绑定列变化后必须重新 `plan` → `install` → 重新建立基线。
- 详细说明见 `docs/26-10-31-cdc-deployment-ai-guide.md` 和 `docs/26-10-31-cdc-live-verification.md`。

#### B. 非达梦 / 已有平台本地镜像：受控绑定

如果数据已经落到平台 PostgreSQL 的 `public.*` 表（你自己的 ETL、批同步或平台镜像），给对象登记受控读取列：

```http
PUT /api/data/v1/governed-bindings/PltPlatform
Content-Type: application/json

{
  "readColumns": ["platform_id", "platform_name", "side", "status"],
  "writeColumns": [],
  "bindingVersion": "<详情返回的 bindingVersion>"
}
```

- 对象 `source_table` 必须是 `public.<table>`。
- 需要 owner 角色和对象管理权限。
- `readColumns` 是列白名单；平台不会查询未登记列。
- 这条路径是“平台本地表”，不支持直接查询远端 PostgreSQL/MySQL；远端数据必须先镜像到平台库。

### 3.4 验证可查询

```http
POST /api/ontology/v1/machine/objects:query
Authorization: Bearer rg_live_...
X-Ontology-Id: 6
Content-Type: application/json

{
  "objectType": "PltPlatform",
  "kw": "DDG-101",
  "filters": {"status": ["UNDERWAY"]},
  "sort": "-last_update",
  "page": 1,
  "size": 20
}
```

成功时 `bound:true`，`items[]` 含 `id`、`title`、`values`。如果 `bound:false`，说明对象没有可查询的受控表，回到 3.3。

---

## 4. 运行时机器 API

### 4.1 对象查询

```http
POST /api/ontology/v1/machine/objects:query
Authorization: Bearer rg_live_...
X-Ontology-Id: 6
Content-Type: application/json

{
  "objectType": "EvtDetection",
  "kw": "",
  "filters": {
    "false_alarm": [0],
    "detect_time": {"min": "2026-11-01", "max": "2026-11-02"},
    "distance_km": {"min": 0, "max": 50}
  },
  "sort": "-detect_time",
  "page": 1,
  "size": 200,
  "withFacets": false
}
```

规则：

- `filters` 的键必须是对象属性 API 名；普通属性给取值数组，数值/时间给 `{min,max}` 闭区间；日期只给日期时包含当天。
- `sort` 前缀 `-` 表示倒序；`page>=1`，`size<=200`。
- 行级范围由调用身份决定；没有授权的对象/行不会返回。
- `withFacets` 默认 `true`，MCP 会去掉分面；机器集成通常传 `false`。
- 对象查询 scope：`objects:read`。

### 4.2 指标查询

```http
POST /api/qa/v1/mcp  ->  tools/call  metric_query
```

或直接调用平台评估器（内部接口，推荐用 MCP）：

```text
metric_query(metric="first_detect_km", days=30, period="day")
```

返回：

```json
{
  "id": "first_detect_km",
  "version": "v3",
  "formula": "MIN(EvtDetection.distance_km)",
  "rows": [{"date": "2026-11-02", "value": "29.1"}],
  "dataCutoff": "2026-11-02 20:25",
  "scope": "2026-10-04 至 2026-11-02 · 时间字段 ... · 当前用户可见数据"
}
```

- 只支持单对象聚合 + 四则运算；跨对象、时序差、几何并集等口径在能力目录里 `trialReady:false`，不可查询。
- `days` 1–93；`period` 为 `day` / `week` / `month`。
- 指标查询 scope：`meta:read`（MCP 的 `metric_query` 工具）。

语义层导出 `GET /api/ontology/v1/exports/metricflow` 现在保留无时间字段的快照指标，
通过平台内置确定性编译器汇总，指标元数据 `config.meta.rengu.hasTimeField=false`。
此类指标不能请求日期窗口或 `metric_time`。时间查询按所选指标及全部派生分项的
`agg_time_dimension` 校验，同一对象的多个指标不能相互借用时间字段。
已同步、已登记的受控镜像沿现有查询守卫执行，专用远端只读连接仍须先同步为受控镜像。

### 4.3 自然语言问数（MCP `qa_ask`）

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "qa_ask",
    "arguments": {"question": "当前有哪些平台，蓝方潜艇在哪里？"}
  }
}
```

返回 `kind`（`answer` / `denied` / `clarify`）、`answer` 卡片和 `rowCount`。它只读、不建会话、不执行动作。动作类问题只给建议，`canStage:false`。API Key 没有 `dynamic:read` 时，回答中的告警、规则命中和动态摘要会被自动去掉。

适合：

- 不确定该用哪个对象/指标时做语义定位。
- 需要平台把自然语言转换成受控 SQL/指标查询。
- 需要解释口径、引用业务知识。

不适合：

- 批量、高频、确定字段的取数；这种情况用对象查询或指标查询。
- 任何写操作；写操作走动作执行或事件入站。

### 4.4 动作执行

先确认动作在能力目录中存在且 `policy.mode` 不是 `suggest`。执行：

```http
POST /api/ontology/v1/machine/actions/ddg_turn:execute
Authorization: Bearer rg_live_...
X-Ontology-Id: 6
Idempotency-Key: erp-20261102-0001
Content-Type: application/json

{
  "objKind": "PltPlatform",
  "objId": "DDG-101",
  "params": {"ddg_id": "DDG-101", "heading_deg": 90}
}
```

- `objKind` / `objId` 是目标对象类型和业务编号，不是数据库主键。
- `params` 按 `actions[].params` 的 API 名填写；必填参数缺失会返回 `422`。
- `Idempotency-Key` 必填；同一键重复请求返回同一个执行记录（HTTP 200 而不是 201）。
- 响应：

```json
{
  "ok": true,
  "status": "待确认",
  "runId": "ACT-20261102-0096",
  "policyMode": "confirm",
  "typeVersion": "v3",
  "checks": {"passed": true, "checked": 3},
  "edits": [],
  "humanChecks": [],
  "retryable": false
}
```

状态含义：`待确认`、`审批中`、`执行中`、`成功`、`失败`、`结果未知`、`已撤回`、`已对账`。

动作策略：

| mode | 机器执行行为 |
|---|---|
| `auto` | 在日/阶段等限制内直接执行；超限或需审批转人工确认 |
| `confirm` | 只生成待确认记录；人工在动作中心/问数中确认 |
| `suggest` | 不导出为工具；机器执行会被拒绝 |

机器调用没有“审批”接口。`confirm` 或需审批的动作必须由有权限的人在平台确认。集成端应轮询：

```http
GET /api/ontology/v1/machine/action-runs/{runId}
Authorization: Bearer rg_live_...
X-Ontology-Id: 6
```

直到状态变为 `成功` / `失败` / `结果未知` / `已对账`。`结果未知` 必须人工对账，不要自动重试。

### 4.5 事件入站

```http
POST /internal/v1/events
Authorization: Bearer rg_live_...
X-Ontology-Id: 6
Content-Type: application/json

{
  "eventId": "erp-order-20261102-0001",
  "eventType": "SubmarineDetected",
  "occurredAt": "2026-11-02T20:30:00+08:00",
  "source": "erp",
  "objectType": "EvtDetection",
  "objectId": "DET-20261102203000",
  "payload": {
    "detect_id": "DET-20261102203000",
    "sensor_id": "SMK-N01",
    "detect_type": "PASSIVE",
    "bearing_mdeg": 181700,
    "range_m": 36300,
    "confidence_pct": 60,
    "false_alarm": false,
    "detect_time": "2026-11-02 20:30:00"
  },
  "dedupKey": "DET-20261102203000",
  "traceId": "trace-erp-0001"
}
```

规则：

- `eventId` 全局唯一、最长 64；同一 `eventId` + 同一信封返回 `{"eventId":"...","deduped":true}`；内容变化返回 `409 EVENT_ID_CONFLICT`。
- `eventType` 用事件类型的 code 或 api；必须是**外部来源**、已发布、`ingest.accepting:true`。
- `objectType` 必须匹配事件类型绑定的对象。
- `payload` 按 `events[].payload` 校验类型和必填；数值不能是字符串（`"60"` 不会自动转 `60`）。
- 信封最大 64 KiB。
- 事件按 `dedupKey` 窗口去重；达到订阅方条件后会触发规则/告警/动作。没有订阅方的事件是惰性的，不会算失败。
- 事件投递是“至少一次”，不保证顺序；外部写入必须幂等。
- 事件定义或发布版本变化后，旧快照的投递可能返回 `EVENT_RELEASE_CHANGED_REQUIRES_REVIEW`；重新发布后再继续。
- scope：`events:ingest`；API Key 还可以用 `eventTypes` 白名单限制。

事件不会直接返回规则结果；要看投递和告警，使用平台事件/告警页面或 `/api/ontology/v1/events` 相关接口（人类会话）。机器集成只需要确认入站成功。

### 4.6 函数调用

```http
POST /api/ontology/v1/machine/functions/FN-1030-001:invoke
Authorization: Bearer rg_live_...
X-Ontology-Id: 6
Content-Type: application/json

{
  "input": {
    "bearing_mdeg": 135200,
    "sensor_lon_udeg": 118950000,
    "sensor_lat_udeg": 15080000,
    "range_m": 39300,
    "passive_cep_dm": 700
  }
}
```

- 只允许已发布函数；返回 `output` 和 `ms`。
- 函数是受限纯计算，不能访问网络、文件或数据库。
- scope：`functions:invoke`；可用 `functionTypes` 白名单限制。

---

## 5. MCP 集成

### 5.1 端点与协议

- 地址：`POST /api/qa/v1/mcp`（注意是 `qa` 模块，不是 `/api/ontology/v1`）。
- 协议：MCP Streamable HTTP 的无状态子集，JSON-RPC 2.0；单请求单响应；服务端不发 SSE，`GET /api/qa/v1/mcp` 返回 405。
- 支持方法：`initialize`、`notifications/initialized`（通知无响应）、`ping`、`tools/list`、`tools/call`。
- 协议版本：`2025-06-18`（兼容 `2025-03-26`、`2024-11-05`）。
- 鉴权：入口要求 `meta:read` scope；每个工具再要求自己的 scope。
- 每次请求独立，不使用会话头；所有请求都要带 `Authorization` 和 `X-Ontology-Id`。

### 5.2 客户端配置模板

不同 MCP 客户端字段略有差异；下面是通用的 HTTP 服务器配置。示例见 `docs/ai/mcp-client.example.json`。

```json
{
  "mcpServers": {
    "rengu-ontology": {
      "type": "http",
      "url": "http://127.0.0.1:8000/api/qa/v1/mcp",
      "headers": {
        "Authorization": "Bearer rg_live_REPLACE_ME",
        "X-Ontology-Id": "6"
      }
    }
  }
}
```

- 桌面客户端若不支持远程 HTTP，可用其 `mcp-remote` 或等价桥接把上面的 URL 暴露为 stdio；不要把密钥写进提交到代码仓库的配置。
- 生产使用 HTTPS；密钥放在客户端密钥库/环境变量中。
- 仓库内自带一个无第三方依赖的冒烟脚本：`RENGU_API_KEY=... RENGU_ONTOLOGY_ID=6 python docs/ai/mcp_smoke.py`，可加 `--ask "当前有哪些平台？"` 验证问数工具。

### 5.3 初始化与工具发现

```http
POST /api/qa/v1/mcp
Authorization: Bearer rg_live_...
X-Ontology-Id: 6
Content-Type: application/json

{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"my-agent","version":"1.0"}}}
```

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-06-18",
    "capabilities": {"tools": {"listChanged": false}},
    "serverInfo": {"name": "rengu-qa", "title": "问数", "version": "1.1"}
  }
}
```

然后：

```json
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
```

工具清单由三部分组成：

1. 问数专属：`qa_ask`、`object_query`、`metric_query`。
2. 平台导出的动作工具：`action_*`，按动作 `api` 或 `id` 生成。
3. 平台导出的事件/函数工具：`event_*`、`fn_*`。

不要硬编码工具名；以 `tools/list` 为准。动作工具只在调用方有 `actions:execute`、动作在密钥白名单内、动作策略不是 `suggest` 时出现；事件工具只导出可入站的外来源类型。

### 5.4 工具调用

```json
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"object_query","arguments":{"objectType":"PltPlatform","kw":"DDG-101","size":5}}}
```

成功响应：

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [{"type":"text","text":"{...JSON...}"}],
    "structuredContent": {"objectType":"PltPlatform","items":[...],"total":1},
    "isError": false
  }
}
```

- 问数专属工具同时返回 `content[0].text` 和 `structuredContent`。
- 平台导出工具（动作/事件/函数）把目标接口的 JSON 放进 `content[0].text`；调用方应解析文本 JSON。
- 业务错误以工具结果 `isError:true` 返回（例如 `DATA_NOT_CONFIGURED`、`ACTION_POLICY_SUGGEST_ONLY`）；协议/权限错误返回 JSON-RPC error（`-32001` 表示 scope 不足）。
- 动作工具由 MCP 按“调用方 + 工具 + 参数 + JSON-RPC id”生成 `Idempotency-Key`；不同 JSON-RPC id 可能生成不同的键。需要跨调用严格去重时，直接在应用层使用稳定的业务幂等键，或改调机器接口并自带 `Idempotency-Key`。

### 5.5 用 MCP 完成一次“查 + 办”

```text
1) tools/list
2) tools/call qa_ask {"question":"DDG-101 现在状态如何？"}
3) tools/call object_query {"objectType":"PltPlatform","filters":{"platform_id":["DDG-101"]}}
4) tools/call action_ddgTurn {"objId":"DDG-101","objKind":"PltPlatform","params":{"ddg_id":"DDG-101","heading_deg":90}}
5) 解析返回的 runId；用 GET /api/ontology/v1/machine/action-runs/{runId} 查询最终状态
```

注意：第 4 步的 `action_ddgTurn` 名称只是示例，必须以 `tools/list` 返回的名称为准。`confirm` 策略下它只暂存，必须人工确认。

---

## 6. 外部系统写回与动作

如果软件需要被本体“操作”，需要在平台登记外部系统和动作类型。

### 6.1 新建外部系统

```http
POST /api/data/v1/ext-systems
Content-Type: application/json

{
  "name": "ERP 生产系统",
  "vendor": "SAP",
  "category": "ERP",
  "endpoint": "https://erp.example.com",
  "auth": "OAuth 2.0（客户端凭证）",
  "authDetail": "POST /oauth/token",
  "idempotent": true,
  "idemKey": "Idempotency-Key",
  "timeoutMs": 10000,
  "maxRetry": 0,
  "rateLimit": "10 次/秒",
  "owner": "王敏",
  "env": "生产"
}
```

- `endpoint` 是外部系统根地址；动作接口必须与它同源。
- 不支持幂等时 `idemKey` 存 `-`、`maxRetry` 为 0。
- 测试连通性：`POST /api/data/v1/ext-systems/{sysId}:test`。

### 6.2 登记接口目录

`PUT /api/data/v1/ext-systems/{sysId}/http-endpoints`，带上当前 `version`：

```json
{
  "version": 13,
  "endpoints": [
    {
      "id": "probe_state",
      "name": "状态探测",
      "purpose": "probe",
      "method": "GET",
      "path": "/health"
    },
    {
      "id": "ddg_turn",
      "name": "驱逐舰转向",
      "purpose": "action",
      "method": "POST",
      "path": "/api/platform/execute",
      "httpMapping": {
        "request": [
          {"value": "ddg_turn", "target": "action"},
          {"source": "params.ddg_id", "target": "ddg_id"},
          {"source": "params.heading_deg", "target": "heading_deg"},
          {"value": "EXT_SERVICE_01", "target": "operator"}
        ],
        "response": {
          "status": "status",
          "success": "SUCCESS",
          "failed": "FAILED",
          "unknown": "UNKNOWN",
          "reason": "reason",
          "code": "code",
          "receiptId": "receiptId"
        }
      }
    }
  ]
}
```

- 探测接口只能有一个、GET、无映射；动作接口支持 POST/PUT/PATCH。
- `httpMapping.request` 只允许 `value` 或 `params.<动作参数>`；不要把密钥、SQL 或任意表达式写进映射。
- 保存后给动作类型选择 `httpEndpointId`，并保存外部系统动作授权（`/action-authorizations`）。

### 6.3 动作类型定义

动作类型通过变更单保存。完整的 CR 流程：

```text
POST /api/ontology/v1/change-requests                 建单
POST /api/ontology/v1/change-requests/{cr}/targets    登记动作目标（kind=action, id=动作编码, isNew=true）
PUT  /api/ontology/v1/change-requests/{cr}/items      写 def / params / config / policy 段
POST /api/ontology/v1/change-requests/{cr}:submit
POST /api/ontology/v1/change-requests/{cr}:approve
POST /api/ontology/v1/releases                         发布已通过的变更单
```

动作常用段：

- `def`：`name`、`api`、`group`、`targetKind`、`owner`、`desc`、`approval`、`runners`。
- `params`：每行 `{api,name,type,required,ruleExpr?}`。
- `config`：`sysId`、`idemKey`、`endpoint`、`httpMapping`、`httpEndpointId`，或声明式写回。
- `policy`：`{mode:"auto"|"confirm"|"suggest", limits:{dailyCount}, overflow:"stage"}`。

如果 AI 只需要“集成运行时”，动作定义可以有人在平台 UI 里完成；AI 通过能力目录拿到 `actions[]` 后执行。

---

## 7. 事件与规则

### 7.1 事件类型定义

事件类型也通过变更单定义：`kind=event`，段 `def` / `payload` / `subs` 等。最少需要：

- `code` / `api` / `name` / `source=external` / `object`（绑定对象）/ 状态 `已发布`。
- `payload`：每个字段 `{api,name,type,required,desc}`；类型使用平台类型名（字符串、整数、金额、百分比、小数、布尔、日期、日期时间、枚举）。
- 去重键和窗口：例如 `detect_id` / `1 小时`。
- 订阅：规则订阅（`rule_code`）或页面订阅；没有订阅方的事件是惰性的。

事件类型发布后才会出现在 capability 的 `events[]`，`ingest.accepting` 为 true 时才能写入。

### 7.2 事件投递语义

- 入站成功只代表平台持久化成功，不代表规则/动作已经执行。
- 投递是至少一次；订阅顺序通常“不保证”，需要顺序时在事件类型上声明对象有序，并由平台按对象串行。
- 事件快照绑定发布版本；定义变化或发布切换时，旧快照可能被拒绝，需要重新发布/重新入站。
- 重试有上限：预检永久错误第一次就终止；HTTP 投递只对可重试状态重试，默认预算 3 次；未知结果人工处理，不盲目重发。

---

## 8. 权限、安全与多本体

- 每个请求显式传 `X-Ontology-Id`；一个 API Key 只绑定允许的工作空间。
- API Key 不能使用 `X-Demo-As`、`previewAs`、`?full` 等人类预览参数。
- 行级范围由绑定角色决定；`rowAccess` 为 `all` 表示不限，`scoped`/`none` 由角色范围控制。没有授权时返回空集或 `EVENT_OBJECT_FORBIDDEN`，不要尝试绕过。
- 生产使用 HTTPS；密钥放在服务端密钥库，不写入前端、日志、URL 或仓库。
- 为不同软件签发不同 API Key，按需授予 scope 和类型白名单，设置到期时间、IP 白名单、每分钟/每日配额。
- 删除/停用对象、绑定、动作或事件前，先停止对应集成并吊销密钥。
- 外部系统写回只允许登记的同源接口；不要让平台访问任意 URL。

---

## 9. 错误处理速查

平台错误统一为：

```json
{
  "code": "DATA_NOT_CONFIGURED",
  "message": "外部数据源已接入，但还没有可查询的同步结果",
  "details": {"..." : "..."},
  "requestId": "..."
}
```

常见错误：

| code / 状态 | 含义 | 处理 |
|---|---|---|
| `401 UNAUTHORIZED` | 缺少/失效的 Cookie 或 API Key | 检查 Authorization |
| `403 FORBIDDEN` / `NO_PERMISSION` | scope、工作空间或行级权限不足 | 检查 API Key scope/白名单/角色绑定 |
| `403 ReadOnly` / `READ_ONLY_VIEW` | 工作空间只读或 viewer 写操作 | 用 editor/owner |
| `404 NOT_FOUND` | 对象/动作/事件/工作空间不存在 | 重新发现能力目录 |
| `409 VERSION_CONFLICT` | 版本锁已变化 | 重新读取 `releases/current` 后重试 |
| `409 EVENT_ID_CONFLICT` | 同 eventId 信封不同 | 生成新 eventId，或复用原信封 |
| `409 EVENT_RELEASE_CHANGED_REQUIRES_REVIEW` | 事件快照与当前发布不一致 | 等发布完成/重新发布，不要重放旧事件 |
| `409 ACTION_POLICY_SUGGEST_ONLY` | 动作只允许建议 | 不要执行；转人工 |
| `409 ACTION_STILL_QUEUED` | 动作仍在队列 | 轮询 runId |
| `422 SUBMISSION_CRITERIA_FAILED` | 动作提交条件未满足 | 按 details 检查参数/对象状态 |
| `422 DATA_NOT_CONFIGURED` | 对象没有可查询绑定/同步 | 完成 3.3 或等待 CDC 首轮同步 |
| `422 SQL_REJECTED` | 问数生成的 SQL 被守卫拒绝 | 使用能力目录字段；不要拼 SQL |
| `429 RATE_LIMITED` | API Key 配额/速率超限 | 退避，检查配额 |
| `503 EVENT_INGRESS_UNAVAILABLE` | 该工作空间未配置事件令牌 | 用 API Key `events:ingest` 或联系管理员 |
| MCP `-32001` | 该工具 scope 不足 | 补 API Key scope |

`REQUEST_ID`：响应头 `X-Request-ID` 与错误体 `requestId` 可用于服务端排障。异步 worker 的日志请求 ID 可能是 `-`，应按 `runId` / `eventId` 关联。

---

## 10. AI 验收清单

集成完成后，按顺序跑：

1. `GET /api/ontology/v1/workspaces/current` 得到工作空间编号。
2. `GET /api/ontology/v1/meta/capabilities` 成功，目标对象/指标/动作/事件都在目录中。
3. `object_query` 对目标对象返回 `bound:true` 且至少一条真实业务行。
4. `metric_query` 对至少一个 `trialReady:true` 指标返回数值和 `dataCutoff`。
5. `qa_ask` 回答一个已知问题，返回 `answer` 卡片。
6. 动作以 `Idempotency-Key` 提交一次，拿到 `runId`；轮询到 `成功` 或 `已对账`。
7. 事件用 `eventId` 入站一次，重复提交得到 `deduped:true`；在事件页面看到入站记录。
8. MCP `initialize` + `tools/list` + `tools/call` 全部成功；工具名不硬编码。
9. 用错误密钥/缺 scope 的密钥验证 `401/403/-32001`，确认最小权限生效。
10. 记录本次集成的 API Key、工作空间、动作/事件白名单和验收时间。

---

## 11. 参考接口

| 用途 | 方法与路径 | scope |
|---|---|---|
| 能力目录 | `GET /api/ontology/v1/meta/capabilities` | `meta:read` |
| 当前定义血缘 | `GET /api/ontology/v1/ontology/lineage` | `meta:read`，叠加页面与属性权限 |
| 对象查询 | `POST /api/ontology/v1/machine/objects:query` | `objects:read` |
| 函数调用 | `POST /api/ontology/v1/machine/functions/{code}:invoke` | `functions:invoke` |
| 动作执行 | `POST /api/ontology/v1/machine/actions/{id}:execute` | `actions:execute` |
| 动作结果 | `GET /api/ontology/v1/machine/action-runs/{runId}` | `actions:execute` |
| 事件入站 | `POST /internal/v1/events` | `events:ingest` |
| MCP | `POST /api/qa/v1/mcp` | `meta:read` + 工具 scope |
| 数据连接 | `POST /api/data/v1/datasources` | 页面权限 |
| 导入对象 | `POST /api/data/v1/datasources/{dsId}/import-to-cr` | 页面权限 |
| 外部只读绑定 | `PUT /api/data/v1/external-read-bindings/{code}` | owner |
| CDC 计划/安装/启用 | `POST /api/data/v1/external-read-bindings/{code}/cdc/*` | owner |
| 受控绑定 | `PUT /api/data/v1/governed-bindings/{code}` | owner |
| 外部系统 | `POST /api/data/v1/ext-systems` | 页面权限 |
| 接口目录 | `PUT /api/data/v1/ext-systems/{id}/http-endpoints` | 页面权限 |
| API Key | `POST /api/admin/v1/api-keys` | 管理员 |
| 当前发布 | `GET /api/ontology/v1/releases/current` | 页面权限 |
| 正式发布 | `POST /api/ontology/v1/releases:formal` | 本体管理员 |

更深入的数据同步与发布操作见仓库 `docs/` 下的 CDC、事件和发布专题文档。

2026-11-03增量能力及保留数据迁移步骤见[能力迁移](26-11-03-capability-migration.md)、[受控远程函数](26-11-03-remote-functions.md)、[源数据库写回与MySQL CDC](26-11-03-source-channel-verification.md)及[分类、用途、掩码与多跳权限](26-11-03-governance.md)。配置了用途策略时，客户端还须传真实授权的 `X-Data-Purpose`；用途字符串不替代身份、密钥scope、对象或行级授权。
