# Palantir专家 交接（动作类型 / 事件类型 / 外部系统 / 回写调用）

> 截至 2026-10-26（UTC+8）。最新分支 `palantir-r3`，最初基于 master `b475283`，2026-10-26 11:02（UTC+8）已变基到 master `0086eab`（含 static-r4 `58ad8d7`、dynamic-r3；迁移 head `dynamic_0003`；`palantir-cr` 已合入）。
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
| 放弃后留下空变更单 | 放弃只删本对象的目标；刷新后再放弃时空单留在草稿列表里。**静态评审已定：空草稿单保留**（提交时 `CR_EMPTY` 拦住，用户可自己删），前端不用删单 | 不做；只剩下面“本轮评审遗留”静态 ② 的 404 处理 |
| 发起时建单成功、加目标失败 | `crStart` 建单成功但加目标失败时库里留一个空单 | 按静态“空草稿单保留”的决定（提交被 `CR_EMPTY` 拦、用户可删）不再补删；前端不做删单 |
| 动作类型 `bulk`（批量执行）字段 | 没做 | 前端 mock / 页面没有这个字段，按“前端为准”不加；`target_kind` 后台已有。【推断】页面加上后再补列和迁移（`down_revision` 接当时的 head） |
| 新建动作 / 事件类型 | 两页有“新建动作类型 / 新建事件类型”按钮，但只打开共用的 `StartChangeModal`，变更对象写成“新建…类型”；mock 和页面都没有新类型的表单（没有字段、必填项、布局），页面也只能显示列表里已有的类型 | 核对过（palantir-r3），本轮不做：做表单要自己定字段，违反“前端为准”。要先由页面归属方给出表单设计；约定（PDE 审 static-r3 时提）：新类型 `target{kind,id:调用方给的编码,isNew:true}`，`config` 一个字段一行、`params` / `payload` 一项一行，都是 `op=add`；发布时 `isNew` 目标要静态的按类型写回建定义行 |
| **发布后生效（动作 / 事件写回）** | 没做。草稿行存进 `cr_item`、改动列表 / 校验能看到；但 master `58ad8d7`（static-r4）起整版发布按 `register_writeback` 登记表写回，动作 / 事件**没登记**，发布时这些段被跳过（`writeback.py` `apply_crs` 里 `hook is None` 那段，master 上约第 133–137 行），只在发布事件说明里记“未写回定义表：action/…、event/…”，变更单照样标“已发布”——**在做完之前，动作 / 事件变更单发布后定义表不变** | 我负责，见 §6【下一轮】 |
| 变更说明 `reason` | 没传 | 共用 `StartChangeModal` 没把“变更说明”发出来（静态 handoff 已提给 PDE） |
| 试运行 `POST /action-types/{id}:dry-run`、模拟触发 `POST /event-types/{id}:simulate` | 后台没实现，页面只弹本地提示 | 我负责。规则见 api 文档 §3.1、§3.3（一律 200，带 `?previewAs=` 时返回 403 `READONLY`） |
| 被规则引用 `GET /action-types/{id}/used-by` | 后台没实现；页面用快照里的 `usedBy` 加 mock `RULES`、`ALERTS` 拼 | 表 `action_type_rule` 已建、已灌种子；规则名和告警数要读动态的规则、告警表（依赖 `dynamic-rules` 合入） |
| 定时对账（`reconcile_task` 表、procrastinate cron、`query_by_key`、`on_reconciled` 回调） | 没建 | 我负责；依赖动态的 `:reconcile`（`dynamic/router.py` 已有）和调度器。外部系统页的“人工核对”已经可用（只改 `writeback_call`，不碰执行记录） |
| 内部接收事件 `POST /internal/v1/events` 和投递 | 没建 | 同“近期事件”，依赖事件表 |
| 按角色返回 403（功能权限） | 测不了 | `app/common/auth.py` 的 `require_func` 目前一律放行（PDE 的扩展点）。路由上已挂 `view` / `manage` 依赖，PDE 实现后补测试 |
| 删除外部系统 | 不做 | 页面没有删除操作 |
| 编号咨询锁测试的反证 | `test_integration_code_lock.py` 通过，但没在去掉锁的代码上跑过确认它会失败 | 下一轮顺手验一次 |
| 合入 master | 本分支已变基到 `0086eab`；本分支没有新迁移，合入时不用改 `down_revision`；变基后本分支改过的测试 / ruff / vue-tsc 还没重跑 | 合入后 `alembic heads` 单 head、空库 `upgrade head` + 种子、全量 pytest（团队统一跑）、vue-tsc / build |

## 3. 代码在哪

