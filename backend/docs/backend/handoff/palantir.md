# Palantir专家 交接（动作类型 / 事件类型 / 外部系统 / 回写调用）

> 截至 2026-10-26（UTC+8）。最新分支 `palantir-cr`，基于 master `770f6d7`（`palantir-r2`、`palantir-r2-fix`、`palantir-generic-fix` 已合入）。
> 接口细节见 `docs/backend/action-event-api.md`（§3 接口、§11 第 2 批、§12 第 3 批）。本文只记做到哪、怎么接。

## 1. 已完成

| 内容 | 提交 | 验证 |
|---|---|---|
| 动作类型、事件类型、外部系统三页切真接口（前端开关 `action`、`integration`）；路由拆分 | `c918b65` | pytest、ruff、`npm run build` |
| 动作类型写回 tab 的外部系统下拉改调 `GET /api/data/v1/ext-systems?writeback=1`（取到之前用 mock） | `93dbcd6` | 下拉结果与 mock `writebackSystems()` 一致；页面确实发出这个请求 |
| 回写调用 `writeback_call`：表、迁移 `integration_0002`、种子（`system/derived/extCalls.json`，756 行）、接口 `GET /ext-systems/{id}/calls`、`GET /writeback-calls`、`GET /writeback-calls/{id}`、`POST /writeback-calls/{id}:mark-checked`；`:test` 落库；外部系统页“最近调用”、详情抽屉、人工核对改走真接口 | `1cd8f9c`、`2620474` | 24 个测试；前端 client 与 mock 逐项比对（9 个系统 × 结果筛选 × 分页 + counts + 详情，679 项，0 差异）；Playwright 页面检查 |
| 动态本体的执行记录、统计：动作类型页的“执行记录”页签、“最近异常”、概览统计改调动态的 `GET /api/ontology/v1/action-runs?actionType=&scope=runs`（前端开关 `dynamic`，按动作类型取全量缓存，统计口径同 mock `runStats()`；取不到时用 mock） | 本批 | 17 个类型 1,849 条记录与 mock `runsOf()` 逐条比对 |
| **第 3 批（palantir-r2）** 事件运行数据：表 `event`、`event_daily_stat`（迁移 `event_0003` 接 `admin_0002`），种子 `eventsOf.json`（1,243 条）、`eventDaily.json`；接口 `GET /event-types/{id}/events`、`GET /event-types/{id}/stats`；事件类型页近期事件、最近事件、运行健康、近 14 天趋势改走真接口 | palantir-r2 | `tests/test_action_event_runtime.py`：15 个类型逐条比对 mock（全量 / 24h / 结果筛选 / 分页 / 健康 / 趋势） |
| **第 3 批** 动作趋势：表 `action_run_daily_stat`（迁移 `action_0002` 接 `event_0003`），种子 `runDaily.json`；接口 `GET /action-types/{id}/stats/daily?days=14`；动作类型页近 14 天执行图改走真接口（取不到 / 后台没起来用 mock） | palantir-r2 | 17 个类型与 mock `runDaily()` 一致 |
| **第 3 批** 外部系统编辑 `updateExtSystem` 改 `PUT`（后台 POST 别名保留） | palantir-r2 | typecheck / build |
| **变更单写库（palantir-cr，默认关）** 两页的发起变更 / 加入、保存草稿、提交审核、放弃草稿接静态 `static-r3` 的变更单写接口（契约 `handoff/static.md`）：`VITE_PALANTIR_CR_WRITE=1` 且 `action` 为真接口时先写库，成功后再改页面；失败（含后台没起来）弹错误、页面不变，不回退 mock、不调 `fallBackIfDown`。开关关着行为不变、不发请求。目标 `kind` 用契约里的 `action` / `event`，`id` = 类型编码，`section` 同 `de.section(name)`。保存草稿：每个变了的 section 先 `GET items` 读回再合并整段 `PUT`（改回已发布值的行去掉）。代码：`src/api/action.ts` 末段（`crStart`、`crWriter`）、`useDraftEdits.ts`（可选 `writer`）、两页 `onStart` | palantir-cr | vue-tsc / build；静态未合并，没联调 |
| **PDE 评审小点（palantir-r2-fix）** 事件类型页：最近事件 / 运行统计 / 近期事件页签在“后台没起来”以外的报错（404 除外）时，原来只给空列表不重试；现改为该区域单行显示错误 + “重试”按钮（重新发请求），布局不变，mock 未改 | palantir-r2-fix | typecheck / build |
| 迁移改接 `integration_0002.down_revision = dynamic_0002` | 最后一个单独提交 | 在临时分支合入 `static-integ2` 和 `dynamic-rules`：只有一个 head，空库 `upgrade head`、种子、`alembic check` 都通过，回写测试 24 个通过 |

