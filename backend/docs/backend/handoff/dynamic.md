# 动态本体交接说明（dynamic）

## 2026-10-26 运行页分页与卡顿清理

- 告警/动作已有SQL分页，原每次翻页却重建完整动作子定义、全部规则和命中汇总；改用只含姓名/系统/对象标签的轻目录，`actions/meta`只读动作头，不加载参数、条件、写回及规则。动作详情也走轻目录；告警详情仅读取所选告警关联的一条规则/动作头及该规则命中计数。
- 情景列表原全表ORM→Python筛选切片，影响明细同样全量切片；现均在DB做过滤、count和LIMIT/OFFSET，列表只解析本页引用的本体版本。情景meta的已存范围取值用SQL DISTINCT投影，不加载所有方案配置。没有引入执行器、外部请求或迁移。
- 三页及影响明细默认10；前端每次page/size/filters变化请求后台。加载下一页保留真实total，避免分页器先收到0而退回第1页。身份/版本/预览切换清空旧上下文，迟到页/详情/元数据/影响响应不能覆盖当前；真模式错误不回演示数据。
- 告警规则筛选新增`GET /alerts/meta`（alerts:view，真实`{rules:[{id,name}]}`）。标签统计后缀已移除；情景页不再展示长篇“他人方案只读”及“执行器未配置”说明段，真实禁写及必要按钮原因仍保留，历史结果仍读DB。
- 量测：同独立DB、page=2/size=10，修改前告警18 SELECT、动作19 SELECT；去掉全定义目录后分别9/10 SELECT。单次耗时有冷启动波动，不作为提速SLA。SQL专项断言无动作子表/全量命中读取且数据查询带LIMIT/OFFSET；前端Vue VM专项验证翻页请求、total不归零、筛选及上下文迟到隔离，见`test_dynamic_pagination_perf.py`、`check-dynamic-pagination.mjs`。

分支 `dynamic-rules`（基于 master b688d5d，线性包含 dynamic-impl ee06847）。负责人：动态本体负责人。
合并顺序：static 分支（含 `static_0002`）先合，dynamic 后合。

## 2026-10-26 C组真实写链收尾（覆盖下方历史演示执行说明）

- 动作目录每请求读取真实Rule/ActionType/ExtSystem/ObjectType及在用用户，元数据按稳定动作code索引，中文名仅唯一时兼容；不从JSON补审批人。真实系统env是字符串，按实际模型输出。审批文字必须能定位唯一在用用户，空配置/角色泛称/人名子串/歧义均`approvalConfigured=false`，新动作503 `ACTION_APPROVAL_NOT_CONFIGURED`；明确免审才走免审。
- 请求不能覆盖已发布审批人。新动作软引用approver/confirmer列存真实userCode，接口联查真实姓名；旧“角色 姓名”仅在能定位唯一在用用户时兼容授权。未指定审批人不能让任意用户批准；免审确认后进入执行器，缺通道整请求503回滚。默认条件检查已接真实受限DatabaseSubmissionChecker（支持矩阵见action-checker.md），未知/自然语言/缺上下文明确failed；默认提交、事件投递、自动对账仍为UnconfiguredAdapters，测试替身只能显式注入，不返回伪成功回执。
- 规则条件阈值/自动暂存草稿与静态CR同事务；R-012既有`固定值：仓库调拨`原被旧JSON白名单误拒，现候选读实际DB配置/事件载荷字段，确认人解析真实在用用户，固定值按受控前缀兼容。发布同步action_type_id/confirmer，避免改autoStage后运行目录仍引用旧动作。
- `rule/conds|mapping|config`、`object/lc_states|lc_transitions`已登记真实发布钩子。作者U010004创建CR→POST规则→保存→提交→U010001审批→整版发布联合验收，rule v1已发布、生命周期快照变化且skipped为空；静态放弃对象目标同事务删除LifecycleDraft，重新加入不复活旧编辑。
- 生命周期草稿保存验证实际列长度、状态/迁移唯一键和类型；迁移id最多16字，完整版本编号最多64字。保存非法422，发布内部防御409，整版回滚。规则/生命周期回滚后再发布取历史最大版本+1，append-only旧版本保留，不撞旧vN。
- **尚未实现真实执行引擎**：规则回放503 `RULE_EVALUATOR_UNAVAILABLE`；情景run/stage-plan503 `SCENARIO_EXECUTOR_UNAVAILABLE`。不再随机生成新增命中或用示意模型生成预测/固定REG-EC建议；已存DB历史result/impacts/compare保留。推演meta标`executorConfigured=false`，业务范围/促销候选来自已存Scenario的scope/drivers，模板只作参数配置说明，不能当成已接入真实数据集/引擎。
- 本段工作无新增迁移（**更正 2026-10-27**：此说法不准确，见文末「dynamic-r4」的迁移清单。）编号锁沿用既有实现；不改mock、不删开发库数据。专项：`test_dynamic_cr_acceptance.py`、`test_dynamic_catalog_db.py`、`test_dynamic_unconfigured.py`及必要既有动态测试。通过正式联合验收后仅在ignored的`frontend/.env.local`开启`VITE_DYNAMIC_CR_WRITE=1`。
- 同C组近7天读链已改滚动时钟：规则list/detail/catalog的weekly及hits7d按实际RuleHit计数；生命周期当前迁移weekly按实际LifecycleChange计数（同逻辑迁移跨版本），非法迁移默认窗口`[now-7天, now]`并排除未来。定义/历史快照的weekly仍保留，不将历史声明值当当前统计；Alert.recent7d按该告警触发时刻回看7天的已定口径不改。专项用边界/过期/未来行及推进时钟90天验证归零。

## 已完成

- **2026-10-26 动作日编号与纯日志并发**：`dynamic_0004`（接 `qa_0005`）增加内部 `action_run_daily_counter`，所有 `create_run` 调用共享 `ActionRunRepo.next_code` 的独立短事务按日预留；主池占满仍能分配，日锁不跨业务事务或外部调用。编号包含历史最大号；业务回滚/取消不复用已预留号，允许空号；分配事务失败则回滚预留。`ActionRunRepo.add_log` 以父行锁保护 `max(seq)+1`，覆盖不修改父状态的「仍无法确认」追加日志；未扩展尚无纯追加入口证据的告警日志。测试 `test_dynamic_action_numbers.py`（临时独立库）、原动作/告警流程与迁移往返。

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
  - **默认仍关**。2026-10-26 用私有库 + uvicorn 对真后台 curl 走了一遍：新建空单 → `POST /rules`（带 cr、objKind=order）→ 登记 isNew 目标 + 6 条 config 行 → `PUT …/draft` 带 lockVersion（回新 lockVersion）→ `:submit`（默认审核人王敏，成功，不再 CR_EMPTY）→ 王敏 `:approve`（已通过）→ `POST /releases`（变更单已发布）都通。**断点**：整版发布只给变更单打版本快照，不把 dynamic 的 `rule_draft` / `lifecycle_draft` 应用成已发布版本（规则仍是 version=None、“草稿中”）。dynamic 侧已实现 `apply_cr` 并登记到静态 r4 的写回钩子（见下条）；钩子合入 master 前开 CR_WRITE 会出现“单子已发布、规则没生效”，所以不开。