- 后台：`backend/app/modules/action/`、`event/`、`integration/`（`models.py`、`repo.py`、`service.py`、`router.py`、`schemas.py`、`seeds/`、`sections.py`）。
- 迁移：`backend/migrations/versions/action_0001*`、`action_0002_run_daily_stat.py`、`event_0001*`、`event_0002*`、`event_0003_event_runtime.py`、`integration_0001*`、`integration_0002_writeback_call.py`。
- 第 3 批代码：`event/models.py`（`Event`、`EventDailyStat`、`EventOutcome`）、`event/repo.py`（`EventRepo`）、`event/service.py`（`EventRuntimeService`）、`event/router.py`（`/events`、`/stats`）；`action/models.py`（`ActionRunDailyStat`）、`action/service.py`（`run_daily`）、`action/router.py`（`/stats/daily`）；前端 `src/api/action.ts` 末尾（`listEvents`、`getEventStats`、`getRunDaily`）。
- 种子数据：`backend/seeds/json/action/…`（第 3 批新增 `derived/eventsOf.json`、`eventDaily.json`、`runDaily.json`）、`backend/seeds/json/system/derived/extCalls.json`（PDE `export-mock` 原样输出，不要手改）。
- 测试：`backend/tests/test_action_*.py`、`test_event_*.py`、`test_integration_*.py`。
- 前端：`src/api/action.ts`、`src/api/integration.ts`；页面 `src/pages/ontology/ActionTypes.vue`、`EventTypes.vue`、`src/pages/data/ExternalSystems.vue`（mock 分支没改）。
- 文档：`docs/backend/action-event-api.md`。

## 4. 下一步怎么接

1. **合并**：`palantir-r3` 已变基到 master `0086eab`，没有新迁移（迁移 head 仍是 `dynamic_0003`），直接合即可。
2. 合并后检查：`alembic heads` 只有一个；空库 `upgrade head` + `python -m app.cli seed`（`event` 1,243、`event_daily_stat` 210、`action_run_daily_stat` 238 行）；`alembic check`、全量 pytest、`npm run build`。
3. 本地联调：前端临时加 `VITE_API_REAL_MODULES=action,integration,dynamic`（不要提交 env 文件）。
4. **抽屉**：动作抽屉请 `actionEventResolvers.ts` 的归属方（PDE）改读动态 `GET /action-runs/{id}`；事件抽屉的 `GET /events/{id}` 已做（palantir-r3），同样请 PDE 改 `event(id)` 读它；`event_delivery` 因 mock 无每个订阅方的投递数据，故意不做。
5. **变更单**：草稿保存 / 刷新恢复 / 提交 / 放弃已在 static-r3 之后真后台联调通过（`VITE_PALANTIR_CR_WRITE=1` + `VITE_API_REAL_MODULES=action,static`，都不提交）；默认仍关，**等 §6 发布写回做完再决定默认打开**。【推断】改动列表「类型」列中文名按页签取（参数、提交条件、本体修改、副作用、写回配置、载荷字段、订阅方），`field` = 行 key。
6. **下一轮先做 §6 发布写回，再做 §5 评审遗留**；然后 `event_delivery` / `event_dedup` / `POST /internal/v1/events` → dry-run / simulate → used-by → 对账任务 → 按角色 403。
7. 【推断】待定：`range=7d` 现在不设时间下限（mock 口径），上线前是否改成真 7 天，见 api 文档 §12.3。

## 5. 本轮评审遗留（下一轮做）

palantir-r3（`5fb6769`）两方评审都已通过；下面是评审提的、本轮没改的点，都归我。

**静态评审**

| # | 问题 | 怎么改 |
|---|---|---|
| 静态 ① | `src/api/action.ts` 的草稿保存（`crWriter.save` → `saveCRItems`，约第 241、299 行）是“读已有行 → 本地合并 → `PUT /change-requests/{id}/items`”，不带 `lockVersion`；同一单在两个标签页里编辑会互相覆盖 | `PUT` 带上读到的 `lockVersion`，冲突（409）时提示“已在别处修改”并重新 `load` 合并，不静默覆盖 |
| 静态 ② | 放弃时 `DELETE` 目标返回 404（目标已被删 / 单已没了），现在当失败处理，页面卡在“放弃草稿失败”（`useDraftEdits.ts` 约第 201 行） | 404 视为“已经没了”：照常清本地草稿状态（`cancel` / `forgetTarget`），不报错 |
| 静态 ③ | `GET /events/{id}` 没有行级数据范围，和事件列表一样 | 等事件行级范围（按门店 / 组织）定下来，列表和单条一起加；现在不做 |

