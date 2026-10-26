# Palantir专家 交接（动作类型 / 事件类型 / 外部系统 / 回写调用）

> 截至 2026-10-26（UTC+8）。最新分支 `palantir-r3`，最初基于 master `b475283`，2026-10-26 11:02（UTC+8）已变基到 master `0086eab`（含 static-r4 `58ad8d7`、dynamic-r3；迁移 head `dynamic_0003`；`palantir-cr` 已合入）。
> 接口细节见 `docs/backend/action-event-api.md`（§3 接口、§11 第 2 批、§12 第 3 批、§13 变更单草稿前端）。本文只记做到哪、怎么接。

## 2026-10-26 内部接收增量

内部 `POST /internal/v1/events` 已完成凭证认证、持久信封、ID幂等冲突、业务键窗口/永久去重、pending与真实发布版本绑定；没有配置投递器，因此不发HTTP、不建假回执，待投递/已去重延迟未知。迁移event_0009→event_0010，旧seed未改。当前实现、安全边界和必要专项见 [event-ingress.md](event-ingress.md)；下文原“内部接收未建”表格仅为历史交接。

## 2026-10-26 模拟与持久对账增量

`:simulate`已实现真实纯校验，返回`testEventId:null/deliveredTo:[]/checks`，明确未落库、未投递；历史与预览均403。`integration_0013`新增持久`reconcile_task`，既有`action.reconcile`任务已接真实每15分钟查询/三次上限/人工介入/重开；缺真实外部配置503且不伪装查无。动态默认事件发射器持久化真实pending事件；未实现订阅方投递。外部系统测试按钮已接只读probe，实际请求才记调用。部署与配置、状态机、准确边界见 [reconciliation.md](reconciliation.md)，本增量取代下方旧“未建/未实现”说明。

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
| 新建动作 / 事件类型 | 两页有“新建动作类型 / 新建事件类型”按钮，但只打开共用的 `StartChangeModal`，变更对象写成“新建…类型”；mock 和页面都没有新类型的表单（没有字段、必填项、布局），页面也只能显示列表里已有的类型 | 核对过（palantir-r3），本轮不做：做表单要自己定字段，违反“前端为准”。要先由页面归属方给出表单设计；约定（PDE 审 static-r3 时提）：新类型 `target{kind,id:调用方给的编码,isNew:true}`，`config` 整体一行（key=`cfg`）、`params` / `payload` 一项一行，都是 `op=add`；发布时 `isNew` 目标要静态的按类型写回建定义行 |
| **发布后生效（动作 / 事件写回）** | 已实现七段 add / update / delete；发布事务同时写定义、新快照和类型版本历史，失败整次回滚 | 见 §6；新建 UI 仍未设计 |
| 变更说明 `reason` | 没传 | 共用 `StartChangeModal` 没把“变更说明”发出来（静态 handoff 已提给 PDE） |
| 试运行 `POST /action-types/{id}:dry-run`、模拟触发 `POST /event-types/{id}:simulate` | 后台没实现，页面只弹本地提示 | 我负责。规则见 api 文档 §3.1、§3.3（一律 200，带 `?previewAs=` 时返回 403 `READONLY`） |
| 被规则引用 `GET /action-types/{id}/used-by` | 已接真实 Rule / Alert；前端独立加载和业务错误提示，按上下文丢弃迟到响应 | 关系顺序随 actionTypes.usedBy 快照；历史规则信息读 rules 段，缺失显示定义不可用。告警总数及待处理数始终是当前记录，待处理仅含“待处理”，不含“处理中”。当前 mode_text 优先；历史按快照 autoStage 推导，不借用当前定义 |
| 定时对账（`reconcile_task` 表、procrastinate cron、`query_by_key`、`on_reconciled` 回调） | 没建 | 我负责；依赖动态的 `:reconcile`（`dynamic/router.py` 已有）和调度器。外部系统页的“人工核对”已经可用（只改 `writeback_call`，不碰执行记录） |
| 内部接收事件 `POST /internal/v1/events` 和投递 | 没建 | 同“近期事件”，依赖事件表 |
| 按角色返回 403（功能权限） | 测不了 | `app/common/auth.py` 的 `require_func` 目前一律放行（PDE 的扩展点）。路由上已挂 `view` / `manage` 依赖，PDE 实现后补测试 |
| 删除外部系统 | 不做 | 页面没有删除操作 |
| 编号咨询锁测试的反证 | 10-26 本地已完成：测试观测 `pg_locks` 等待后才放行第一事务，不再依赖固定 sleep | 正常测试通过；独立进程移除锁的受控反证按预期失败，正常进程重新通过 |
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
| 动态 ③ | 10-26 本地已修：`test_integration_code_lock.py` 不再靠固定 sleep 猜测并发窗口 | 双事务通过 `pg_locks` 确认真正等待咨询锁，再提交第一事务；正常与移除锁反证均已验证 |

