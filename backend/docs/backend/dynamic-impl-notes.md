# 动态本体（告警 / 动作中心）后台实现说明

> 依据：`ui-vue/docs/backend/dynamic-api.md`（未改动）。代码：`app/modules/dynamic/`，迁移 `migrations/versions/dynamic_0001_alert_action_run.py`。
> 基线：共享仓库 `ui-vue` 的 `master` = `fa07b57`（骨架 + qa + static + Palantir action / event / integration，单 head `event_0001`；骨架在 `backend/`）。本分支先在 `c705c25` 上搬入，再 rebase 到 `fa07b57`。最初在独立仓库基于骨架 `72204b0` 开发，搬入后对应关系：`72204b0`→`0c35c1d`、`b1fc30a`→`c646bd5`、`56a6dd2`→`4c22942`；本文和提交信息里引用的 `5b8fc21` 是 ui-vue 前端 mock 的导出版本，搬迁前后不变。
> 本文只记**规格没写死、实现时自行判断**的点（[推断]）、桩、软引用和已知差异。

## 1. 范围

- 表：`alert`、`alert_log`、`action_run`、`action_run_log`（迁移 `dynamic_0001`，down_revision=`event_0001`；无 branch_labels、无合并迁移；可完整 downgrade）。
- 接口（前缀 `/api/ontology/v1`，共 18 个）：
  - 告警：`GET /alerts`、`GET /alerts/{id}`、`POST /alerts/{id}:claim`、`POST /alerts:batch-claim`、`POST /alerts/{id}:transfer`、`POST /alerts/{id}:close`、`POST /alerts:batch-close`、`POST /alerts/{id}:start-action`、`GET /people`
  - 动作：`GET /action-runs`、`GET /action-runs/{id}`、`GET /actions/meta`、`POST /action-runs:stage`、`:confirm`、`:withdraw`、`:approve`、`:resubmit`、`:reconcile`
  - 内部：`DynamicReconciliationListener.on_reconciled(action_run_id, ReconcileResult)`（不出 HTTP）
- **不在本批**：版本快照的 seed_section（规则 rules、生命周期 lifecycles）还没登记，`ontology_release` 快照里暂时没有动态的分区；规则 / 生命周期表等对应模块合入后再做。
- 鉴权：18 个接口（GET 也一样）都依赖 `CurrentUserDep`（未知 / 停用的 `X-Demo-As` → 401），并挂了 `require_func`（骨架里目前是桩）：告警页 key `alerts`，动作中心 key `action-center`；读 = `view`，写 = `manage`（级别照前端 `roles.ts` 的 `FuncLevel`，不是 edit）。`start-action` 按 `alerts manage` 校验 [推断]。
- 分页：列表返回 `app.common.pagination.Page` 的子类（多了 counts），页码参数用骨架 `PageParams`（router 只给默认条数：告警 12、动作 16，照前端；page 不自设下限，0 / 负数怎么处理由骨架 `PageParams` 定——骨架 9c3e506 起收成第 1 页，在那之前的骨架上转成 422 VALIDATION_FAILED），上限 `MAX_PAGE_SIZE`。本分支基线的骨架还没有 `clamp` / `list_page` 收拢 / `Page.of`，所以切片（count + offset/limit）只在 `service.fetch_page` 一处，留了 TODO：骨架分支 9521968 / 9c3e506 合并后删掉它，改 `self.repo.list_page(stmt, page)` + `AlertPage.of` / `ActionRunPage.of`。**页码越界**：前端 mock `src/mock/paginate.ts:13` 收拢到最后一页（`Alerts.vue:72` 自己 slice、`Actions.vue:67` 用 paginate）；收拢由骨架 `PageParams.clamp` / `Page.of` 做（响应 page = 收拢后的值，total=0 → page 1），**这不是动态的 [推断]**；本分支的测试不断言越界页的结果。
- 迁移顺序：`down_revision` = 当前 master 的 head `event_0001`（单 head）；之后再有模块先合入时由合并方改成当时的 head。`tests/test_dynamic_migration.py` 不假定 dynamic_0001 是 head：按 ScriptDirectory 取它的 down_revision，在临时库升到 `dynamic_0001`、降回去，只比较 dynamic 自己的四张表和类型；骨架 `tests/helpers/migrations.py::assert_module_round_trip` 合并后改用它。
- 动作类型：本批仍是软引用 `action_run.action_type_code`（FK to add later → Palantir `action_type.code`）。在 master `fa07b57` 全量 seed 的临时库核对：action_run 种子里 15 个不同的动作类型编码，15 个都在 `action_type`（共 17 行）里，没有缺的；其中 `adjust_region_budget` 状态是 `changing`，其余 `published`。历史列表只在用户传 `actionType` 筛选时按编码过滤，不 join 动作类型表，类型下线 / 缺失的记录照样列出（`tests/test_dynamic_actions.py::test_history_keeps_runs_with_retired_or_missing_action_type`）。
- 枚举列都用骨架的 `enum_column`（varchar + CHECK，不建 PG 原生 enum），downgrade 后不留类型（`tests/test_migrations.py`）。
- 模块外改动：只有 `app/db/models.py` 一行 import（让 Alembic 看见 dynamic 的表）。`SEED_MODULES` 骨架里已有 `dynamic`，没改；路由由骨架自动发现。