- **发布写回（dynamic 侧，r3 第三批）** `backend/app/modules/dynamic/writeback.py`
  - `apply_cr(session, cr_code, *, by, at=None) -> {"rules": [...], "lifecycles": [...]}`：同一事务（只 flush 不 commit）把该单的草稿转正，返回实际转正的 code。
    - 规则：新规则 → v1；已发布 → version+1，追加 `rule_version`（by 传用户 code 会换成姓名）；状态还原：变更中 → 已发布，**已停用保持已停用**；写 published_by/at、change_note；删 `rule_draft`。
    - 生命周期：已发布状态机叠加 edits（addStates / addTransitions / editTransitions / removeTransitions，规则同页面）→ 新 `lifecycle_version`（code `{lc}:v{n}`，带 snapshot）+ 状态 / 迁移行，`lifecycle.version` 指向新版；删 `lifecycle_draft`。状态类型 / 触发方式 / 起止状态无效 → 409 `WRITEBACK_FAILED`（与静态同码，整个发布回滚）。
    - 幂等：本单没有草稿行就什么都不做。单目标入口 `promote_rule(session, cr, rule_code, by=, at=)` / `promote_lifecycle(session, cr, lc_code, by=, at=)`。
  - **挂静态钩子**：静态 r4 `app/modules/static/writeback.py` 的 `register_writeback(kind, section, fn)`，钩子签名 `async (ctx: Ctx, target_id, rows: list[CrItem]) -> set[str]`（ctx.session / ctx.cr.code / ctx.by / ctx.now）。本模块 import 时 `register_hooks()`：有该函数就登记 `(rule, conds|mapping|config)` → `rule_hook`、`(object, lc_states|lc_transitions)` → `lifecycle_hook`（都调 promote_*，返回空集合 = 不改静态快照节）；master 上没有该模块则 `HOOKED=False`，只暴露 apply_cr。由 `rules_router.py` import 触发登记（app 加载即生效）。已用 static-r4 工作区代码（含未提交改动）只读拷贝验证：5 个 (kind, section) 都登记成功，重复登记幂等。
  - 测试：`test_apply_cr_promotes_rule_drafts`（新规则 / R-015 改动 / R-025 停用 + 幂等）、`test_apply_cr_promotes_lifecycle_draft`（加状态 + 加迁移 + 删迁移 + 幂等）、`test_apply_edits_rejects_bad_kind`，都直接调函数。
  - **CR_WRITE 仍关**：master 上没有静态写回钩子，e2e 发布不会转正草稿；static-r4 合并后（钩子生效）再按上面的 e2e 走一遍，规则变 v1 / 已发布即可打开。
  - 2026-10-26 更新：static-r4 已合入 master `58ad8d7`（`register_writeback` 生效，本分支合并后 `HOOKED=True`）。下一轮：合并后按 `/tmp/e2e.sh` 重跑 e2e，并按 PDE 意见补一条“规则发布写回”的联合测试；通过后再打开 `VITE_DYNAMIC_CR_WRITE`。未登记的 kind/section 会被跳过但单子仍标已发布（Palantir 非阻塞意见），前端需给出提示。
  - 告警审批人目前取 `ACTION_META.approver`，下一轮改为查动作类型表的 `approval` 配置（Palantir 意见①）。
  - 注意：钩子只对「本单有 cr_item 的段」调用；一张单里开了草稿但没登记任何行（例如只点了发起变更没保存）的规则不会被转正，草稿留在单上（与静态“没改就不写回”一致）。需要的话静态可在 apply_crs 末尾对每张单调一次 `apply_cr(session, cr.code, by=by, at=now)` 兜底（幂等，不会重复转正）。
  - 新建规则（真接口）先弹「新建规则」框选对象类型（static 真接口取本体对象类型，否则 mock 四类）和名称，再进变更弹窗；`POST /rules` 带 objKind。

## 未完成

1. **变更单仍是前端 mock（CR_WRITE 关时）**：草稿归属 / 草稿视图来自公共 `useDraftView`（mock `DRAFT_TARGETS` / `CHANGE_REQUESTS`），新建变更单号也是前端生成的；后端 `rule_draft.cr_code` / `lifecycle_draft.cr_code` 是软引用，没有外键。`CR_WRITE` 打开后只有种子里已有的变更单（mock 与库里都有，如 CR-0011）能真正提交 / 放弃。
2. **生命周期页**：「发起变更」按钮在静态的 `ObjectTypesBody.vue`（`useStaticCr('object')`，不归 dynamic），由静态侧建 / 加入变更单；dynamic 保存生命周期草稿时幂等登记 object 目标并写 lc_* 行，所以不另做。静态侧放弃整个 object 目标只删 cr_items，不删 `lifecycle_draft`；dynamic 读草稿时忽略「变更单在库里但没登记该 object 目标」的残留草稿（可选：static 放弃 object 目标时回调 dynamic 清理）。
6. ~~废弃列~~（集成时已做）：`rule_hit.store_id` / `store_label` 与告警同名列一起在 `dynamic_0003` 换成 `dims` JSONB（升级迁值、降级还原两列）；接口 `dims` 直接读列。
7. ~~ObjBehaviorAutomation.vue 本地 dimsOf()~~（集成时已做）：mock 命中 / 回放样本由 `mock/rules.ts` 用公共 `alertDims()` 给出 `dims`，页面直接读 `dims`（Rules.vue 的 `dimsOf` 只剩 `h.dims ?? []`）。mock 数据值不变。
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

## dynamic-r3b：运行页去零售写死（告警 / 动作中心 / 推演模板）

分支 `dynamic-r3b`（基于 master `b475283`）。只动运行页；规则、生命周期、变更单部分归 `dynamic-r3`。

**已做（对应上文「通用化」第 1 条、第 3 条告警部分、第 4 条）**
- 迁移 `dynamic_0003`（down_revision `static_0003`）：`alert.store_id / store_label` → `alert.dims`（JSONB，`[{objKind,id,label}]`）。升级时原门店值迁成 `{objKind:"store",…}`，降级还原两列。
- 告警接口：`AlertOut` 去掉 `storeId/storeLabel`，加 `dims[{objKind,id,label,kindLabel}]`、`objKindLabel`（对象类型显示名，取对象类型目录）；`AlertDetail.approver` = 规则绑定动作类型的审批人配置（`ACTION_META.approver`，Palantir 动作类型表接上后换查库），未绑定动作为 null。`:start-action` 的审批人同样取动作类型配置（`approver_for_action`），不再按对象类型写死王敏 / 陈立军。演示值不变（R-007/R-012 → 品类经理 王敏，R-015/SM-ORD → 区域运营 陈立军）。
- `Alerts.vue`：对象称呼、审批人、处理记录文字都取元数据；列表「对象」列后按 dims 追加维度值，详情按 dims 逐个显示（标签 = 维度对象类型名）。mock 回退用 `mock/dynamic.ts alertDims()` 把演示门店整形成 dims，值不变。
- `Actions.vue`：对象称呼真接口取 `/actions/meta.objKindLabel`，mock 取 `OBJ_KIND_LABEL`；未登记类型显示编号。

**没做 / 待办**
- 告警筛选按 `/facets` 生成：告警页现在只有级别 / 规则 / 关键词（流程字段），没有区域 / 门店筛选可替换。要按告警对象类型的可筛选属性筛，后台得 join 业务表（facets 的筛选值落到 alert.obj_id），下一轮和规则命中 dims 一起做：`GET /alerts?filters=<JSON>&objKind=` 复用 static 的 filters 校验（422 规则一致）。
- ~~`mock/rules.ts` 命中 / 回放读 `Alert.storeId`~~：集成时已改为 `dims: alertDims(…)`；`rule_hit` 两列并入 `dynamic_0003`。
**情景推演模板化（已做，dynamic-r3b 第二轮）**
- 模板定义唯一来源 `src/mock/scenarioTemplates.ts`（`ScTemplate`）；后台读同一份导出 `backend/seeds/json/dynamic/SC_TEMPLATES.json`（改 TS 后重新导出，测试会比对 `/scenarios/meta.templates` 与该 JSON）。演示模板 `demo_retail`（目标对象类型 product）原样复现原页面：范围 area / category / weeks，三组驱动，6 个 KPI，主序列，影响明细列，对比行，建议动作 → 动作名。
- 引擎按模板 `engine` 键插拔：后台 `app/modules/dynamic/scenario_templates.py` `ENGINES`（`demo_retail` = 原 `rules_sim.simulate / stage_plan`，未改一行）；前端 `SC_ENGINES`（`demo_retail` = `mock/scenarios.ts simulate / stagePlanOf`，值不变）。
- 迁移并进 `dynamic_0003`：`scenario.template_code`（String 64，存量全部 `demo_retail`），降级删列；仍只有一个 dynamic 迁移。
  - （更正：`dynamic_0003` 在已应用后被原地修改过，已应用旧版本的库由 `dynamic_0023` 收敛，见文末。）