## 6. 已实现：动作 / 事件发布写回

审核通过的变更单在整版发布事务中写回定义表，不再静默跳过动作 / 事件段。实现位于 `action/writeback.py`、`event/writeback.py`，由各自 `sections.register()` 显式幂等登记（`load_sections()` 调用；import sections 本身不登记），不修改 main / CLI。

| kind | section | 行 key | after 字段 |
|---|---|---|---|
| action | params | api | name、api、type、required、def、rule |
| action | conds | name | kind、name、expr、fail |
| action | edits | `${op}:${obj}` | op、obj、field |
| action | effects | target | kind、target、when、note |
| action | config | cfg | 整体 `{sysId, endpoint, idemKey}` |
| event | payload | api | name、api、type、required、desc、sample |
| event | subs | name | kind、name、ruleId（可空）、filter、then、status；weekly 只读、忽略草稿值 |

- 支持 add / update / delete。update 按旧 key 定位唯一原行，after 可只含修改字段；身份字段改名先查重，再原位更新。add 的 key 必须与 after 身份字段一致，且必填字段齐全；delete 必须有原行。新 identity 也须满足 CrItem.item_key 的 128 字符上限，保证发布后还能编辑。config 删除表示恢复本体内部默认配置（sysId=null、endpoint=“—”、idemKey=""）。不存在的目标（非 isNew）、歧义 / 重复 key、信封字段冲突、未知字段 / 枚举、非布尔 required、NUL / 超长字符串、sysId 不存在或系统不符合生产且未停用条件，返回 409 WRITEBACK_FAILED，整次发布不生成版本、不改 CR 状态。NUL 通常在通用草稿保存边界先返回 422，hook 保留内部调用防御。
- 旧 seed 中 api=alert 的重复参数继续保留；针对这个歧义 key 的 update / delete / add 拒绝，不用 dict 选中其中一条。payload 不得新增 / 改名成全局 EventEnvelopeField.api。
- 原位修改保留未编辑内部字段：ActionParam.rule_expr、SubmissionCriterion.expr_ast、EventSubscription.filter_expr、订阅方 weekly、ActionTypeRule.generates 等。对应 rule / expr / filter 文本真正改变时清空其旧 AST，未改文本则保留；现有执行器尚未读取这些 AST，不凭空解析文本。
- isNew 后台骨架：调用方提供 code，name 使用 target.name（缺省 code），api=code；动作默认空分组、无外部系统，事件默认外部系统来源、保留 30 天，其余现有可空 / 空字符串默认值。每段独立确保父行存在，不依赖 config 先执行。仍在线的 code / api 冲突拒绝；回滚下线类型可经明确 update 等操作再次发布恢复，保留旧子行，add 不覆盖已有 key。这只是后台最低有效定义，新建 UI / 目标对象等产品必需字段表单仍待页面方设计。
- 所有七段完成后，builder 对本次 touched 目标各追加且只追加一行 ActionTypeVersion / EventTypeVersion，definition 存完整新定义，版本取当前指针与全部历史 vN 的最大值 + 1。一个目标多段 / 多 CR 只升一次；回滚后再次发布不复用旧版本号。旧 seed 历史行和旧发布快照不变，重复 seed 不覆盖非 seed 新版本。整版 restore_section 沿用现有逻辑，只恢复当前定义和指针，不修改只追加历史。
- `tests/test_action_event_writeback.py` 覆盖七段 CRUD / rename、新建、旧 AST 与 weekly / generates 保留、文本变更清 AST、非法输入整次发布回滚、重复 seed 与信封冲突、同目标两 CR 只追加一版、回滚再发布版本号不冲突。兼容回归另跑原有 action/event 快照、restore、seed 和静态发布测试。
- 验证：独立 `rengu_agent_admin_test`，`uv run pytest tests/test_action_event_writeback.py tests/test_static_writeback.py tests/test_action_event.py -k 'not fresh_db and not migrations_round_trip' -q`，84 passed / 3 deselected；随后新增的下线类型重新发布用例单跑 1 passed（当前写回文件共 21 个用例）。ruff check / format 与 git diff --check 通过；迁移和全量测试由协调者独立执行。

## 7. 只读核对（历史记录；simulate现状见下方补充）

2026-10-26 核对 `frontend/docs/backend/action-event-api.md`、`frontend/docs/backend/api-index.md`、前端按钮和后台 contracts / ports。**本轮未实现这两个接口，没有新增求值器或测试通道；保持已定的不落库合同，不将现有前端本地成功提示视为后台能力。**