- 种子阶段 `integration_runs` 已在 `app/seeds/registry.py` 登记，**`seeds/loader.py` 不用改**。
- 【推断】的取舍：`kind`（种子 GET 记为 query，其余记为 write，测试调用记为 test）；测试调用排在最前；`counts` 按系统统计、不受结果筛选影响；`:test` 落库；不建 `biz_code`（前端没有这个字段）。

## 2. 未完成

页面上还读 mock 的操作，以及还没写的后台：

| 页面操作 / 功能 | 现状 | 原因 / 归属 / 依赖 |
|---|---|---|
| 动作类型的执行记录抽屉（`drawer.open('action', id)`） | 读 mock | **卡住**：抽屉解析在公共文件 `src/components/actionEventResolvers.ts`（不归我）。动态的 `GET /action-runs/{id}` 已有，需抽屉归属方改解析器 |
| 事件抽屉 `drawer.open('event', id)` / `GET /events/{id}` | 读 mock | 同上（公共抽屉）；另外逐订阅方投递要 `event_delivery` 表，没建 |
| `event_delivery`、`event_dedup` 表 | 没建 | 我负责；近期事件 / 健康已用 `event` 表直接算 |
| 变更单写库联调 / 打开开关 | 代码已写（palantir-cr），开关 `VITE_PALANTIR_CR_WRITE` 默认关 | 等 `static-r3` 合入 master 后联调再打开；**PDE**：在共用 `src/env.d.ts` 的 `ImportMetaEnv` 补 `VITE_PALANTIR_CR_WRITE?: string`（现在用类型断言读） |
| 刷新后草稿改动叠回页面 | 草稿视图仍是前端内存，刷新后看不到库里已保存的行 | 要按 `GET /change-requests/{id}/items?kind=&targetId=` 叠回 `useDraftEdits` 的 saved（同静态未完成第 9 条） |
| 放弃草稿后登记表、改动数 | 放弃后 `useDraftView` 里本对象仍登记在该单（mock 同样）；横幅改动数仍按前端计 | 登记表删目标要静态的 `forgetTarget`（static-r3 新增，未合并）；改动数改读库要 PDE 共用的 `useDraftView` 同步入口 |
| 新建动作 / 事件类型 | 按钮仍只把当前选中类型登记进变更单（同 mock），没有新类型表单，`isNew` 不用 | 页面没有新建表单，待定 |
| 变更说明 `reason` | 没传 | 共用 `StartChangeModal` 没把“变更说明”发出来（静态 handoff 已提给 PDE） |
| 试运行 `POST /action-types/{id}:dry-run`、模拟触发 `POST /event-types/{id}:simulate` | 后台没实现，页面只弹本地提示 | 我负责。规则见 api 文档 §3.1、§3.3（一律 200，带 `?previewAs=` 时返回 403 `READONLY`） |
| 被规则引用 `GET /action-types/{id}/used-by` | 后台没实现；页面用快照里的 `usedBy` 加 mock `RULES`、`ALERTS` 拼 | 表 `action_type_rule` 已建、已灌种子；规则名和告警数要读动态的规则、告警表（依赖 `dynamic-rules` 合入） |
| 定时对账（`reconcile_task` 表、procrastinate cron、`query_by_key`、`on_reconciled` 回调） | 没建 | 我负责；依赖动态的 `:reconcile`（`dynamic/router.py` 已有）和调度器。外部系统页的“人工核对”已经可用（只改 `writeback_call`，不碰执行记录） |
| 内部接收事件 `POST /internal/v1/events` 和投递 | 没建 | 同“近期事件”，依赖事件表 |
| 按角色返回 403（功能权限） | 测不了 | `app/common/auth.py` 的 `require_func` 目前一律放行（PDE 的扩展点）。路由上已挂 `view` / `manage` 依赖，PDE 实现后补测试 |
| 删除外部系统 | 不做 | 页面没有删除操作 |