- 接口：`ScenarioOut.templateCode`；`/scenarios/meta` 增加 `templates`（原 areas / categories / promoTypes / baseDrivers / statuses 保留，baseDrivers 取模板默认）；`:run` body 改为 `{scope:{…}, drivers:{…}}`（任意键），按模板字段校验：未知键「模板里没有这一项」、类型、可选值（沿用原文案）、min/max → 422 `VALIDATION_FAILED`；依赖项关闭时按 `offValue` 归零；无驱动 → `SCENARIO_NO_DRIVER`（文案取模板 `noDriverHint`）。新建 / 复制按来源方案模板（缺省演示模板）取默认值。对比 sameScope 按 `(templateCode, scope)`。202 + 轮询、savepoint 失败处理不变。
- `Scenarios.vue`：范围 / 驱动表单、KPI 卡格式与提示、折线序列、影响明细列（obj / money / delta / range / text 通用列类型）、对比行、暂存确认里的动作名都按模板渲染；页面代码里不再有区域 / 品类 / 门店 / 商品 / 促销等字样。
- 范围取值接 static 分面（第三轮）：范围项声明 `propRef: "对象类型.属性"` + `allOption`（演示：area → `store.region_code`、category → `product.category_id`）。引擎注册表带能力位 `accepts_facet_values`（前端 `SC_ENGINES.acceptsFacetValues`）：为 true 时，真接口下前端调 `GET /object-types/{ot}/facets`，该属性 `hasData` 且有取值 → 可选值 = allOption + 分面取值；mock / `hasData:false` / 属性不可筛选 → 模板固定 options。后台 `:run` 用同一规则（`scenario_templates.scope_options`）校验，前后台 422 一致。演示引擎 `demo_retail` = false（只认固定项，分面原值是编码会算失败），所以演示模板不取分面、只用固定 options。
- 无头 Playwright 冒烟（mock）：推演 / 告警 / 动作中心渲染、新建方案运行完成、无控制台错误。
- 没做（与原设计的差距）：模板还是文件元数据，没建 `scenario_template` 表和 `GET /scenario-templates`；分面值直接用属性原值（编码），没取显示名；影响明细表 `scenario_impact` 仍是演示引擎的行结构（模板 `impact.columns` 把行字段映射到通用列），没改成 `{objKind,objId,dims,metrics}`；建议动作仍按动作名映射，没接动作类型编号。


### 动态路径编号 NUL 边界修复（2026-10-26）

本地只读 GET `/alerts/bad%00id`、`/action-runs/bad%00id` 已复现驱动 500。`dynamic/router.py` 的全部 11 个 `{code}` 路径统一使用模块内 `DynamicPathCode`（Annotated Path，排除 NUL），非法编号在路由边界返回 422 VALIDATION_FAILED。`tests/test_dynamic_path_validation.py` 仅用 users 基础夹具、有效请求体逐路由验证 `details.fields=[{field: "code", ...}]`（统一错误合同去掉 path 前缀），正常缺失编号仍为 404；未改仓储、模型、规则路由或全站中间件。

### 运行处理闭环（2026-10-27，本轮静态验证）

- 告警、动作的列表、计数、详情和写入入口复用 `scoped_instances`，同时保留当前本体 RLS。没有受控业务映射的本体不借用零售表。告警维度引用也按对象行权限过滤。
- 转派目录必须携带 `alertId`，先验证本人可见该告警，再筛选当前本体在用 owner/editor、真实 alerts manage 和该实例可见性。新处理人持久化稳定用户编号，展示姓名及编号；旧姓名仅在当前本体能唯一解析时兼容。批量认领/关闭先锁住可见记录；批量关闭跳过关联动作仍在途的记录，响应返回实际处理编号及数量。
- `GET /actions/available` 与 `GET /actions/{code}/targets?page=1&size=10` 提供已发布动作及真实对象候选。复用 `execution_definition`、`action_operation_objects`、对象操作授权、适配器本地配置与审批人真实权限；缺配置显示不可用原因。候选对象还与审批人的行权限取交集。
- `POST /actions/{code}:validate` 仅复用参数、条件、编辑计划及副作用预检，不创建 ActionRun、日志、队列或业务结果，不调用外部系统。手动暂存再次走相同预检；只有实际成功的暂存响应才显示待确认，按本体、本人和请求键去重。公共暂存接口只接受手动来源，规则/告警/推演来源由对应受控入口生成。
- 告警发起动作填写实际 JSON 参数，不把触发值摘要猜作参数。缺少规则定义仍展示原告警和处理记录；触发描述优先 `trigger.text`，缺条件不渲染空白条件行。
- `dispatchPhase` 来源是 `WritebackCall` 的排队/领取阶段，界面分别显示待执行、调用中。待执行不能人工对账；领取中也阻止人工结论，直到真实自动对账任务已进入人工处理阶段。未知结果继续走已有对账契约，不增加业务状态或重发写入。
- 动作参数按结构化定义核验跨对象引用，复用已有对象绑定/行权限；无法证明可见的旧摘要、日志及外部原因不输出。确认、审批、重提、对账重新验证完整引用与涉及对象的操作授权；不通过时保留原记录，不猜权限或参数。撤回仍只允许既有确认人且不执行动作。
- 两页默认 10 条服务端分页，接收后端收拢页码；页签不带数字，空表仅显示一处空态。上下文切换会丢弃迟到响应并清空弹层、选择和参数；实际对象类型使用通用对象引用。

本轮没有新增表或迁移（**更正 2026-10-27**：此说法不准确，见文末「dynamic-r4」的迁移清单。），没有提交 CR、生成告警、暂存/确认动作、修改权限、执行外部请求或后台测试。仅完成 AST/Ruff 与前端类型检查；以下实际路径待主会话浏览器核查，不代表已执行通过：

1. `/use/ops/alerts?ontology=1` 查看真实来源、触发描述、缺条件行、分页；可展开转派名单但取消，不认领/转派/关闭记录。
2. `/use/ops/actions?ontology=1` 查看历史/未知结果、可用性原因；打开「发起动作」，若存在真实可用候选则选择实际对象、保留缺参 `{}`，仅点击「校验参数与条件」后取消。不得点击「暂存待确认」「确认提交」「审批并执行」「重新提交」或提交人工对账。
3. 切到本体 2 检查空列表和元数据隔离，不创建样本。非空写入、并发、权限拒绝、真实执行与对账结果均未验证。

主会话实际 UI 证据（2026-10-27）：

- 零售本体的真实告警 `AL-20261025-0143` 已显示人类可读的生命周期触发描述及真实条件；转派目录实际展示王敏 `U010001`、邱桂英 `U010245` 等稳定编号。仅打开查看，未认领、转派或关闭。截图：`frontend/shots/use-completion-20261027/alert-transfer-options.png`。
- 动作 `ACT-20261025-0550` 的确认人和当前身份均为王敏；界面已按服务端参数不可核验的真实原因禁用确认，不再误说“不是确认人”。未补权、修改或激活旧动作。
- 手动动作弹窗已真实加载动作目录，各项展示审批人或受控映射未配置的原因，暂存按钮禁用。期间开发 HMR 重建导致弹窗收起，不记作产品流程失败；全组冻结后仍需最后打开/取消核查。
- 上述证据不包含批量关闭、暂存、参数校验请求、确认提交、生产动作、非空对账或并发执行验证。

### 使用区针对测试完成（2026-10-27，基线 9d31149）

用户后续明确授权完整针对测试，以上仅静态检查/未执行的限制是前一阶段记录。本阶段报告见 [告警与动作测试记录](../../../../docs/26-10-27-use-tests-ops.md)，完整命令与 [JUnit](../../../../docs/use-tests-ops-results.xml) 一并保留。

