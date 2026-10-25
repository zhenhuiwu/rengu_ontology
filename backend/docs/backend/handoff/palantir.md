# Palantir专家 交接（动作类型 / 事件类型 / 外部系统 / 回写调用）

> 截至 2026-10-26 02:00（UTC+8）。分支 `palantir-integ`，基于 master `b688d5d`。
> 接口细节见 `docs/backend/action-event-api.md`（§3 接口、§11 第 2 批改动）。本文只记做到哪、怎么接。

## 1. 已完成

| 内容 | 提交 | 验证 |
|---|---|---|
| 动作类型、事件类型、外部系统三页切真接口（前端开关 `action`、`integration`）；路由拆分 | `c918b65` | pytest、ruff、`npm run build` |
| 动作类型写回 tab 的外部系统下拉改调 `GET /api/data/v1/ext-systems?writeback=1`（取到之前用 mock） | `93dbcd6` | 下拉结果与 mock `writebackSystems()` 一致；页面确实发出这个请求 |
| 回写调用 `writeback_call`：表、迁移 `integration_0002`、种子（`system/derived/extCalls.json`，756 行）、接口 `GET /ext-systems/{id}/calls`、`GET /writeback-calls`、`GET /writeback-calls/{id}`、`POST /writeback-calls/{id}:mark-checked`；`:test` 落库；外部系统页“最近调用”、详情抽屉、人工核对改走真接口 | `1cd8f9c`、`2620474` | 24 个测试；前端 client 与 mock 逐项比对（9 个系统 × 结果筛选 × 分页 + counts + 详情，679 项，0 差异）；Playwright 页面检查 |
| 迁移改接 `integration_0002.down_revision = dynamic_0002` | 最后一个单独提交 | 在临时分支合入 `static-integ2` 和 `dynamic-rules`：只有一个 head，空库 `upgrade head`、种子、`alembic check` 都通过，回写测试 24 个通过 |

- 种子阶段 `integration_runs` 已在 `app/seeds/registry.py` 登记，**`seeds/loader.py` 不用改**。
- 【推断】的取舍：`kind`（种子 GET 记为 query，其余记为 write，测试调用记为 test）；测试调用排在最前；`counts` 按系统统计、不受结果筛选影响；`:test` 落库；不建 `biz_code`（前端没有这个字段）。

## 2. 未完成

页面上还读 mock 的操作，以及还没写的后台：

| 页面操作 / 功能 | 现状 | 原因 / 归属 / 依赖 |
|---|---|---|
| 动作类型“执行记录”、“最近异常”、统计（`runStats`） | 读 mock | 数据在动态的 `GET /action-runs`（动态负责人）。前端改调动态接口即可，参数见 api 文档 §3.1（`actionType`、`status`、`size=14`） |
| 事件类型“近期事件”、趋势（`GET /event-types/{id}/events`、`/stats`）、事件抽屉 `GET /events/{id}` | 后台没实现，页面读 mock | 要先建事件表和投递表（`event`、`event_delivery`、`event_dedup`，api 文档 §9.1），再从 `eventsOf.json` 并集灌种子；我负责 |
| 两页的新建、编辑、草稿保存、提交审核、放弃草稿 | 读 mock（`useDraftEdits` 本地） | 走变更单（静态 / versioning）。`static-integ2` 已有变更单接口：`GET /change-requests`（列表 + 详情）、`:submit` / `:withdraw` / `:approve` / `:reject` / `:unapprove` / `:reopen`、`DELETE`，格式见 `docs/backend/static-api.md` §3.1。action / event 的 `sections.py` 已登记快照段 |
| 试运行 `POST /action-types/{id}:dry-run`、模拟触发 `POST /event-types/{id}:simulate` | 后台没实现，页面只弹本地提示 | 我负责。规则见 api 文档 §3.1、§3.3（一律 200，带 `?previewAs=` 时返回 403 `READONLY`） |
| 被规则引用 `GET /action-types/{id}/used-by` | 后台没实现；页面用快照里的 `usedBy` 加 mock `RULES`、`ALERTS` 拼 | 表 `action_type_rule` 已建、已灌种子；规则名和告警数要读动态的规则、告警表（依赖 `dynamic-rules` 合入） |
| 定时对账（`reconcile_task` 表、procrastinate cron、`query_by_key`、`on_reconciled` 回调） | 没建 | 我负责；依赖动态的 `:reconcile`（`dynamic/router.py` 已有）和调度器。外部系统页的“人工核对”已经可用（只改 `writeback_call`，不碰执行记录） |
| 内部接收事件 `POST /internal/v1/events` 和投递 | 没建 | 同“近期事件”，依赖事件表 |
| 按角色返回 403（功能权限） | 测不了 | `app/common/auth.py` 的 `require_func` 目前一律放行（PDE 的扩展点）。路由上已挂 `view` / `manage` 依赖，PDE 实现后补测试 |
| 删除外部系统 | 不做 | 页面没有删除操作 |

## 3. 代码在哪

- 后台：`backend/app/modules/action/`、`event/`、`integration/`（`models.py`、`repo.py`、`service.py`、`router.py`、`schemas.py`、`seeds/`、`sections.py`）。
- 迁移：`backend/migrations/versions/action_0001*`、`event_0001*`、`event_0002*`、`integration_0001*`、`integration_0002_writeback_call.py`。
- 种子数据：`backend/seeds/json/action/…`、`backend/seeds/json/system/derived/extCalls.json`（PDE `export-mock` 原样输出，不要手改）。
- 测试：`backend/tests/test_action_*.py`、`test_event_*.py`、`test_integration_*.py`。
- 前端：`src/api/action.ts`、`src/api/integration.ts`；页面 `src/pages/ontology/ActionTypes.vue`、`EventTypes.vue`、`src/pages/data/ExternalSystems.vue`（mock 分支没改）。
- 文档：`docs/backend/action-event-api.md`。

## 4. 下一步怎么接

1. **合并顺序**：static（`static_0002` 接 `event_0002`）→ dynamic（`dynamic_0002` 改接 `static_0002`，这一步由动态负责人改）→ 本分支。如果本分支要单独先合，回退最后一个提交（`integration_0002` 改接）即可回到 `event_0002`。
2. 合并后检查：`alembic heads` 只有一个 head；空库跑 `upgrade head` 和 `python -m app.cli seed`（`writeback_call` 756 行）；跑 `alembic check`、全量 pytest、`npm run build`。
3. 本地联调：`RENGU_DATABASE_URL=… uv run uvicorn …`，前端加 `VITE_API_REAL_MODULES=action,integration`（只在命令前面临时加，不要提交 env 文件）。
4. **变更单**：02:00 合并后，把动作类型、事件类型的新建、编辑、草稿保存、提交审核、放弃草稿接到 `static-integ2` 的变更单接口（`GET /change-requests`、`:submit` / `:withdraw` / `:approve` / `:reject` / `:unapprove` / `:reopen`、`DELETE`，格式见 `docs/backend/static-api.md` §3.1）。
5. **执行记录**：动态的 `GET /action-runs` 已在 master（`b688d5d`）。动作类型页的“执行记录”、“最近异常”和统计只要改 `src/api/action.ts` 和 `ActionTypes.vue`，改调这个接口（参数 `actionType`、`status`、`page`、`size=14`），再和 mock `runsOf()` 逐项比对。这一批没来得及做。
6. 其余按第 2 节的顺序：事件表和近期事件 → dry-run / simulate → used-by → 对账任务。
