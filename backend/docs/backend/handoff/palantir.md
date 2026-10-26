# Palantir专家 交接（动作类型 / 事件类型 / 外部系统 / 回写调用）

> 截至 2026-10-26（UTC+8）。最新分支 `palantir-r3`，基于 master `b475283`（迁移 head `static_0003`；`palantir-cr` 已合入）。
> 接口细节见 `docs/backend/action-event-api.md`（§3 接口、§11 第 2 批、§12 第 3 批、§13 变更单草稿前端）。本文只记做到哪、怎么接。

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
| **变更单写库（palantir-cr，默认关）** 两页的发起变更 / 加入、保存草稿、提交审核、放弃草稿接静态 `static-r3` 的变更单写接口（契约 `handoff/static.md`）：`VITE_PALANTIR_CR_WRITE=1` 且 `action` 为真接口时先写库，成功后再改页面；失败（含后台没起来）弹错误（`message（code）`，422 带 `details.fields`）、页面不变，不回退 mock、不调 `fallBackIfDown`。开关关着行为不变、不发请求。目标 `kind` 用契约里的 `action` / `event`，`id` = 类型编码，`section` 同 `de.section(name)`。保存草稿：每个变了的 section 先 `GET items` 读回再合并整段 `PUT`（改回已发布值的行去掉）。代码：`src/api/action.ts` 末段（`crStart`、`crWriter`）、`useDraftEdits.ts`（可选 `writer`）、两页 `onStart` | palantir-cr | vue-tsc / build；静态未合并，没联调 |
| **变更单草稿（palantir-r3，开关仍默认关）** 打开 `VITE_PALANTIR_CR_WRITE` 的前置条件：放弃草稿先弹确认卡，删库成功后调 PDE 的 `useDraftView().cancel` 撤掉登记；横幅 / 提交确认的改动数读库里的 `changes`；刷新后按 `draft-targets` + `status=草稿` 列表恢复登记，按 `GET items` 把已保存的行叠回页面（等快照就绪后再叠）。代码：`src/api/action.ts`（`crRestore`、`crChanges`、`crWriter.load/changes`）、`useDraftEdits.ts`（`hydrate`、`ready`）、两页 `onDiscard` 和恢复段 | palantir-r3 | vue-tsc；本地真后台（自建库 `rengu_test_palantir_r3`，开关打开）用 Playwright 走通：发起（CR 落库）→ 删一条提交条件 → 保存（库里 `changes=1`）→ 刷新后草稿行、“1 处改动”都在 → 提交（没审核人 409 `CR_NO_REVIEWER` 弹错；补审核人后 200，CR 变“审核中”）；另一个对象 发起 → 保存 → 刷新 → 放弃（确认卡 → `DELETE target` 204 → 切回已发布，`draft-targets` 里没有了）；事件类型页刷新后 CR-0013 草稿和“2 处改动”恢复 |
| **外部系统编号并发（palantir-r3）** `ExtSystemService._next_code` 取号前拿事务级咨询锁 `integration.ext_system.code`，并发新建不再撞号。动作 / 事件类型没有新建接口，不涉及 | palantir-r3 | `tests/test_integration_code_lock.py`（两个独立事务并发新建，编号连续不重复）；与 `test_action_integration_write.py`、`test_integration_writeback_calls.py` 一起 33 个通过；ruff |
| **事件详情接口（palantir-r3）** `GET /api/ontology/v1/events/{id}`：从现有 `event` 表取一条，返回 mock `EventRecord`（同 `eventById`，字段同近期事件列表），不存在 404 `NOT_FOUND`；没加表、没加迁移。代码：`event/repo.py` `get_with_type_code`、`event/service.py` `EventRuntimeService.event`、`event/router.py` | palantir-r3 | `tests/test_action_event_runtime.py`：全部种子事件逐条与 mock 比对 + 404 |
| **PDE 评审小点（palantir-r2-fix）** 事件类型页：最近事件 / 运行统计 / 近期事件页签在“后台没起来”以外的报错（404 除外）时，原来只给空列表不重试；现改为该区域单行显示错误 + “重试”按钮（重新发请求），布局不变，mock 未改 | palantir-r2-fix | typecheck / build |
| 迁移改接 `integration_0002.down_revision = dynamic_0002` | 最后一个单独提交 | 在临时分支合入 `static-integ2` 和 `dynamic-rules`：只有一个 head，空库 `upgrade head`、种子、`alembic check` 都通过，回写测试 24 个通过 |