- 最终 12 文件完整合集 **68 passed / 0 failed / 0 error / 0 skipped / 0 xfail，277.29s**，只有既有 pytest_asyncio 弃用警告。包括告警/动作/认证/未配置/分页性能、动作映射/checker/目录、对账、共享 upsert、新运行链、动作编号/日志并发；没有以重叠小组相加计数。诊断轮的 25 pass/28 fail 与 fixture/旧契约/产品问题分类均保留在报告，不掩盖早期失败。
- 产品修复：共享 AuditLog 可空本体追溯列不再改变全局 upsert 唯一键；未配置动作提交通道返回准确 ACTION_EXECUTOR_UNAVAILABLE；create_run 把整数规则版本规范为字符串；本地成功日志不再虚构外部单号 None。nullable 外部系统执行定义沿用 `FOR UPDATE OF ActionType` 并经真实 PG 回归。
- 页级权限核验批处理复用原 scoped_instances、当前本体 RLS、行谓词和编号唯一性；引用未知或不可见仍隐藏参数/日志并拒绝写入。受控 5/13 行告警 SELECT 从 27/35 变为 23/23，动作 42/42；只缓存本请求元数据，不共享权限结果。
- ops 自动化只写 `rengu_test_use_ops_20261027`，真实成员与角色绑定，不 mock auth；普通外部 HTTP transport fail-fast。编号用例在该唯一库各自本体隔离，保留独立事务技术证据，不 DROP/TRUNCATE 基线。开发库只读、auto_ontology 未访问，没有生产 worker 或外部执行。
- 主任务另授权 QA 隔离库 8001/5282 的精确本地 UI 链：`ops_local` / `OPS-UI-LOCAL` 两次 enabled true/false 确认成功，最终标记恢复 false；QA agent 只读核恰 2 个 succeeded/INTERNAL Run、各 1 个 ActionExecuted、0 个 WritebackCall。第二条新日志为“动作执行成功”，第一条旧日志不改。参数 `{}` 预检 422 的 inline 必填错误持续显示，改 true 后立即清除，重预检 200 后取消未增加 Run；确认卡准确显示免审与外部系统“—”。专用测试服务已由主任务关闭，数据保留。
- 所属 16 文件 Ruff 与前端 typecheck 通过。没有全仓、独立迁移恢复、生产队列、真实外部对账或分布式负载结论；ERP/Kafka、预测模型继续后置。功能代码已冻结，该阶段无新增迁移（**更正 2026-10-27**：此说法不准确，见文末「dynamic-r4」的迁移清单。）

## dynamic-r4（2026-10-27）：0003 收敛迁移、推演模板入库、告警分面

**dynamic 迁移完整清单**（更正上文多处「无新增迁移 / 只有一个 dynamic 迁移」）：
| 迁移 | 接在 | 内容 |
| --- | --- | --- |
| `dynamic_0001` / `dynamic_0002` | — / `static_0002` | 告警、动作；规则 / 命中 / 情景 / 生命周期 |
| `dynamic_0003` | `static_0003` | `alert` / `rule_hit` 门店列 → `dims`，`scenario.template_code`（**已应用后被原地改过**） |
| `dynamic_0004` | `qa_0005` | `action_run_daily_counter` 动作日编号 |
| `dynamic_0015` | `event_0014` | 规则定时游标与运行历史（opt-in，无种子回填） |
| `dynamic_0023` | `notify_0022` | 幂等收敛 `dynamic_0003` 的原地修改 |
| `dynamic_0024` | `dynamic_0023` | `scenario_template` 表（当前 alembic 单头） |

- `dynamic_0023`：任何停在旧版 0003 的库都会收敛到新版 0003 的终态。具体是：`alert` / `rule_hit` 缺 `dims` 时补列；残留 `store_id/store_label` 时回填 `dims=[{objKind:"store",id,label}]` 再删旧列（回填期间临时 NO FORCE RLS，结束后恢复 FORCE）；`dims` 统一为 JSONB NOT NULL、无默认值、带注释；重建 `rule_hit` append-only 触发器；补 `scenario.template_code`。已是终态的库执行后无变化。downgrade 为空操作。测试：`test_dynamic_0023_reconcile.py`（临时库篡改成旧形态后升级，与全新库的结构比对一致，再降级和升级一次）。
- `dynamic_0024`：`scenario_template` 表（按本体隔离，带 RLS/FORCE，唯一键 `(ontology_id, code)`）。把原 `seeds/json/dynamic/SC_TEMPLATES.json` 的配置迁入 ontology 1（`is_seed=true`，`ON CONFLICT DO NOTHING`）；其他本体不写任何数据。`scenario_templates.py` 改为读库。新增 `GET /scenarios/templates`；`meta` 在当前本体没有模板时返回空，并给出 `unavailableReason`；推演页对应显示空状态。**`:run` / stage-plan 仍然是 503 `SCENARIO_EXECUTOR_UNAVAILABLE`**（用户决定不接示意引擎）。
- 告警分面：`GET /alerts` 新增筛选参数，`level` / `ruleId` / `objKind` 支持逗号多选，`dim=objKind:id` 可重复（同一对象类型多个值取或，不同类型取且）。新增 `GET /alerts/facets`，筛选参数与列表相同，返回 `level` / `ruleId` / `objKind` / `dims` 计数；每一项计数按其余已选条件过滤、不受自身选择影响，`dims` 每个对象类型最多返回 50 个值；行级可见性与列表一致（`scoped_instances`）。告警页在真接口模式下给级别 / 规则选项附上计数，并新增对象类型和维度多选；mock 模式不变。注意：之前计划的 `filters=<JSON>`（join 业务表、按对象属性筛选）**没有做**，目前只按告警自带的 `obj_kind` 和 `dims` 筛选。
- 本轮只写了最小自测：0023 收敛 1、推演模板 / 情景生命周期共 20、告警分面 1；另跑了 `vue-tsc --noEmit` 和改动文件的 Ruff。

**交给 Codex 测试的部分（本轮未写测试或未跑）**
1. 规则回放（`RULE_EVALUATOR_UNAVAILABLE` 以外的回放路径、样本 `dims`）。
2. 规则定时调度（`dynamic_0015` 游标、运行历史、worker 调度）。
3. 发布写回（`apply_cr` 规则 / 生命周期草稿晋升、幂等、`WRITEBACK_FAILED` 回滚）。
4. 事件去重（规则命中 / 告警的去重键、重复事件不重复生成告警）。
5. 告警分面 UI（`Alerts.vue` 的对象类型 / 维度多选、计数显示、切换本体后清空选择、翻页回第 1 页），后端分面只有一个一致性测试。
6. `dynamic_0024` 在非 1 号本体、真实快照库（`database/exports/rengu_ontology-2026-10-27.sql`）上的升级。
- 本轮观察到的既有失败（与本轮改动无关，未修）：`test_dynamic_rules.py::test_hits_paging_and_detail`（AL-…0126 / 0127 排序）；`test_migrations.py` 有 3 个失败（`notify_0022` 命名检查找不到 `app/modules/notify`，`notify_0022` 降级报错），连带 `test_dynamic_migration.py` 往返受阻；`test_dynamic_alerts.py` 的 3 个 `start_action_*` 用例在私有空库上 fixture 报错（`grant_fixture_roles` 找不到 U010001 / R-01）；`rule_runtime.py:268` E501。

## dynamic-r5（2026-10-27）：告警按对象属性筛选、单对象动态摘要

无新增迁移，已变基到 master c3db490，alembic 单头是 `admin_0025`。前端未改（`filters` 目前只有接口能用，告警页没有入口）。

- **告警 `filters=<JSON>`**（更正 r4 的「没有做」）：`GET /alerts` 和 `GET /alerts/facets` 新增 `filters`，格式与 `GET /object-types/{id}/facets` 的 `filters` 相同（`{"属性编码": ["枚举值", …]}`，区间写 `{"min":…,"max":…}`）。用法：
  - 必须同时给且只给一个 `objKind`；不给或给多个都返回 422（字段 `objKind`）。
  - 解析和校验全部复用 static / 通用对象浏览：`parse_filters` 负责非 JSON 和非对象的 422；`Bound.check` 负责未知属性、不可筛选属性、敏感或隐藏属性的 422（字段 `filters.<key>`）；`Bound.cond` 负责取值形态的 422（枚举要给数组，区间要给 min/max）。对象类型不存在或未接入数据时返回 422。
  - 命中对象的范围取 `Bound.stmt(user)`，也就是对象行级范围加上对象读取权限；告警自身仍走 `scoped_instances`。最终条件是 `alert.obj_kind = objKind AND alert.obj_id IN (命中对象编号)`。
  - 本地数据源用子查询；外部数据源先取编号再 IN，上限 5000 个，超过返回 422「请收窄筛选条件」。
  - 分面接口里，`filters` 对每一项都生效，包括 `objKind` 分面本身（否则计数会混入其他对象类型），这和其余参数「不受自身选择影响」的规则不同。同一请求内的条件有缓存。
  - 代码在 `service.py` 的 `AlertService._object_filter`，接入点是 `_filtered`。