### 已有合同与冲突

- `POST /action-types/{id}:dry-run`：专章 §3.1（约第 162 行）入参为 query `view/cr/ver`、body `{params?}`，返回 `200 {passed, checked, failed[{name,fail}]}`；条件不满足也是 200，`checked` 为当前视图未删除条件数。专章 §3.4 明确 `SUBMISSION_CRITERIA_FAILED` 422 只用于真实提交，不用于 dry-run。api-index §3.7（约第 664 行）仍把条件失败列为 `SUBMISSION_CRITERIA_FAILED`，两者冲突；后续对齐应以专章明确的 200 结果体为依据，不在此次记录中修改正文合同。
- `POST /event-types/{id}:simulate`：专章 §3.2（约第 193 行）入参为 query `view/cr/ver`、body `{payload?}`，缺省载荷取字段 sample，返回 `{testEventId, deliveredTo}`。只面向非停用订阅方（异常状态仍包括），不产生告警、不执行动作、不写下游。两接口均按各页面 `view` 权限开放；类型为草稿返回 409 `TYPE_IS_DRAFT`，不存在返回 404；历史 `?ver=` 允许并使用所查看定义，`?previewAs=` 返回 403 `READONLY`，历史与预览同时存在按预览阻断。
- **模拟存储冲突**：专章 §3 的只读白名单、§7 覆盖项 E8，以及 api-index §3.12、§7 和变更记录，均明确 simulate **不落库、不写 event**；专章后补 §12.3-5 却写“模拟触发的测试事件以后写 true”（`is_test`）。本轮保持前者这一已定合同。现有 `EventRepo.get_with_type_code` 允许查看测试事件，列表和统计排除 `is_test=true`，只说明读取能力，不代表 simulate 已落库，也不授权改变不落库合同。若以后选择测试事件留痕，须显式同步接口索引、只读白名单的落库说明及历史版本行为，不能凭后补注释直接写 event。

### 已核实的实现缺口

- `ActionTypes.vue.dryRun()` / `EventTypes.vue.simulate()` 仅在确认后显示本地成功；`api/action.ts` 尚无对应请求。确认卡 fields 只展示默认值 / sample，不收集结构化参数或实际目标对象。
- `ActionTypeService` / `EventTypeService` 当前按 `ctx.ver` 读取定义，未消费 `view/cr`。实现草稿试验需纯内存叠加本 CR 改动，不可调用发布写回、临时改定义表再回滚来冒充只读计算。
- `dynamic.ports.SubmissionChecker` 仍为一律通过的桩；`ActionSubmitter` 也仍为一律成功的桩。`contracts.actions` 有真实提交 / 事件 emit 等接口，没有 dry-run 或隔离测试投递端口，不可用这些桩声称真实校验或投递成功。
- 种子有 37 种中文提交表达式，涉及角色、对象状态、集合 / 日期、CRM 标签、会员分群、预算和发放记录等；没有现成求值器或已填充的语义 AST，部分业务表不存在，`CurrentUser.roles` 也未解析。动态动作 `params` 仍是文本，不能未经合同设计就按展示名称猜数据库字段、挑一个示例对象，或直接 eval 中文文本。
- 订阅过滤有 7 种原文，包含“同门店当周 ≥ 3 条”等统计语义。`EventSubscription` 目前只有名称、过滤 / 后续处理原文和可选规则编码，没有测试 topic / URL、transport 或收件确认。`then_text` 中出现的 Kafka topic 是展示原文，不是已配置的隔离测试通道。只数非停用订阅方就返回 deliveredTo，或生成 UUID 并放入本地列表，都不能证明那些订阅方已收件。

### 后续最小真实实现前的三个决定

1. **结果存储语义**：本轮已定保持不落库。若后续确需 `is_test=true` 的可查询测试事件，先明确并修改上述冲突合同、历史只读说明和统计隔离要求。
2. **测试通道及成功语义**：明确隔离目的地、可使用的 transport 和何种确认才算 delivered。若产品只要过滤预览，须将接口 / 文案改成 matched 或 wouldDeliver，不能继续声称已投递；也不能触发生产通知、真实动作或外部写入。
3. **条件语言与数据上下文**：确定最小支持的表达式、结构化 params / payload 格式、目标对象和用户角色来源，以及缺少的数据源边界。可以先支持有真实数据的有限比较 / 集合条件；未知表达式或缺上下文必须明确“未能验证”，不可算通过或凭空给成功结果。

前端真模式下两个伪成功入口的处理由前端归属方另行完成；此次仅补交接记录，不修改页面或既定接口合同。
