# 2026-11-01 用户重启后的公开 HTTP 合同只读核对

当前结论：**本地问数动作卡“生成 → 预校验 → 当前对话暂存 → 明确确认一次 → 成功 → 刷新恢复同卡”闭环已真实通过。** 用户手动重启后，新合同已完成公开只读核对；主会话随后发现并修复预校验错误，正常重启 API 加载补丁，并完成下面的真实页面与受限只读查询核对。API PID 49836、前端 PID 66608 保持运行；此前 PID 23236 未加载与第一次重启 PID 2460 均仅代表历史阶段。外部自动化与军事业务未验证、未执行。

本报告编写者的公开 HTTP 核对时间为 12:56–12:58，未启停服务；后续启动、页面及受限只读查询事实来自主会话和对应 agent，本报告未再发健康请求、读取数据库或运行测试。

## 最终真实页面与只读结果

主会话在新 API 加载修复后继续验收，保留了失败与成功两类结果：

1. **SAR001 预校验修复已生效，既有业务守卫继续拒绝。** 新卡预校验通过；第一次 stage 因告警待处理返回 `STATE_INVALID`。主会话仅在 UI 将合成告警 `AL-e958df05e2d22d25372f9b3c5f68c7c6fcc53a1d04cc365474af8885ded3` claim 给 admin。再次 stage 因旧 `ACT-20261028-0006` 属于不同动作返回 `IDEMPOTENCY_CONFLICT`；没有删除、复用或执行旧 run，SAR001 总 run 仍为 3。该路径没有绕过状态或幂等守卫。
2. **SAR003 新独立意图完成当前对话的一次明确确认。** 主会话另开 QA 会话，选择已有无告警的 SAR003；它只有历史 `ACT-20261028-0002` 问数 mark 成功记录、无非终态 run，没有点击旧卡。新卡 `markRescueInProgress v1` 预校验 passed，当前对话 stage 得到 `ACT-20261101-0001`。确认弹窗明确显示 `RescueIncident/SAR003`、参数 `—`、位置“本体内部”、幂等键 `ont-4-act-20261101-0001`；主会话明确确认一次，同卡显示成功，没有去执行中心再次执行。完整刷新后自动恢复同一会话、同一 run 的成功卡，且无确认按钮。截图：[真实问数动作卡成功](evidence/qa-inline-success-2k-20261101.jpg)。
3. **受限只读查询确认持久状态与唯一新 run。** `ACT-20261101-0001` 为 `succeeded`，类型 `markRescueInProgress v1`，目标 `RescueIncident/SAR003`，`source=qa`、`actor=admin`、`confirmer=U010001`，`alert_id` 与外部系统均为空。北京时间确认于 `13:27:43.873`，完成于 `13:27:43.941`。SAR003 原状态已是“搜救中”，本次后仍为“搜救中”；`lock_version` 从 2 到 3、`updated_at=13:27:43.975`。本轮 API 启动时间 `13:12:59` 后该目标新增 run 恰为 1，QA 持久卡绑定同一 run 为 true。这证明本次动作执行及卡片持久化，**不能写成状态值从其他值转为搜救中，也不能将时间戳差当作性能 benchmark**。

其他新后台只读页面结果：

| 页面流程 | 实际结果与边界 |
| --- | --- |
| 事件异常按原因筛选“本体发布版本不一致” | 正常返回空列表，该 fixture 无匹配样本；未再显示旧“不支持”消息，console error 为空。截图：[原因筛选](evidence/event-reason-filter-loaded-2k-20261101.jpg)。这是空结果路径与新页面合同验证，不能代替有样本错误的精确过滤验证，也未单独核对原始响应中的 `errorFilter` 值 |
| 工作空间目录、w4 对象类型 | 目录正常显示 5 条；w4 对象类型列表 2 条，详情正常，无 500 |
| ws15 本聊天 DM 合成 LiveFixture 镜像 | 列表 101 条，第二页显示 `RGV0011` 等；点击 `SYN0011` 进入 `id=RGV0011` 详情，`Mixedcasevalue=Mixed0011`、`Testnumber=11`、`state=baseline` 与业务自有 ontology/mirror 字段均显示，console error 为空。截图：[镜像只读列表与详情](evidence/mirror-read-loaded-2k-20261101.jpg)。未写源表、未测试 CDC、未执行其他动作 |

以上页面均在实际 2560×1440 视口检查，无 overflow。普通模型仅用于真实 QA 的 3 问，没有批量模型评测、全量后台测试或性能 benchmark。主会话已关闭本轮新建 tab 14/15/16/17，恢复首选本体 1 并取消临时视口，未改原用户 tab。