静态另外决定：空草稿单保留（提交时 `CR_EMPTY` 拦住，用户可删），前端不需要删单，已更新 §2 两行。

**动态评审**

| # | 问题 | 怎么改 |
|---|---|---|
| 动态 ① | 路径参数带 NUL 返回 500：`GET /events/EV-%00x`、`GET /event-types/x%00/stats` | 和静态一样返回 422：路径参数校验拒绝 `\x00`（router 层或共用依赖），各补一条测试 |
| 动态 ② | `event/repo.py:47` 的 `get_with_type_code` 排除了测试事件（`is_test`），所以 `:simulate` 返回的 `testEventId` 用 `GET /events/{id}` 查是 404 | 单条查询不排除测试事件（列表照旧排除），或明确约定测试事件不可查、`:simulate` 不返回可点的 id；先和动态 / PDE 对齐，【推断】倾向前者 |
| 动态 ③ | `test_integration_code_lock.py` 靠 `asyncio.sleep(0.5)`（持锁不提交）制造并发窗口，慢机器上可能不稳 | 改成事件同步：第一个事务拿到锁后 set 一个 `asyncio.Event`，第二个等它再开始，并确认第二个在第一个提交前被阻塞（如 `pg_locks` 查等待），去掉固定 sleep |

## 6. 【下一轮】动作 / 事件发布写回（接 static-r4 `register_writeback`，master `58ad8d7` 起可用）

目标：审核通过的动作 / 事件变更单整版发布时，把 `cr_item` 写回 action / event 定义表；现在没登记，发布会静默跳过（见 §2“发布后生效”）。

1. **登记位置**：在 `app/modules/action/sections.py`、`app/modules/event/sections.py` 模块级登记（`load_sections()` 会 import 各模块 `sections.py`，import 即登记；`main.py` 和 `cli.py` 都调它）。
   ```python
   from app.modules.static.writeback import Ctx, register_writeback, writeback_error
   for seg in ("params", "conds", "edits", "effects", "config"):
       register_writeback("action", seg, _ACTION_HOOKS[seg])
   for seg in ("payload", "subs"):
       register_writeback("event", seg, _EVENT_HOOKS[seg])
   ```
   段名和前端 `de.section(name)` 一致：动作 `params`（key = 参数 `api`）、`conds`（key = 条件 `name`）、`edits`（key = `${op}:${obj}`）、`effects`（key = `target`）、`config`（单行 key = `cfg`，`after` = `{sysId, endpoint, idemKey}`）；事件 `payload`（key = 字段 `api`）、`subs`（key = 订阅方 `name`）。同一 (kind, section) 重复登记会抛 `ValueError`（模块重复 import 是同一个函数对象，不会触发）。
2. **函数签名**：`async def fn(ctx: Ctx, target_id: str, rows: list[CrItem]) -> set[str]`。
   - `rows`：同一目标、同一段的 `cr_item`，按 `seq` 排好；每行用 `item_key`、`op`（add / update / delete）、`before`、`after`。
   - `ctx`：`session`、`cr`、`ver`、`now`、`by`、`is_new`、`name`（`cr_target.target_name`，新建定义的名称兜底）。
   - 在发布事务里执行，只改 action / event 自己的表。
   - **返回 `set()`**：返回值是要重组的**静态**快照节名（`DIRTY`）；我们的 `ActionTypesSection` / `EventTypesSection.build_section` 每次都按定义表重组，不需要标脏。
   - 出错（目标不存在且不是新建、key 对不上、取值不认识、外部系统不存在等）`raise writeback_error(msg, **details)` → 409 `WRITEBACK_FAILED`，整个发布回滚。
3. **新建定义**：共用 `_ensure_action(ctx, tid)` / `_ensure_event(ctx, tid)`：按编码取定义行；没有且 `ctx.is_new` 时建一行（名称用 `ctx.name`），没有又不是新建时 `writeback_error`。**各段执行先后没有保证**（`apply_crs` 按 `cr_item` 分组顺序调），所以每个段函数开头都调它，不能假设 `config` 先跑。新建类型的表单本身还没有（见 §2）。
4. **不改**：现有 `restore_section`（整版回滚）保持原样；`build_section` 保持原样。
5. **测试**（写在 `tests/test_action_event_writeback.py`）：每段 add / update / delete 各一条，发布后定义表和新版快照都变；`is_new` 新建；不存在目标 → 409 且发布回滚（版本号、CR 状态都不变）；同一单里动作和事件各一个目标；发布事件说明里不再出现“未写回定义表：action/…”。只跑这个文件 + ruff。
6. **前端**：不用改；发布后 `?ver=` 新版本的快照按定义表重组，两页读快照即可看到。