- 种子阶段 `integration_runs` 已在 `app/seeds/registry.py` 登记，**`seeds/loader.py` 不用改**。
- 【推断】的取舍：`kind`（种子 GET 记为 query，其余记为 write，测试调用记为 test）；测试调用排在最前；`counts` 按系统统计、不受结果筛选影响；`:test` 落库；不建 `biz_code`（前端没有这个字段）。

## 2. 未完成

页面上还读 mock 的操作，以及还没写的后台：

| 页面操作 / 功能 | 现状 | 原因 / 归属 / 依赖 |
|---|---|---|
| 动作类型的执行记录抽屉（`drawer.open('action', id)`） | 读 mock | **卡住**：抽屉解析在公共文件 `src/components/actionEventResolvers.ts`（不归我）。动态的 `GET /action-runs/{id}` 已有，需抽屉归属方改解析器 |
| 事件抽屉 `drawer.open('event', id)` | 抽屉仍读 mock；后台 `GET /events/{id}` 已有（palantir-r3） | **PDE**：在 `src/components/actionEventResolvers.ts` 的 `event(id)` 把 `eventById(id)` 换成 `GET /api/ontology/v1/events/{id}`，接法同动作抽屉（`c5ec3b3`：后台没起来回退 mock）；返回就是 mock `EventRecord`，抽屉其余代码不用改，事件类型仍用 `eventTypeById` 或 `GET /event-types/{id}`。mock 没有逐订阅方投递明细，按“前端为准”不建 `event_delivery`，等页面要显示时再建 |
| `event_delivery`、`event_dedup` 表 | 没建 | 我负责；近期事件 / 健康已用 `event` 表直接算 |
| 打开开关 `VITE_PALANTIR_CR_WRITE` | 前置条件都做了、本地联调已走通（palantir-r3），提交的代码里仍默认关 | 是否默认打开由 PDE / 用户定；打开时本地设 `VITE_PALANTIR_CR_WRITE=1` + `VITE_API_REAL_MODULES=action,static`（不提交） |
| 放弃后留下空变更单 | 放弃只删本对象的目标；`cancel` 只删“本会话新建且已空”的单，刷新后再放弃时空单留在草稿列表里 | 归 PDE 的 `useDraftView.cancel` / 静态：要么 `cancel` 也删库里已空的单，要么静态 `DELETE target` 后自动删空单。【推断】 |
| 发起时建单成功、加目标失败 | `crStart` 先写库再改页面；建单成功但加目标失败时库里会留一个空单，页面没进草稿所以没东西可撤 | 静态若把“建单 + 首个目标”做成一个事务（`POST /change-requests` 带 `target`）就不会有；否则我这边补删空单 |
| 动作类型 `bulk`（批量执行）字段 | 没做 | 前端 mock / 页面没有这个字段，按“前端为准”不加；`target_kind` 后台已有。【推断】页面加上后再补列和迁移（`down_revision` 接当时的 head） |
| 新建动作 / 事件类型 | 两页有“新建动作类型 / 新建事件类型”按钮，但只打开共用的 `StartChangeModal`，变更对象写成“新建…类型”；mock 和页面都没有新类型的表单（没有字段、必填项、布局），页面也只能显示列表里已有的类型 | 核对过（palantir-r3），本轮不做：做表单要自己定字段，违反“前端为准”。要先由页面归属方给出表单设计；约定（PDE 审 static-r3 时提）：新类型 `target{kind,id:调用方给的编码,isNew:true}`，`config` 一个字段一行、`params` / `payload` 一项一行，都是 `op=add`；发布时 `isNew` 目标要静态的按类型写回建定义行 |
| **发布后生效** | 本轮只到草稿阶段：草稿行存进 `cr_item`、改动列表 / 校验能看到 | 静态发布还不把 `cr_item` 写回定义表（static 未完成第 9 条），审核通过、发布后动作 / 事件定义**不会变**；静态做完后要在 action / event `sections.py` 的 `build_section` 按 `cr_item` 展开（我负责这两节） |
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
