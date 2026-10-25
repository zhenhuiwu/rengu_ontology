# 动态本体交接说明（dynamic）

分支 `dynamic-rules`（基于 master b688d5d，线性包含 dynamic-impl ee06847）。负责人：动态本体负责人。
合并顺序：static 分支（含 `static_0002`）先合，dynamic 后合。

## 已完成

**后端（5 个模块全部落库、接口齐全、有测试）**
- 告警 alerts、动作中心 action-runs（dynamic_0001）。
- 规则 rules / 命中 rule-hits / 回放、情景推演 scenarios、对象生命周期 lifecycles（dynamic_0002，23 个接口）。
- 快照段 `rules`、`lifecycles`；种子 `seeds/json/dynamic/*`；迁移往返测试。
- `dynamic_0002.down_revision = "static_0002"`（最后一个迁移提交）。在 static-integ2（743ec27）+ 本分支的临时合并上验证过：`alembic upgrade head` 单头，动态与迁移测试全部通过。

**前端接真接口（`dynamicApi.isReal()`；开关 `VITE_API_REAL_MODULES` 含 `dynamic` 时生效，默认仍是 mock，mock 路径不变）**
- 告警处理、动作中心（ee06847）。
- 规则与自动化（`/build/ontology/rules`）：列表筛选 / 搜索、详情、命中历史（7d/30d、分页、抽屉）、回放试跑（当前 / 草稿版本）、版本、已暂存动作、草稿保存（`PUT /rules/{id}/draft`）。
- 对象生命周期（对象类型 → 生命周期页签）：状态机、非法迁移、状态变化（分页 / 只看非法）、`?ver=` 历史只读、草稿新增状态 / 新增·修改·删除迁移 / 撤销（整份 `PUT …/lifecycle/draft`）。
- 情景推演（`/use/ops/scenarios`）：列表 / 元数据 / 对比、新建 / 复制、改名、运行后取结果、影响明细服务端分页、暂存计划 / 暂存弹窗。

## 未完成

1. **情景推演页接线**：已全部接通（真接口模式下全部走接口，mock 路径不变），不再是未完成项。在 static-integ2 + 本分支的临时合并后端上，用无头 Playwright 逐项验证通过（15/15）：列表 / 元数据 / 状态筛选 / 关键字、选中详情、影响明细服务端分页、复制、改名（PATCH）、运行后取结果、重新运行确认、暂存计划 + 暂存、对比（URL 两个 + 勾选第三个）+ 对比里选定后暂存、新建、他人方案只读。
   遗留：`:run` 是同步的（前端「运行中」状态只是请求期间的乐观显示）；动作中心还没有暂存→确认→审批的界面（见第 4 条）。
2. **规则页真接口模式下仍走 mock 的部分**
   - 变更单 / 草稿视图：`useDraftView`、`CHANGE_REQUESTS`（草稿归属哪个 CR、草稿视图开关）仍是 change 模块 mock。
   - 目录：`ACTION_TYPES` / `actionTypeById` / `paramsOf`、`eventTypeById`、`metricById` 仍读 mock。
   - 「新建规则」按钮打开的是 change 模块 mock 的 StartChangeModal；草稿提交 / 放弃也是 change 模块 mock。
3. **生命周期页真接口模式下仍走 mock 的部分**：草稿 CR 来自 mock `useDraftView` / `DRAFT_TARGETS`；`actionTypeById`、`EVENT_TYPES` 仍是 mock。
4. **后端仍是 mock / 示意的缺口**
   - `SCENARIO_CONFIG_DIRTY`（`:stage` 时结果配置 ≠ 方案配置）实际走不到：`:run` 同步算完并把配置写回方案，两者总是一致；推演引擎改异步、或允许只保存配置不运行之后才会出现。
   - `rule_draft.cr_code` / `lifecycle_draft.cr_code` 只是变更单编码的软引用，没有外键。
   - 动作中心没有「暂存 → 确认 → 审批」的完整界面（推演 / 规则只能暂存为待确认动作）。
   - 动作审批 approve 不校验审批人是否有权限 / 是否为指定审批人。

## 代码在哪

| 内容 | 位置 |
| --- | --- |
| 迁移 | `backend/migrations/versions/dynamic_0001_*.py`、`dynamic_0002_rules_scenarios_lifecycles.py` |
| 告警 / 动作 | `backend/app/modules/dynamic/`（`router.py`、`service.py`、`schemas.py`、`models.py`、`catalog.py`） |
| 规则 / 推演 / 生命周期 | `backend/app/modules/dynamic/rules_router.py`、`rules_service.py`、`rules_sim.py`、`rules_schemas.py`、`rules_models.py` |
| 快照段 | `backend/app/modules/dynamic/sections.py` |
| 种子 | `backend/app/modules/dynamic/seeds/`、`seeds/json/dynamic/*` |
| 测试 | `backend/tests/test_dynamic_*.py`、`tests/dynamic_support.py` |
| 前端接口封装 | `src/api/dynamic.ts` |
| 页面 | `src/pages/ops/Alerts.vue`、`Actions.vue`、`Scenarios.vue`；`src/pages/ontology/Rules.vue`；`src/pages/ontology/parts/object/ObjBehaviorLifecycle.vue` |
| 说明文档 | `docs/backend/dynamic-api.md`、`docs/backend/dynamic-rules-impl-notes.md`、`backend/docs/backend/dynamic-impl-notes.md` |

## 下一步怎么接

1. **变更单接口（合并后再接，现在不接）**：static-integ2 上已有变更单接口：`GET /change-requests`（列表 / 详情）、`:submit`、`:withdraw`、`:approve`、`:reject`、`:unapprove`、`:reopen`、`DELETE`，见 `docs/backend/static-api.md` §3.1。合并后把规则页、生命周期页的 CR / 草稿视图部分（`useDraftView`、`CHANGE_REQUESTS`、新建规则、草稿提交 / 放弃）切到这些接口；同时补 `rule_draft.cr_code`、`lifecycle_draft.cr_code` → `change_request` 的外键迁移，并考虑把草稿保存并入 `/change-requests/{cr}/items`。
2. **PDE 共享文件跟进**
   - `src/api/dynamic.ts` 的 `write()`（PUT / PATCH 的临时实现）：pde-integ 215819d 给 apiClient 加了 put / patch / delete，合并后换成 `api.put` / `api.patch`，删掉 `write()`。本分支没改 `src/api/http.ts`。
   - `backend/app/modules/dynamic/catalog.py` 里的规则查询仍读 JSON（`seeds/json`），应改为读 `rule` 表。
   - `backend/app/modules/dynamic/router.py`：include `rules_router` 的几行（以及总路由里 dynamic 的 include）合并时注意与其他模块的 include 行冲突。
   - `backend/tests/dynamic_support.py`：夹具改为先建 app（保留 `import app.main` 这一行），合并时保留。
   - 迁移依赖：`dynamic_0002` 依赖 `static_0002`；单独检出本分支（不含 static）时 alembic 会报缺 `static_0002`，属预期。
   - export-mock 重新导出：前端 mock 改动后需重新导出 `seeds/json/dynamic/*`。
3. **目录 / 动作类型**：规则页、生命周期页的动作类型、事件类型、指标目录在 static / event 模块接真接口后改读接口。
4. **后端缺口**：推演引擎改异步任务（`rules_service.py` 中 `EXTENSION POINT (PDE)`，前端「运行后取结果」改轮询）；动作审批加审批人校验；动作中心补暂存→确认→审批界面。