## 2. FK to add later（现为按业务编号的软引用列）

| 表.列 | 将来指向 | 原因 |
|---|---|---|
| `alert.rule_code`、`action_run.rule_code` | 规则表 `rule.code` | 规则归 Palantir/动态规则模块，尚未合入 |
| `alert.hit_code` | `rule_hit` | 同上 |
| `action_run.action_type_code` | `action_type.code`（Palantir） | 未合入；当前从 `seeds/json/action/ACTION_TYPES.json` 读（`catalog.py`） |
| `action_run.sys_code` | `ext_system.code`（integration） | 未合入；当前读 `seeds/json/system/EXT_SYSTEMS.json` |
| `alert.obj_id`、`action_run.obj_id`（+ obj_kind） | 静态本体对象 / biz 表 | 对象种类多态（商品/订单/门店/会员…），等静态对象类型合入后再定 |
| `action_run.scenario_code` | 推演 `scenario` | 未合入 |
| `action_run.qa_session` | 问数会话 | 未合入 |
| 事件类型 `ActionExecuted` / `ActionOutcomeUnknown` | `event_type` | 只作为字符串发给 emitter |

已有的真 FK：`alert.action_run_id → action_run.id`（use_alter）、`alert_log.alert_id`、`action_run.alert_id / retry_of_id / retried_as_id`、`action_run_log.action_run_id`。人员（assignee / initiator / confirmer / approver / actor）照前端 mock 存姓名字符串，不是 FK。

## 3. 桩（`app/modules/dynamic/ports.py`，都可用 FastAPI dependency_overrides 替换）

| 端口 | 桩 | 真实实现方 |
|---|---|---|
| `submit(action_run_id, idempotency_key)` | `StubSubmitter`：一律成功，回执 `STUB-<幂等键>` | Palantir 写回适配器（重试、超时判定在适配器内） |
| `emit(event_id, type, payload)` | `RecordingEmitter`：只记在内存 | 事件总线 / outbox |
| `reopen_reconcile(action_run_id)` | `RecordingScheduler` | integration 的 reconcile_task |
| 提交条件 criteria | `StubSubmissionChecker`：永远通过 | 动作类型提交条件（Palantir） |
| 规则 / 动作类型 / 外部系统目录 | `JsonRefCatalog` 读前端 mock JSON | 各模块的表 |

## 4. [推断] 清单

