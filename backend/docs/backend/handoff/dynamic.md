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
- 情景推演（`/use/ops/scenarios`）：列表 / 元数据 / 对比、新建 / 复制、改名、运行（异步，见下）、影响明细服务端分页、暂存计划 / 暂存弹窗。

**dynamic-r2（本轮）**
- 审核意见：推演页“我的方案”改用 `app.user.name`（同告警页，演示身份切换后跟着变）；规则列表为空时不再请求详情 / 命中；动作“重新提交”后选中新动作 `r.id`（mock 同步）。
- `src/api/dynamic.ts`：删掉临时 `write()`，改用 `api.put` / `api.patch`。
- 新建规则：后端 `POST /rules`（未发布 `version=null`、状态变更中；带 `cr` 时同时开草稿 v1）、`POST /rules/{id}/draft`（发起变更 / 加入变更单时登记草稿，幂等；已在别的草稿单 409 `TARGET_IN_OTHER_CR`）、`DELETE /rules/{id}/draft?cr=`（放弃草稿；未发布的新规则一并删除）。列表包含“未发布但有草稿”的新规则。
  前端：真接口模式下「新建规则」= `POST /rules` → 公共 `start()` 登记到所选 / 新建变更单 → `POST /rules/{id}/draft` → 跳到新规则草稿视图；「发起变更」也会 `POST /rules/{id}/draft`，之后“保存到草稿”能落库。
- 目录：规则页、生命周期页的动作类型 / 事件类型 / 参数读 Palantir `src/api/action.ts`（`actionTypes()`、`eventTypes()`、`paramsOf`，action 开关为真接口时先 `loadSnapshot()`）；规则页指标名在 static 开关为真接口时 `loadStatic(['metrics'])` 后读 `METRIC_LIST`，找不到回退 mock。都是只读调用，没改他们的文件。
- 审批人校验：`POST /action-runs/{id}:approve` 只有审批人能批（审批人 = “角色 姓名”，按姓名比对；未指定审批人不限），否则 403 `NOT_APPROVER`；`can.approve` / `can.reasons.approve` 同步。mock 页面没有审批按钮（只有“等待 X 审批”文字），按要求不新增界面。
- 推演异步：`:run` 校验后置“运行中”并回 202（详情），进程内后台任务（FastAPI BackgroundTasks，新开会话）计算；`GET /scenarios/{id}/result` 运行中回 202 `{status:"运行中"}`、失败回 409 `SCENARIO_FAILED`、完成回结果。前端 `waitScenarioResult()` 每 0.8s 轮询（最多 2 分钟）。
- **变更单闭环（dynamic-r3，`VITE_DYNAMIC_CR_WRITE` 仍默认关）**：
  - 后端 `app/modules/dynamic/cr_link.py`：规则 / 生命周期草稿在**同一事务**里调 static `CrWriteService` 登记 cr_target + 写 cr_items（变更单号在 change_request 表里才登记；mock 单号 / 不存在的单号照旧只落 dynamic 草稿表）。规则 kind `rule`，section `conds` / `mapping` / `config`（新规则 config 行全是 add；已发布规则 autoStage.* 为 update）；生命周期 kind `object`（id = 对象类型），section `lc_states` / `lc_transitions`。`PUT …/draft` 可带 `lockVersion`（只校验第一次 PUT），回 `lockVersion` / `crChanges`。`POST /rules` 带库里的 cr 时一并登记 isNew 目标 + config 行，所以提交不会 CR_EMPTY。`DELETE /rules/{id}/draft` 先撤登记再删草稿（CR_LOCKED 时整体回滚，不留孤儿）。
  - 前端 `src/api/dynamic.ts`：`crOpen`（POST /change-requests 带 target / POST …/targets 加入）、`crDropEmpty`、`crErrText`（CR_EMPTY / CR_BLOCKED（列 details.blocked）/ CR_NO_REVIEWER / CR_LOCKED / VERSION_CONFLICT / TARGET_IN_OTHER_CR「已在 CR-xxxx 中」）、lockVersion 缓存；`CrTarget.name`。
  - 规则页：新建规则 / 发起变更走真变更单（CR_WRITE 开时），失败用 `useDraftView.cancel(cr, target)` 撤登记，新建的空单删掉；放弃草稿加确认卡；保存草稿不再把 `{cr,draft}` 当详情覆盖。生命周期页保存草稿同样登记、报错用 crErrText（提交 / 放弃在静态 ObjectTypesBody 的 `useStaticCr('object')` 横幅里）。
  - **默认仍关**。2026-10-26 用私有库 + uvicorn 对真后台 curl 走了一遍：新建空单 → `POST /rules`（带 cr、objKind=order）→ 登记 isNew 目标 + 6 条 config 行 → `PUT …/draft` 带 lockVersion（回新 lockVersion）→ `:submit`（默认审核人王敏，成功，不再 CR_EMPTY）→ 王敏 `:approve`（已通过）→ `POST /releases`（变更单已发布）都通。**断点**：整版发布只给变更单打版本快照，不把 dynamic 的 `rule_draft` / `lifecycle_draft` 应用成已发布版本（规则仍是 version=None、“草稿中”）。需要 static / versioning 提供发布钩子（发布时按 cr 回调各模块 apply），dynamic 侧再实现「把该 cr 的规则 / 生命周期草稿升为新版本」；在此之前开 CR_WRITE 会出现“单子已发布、规则没生效”，所以不开。
  - 新建规则（真接口）先弹「新建规则」框选对象类型（static 真接口取本体对象类型，否则 mock 四类）和名称，再进变更弹窗；`POST /rules` 带 objKind。