- **单对象动态摘要**：`GET /object-types/{objectId}/objects/{objId}/dynamic-summary?limit=5`（`limit` 取 1–50，表示每段最近几条），给问数和 AI 只读使用。代码在 `summary.py` 的 `ObjectDynamicSummary`，路由在 `rules_router.py`。
  - 入口与对象详情相同：`require_object_read`（`object-search` 或 `object-detail` view），并声明 `require_api_scope("objects:read")`，所以 API 密钥服务账号持 `objects:read` 即可调用，其他 scope 的密钥返回 403。对象类型不存在返回 404。对象本身先过 `visible_references`（与告警维度同一行级加唯一编码谓词）：不可见返回 404，不区分「不存在」，没有接入行级映射的对象类型也一律 404。
  - `alerts`：需要 `alerts` view。返回未关闭（待处理加处理中）告警的 `openCount`、`byStatus` 和最近 `latest`；复用 `AlertService._filtered`、`_visible_list`，输出和 `GET /alerts` 相同。
  - `ruleHits`：需要 `rules` view。返回 `total` 和最近 `latest`，经 `scoped_instances`，输出用 `RuleService.hit_out`。
  - `lifecycle`：需要 `object-types` view（与 `/lifecycle/*` 相同）。对象类型没有生命周期时为 `defined:false`。有生命周期时返回：
    - `current.state`：优先读对象数据里的生命周期字段（`Lifecycle.field` 是展示文本，依次按属性编码、括号里的编码、显示名去匹配已接入属性；读取时使用对象详情同一套绑定和行级范围）；读不到时退回最近一次变化的 `to`，并在 `source` 里标明是 `object` 还是 `lastChange`。
    - `lastChangeTo`：最近一次变化的新状态。
    - `changeTotal`、`changes`、`illegalTotal`、`illegal`（`transition` 为空的那些变化）。变化记录按 `(对象类型, objId)` 走 `scoped_instances`。注意：现有的 `/lifecycle/changes` 不做行级裁剪，摘要比它更严。
  - `actionRuns`：需要 `action-center` view。直接调用 `ActionService.search(scope=all, objKind, objId)`，返回 `total`、`statusCounts`、`latest`。
  - 缺少某段的页面权限时，该段返回 `{"available": false, "reason": "no-permission"}`，不报 403；四段都没有权限时也返回 200（只有对象头）。
  - **服务账号的限制（待决定）**：`SCOPE_PAGES["objects:read"]` 只给 `object-search` / `object-detail`，所以 `objects:read` 密钥调用时四段全是 `available:false`（已实测）。要让服务账号看到动态段，需要 本体小白 在 `api_keys.py` 给 `objects:read` 加上 `alerts` / `rules` / `object-types` / `action-center` view，或者新增一个 `dynamic:read` scope；也可以由产品决定「对象可读即可看摘要段」。本轮没有自行放宽权限。
  - `objLabel` 取各段记录里的对象名称，取不到时使用 objId。
- 本轮最小自测：`test_dynamic_alerts.py::test_object_property_filters`（逐个枚举值核对列表 total 和分面计数一致，并覆盖 5 种 422 × 2 个接口）；`test_dynamic_summary.py`（与 `/alerts`、`/action-runs` 交叉核对，检查 limit 截断、生命周期字段、404 / 422；`objects:read` 密钥 200、`meta:read` 密钥 403）。`test_dynamic_auth.py` 通过（每个路由都有 `require_func` 或 `require_object_read`、401）。

**交给 Codex 测试的部分（本轮未覆盖）**
1. `filters` 在外部数据源对象类型上的行为（`bd.read` 取编号、5000 上限的 422），以及区间筛选和多键组合。
2. `filters` 与对象读取权限的组合：对象行级范围比告警范围窄时，被筛掉的告警不应出现。另外，用户没有对象读取权限时，`Bound.stmt` 会抛出的错误码是什么，需要确认后写进断言。
3. 告警页接入 `filters`（前端未做）。
4. 摘要的分段权限：缺 `alerts`、`rules`、`object-types` 或 `action-center` view 时对应段为 `available:false`；缺对象读取（object-search / object-detail）时 403。
5. 摘要的行级可见性：受限角色看不见的对象返回 404；对象可见但部分告警、动作对应的维度不可见时的裁剪；没有行级映射的对象类型返回 404 是否符合预期。
6. 生命周期 `current`：对象数据和变化记录不一致时（种子里 SO202610250720852 的对象数据是「已签收」，变化记录是非法迁移到「待支付」）该以谁为准，需要产品确认；外部数据源或未接入数据时会退回 `lastChange`。
7. 摘要 `limit` 边界和性能（大量命中或动作时的 count 开销）。
8. 摘要的 API 密钥路径：密钥授予与账号授予取交集后的分段结果、密钥的 IP 白名单 / 配额 / 吊销 / 过期对此路由的效果；若 `SCOPE_PAGES` 扩展，需补分段可见断言。

## dynamic-r6（2026-10-27）：API 密钥 dynamic:read、决策评估 + 决策记录

迁移头 `dynamic_0027`（接 `admin_0026`）。

### 1. API 密钥 dynamic:read

- 下面这些只读 GET 路由用 本体小白 的 `require_api_scope("dynamic:read")`（人工登录不受影响）：
  - 告警：`/alerts`、`/alerts/facets`、`/alerts/meta`、`/alerts/{id}`、`/people`
  - 动作：`/action-runs`、`/actions/meta`、`/action-runs/{id}`
  - 规则命中：`/rules/{id}/hits`、`/rule-hits/{id}`
  - 生命周期：三个 GET
- 单对象动态摘要的路由仍然只要 `objects:read`。dynamic:read 不加在路由上，是因为加了以后只有 objects:read 的密钥会直接 403。改为在 `ObjectDynamicSummary` 里逐段判断：密钥没有 dynamic:read 时，四段都返回 `{available:false, reason:"missing-scope", required:"dynamic:read"}`。
- 密钥只有带 objects:read 才能看到行，否则行级范围是 DENY_ALL。只有 dynamic:read 的密钥列表都是空的，单条读取返回 404。

### 2. `POST /decisions:evaluate`（只给建议）+ `GET /decisions`、`GET /decisions/{id}`

- **代码位置**：`app/modules/dynamic/decisions.py`；表在 `models.DecisionLog`，迁移是 `dynamic_0027_decision_log.py`。
- **请求**：`{alertId}` 或 `{objectTypeId, objectId}` 二选一，另外可带：
  - `caller{aiSessionId, aiRoundId, agent}`：调用方自报，只记录，不参与鉴权
  - `dryRun`：默认 true
  - `limit`：1–20，默认 5
- **流程**（全部按元数据，没有零售写死）：
  1. 读取上下文：告警走 `AlertService.get`，要按行级范围可见，否则 404；对象走 `ObjectDynamicSummary.get`，拿到未关闭告警、近期命中和生命周期。
  2. 找候选动作，来源有两类：
     - 当前告警、其余未关闭告警、近期命中各自规则绑定的动作。查找顺序：`Rule.action_type_id` → `auto_stage.actionTypeId` → 目录规则的 actionTypeId 或动作名。
     - 目标对象类型等于该对象类型、已发布且未退役的动作。生命周期有未登记迁移时，加一条 lifecycle 理由。
  3. 预填参数，只认三种元数据：
     - 规则 `autoStage.mapping`：`告警.ID` / `告警.对象` / `命中.对象` / `固定值：X` / `告警.<维度类型名>`
     - 参数类型 `对象引用 · <类型名>`：取对象本身、告警或维度
     - 枚举默认值：要在规则文本的选项里
     
     填不上的必填参数放进 `unfilledParams`。
  4. 试运行：调 `DatabaseSubmissionChecker.run`，结果放在 `dryRun{ran, passed, checked, failed}`。发起检查：调 `launch_definition`，结果放在 `availability`。两者都在保存点里执行，执行完一律回滚。
  5. 执行策略：`policy.load` + `caller_of`，带 AI 会话时算「问数」，得到 `policy{mode, callers, callerType, callerAllowed, limits}`。不调用 `gate`。
  6. 打分排序（权重见 `REASON_WEIGHT`），写一条 `decision_log`，返回 `decisionId`。