1. **列表顺序**：业务新写入的行在前（新→旧，等于前端 `unshift`），种子行保持 mock 原顺序（按写入 id）。告警首屏与 `ALERTS.json`、动作中心与 `ACTIONS.json` 顺序逐条一致（有测试）。
2. **`GET /action-runs` 的 `scope`**：规格没写这个参数。`center` = 动作中心记录（ACTIONS + 新建，`in_center=true`），`runs` = 动作类型页执行历史（RUNS），不传 = 全部；传了 `tab` 时固定 `center`。`counts` 只按 center 算，`statusCounts` 按当前筛选算。`tab=unknown` 时「结果未知」排前。
3. **`sessionId` 过滤**：按 `action_run.qa_session` 精确匹配（问数页「本会话动作」）。
4. **`source` 形状**：center 行输出对象 `{type, ruleId, ruleVersion, alertId, confirmer, session, …}`；RUNS 行输出来源原文字符串（mock 就是字符串）。
5. **`confirmer` 列只存 `source.confirmer`**（规则固定确认人 / 推演 owner / 告警页处理人）。规格表 2.1 写「其余 = 发起人」，但若填了发起人，`NOT_CONFIRMER` 会把手动/问数动作也锁给发起人，和 2.3「没有 source.confirmer 时不校验操作人」冲突；按后者实现。
6. **规则来源暂存的确认人**：请求没带 `source.confirmer` 时取规则固定确认人（`RULE_CONFIRMER`）。
7. **幂等重放**（P9）：同幂等键、关键内容相同 → 200 返回原记录（`stage` 新建 201）；内容不同 → 409 `IDEMPOTENCY_CONFLICT`，`details={idemKey, id}`。比较字段：stage = 动作类型 + 对象 + 参数；start-action = 动作类型 + 对象 + 告警。
8. **start-action 忽略种子历史的同号幂等键**：RUNS 历史里有大量 `act-al-…` 键恰好与当前待处理告警同号（部分唯一索引也豁免种子行），不把它们当重放，否则会把一条历史失败记录挂到新告警上。
9. **动作编号** `ACT-yyyymmdd-NNNN`：当天最大号 + 1，含 RUNS 历史（RUNS 当天已到 3849，所以新号从 3850 起，不是前端 mock 的 0553）。
10. **start-action 确认卡「触发值」**：`params = "{value}，阈值 {threshold}"`。审批人照前端按对象类型写死（P5 未做）。
11. **approve 不校验审批人身份**：前端没有审批入口（[待定]），只校验状态 `审批中`。
12. **重新提交后告警改指向新动作**：原动作挂在告警上时，`alert.action_run_id` 改为新动作，在途锁、成功后自动转已解决都看新动作。
13. **批量关闭照前端 `closable`**：只处理本人处理中的，不查在途锁，不返回 skipped（P3 未做）。
14. **对账外部已成功不自动解决告警**（P10 未做），只发 `ActionExecuted`。
15. **`recent7d`**：同规则 + 同对象、触发时间在本条前 7 天内（含本条）。字段名固定为 `recent7d`（不走 camel 生成器的 `recent7D`）。
16. **锁定原因文字**照前端：`关联动作{状态}，有结果前不能转派/关闭`。NOT_CONFIRMER 文案：有规则时「该动作由规则 R-xxx 暂存，指定确认人为 X…」，否则「该动作指定确认人为 X…」。
17. **告警日志 op / from / to** 由日志文本推出，只落库不输出。
18. 种子派生（详见 `seeds/__init__.py` 文档串）：RUNS 缺 sysId / typeVersion 时取动作类型目录；RUNS 的对账说明文字进 `reconcile_note`，结论记外部已成功；494 行 RUNS 没有 objKind 保持 null。

## 5. 契约与规格的冲突

- **对账结论落到哪个状态**：`backend/app/contracts/actions.py:98-99` 的 `ReconcileOutcome` 注释写「succeeded → 动作状态『成功』，not_executed → 『失败』」；`docs/backend/dynamic-api.md:244`（另见状态图 `:224-226`）写「查到也转『已对账』，不转『成功』」，`reconcile_result` 记结论。**实现按 dynamic-api.md**（人工对账、`on_reconciled` 都 → `已对账`），not_executed 的记录不能重新提交（409 STATE_INVALID，文案「如仍需执行请重新发起」）。需要 PDE / Palantir 统一契约注释。
- `on_reconciled` 用契约的 `ReconcileResult(outcome, receipt_id, detail)`；查到成功时有回执就补到 `ext_ref`。

## 6. 已知局限

- 种子 loader 只能整模块灌（`run_seeds(session, ["admin", "dynamic"])`），dynamic 依赖 admin 的 sys_user（PEOPLE 按姓名找用户，找不到报错不新建）。种子在测试里约 2.6 秒一次。
- 目录数据（规则、动作类型、外部系统、动作 meta）现在读 mock JSON，等对应模块合入后换成查表。
- 测试里 `as_()` 只能切到 sys_user 里存在的人；mock 里有些确认人（如 周晓峰）不在 sys_user，用他们的身份调接口会 401 UNAUTHORIZED；测试改用规则 R-015（确认人 陈立军）现场暂存来测 NOT_CONFIRMER。

## 7. 对骨架的需求

- 一个公共的「按姓名 → 用户」查询 / 校验（transfer 目标人现在由 dynamic 自己查 `sys_user.alert_assignable`）。
- 契约 `ReconcileOutcome` 注释与规格对齐（见第 5 节）。
- `EventEmitter` / `ActionSubmitter` / `ReconcileScheduler` 的真实实现（协议在 `app/contracts/actions.py`，dynamic 现用 `ports.py` 里的桩，通过 `get_submitter` 等依赖注入，合入时替换这几个依赖即可）。