## 未完成

1. **变更单仍是前端 mock（CR_WRITE 关时）**：草稿归属 / 草稿视图来自公共 `useDraftView`（mock `DRAFT_TARGETS` / `CHANGE_REQUESTS`），新建变更单号也是前端生成的；后端 `rule_draft.cr_code` / `lifecycle_draft.cr_code` 是软引用，没有外键。`CR_WRITE` 打开后只有种子里已有的变更单（mock 与库里都有，如 CR-0011）能真正提交 / 放弃。
2. **生命周期页**：「发起变更」按钮在静态的 `ObjectTypesBody.vue`（`useStaticCr('object')`，不归 dynamic），由静态侧建 / 加入变更单；dynamic 保存生命周期草稿时幂等登记 object 目标并写 lc_* 行，所以不另做。静态侧放弃整个 object 目标只删 cr_items，不删 `lifecycle_draft`；dynamic 读草稿时忽略「变更单在库里但没登记该 object 目标」的残留草稿（可选：static 放弃 object 目标时回调 dynamic 清理）。
6. **废弃列（不加迁移）**：`rule_hit.store_id` / `store_label`（及告警的同名列）保留不删，接口改出通用 `dims: [{objKind,id,label}]`，目前由 store_id 转换；迁移归 dynamic-r3b（dynamic_0003），之后再改存 JSON。
7. **ObjBehaviorAutomation.vue**：命中对象旁的门店改为本地 `dimsOf()` 转换的通用维度（mock storeId → store 维度）；集成 dynamic-r3b 后可换成其公共 `alertDims()` 类转换。数据仍是 mock。
8. **生命周期对象引用**：ObjRef 现按 objectId 引用任意对象类型；对象注册表（objectRegistry）解析不了的类型（如 member / promotion / refund）在开发环境会有告警，需要 PDE 在注册表加不告警的 `canResolve(kind)` 或补解析器。
3. **审批界面**：动作中心 mock 没有审批 / 暂存→确认→审批的界面元素，按要求没加；后端接口和权限校验已就绪。注意：种子（mock 导出）里部分审批人（如赵磊、吴佳）不是系统用户，这些动作在库里没人能审批；测试里把 ACT-20261025-0551 的审批人改成王敏再审批。
4. **推演异步的限制**：进程内后台任务，进程重启时正在算的方案会停在“运行中”（没有恢复 / 超时回收）；`SCENARIO_CONFIG_DIRTY` 仍走不到（`:run` 把配置写回方案）。
5. **新建规则只建空壳**：名称“未命名规则”、对象 product、无条件；草稿里能改的仍只有自动暂存和阈值（与 mock 页面一致），条件 / 触发方式编辑界面 mock 本身也没有。

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