- **只建议**：每条建议带 `handoff`，即 `POST /api/ontology/v1/action-runs:stage` 的请求体，`requiresHumanConfirmation=true`。本接口不暂存，也不执行。
- **权限**：
  - 人：alerts view，算作「动态页面可读」。试运行另需 action-types view，没有时 `dryRun.reason=no-permission`。发起检查另需 action-center manage，没有时 `availability.checked=false`。
  - API 密钥：需要 dynamic:read。`dryRun=true` 时还要 actions:dry-run，否则返回 403 NO_PERMISSION，或者改传 `dryRun=false`。密钥有动作白名单时，名单外的动作 `dryRun.reason=key-allowlist`。
- **审计**：`decision_log` 记录以下字段：
  - `caller_kind`：user / service_account / ai
  - `caller_id`、`caller_name`
  - `api_key_prefix`
  - `ai_session_id`、`ai_round_id`
  - 输入（请求、调用方、上下文、信号）和输出（建议、notes）
  
  表只追加：`append_only_guard` 禁止 UPDATE / DELETE / TRUNCATE。RLS 是 ontology_scope；`rengu_ontology_scoped` 只授 SELECT 和 INSERT。
- **读取**：`GET /decisions` 支持按 objKind / objId / alertId / callerKind / aiSessionId 过滤并分页；`GET /decisions/{id}` 取单条。行级范围是 `scoped_instances(obj_kind, obj_id)`，记录对应的对象不可见就返回 404。
- **演示「库存告警 → 补货建议」**（R-012 告警，例如 AL-20261025-0139，商品 1011846）：
  - 第 1 名是 `request_replenish`，已预填 `product` / `store` / `mode=仓库调拨` / `alert`。`qty` 未填，因为映射是表达式「安全库存 × 1.5 − 可用库存」，没有求值器。
  - 第 2、3 名是 `adjust_store_price` 和 `start_price_approval`，来自近期命中规则和目标类型。
  - 种子缺口（没有改 mock）：
    - 所有动作都是 `availability.available=false`：「动作未配置可用审批人」。
    - 试运行不通过，原因有：
      - 缺 qty。
      - `关联告警` 要求当前用户是告警处理人。
      - 商品 / 门店的参数规则写的是名称而不是条件，「不可校验」。
      - 「执行人角色」里有未登记的角色（供应链计划员）。
      - 有「原因必填」条件，但 request_replenish 没有 原因 参数。
      - `商品.状态` 拿不到对象上下文。
    - R-019 没有绑定动作，进了 `notes`。

### 留给 Codex 的未测部分

- 除 `test_dynamic_decisions.py` 两个用例（演示链路、密钥范围）以外都没测，包括：
  - 行级范围：受限角色看不到 `GET /decisions` 里别人的对象
  - `callerKind` / `objKind` / `alertId` 过滤和分页
  - `limit` 截断
  - `caller.agent`
- 保存点回滚：试运行或发起检查中途写库时，确认什么都没留下。只间接验证过 action_run 总数不变。
- 服务账号密钥（principal 是服务账号）：`caller_kind=service_account`、`caller_of → 服务账号`，以及策略 callers 不含服务账号时 `callerAllowed=false`、扣分。
- 动作白名单密钥（`key-allowlist`）；`policy.mode=suggest` 和 `limits` 的展示。
- 规则 `action_type_id` 直接绑定与 autoStage 不一致时的优先级；动作已退役、只在 changing 状态时的 notes。
- `decision_log` 只追加触发器的 UPDATE / DELETE 拒绝，以及迁移往返（`test_migrations` 现有失败卡在 notify_0022，不是本分支造成的）。
- dynamic:read 新加到的规则命中、生命周期、动作 GET 路由：密钥有 / 无 dynamic:read 的情况，只测了 `/alerts`。

## dynamic-r7（2026-10-27）：参数表达式求值、告警自动决策建议、QA worker 挂载、服务账号显示

本批没有新迁移，迁移头沿用 master。`decision_log.alert_code` 已经有索引，告警和决策记录靠它关联。情景推演 `:run` 仍然返回 503（用户决定），本批没有动。

### 1. 决策评估：参数映射表达式（`param_expr.py`，`decisions._prefill`）

- 适用范围：数值类参数（类型含 整数 / 小数 / 数值 / 数字 / 金额 / number / integer / decimal / float）。按「规则映射文本 → 参数默认值」的顺序，把它们当表达式求值。
- 允许的写法只有：`+ − × ÷ * / ( )`（含全角括号和全角加减）、数字、属性名。词法切分后转成带占位符的 Python 表达式，`ast` 白名单只放行 BinOp / UnaryOp / Constant / Name，然后交给 `functions.runtime.Interpreter` 求值（有步数预算）。不用 `eval`，长度上限 200。
- 名字怎么解析：
  1. 先查告警时刻的事实（`alert_facts`）：
     - 告警值里的「标签：数值」对。
     - 规则只有一个比较条件时：条件字段 ← 告警触发值里唯一的数字；条件值是文本时 ← 告警阈值里唯一的数字。
  2. 再查对象当前属性：先告警对象，后维度对象。按 api_name 或显示名匹配，可以带「类型名.」前缀。读取走 `qa.explore_api._bind`，有行级范围。
- 填不上（缺属性、为空、不是数值、除数为 0、超出预算）时，该参数留在 `unfilledParams`，并带上 `reason`。整数参数四舍五入。来源写进 `paramSources`，例如 `规则映射表达式：安全库存 × 1.5 − 可用库存；安全库存=120（告警阈值），可用库存=37（告警触发值）`。
- 演示：AL-20261025-0139 → `request_replenish.qty = 143`。

### 2. 告警自动决策建议（`auto_decision.py`）

- **时机**：不在建告警的事务里做。动态调度 worker（`python -m app.modules.dynamic.worker`，每分钟跑 `rule.sweep`）在 `_sweep_workspace` 最后、所有运行事务提交之后调用 `decide_pending(ontology_id)`，外面包了 try/except，只记日志。
- **选哪些告警**：非种子、待处理 / 处理中、24 小时内、还没有任何 decision_log 的，每轮最多 20 条。定时规则的告警当轮就会评估；事件规则（`event_rule_runtime.dispatch`，在事件 worker 的事务里）建的告警在下一次 sweep 补上。没有做同步钩子，避免拖慢或破坏事件事务。
- **身份**：告警的 `created_by`（规则运行身份）→ 规则 `trigger.runAsUserId` → `published_by`。
- **调用方式**：`DecisionService(..., system={"trigger":"rule","ruleId":...})` 跑 `evaluate(dryRun=true, limit=5)`。
  - `caller.kind=system`，`caller_name=「规则 X 自动评估」`。
  - 执行策略的调用方类型是「规则」。
  - 只给建议，不暂存。
- **失败处理**：单条失败写一条 `output.error` 的 decision_log，下一轮就不会反复重试。告警本身不受影响。
- **接口**（都要 alerts view；告警不可见时 404）：
  - `GET /alerts/{id}/decision` → `{alertId, decision|null}`：最近一条，返回结构同 `GET /decisions/{id}`，多一个 `error` 字段。
  - `POST /alerts/{id}/decision:evaluate`：当前用户手动生成一条，caller 是 user 或 service_account。
- **前端**（`Alerts.vue`，只在真接口模式下显示）：详情里有「AI 处置建议」面板，内容包括来源（自动评估 / 手动）、前三条建议的理由、参数和来源、待补参数及原因、预校验 / 可用性 / 策略，以及「生成建议 / 重新生成」按钮。点「采用建议」时：
  - 建议的是规则绑定的动作，且我是处理人、可以发起：预填参数，打开本页原有的发起动作弹窗，经确认后提交。
  - 其他情况：跳到动作中心 `?launch=&launchObj=&launchParams=`，`Actions.vue` 打开发起弹窗并预填，仍由人确认暂存。

### 3. Worker 挂载 QA 页面定时任务

- `dynamic/worker.py` 的 `build_app` 调用 `app.modules.qa.page_schedule.register(app)`，worker 同时监听 `rules` 和 `qa_pages` 两个队列。
- master 上还没有 `qa.page_schedule`，导入失败时记一条 warning 后跳过，worker 照常启动。任务代码归 QA。

### 4. 服务账号执行主体的显示

- `ActionService.person_label`：`api:{编号}` 显示为「服务账号 {名称或编号}」。名称按批从 `ServiceAccount` 只读预取；当前调用方就是这个服务账号时，直接用它的名字。
- 用到的地方：确认人、审批人、发起人、动作日志的 desc，以及自动执行日志里的「执行主体 …」。`created_by` 仍然保存原始的 `api:{编号}`（palantir-t5 的行为不变）。

