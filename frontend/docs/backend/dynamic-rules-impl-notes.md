# 动态本体第二部分：规则 / 情景推演 / 生命周期 实现说明

分支 `dynamic-rules`（基于 master b688d5d）。规格：`dynamic-api.md` §4、§6.3–6.5；CONVENTIONS §7、§16；前端页面与 mock 为准。

## 文件

| 文件 | 内容 |
|---|---|
| `app/modules/dynamic/rules_models.py` | 13 张表：rule / rule_version / rule_draft / rule_hit、scenario / scenario_result / scenario_impact、lifecycle / lifecycle_version / lifecycle_state / lifecycle_transition / lifecycle_change / lifecycle_draft |
| `migrations/versions/dynamic_0002_rules_scenarios_lifecycles.py` | down_revision = static_0002（static 分支先合并；本分支单独跑需先合入 static_0002）；枚举走 enum_column（varchar + CHECK） |
| `app/modules/dynamic/seeds/rules.py` | 种子（RULE_DEFS / RULE_DRAFTS / SCENARIOS / LIFECYCLES + derived/*） |
| `app/modules/dynamic/rules_sim.py` | 推演模型、回放，逐位照搬 mock（simulate / stagePlanOf / replay） |
| `app/modules/dynamic/rules_schemas.py` / `rules_service.py` / `rules_router.py` | 接口；`router.py` 末尾 include 一行 |
| `app/modules/dynamic/sections.py` | 快照 section `rules`、`lifecycles`（`BUILDERS`，build / seed / restore） |

## 接口 ↔ 页面操作（前缀 /api/ontology/v1）

规则（页面 key `rules`）：
- `GET /rules?trigger&status&kw&page&size=16`：左侧列表、筛选、搜索
- `GET /rules/meta`：确认人、参数取值来源下拉、筛选项 [推断]
- `GET /rules/{id}?view=draft&cr`：详情 + 草稿（displayStatus / cr / editable / editTip / hits7d / stagedPending）
- `PUT /rules/{id}/draft`（manage）：编辑阈值、自动暂存开关 / 动作类型 / 确认人 / 参数映射，保存到草稿 [推断]
- `GET /rules/{id}/hits?range=7d|30d&page&size=12`：命中记录
- `GET /rule-hits/{id}`：命中抽屉（告警详情 + 时间线 + 关联动作）
- `POST /rules/{id}:replay {range, version}`（manage）：回放试跑，不写库
- `GET /rules/{id}/versions`：版本历史（草稿行在最前，带 cr）
- 待确认动作数沿用 `GET /action-runs?ruleId&status=待确认`

情景推演（`scenarios`）：
- `GET /scenarios?status&kw&page&size=12`、`GET /scenarios/meta`（范围 / 品类 / 促销下拉）
- `POST /scenarios {from?}`：新建 / 复制为新方案；`PATCH /scenarios/{id} {name}`：改名
- `POST /scenarios/{id}:run {scope, drivers}`：运行 / 重新运行（同步算完，状态直接 已完成）[推断]
- `GET /scenarios/{id}`、`/result`、`/impacts?page&size=6`、`/stage-plan`
- `GET /scenarios:compare?ids=a,b,c`（≤3 个已完成）
- `POST /scenarios/{id}:stage {keys}`：转为暂存动作（ActionService.stage，source.type=推演），之后方案锁定

生命周期（`object-types`）：
- `GET /object-types/{id}/lifecycle?view=draft&cr&ver`：状态机、版本；`?ver=` 为该版发布时生效的状态机
- `GET /object-types/{id}/lifecycle/changes?illegal=1&page&size=6`、`/lifecycle/illegal?since=7d`（历史版本下为空）
- `PUT /object-types/{id}/lifecycle/draft`（manage）：LcDraft 改动（addStates / addTransitions / editTransitions / removeTransitions）[推断]

错误码：STAGE_MAPPING_MISSING 422、SCENARIO_NOT_OWNER 403、SCENARIO_RUNNING 409、SCENARIO_STAGED_LOCKED 409、
SCENARIO_NO_DRIVER 422、SCENARIO_CONFIG_DIRTY 409、COMPARE_LIMIT 422、STATE_INVALID 409、VALIDATION_FAILED 422（details.fields）。

## 裁定 [推断]

- master 上没有变更单（CR）表：草稿存 `rule_draft` / `lifecycle_draft`，`cr_code` 是软引用；没有草稿又不带 cr 的保存 → 409 STATE_INVALID。
- 生命周期草稿只存改动（LcDraft 形状），由页面叠加；后端在保存时校验（重复状态、迁移引用不存在的状态等）。
- 推演同步计算；`SCENARIO_RUNNING` 只在状态是 运行中 时出现（目前接口不会留下 运行中）。
- 整版回滚：快照里没有的规则 / 生命周期 version 置空（列表不再出现），其余按快照 upsert；生命周期缺的版本行按快照补建。
- `rules` 的 seed_section 每个历史版本都用当前 RULE_DEFS（mock 没有规则的历史定义）；`lifecycles` 用 derived/lifecycleAsOf 取发布时间点。

## PDE 需要的共享改动（本分支未改）

1. 变更单表落地后：`rule_draft.cr_code` / `lifecycle_draft.cr_code` 改成外键，草稿保存并入 `PUT /change-requests/{cr}/items`（target_kind = rule / lc_states / lc_transitions）。
2. `catalog.py`（JsonRefCatalog）里规则查找仍读 JSON，可改为读 `rule` 表。
3. export-mock 已有导出覆盖本分支种子；若前端 mock 改动需重新导出 `seeds/json/dynamic/*`。
4. `api.py` 无改动（规则路由经 `dynamic/router.py` 挂载）。
5. 前端 `VITE_API_REAL_MODULES` / apiClient 未动；页面接线未做。