1. **static-r3 合并后**：把 `useDraftView`（公共，PDE）改为读 `GET /change-requests/draft-targets` + 变更单列表；「新建规则」/「发起变更」改为 `POST /change-requests`（新建，带 `target`）或 `POST /change-requests/{id}/targets`（加入），再 `POST /rules/{id}/draft`；默认打开 `CR_WRITE`（删掉开关）；补 `rule_draft.cr_code`、`lifecycle_draft.cr_code` → `change_request.code` 外键迁移；考虑把草稿保存并入 `PUT /change-requests/{cr}/items`（section `conds|mapping|config`、`lc_states|lc_transitions`）。
2. **PDE 共享文件跟进**
   - `src/env.d.ts`：`ImportMetaEnv` 补 `VITE_DYNAMIC_CR_WRITE?: string`（现在 `dynamic.ts` 里用类型断言读）。
   - `backend/app/modules/dynamic/catalog.py` 里的规则查询仍读 JSON（`seeds/json`），应改为读 `rule` 表。
   - `backend/tests/dynamic_support.py`：夹具改为先建 app（保留 `import app.main` 这一行），合并时保留。
   - 迁移依赖：`dynamic_0002` 依赖 `static_0002`。export-mock：前端 mock 改动后需重新导出 `seeds/json/dynamic/*`。
3. **推演任务队列**：`rules_service.py` `ScenarioService.compute` 的 `EXTENSION POINT (PDE)`：换成真实任务队列，并在启动时把残留的“运行中”标为失败。
4. **审批界面**：需要设计后再接 `approveActionRun`（`can.approve` 已给出）。
5. **规则编号撞号**：`RuleService._next_code` 取“当前最大号 + 1”，两人同时新建规则会撞号返回 500；下一轮与问数 P2 / 会话编号一起按 `ER-` 的办法加锁。
6. **打开 `VITE_DYNAMIC_CR_WRITE` 前必做**
   - 「放弃草稿」必须加确认卡（现在点了直接放弃）。
   - 规则 / 生命周期草稿保存时还要登记 cr_item：`PUT /change-requests/{cr}/items`，规则用 section `conds|mapping|config`，生命周期用 `lc_states|lc_transitions`（`kind: "object"`）；不登记的话提交会返回 409 `CR_EMPTY`。
   - CrTarget 加 `name`（显示名）。
   - 放弃顺序是先调静态 `DELETE /change-requests/{cr}/targets/...` 再删自己的草稿，第二步失败会留下孤儿草稿；需要改顺序或补偿。
   - `rule_draft.cr_code` / `lifecycle_draft.cr_code` 外键迁移排在 `static_0003` 之后。
7. **发起变更失败回滚**：`Rules.vue` 里 `openRuleDraft` 失败时，加入已有单的情况已改为先落库再 `start()`；新建单的情况只切回已发布视图，前端 mock 里的登记（`useDraftView` 的 TARGETS）留着——需要 PDE 在公共 `useDraftView` 加撤销登记（如 `cancel(cr, target)`）。

## 通用化（去零售写死）待办

目标：做成 Palantir 式通用工具，筛选项 / 列由对象类型、属性元数据决定，门店演示只作为样例数据。以下行号按 f1da64e。