### 测试与未测（交 Codex）

- 已加：`test_dynamic_decisions.py` 增加三项：表达式安全求值的单元测试；auto `decide` 后用 GET / POST `/alerts/{id}/decision` 验证 qty=143、caller system、策略调用方「规则」；`person_label` 单元测试。
- 未测：
  - `decide_pending` 和 sweep 收尾的整链（需要 worker 用真 sessionmaker，测试事务里看不到提交）。
  - 失败记录分支。
  - 事件规则告警靠下一次 sweep 补评估。
  - 多比较条件的规则（这时只用「标签：数值」对和对象属性）。
  - 维度对象属性的解析。
  - 前端面板和动作中心的 `launch` 预填（只做了 vue-tsc）。
  - QA 挂载（模块存在时的 `register`）。
  - `load_service_names` 的数据库查询路径。
- 本环境里一直存在、和本批无关的失败：
  - `test_palantir_machine` / `test_dynamic_actions` 的 `grant_fixture_roles` 夹具断言（改动前就失败）。
  - `test_dynamic_rules::test_hits_paging_and_detail`。
  - 3 条 `test_migrations`（notify_0022）。

## 未完成清单（交 Codex）

基线 master `e657b08`（2026-10-27），迁移头 `dynamic_0027`。范围：动态模块（告警、动作中心、规则、情景推演、生命周期、决策评估）。每条按「是什么 / 为什么没做 / 涉及文件和接口 / 怎么验证」写；「为什么没做」分三类：**待定**（等老板定）、**Codex 修复**（含只缺测试的）、**环境问题**（不在代码里）。上文各轮小节里已标「已做 / 已解决」的不再列。

### A. 待老板定

1. **人工复核项（`humanChecks`）和将推送的编辑（`declaredEdits`）在动态页面显示**
   - 是什么：palantir-t7（action-event-api §18.2/§18.3）对外部 Webhook 通道的动作，把算不了的提交条件变成 `humanChecks:[{name,rule,reason}]`，把自然语言编辑作为声明效果随签名推送（`edits[].status = "external/declared"`）。动态页面目前都不显示，确认人看不到要核对什么。
   - 为什么没做：待定。这是动态负责人提的小前端适配，PDE 建议做，还没得到老板同意，本批按指示跳过。
   - 涉及：
     - 后端已有的数据：`decisions._dry_run` 用 `**result` 原样展开 `DatabaseSubmissionChecker.run` 的结果，所以 `POST /decisions:evaluate`、`GET /alerts/{id}/decision` 每条建议的 `dryRun.humanChecks` **已经透传**（Webhook 动作有不可判定条件时才有这个键）。机器接口 `_machine_out`（`router.py`）有 `humanChecks` 和 `edits[].status`。
     - 后端缺的：人工会话用的 `POST /actions/{code}:validate` 只回 `{passed, message}`，把 `humanChecks` 丢了（`router.py validate_launch`，`ActionService.validate_launch`）；`POST /alerts/{code}:start-action`（`StartActionOut`）和 `POST /action-runs:stage` 返回 `ActionRunDetail`，里面没有 `humanChecks` 字段，只在时间线里多一条 warn 日志「需确认人人工核对的提交条件（N 项）」（`service.py HUMAN_CHECK_TEXT`）。`ActionRunDetail` 也没有声明编辑列表。
     - 前端：`src/pages/ops/Alerts.vue` 的「AI 处置建议」面板（dynamic-r7）和本页发起动作确认框；`src/pages/ops/Actions.vue` 的发起弹窗（`launch*`、`validateActionLaunch`）和确认 / 审批视图；`src/api/dynamic.ts` 类型。
   - 建议做法：`:validate` 返回 `humanChecks`；`ActionRunDetail` 加 `humanChecks`（从暂存时的时间线 / 运行记录取，或暂存时存下来）和 `declaredEdits`（复用 `_machine_out` 的 `declared_text` 判断）；前端按字段通用渲染「需人工复核」清单（name / rule / reason）和「将推送的编辑」列表，不认具体动作。
   - 怎么验证：私有库按 `test_palantir_mainline.py` 的准备步骤，对 `request_replenish`（EXT-WEBHOOK）调 `:validate`、`:start-action`、`GET /action-runs/{id}`，断言 `humanChecks` 非空、声明编辑带 `external/declared`；`POST /decisions:evaluate` 断言第 1 名的 `dryRun.humanChecks` 存在；前端 `vue-tsc --noEmit`，再在真接口模式下打开告警详情和确认框看两张清单。

2. **审批触发提交时，提交条件按确认人复核**
   - 是什么：palantir-t7（§18.4）已生效：审批人审批后触发提交（以及排队执行器）时，提交条件按记录的确认人身份复核，审批人自己的功能 / 对象 / 操作权限仍按审批人查。影响动态的 `:start-action` → 审批 → 执行流程（`ActionService.approve`）。
   - 为什么没做：待定。代码已改，等老板确认这个口径；不同意就要回退成按审批人复核（原来审批人没有「执行角色」会 422）。
   - 涉及：`app/modules/action/checker.py`、`app/modules/dynamic/service.py`（approve / 提交路径）、`POST /action-runs/{id}:approve`。
   - 怎么验证：`test_palantir_mainline.py`（U010812 审批后派发）；另补一条：审批人缺执行角色、确认人有时审批成功，确认人停用时按当前身份复核。

3. **情景推演 `:run` / stage-plan 保持 503**
   - 是什么：`POST /scenarios/{id}:run`、stage-plan、建议生成都返回 503 `SCENARIO_EXECUTOR_UNAVAILABLE`（`rules_service.py` 约 986/1005/1024 行，`scenario_templates.py:46`）。
   - 为什么没做：待定（用户决定），有真实预测模型和数据集之后再接，不接示意引擎。
   - 涉及：`ScenarioService`、`scenario_templates.ENGINES`、`Scenarios.vue`。接上后还要处理进程内后台任务的问题（重启后停在「运行中」，见上文「未完成」第 4 条、「下一步」第 3 条）。
   - 怎么验证：现在只需验证 503 和页面上的空状态 / 原因；接入后按模板字段跑 `:run` → 轮询 `/result`。

### B. 演示环境（环境问题，不在代码里）

4. **演示主线的运行配置**
   - 是什么：主线「库存告警 → 补货建议 → 人工确认 → Webhook → 决策日志」目前只在测试里走通，真实环境还缺：
     - `RENGU_INTEGRATION_ADAPTERS` 加上 `1/EXT-WEBHOOK`（`submit_path`、`actions`、`signing_secret`）；
     - `RENGU_INTEGRATION_REAL_CONNECT=true`；
     - HTTPS 接收端；
     - 派发任务 `dispatch_queued`（`app/modules/integration/worker.py` / `app/jobs`）在跑；
     - 动态调度 worker `python -m app.modules.dynamic.worker`（监听 `rules` 和 `qa_pages` 两个队列）在跑，否则不会每分钟 `rule.sweep`，也不会 `decide_pending` 自动生成决策建议，问数页面定时刷新也不跑；
     - 开发库 `rengu` 是 Codex 的快照，**没有灌 `demo_mainline` 种子**（R-09 / R-10 角色、U010011 / U010812 成员、AL-0139 处理人、EXT-WEBHOOK 通道、ACT-0552 已撤回）。
   - 为什么没做：环境问题。在哪个环境配、由谁配，等老板定；不能把 worker 跑在共享库上做实验。
   - 涉及：部署环境变量；`app/modules/dynamic/worker.py`；种子阶段 `demo_mainline`。
   - 怎么验证：配好后从前端用周晓峰认领 AL-20261025-0139 → 生成 / 查看 AI 建议（`qty=143`）→ 发起 `request_replenish` → 姚娜审批 → 接收端收到验签通过的请求 → 已对账 → `GET /decisions` 有记录。

### C. 已知失败（Codex 修复）