## 3. 代码在哪

- 后台：`backend/app/modules/action/`、`event/`、`integration/`（`models.py`、`repo.py`、`service.py`、`router.py`、`schemas.py`、`seeds/`、`sections.py`）。
- 迁移：`backend/migrations/versions/action_0001*`、`action_0002_run_daily_stat.py`、`event_0001*`、`event_0002*`、`event_0003_event_runtime.py`、`integration_0001*`、`integration_0002_writeback_call.py`。
- 第 3 批代码：`event/models.py`（`Event`、`EventDailyStat`、`EventOutcome`）、`event/repo.py`（`EventRepo`）、`event/service.py`（`EventRuntimeService`）、`event/router.py`（`/events`、`/stats`）；`action/models.py`（`ActionRunDailyStat`）、`action/service.py`（`run_daily`）、`action/router.py`（`/stats/daily`）；前端 `src/api/action.ts` 末尾（`listEvents`、`getEventStats`、`getRunDaily`）。
- 种子数据：`backend/seeds/json/action/…`（第 3 批新增 `derived/eventsOf.json`、`eventDaily.json`、`runDaily.json`）、`backend/seeds/json/system/derived/extCalls.json`（PDE `export-mock` 原样输出，不要手改）。
- 测试：`backend/tests/test_action_*.py`、`test_event_*.py`、`test_integration_*.py`。
- 前端：`src/api/action.ts`、`src/api/integration.ts`；页面 `src/pages/ontology/ActionTypes.vue`、`EventTypes.vue`、`src/pages/data/ExternalSystems.vue`（mock 分支没改）。
- 文档：`docs/backend/action-event-api.md`。

## 4. 下一步怎么接

1. **合并**：`palantir-r2` 只加了 `event_0003`（接 `admin_0002`）→ `action_0002` 两个迁移。若 master 在 `admin_0002` 之后又有新迁移，把 `event_0003.down_revision` 改接新 head，保持单 head。
2. 合并后检查：`alembic heads` 只有一个；空库 `upgrade head` + `python -m app.cli seed`（`event` 1,243、`event_daily_stat` 210、`action_run_daily_stat` 238 行）；`alembic check`、全量 pytest、`npm run build`。
3. 本地联调：前端临时加 `VITE_API_REAL_MODULES=action,integration,dynamic`（不要提交 env 文件）。
4. **抽屉**：请 `actionEventResolvers.ts` 的归属方把 `action(id)` 改读动态 `GET /action-runs/{id}`；事件抽屉等 `event_delivery` + `GET /events/{id}`（我负责）。
5. **变更单**：`static-r3` 合入后，本地 `.env.local` 设 `VITE_PALANTIR_CR_WRITE=1` + `VITE_API_REAL_MODULES=action,static`（都不提交）联调：发起 → 保存草稿 → 变更与发布页看到改动 → 提交审核 / 放弃；没问题再决定默认打开。【推断】改动列表「类型」列中文名按页签取（参数、提交条件、本体修改、副作用、写回配置、载荷字段、订阅方），`field` = 行 key。
6. 其余：`event_delivery` / `event_dedup` / `POST /internal/v1/events` → dry-run / simulate → used-by → 对账任务。
7. 【推断】待定：`range=7d` 现在不设时间下限（mock 口径），上线前是否改成真 7 天，见 api 文档 §12.3。