1. **告警审批人 / 对象称呼写死**：`src/pages/ops/Alerts.vue:271,275,288`，后端 `backend/app/modules/dynamic/service.py:117-118`——商品 → 「品类经理 王敏」、其他 → 「区域运营 陈立军」，对象称呼写死订单 / 商品 / 门店。改为：审批人取动作类型配置，对象称呼取对象类型显示名。
2. **级别 / 页签状态**：`Alerts.vue:317`（高 / 中 / 低）、`Alerts.vue:59-63`、`src/pages/ops/Actions.vue:53-56`（页签状态）。属流程枚举、非零售，可保留；可选改由现有 `/meta` 下发。
3. **（r3 已做：命中 / 回放样本出 `dims`，规则页“维度”列；存储列未改）门店列 / 门店字段**：`src/pages/ontology/Rules.vue:256-262,283,315,636`（商品类规则加「门店」列、对象列标题写「商品」）；后端 `models.py:106`、`rules_models.py:142`、`schemas.py:30` 的 `store_id` / `store_label`，`rules_service.py:107,343-354` 生成样本时取门店 / 商品。改为：命中 / 告警存通用维度 `dims: [{objKind, id, label}]`（JSON，来自规则去重键），列按该规则的维度生成。
4. **（r3 已做：规则页对象列标题取 titleProp）固定列表列**：`Alerts.vue:159`、`Actions.vue:148`、Rules 各表的列本身是通用的；「对象」列应显示对象类型的标题字段（titleProp），不写死称呼。
5. **情景推演模型（最大缺口）**：
   - 范围写死区域 / 品类 / 周数：`src/pages/ops/Scenarios.vue:108-110,223,445-446,524`；`rules_schemas.py:58-59`；`rules_service.py:654,692-697,778,837`。
   - 驱动写死价格 / 补货 / 促销：`Scenarios.vue:170,455-460,486,548-550`；`rules_schemas.py:64-69`；`rules_service.py:504-506,701`。
   - 影响明细列写死商品 / 门店 / 销售额 / 缺货率：`Scenarios.vue:259-262,502`；影响表 `rules_models.py:194-205`（sku / category / store_id）。
   - 建议动作写死「发起调价审批 / 补货申请 / 发放复购召回优惠券」：`rules_sim.py:195-285`、`Scenarios.vue:342`。
   - 改为元数据定义的「推演模板」：范围 = 某对象类型属性上的筛选；驱动 = 带范围的数值参数（或动作类型参数）；KPI = 指标；影响行 = 通用 `{objRef, dims{}, metrics{}}`；建议 → 动作类型映射。推演引擎按模板可插拔，门店演示是其中一个样例模板。
6. **（r3 已做）生命周期对象引用**：`src/pages/ontology/parts/object/ObjBehaviorLifecycle.vue:262` 写死 `['order','store','product']`。改为任意对象类型（ObjRef 按 objectId）。`:294` 的触发方式是通用的，保留。
7. **（r3 已做：新建弹窗选对象类型，不传时取本体第一个）新建规则默认对象类型**：`rules_service.py:387` 默认 `"product"`。改为新建弹窗里必选对象类型。

**依赖**
- static：`GET /object-types/{id}/meta`（`{ titleProp, properties: [{ key, name, dataType, searchable, filterable, sortable, listed, order, visibility, format, unit }] }`）和 `GET /object-types/{id}/facets?filters=...`（enum 取值 + 计数 / range 最小最大，随已选筛选联动），以 master 770f6d7 的 `backend/docs/backend/generic-object-explorer.md` 为准。用于第 3、4、5、6 条（列、标题字段、范围筛选取值，替换 `SC_AREAS` / `SC_CATEGORIES` / `PROMO_TYPES`）。
- Palantir：动作类型的审批人配置（第 1 条）。
- 其余（推演模板模型、通用维度、第 7 条）可在 dynamic 内完成。

**PDE 共享文件请求（dynamic-r3）**
- `src/api/static.ts`：导出 `registerDraft`（dynamic.ts 里暂时复制了同样逻辑 `registerCR`）。
- `useDraftView.ts` / `env.d.ts` / router：无需改动。
- `ObjectTypesBody.vue`（static）：可选——放弃 object 目标后通知 dynamic 删 lifecycle_draft。
- objectRegistry：不告警的 `canResolve(kind)`（见未完成第 8 条）。
- static / versioning：整版发布时按变更单回调各模块 apply 的钩子（dynamic 用来把规则 / 生命周期草稿升为已发布版本；CR_WRITE 默认开的前提）。