## 真实动作卡预校验：失败、修复与再次加载

主会话在第一次重启后的真实页面中成功生成动作卡，但 dry-run 沿 `action.checker.DatabaseSubmissionChecker.run → business_edits` 失败：传入的 `SimpleNamespace` 缺少 `ontology_id`。动作卡校验失败，没有 stage、confirm 或执行业务动作。页面证据：[真实预校验失败](evidence/qa-inline-preflight-error-2k-20261101.jpg)。

后端修复仅在 `backend/app/modules/action/checker.py:551` 为该上下文补 `ontology_id=require_ontology_id(self.session)`，从可信事务 scope 获取空间。`backend/scripts/check_qa_inline_actions.py:131` 追加的内存检查通过，覆盖事务空间为 17 时不采上下文/用户的 999，映射错误仍继续阻止；Python 内存编译与限定 diff 检查通过。以上为修复 agent 反馈的局部离线证据，本报告未重跑。

前端修复使明确 `action.dryRun.passed=false` 的卡不能 stage 或确认；已有 run 仍只允许 GET/刷新，失败的待确认卡不能确认。对应局部前端检查通过。真实失败后未通过 UI 发起 stage、confirm 或执行。

用户手动重启已改变此前的加载阻断状态；主会话随后按已核查的原启动方式（`app.main`、uvicorn、host `127.0.0.1`、port `8000`、`--env-file .env`）正常重启一次加载新补丁成功。启动 helper PID 53804，实际 API listener PID **49836**，启动时间 **2026-11-01 13:12:59**，晚于 `checker.py` 修改时间 **13:07:29**。主会话反馈 `/readyz` 返回 200、`{"status":"ok","db":"ok"}`，`.env` 的 SHA-256 前后未变；前端 PID 66608 未动，没有额外 worker。

上述新进程与时间顺序支持补丁已正常重新加载；最终真实动作卡的预校验、确认、执行与刷新恢复结果见前节。局部断言与就绪检查本身仍不能替代这些真实业务证据。

## 第一次用户重启后的进程与源码时间（12:56–12:58 历史快照）

当时只读进程与监听查询确认（API 后续已被上节 PID 49836 替换）：

| 服务 | 当前 PID | 启动时间（本地） | 监听 |
| --- | --- | --- | --- |
| API / python | 2460 | 2026-11-01 12:53:10 | 127.0.0.1:8000 |
| 前端 / node | 66608 | 2026-11-01 12:53:11 | 0.0.0.0:5280 |

相关源码最后修改时间早于此次启动：

| 文件 | 最后修改时间（本地） |
| --- | --- |
| `backend/app/modules/data/external_mirror.py` | 2026-11-01 01:52:57 |
| `backend/app/modules/static/read_pages.py` | 2026-11-01 01:46:53 |
| `backend/app/modules/ontology/router.py` | 2026-11-01 01:46:53 |
| `backend/app/modules/event/exceptions.py` | 2026-10-31 18:38:38 |
| `backend/app/modules/dynamic/rules_router.py` | 2026-10-31 16:46:45 |
| `backend/app/modules/admin/api_keys.py` | 2026-10-31 17:09:52 |

启动时间晚于源码是已加载当前变更的证据之一；结合下面的新接口合同，已确认当前服务包含新增路由声明。时间顺序不能单独证明进程内每个函数与磁盘文件逐字相同，也不能证明相关性能分支已被实际业务请求执行。

## 第一次重启后的实际公开 HTTP 请求（历史快照）

先阅读 `backend/app/main.py` 与 `backend/app/api.py` 定位公开入口；仅向现有 `http://127.0.0.1:8000` 发送下列三个无身份 GET，各一次，没有猜测其他端口或业务路径。

| 公开 GET | 实际状态 | 实际结果与边界 |
| --- | --- | --- |
| `/healthz` | 200 | `{"status":"ok"}`；源码契约为不查库的存活检查 |
| `/readyz` | 200 | `{"status":"ok","db":"ok"}`；源码仅通过既有 session 执行 `SELECT 1`，不是业务查询或数据库性能采样 |
| `/openapi.json` | 200 | 返回 `rengu_ontology backend`、版本 `0.1.0` 的实际服务 schema；核对以下注册合同 |

没有携带 Authorization、Cookie 或身份覆盖请求头，没有读取浏览器 Cookie、现有会话密钥、环境或数据库凭据，也没有构造任何 token。