5. **`test_dynamic_rules.py::test_hits_paging_and_detail`**：AL-…0126 / 0127 的顺序断言失败，改动前就有，原因没查。建议：先确认两条命中的时间是否相同；相同就给命中列表加编号作第二排序键，否则按真实数据改断言。验证：单跑该用例。
6. **`test_dynamic_alerts.py` 的 `test_start_action_full_flow` / `test_start_action_order_approver` / `test_start_action_errors` 准备阶段报错**：私有空库上 `grant_fixture_roles` 找不到 U010001 / R-01（`test_palantir_machine`、`test_dynamic_actions` 同一夹具也一样）。master 上本来就有。建议：夹具先灌 admin / 用户种子再授角色，或按 `demo_mainline` 之后的种子改用户。验证：在全新私有库上单跑这 3 条。
7. **迁移往返**：`test_migrations.py` 3 条失败（`notify_0022` 命名检查找不到 `app/modules/notify`，`notify_0022` 降级报错），另有 `test_migration_round_trip` 卡在 `admin_0025:165` 禁止降级；连带 `test_dynamic_migration.py` 往返和 `dynamic_0027`（`decision_log`）降级都没真正跑过。不是动态代码造成的。验证：修好后 `alembic downgrade` 到 `dynamic_0023` 之前再 `upgrade head`，单头仍为 `dynamic_0027`。
8. **Ruff E501**：`app/modules/dynamic/rule_runtime.py` 有超过 110 列的行（268，另有 105 / 198 / 215 / 315 / 372 / 376）。建议统一跑 ruff format 时一起改。验证：`ruff check app/modules/dynamic/rule_runtime.py`。

### D. 规则和告警运行时的能力边界

9. **跨对象 / 指标条件的规则不能执行（R-012、R-019 等）**
   - 是什么：事件规则运行时（`docs/26-10-27-rule-alert-runtime.md`）只支持已声明事件字段的有限表达式和明确版本的生命周期迁移条件。R-012 的 `product.safetyStock(store)` 是跨对象函数，R-019 的 `gross_margin@v2(sku, store, price = event.newPrice)` 是带参数的指标调用，`rule_runtime.py`（约 105 / 171 / 198 行）明确报不可执行。`metric_rules.py` 只支持定时规则里写明的 `metric@vN(object, bucket)` 和 `prev` / `wow`，缺真实时间桶或上周基线时同样不可执行。R-019 还是 `output=通知`，没有真实通知通道会被拒绝，并且没有绑定动作（不在演示主线，已定先不补）。
   - 为什么没做：Codex 修复 / 待定。需要先定跨对象读取和带参指标的执行口径；改规则表达式属于改种子既有值，要老板同意。
   - 涉及：`rule_runtime.py`、`event_rule_runtime.py`、`metric_rules.py`、`seeds/json/dynamic/RULE_DEFS.json`。
   - 现状对演示的影响：演示告警 AL-20261025-0139 是种子数据，不是运行时生成的；真实库存事件进来不会产生新的 R-012 告警。
   - 怎么验证：给 R-012 投一条 `inventory_changed` 事件，现在应返回明确不可执行；接入口径后应生成 Alert + RuleHit，且同一事件重入不重复。

10. **事件规则产生的告警下一次 sweep 才评估**：`event_rule_runtime.dispatch` 在事件 worker 的事务里建告警，不同步调 `decide_pending`，等动态 worker 下一次 `rule.sweep`（每分钟）补评估。这是有意的设计（不拖慢事件事务），只在 worker 在跑时才会发生。验证：worker 在私有库跑起来，投一条能命中的事件，1 分钟内 `GET /alerts/{id}/decision` 有记录。
11. **种子告警不会自动评估**：`auto_decision.decide_pending` 只选非种子（`Alert.is_seed = false`）、待处理 / 处理中、24 小时内、还没有决策记录的告警。AL-0139 这类种子告警要在详情里点「生成建议」（`POST /alerts/{id}/decision:evaluate`）。不是缺陷；演示时要记得手动生成。

### E. 已写但没测的部分（Codex 测试）

12. **`decide_pending` 和 sweep 收尾的整链**（`auto_decision.py`、`worker._sweep_workspace`）：要用 worker 的真 sessionmaker，测试事务里看不到提交。另有**失败记录分支**（单条失败写 `output.error` 的 decision_log，下一轮不重试）。验证：私有库起 worker 或直接调 `decide_pending(ontology_id)`，构造一条会抛错的告警，看 decision_log 有 error 且下一轮不再选它。
13. **多比较条件的规则做参数表达式求值**（`param_expr.py`、`decisions._prefill`）：只用告警值里的「标签：数值」对和对象属性。验证：两个比较条件的规则 + 表达式映射，检查 `paramSources` 和 `unfilledParams.reason`。
14. **维度对象属性读取**：表达式里的名字先查告警对象、再查维度对象（`qa.explore_api._bind`，带行级范围），维度这条没测。验证：映射里引用维度对象（如门店）的属性，以及维度对象不可见时进 `unfilledParams`。
15. **前端「AI 处置建议」面板和动作中心预填**（`Alerts.vue`、`Actions.vue` 的 `?launch=&launchObj=&launchParams=`）：只做过 vue-tsc。验证：真接口模式下两条分支都点一遍——规则绑定动作且我是处理人 → 本页弹窗预填；其他 → 跳到动作中心弹窗预填，取消不暂存。
16. **QA 页面任务挂载**：`worker.build_app` 调 `app.modules.qa.page_schedule.register(app)`。现在 master 上已有 `qa/page_schedule.py`（qa-p3），挂载真实生效但没测。验证：`build_app()` 后任务表里有 `qa.page.sweep`，worker 监听 `qa_pages`。
17. **服务账号名称查询**：`ActionService.person_label` 的 `load_service_names` 数据库路径（`api:{编号}` 显示「服务账号 名称」）只测了单元。验证：用服务账号密钥暂存一个动作，再用人工会话读详情，看发起人显示名称。
18. **决策评估（dynamic-r6）其余未测项**：`GET /decisions` 的行级范围和过滤 / 分页、`limit` 截断、`caller.agent`、保存点回滚不留数据、服务账号 `callerAllowed=false`、`key-allowlist`、`policy.mode=suggest`、规则直接绑定与 autoStage 不一致时的优先级、`decision_log` 拒绝 UPDATE / DELETE、dynamic:read 在命中 / 生命周期 / 动作 GET 路由上的有无（只测了 `/alerts`）。明细见上文「dynamic-r6 · 留给 Codex 的未测部分」。
19. **告警筛选和单对象摘要（dynamic-r5）**：`filters` 在外部数据源（5000 上限）、区间和多键组合、与对象行级范围的组合；摘要的分段权限、行级裁剪、`limit` 边界、API 密钥路径；生命周期 `current` 以对象数据还是变化记录为准（SO202610250720852 不一致）待产品确认。告警页还没有 `filters` 的入口。明细见上文「dynamic-r5 · 交给 Codex 测试的部分」。
20. **dynamic-r4 留下的未测项**：规则回放、规则定时调度（`dynamic_0015` 游标 / 运行历史）、发布写回 `apply_cr`（晋升、幂等、`WRITEBACK_FAILED` 回滚）、事件去重、告警分面 UI、`dynamic_0024` 在非 1 号本体和真实快照库上的升级。明细见上文「dynamic-r4 · 交给 Codex 测试的部分」。

### F. 以前留下的、仍未收口的项

21. **`VITE_DYNAMIC_CR_WRITE` 默认仍关**（`frontend/src/api/dynamic.ts` `CR_WRITE`）：静态写回钩子已在 master，但「规则发布写回」的联合 e2e（新建规则 → 变更单提交 → 审批 → 发布 → 规则 v1 / 已发布）在当前 master 上没有重跑；未登记的 kind/section 被跳过时前端没有提示。验证：按上文「发布写回」一节的 e2e 在私有库跑通后，再决定是否默认打开。
22. **审批界面**：动作中心没有专门的审批界面设计，后端 `:approve` 和 `can.approve` 已就绪；演示审批人现在是姚娜（U010812）。要不要做界面待定。
23. **推演模板没有管理接口**：`scenario_template` 表只有 `GET /scenarios/templates`，没有编辑接口；影响明细表仍是演示引擎的行结构；建议动作按动作名映射。跟第 3 条一起等真实模型。
24. **生命周期**：静态放弃 object 目标时不删 `lifecycle_draft`（动态读时忽略残留）；摘要里 `/lifecycle/changes` 不做行级裁剪，比摘要宽。验证：放弃后重新加入，草稿不复活；受限角色读 `/lifecycle/changes` 的结果。