## 新接口合同的实际注册结果

以下结论均来自当前 8000 服务返回的 OpenAPI，不是本地导入应用生成的 schema；未调用下表业务接口。

| 合同 | 当前服务实际 schema |
| --- | --- |
| `GET /api/ontology/v1/event-types/{type_id}/delivery-exceptions` | 已注册可选 `error` query，字符串最长 128，正则 `^[A-Z][A-Z0-9_]*$`；保留 state、kw、page、size 参数 |
| `GET /api/ontology/v1/rules/{rule_id}/definition` | 已注册；200 response 引用 `PublishedRuleMetadata`，其中 `release`、`definition` 均为必需字段 |
| `PATCH /api/admin/v1/api-keys/{id}` | 已注册；request body 引用 `KeyPolicyIn`，含 `name`、`note`、`ipAllow`、`expiresAt`、`version` 及既有策略字段，额外字段拒绝 |
| `POST /api/admin/v1/api-keys/{id}:rotate` | 已注册；request body 引用 `KeyRotateIn`，必需 `version` 且最小为 1，额外字段拒绝 |

异常列表返回类型为通用 `dict`，实际 OpenAPI 只声明 `object` 与 `additionalProperties: true`，不会列出 `errorFilter`。源码 `exceptions.py::inbox` 确认 `error` 非空时按 `EventDelivery.error == error` 过滤，并在顶层返回 `errorFilter: error`。12:56–12:58 的公开核对未发送认证业务请求；主会话随后在真实页面验证该原因筛选正常返回空且不再显示旧“不支持”消息，但 fixture 无匹配样本，也未单独报告原始响应的 `errorFilter` 值，不能把它写成有样本的精确过滤与字段回显已全面验证。

同理，definition 的真实发布记录读取、Bearer scope、功能快照与 RLS 拒绝行为，以及密钥维护/轮换的实际写入行为，均不属于本次公开合同核对结果。PATCH 与 POST 只查看注册，从未发送。

## 性能变更的证据边界

本次读取并沿用 [请求级数据库说明](26-11-01-db-request-tuning.md)、[列表查询说明](26-11-01-list-query-tuning.md)、[前端请求调度说明](26-11-01-frontend-request-tuning.md)、[此前业务基线](26-11-01-performance-business-check.md)，以及 10-31 的[综合复检](26-10-31-recheck-resolution.md)、[规则机器读取](26-10-31-recheck-metadata-read.md)、[投递修复](26-10-31-recheck-delivery-fixes.md)。旧报告保留当时语境，本文件记录用户重启后的新增事实。

当前源码仍显示：`load_governed_tables → governed_mirrors`；`StaticService.object_list → read_pages.object_list`；`/workspaces` 与 `/workspaces/catalog → workspace_directory`。性能文件的修改早于当前 API 启动，且当前服务已注册同时期新增合同，旧进程未加载的阻断状态已改变。

本报告编写者没有通过运行时调试读取函数体、采集 SQL、查询真实业务列表或测量延迟；主会话后续已真实验证工作空间、对象类型及合成镜像的只读列表/详情，证明这些已加载服务路径能完成相应页面读取。仍不能把进程时间、schema 与页面正常读取作为批量 JOIN 的实际 SQL 数、列投影收益或目录查询次数的测量，也不能据此声称已实测提速、吞吐提升或数据库负载下降。前端搜索去抖、筛选回首页等行为仍以各次页面证据的具体范围为准。

## 本报告编写者未执行

本报告编写者未运行 pytest、后台 suite、全量/集成/迁移测试、自检脚本或 benchmark；未调用模型、业务写接口、轮换/签发/吊销密钥、事件处置或 worker；未导入或启动 app/lifespan；未访问 DB、环境文件、凭据、军事报告或外部系统；未修改代码、配置、历史报告，未提交或推送。本人唯一新增和修改文件为本报告。主会话的 3 次真实 QA、一次合成告警 claim、SAR003 新卡的 stage/单次确认执行、受限只读查询、局部修复验证与正常重启已单独注明，不受本段未执行声明混淆。

风险与未验收项：本地 SAR003 的单次明确确认闭环与持久化已实测通过，SAR001 守卫拒绝有真实证据；公开 schema 与这些限定页面结果不构成通用 Bearer、跨空间/RLS、全部错误样本、密钥生命周期或性能收益的全面验收。外部自动化与军事业务没有验证或执行，健康检查成功不代表全部业务就绪。
