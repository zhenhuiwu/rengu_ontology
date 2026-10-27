# Codex 工作单：第一档收尾

基线：GitHub main `ff73fe1`。本文件根据 `codex-index.md` 和五份清单（`palantir.md`、`dynamic.md`、`static.md`、`xiaobai.md`、`qa.md` 末尾的「未完成清单（交 Codex）」）整理。下文「出处」写成「文件 #条目号」，细节以原文为准。

## 1. 目标

- 完成第一档「演示和试点完整」：服务完整、端到端可用，演示主线能在真实环境里从前端走通。
- 这是通用本体平台：代码里不写死零售业务，不定义业务指标口径（口径放在种子和配置里，由本体负责人定）。
- 第二、三档不做：
  - 第二档：正式登录 / SSO、外部通知渠道（邮件、企业微信等）、应用普通库账号、worker 部署和监控、达梦 / 金仓驱动和方言、迁移往返 / 备份演练、压测。
  - 第三档：Workshop 页面搭建、属性级权限 / 标记、本体分支、跨本体复用 / 模板。
- ERP、Kafka 永远不接；对外部 Web 系统只走 Webhook。

## 2. 硬规则

1. 不重灌开发库 `rengu`（Codex 快照），只追加。
2. 不改既有 mock / 种子值，除非老板批准。已批准的只有 `demo_mainline` 里那几处（审批文字加「（U010812）」、补货通道改 EXT-WEBHOOK、ACT-20261025-0552 作废并解除与 AL-20261025-0139 的关联）。
3. 写入和导出不得假成功：失败就返回明确错误，不吞错、不返回示意数据。
4. 只连本地 PostgreSQL，不连任何外部库。
5. 不提交密钥：`signing_secret`、API 密钥、模型 Key 只放在本地环境变量里，不进仓库、种子和日志。

## 3. 任务（按优先级）

状态：☐ 未开始 / ◐ 进行中 / ☑ 完成。完成一组就更新这里。

### P0-1 修已知测试失败，跑全量测试和构建　◐

- 内容（都是 master 上本来就有的失败）：
  - `test_palantir_webhook.py` 在 `use_ops_support` 下有 8 条失败：外网拦截要放行回环地址 127.0.0.1。出处：palantir #2
  - `test_event_delivery_boundary.py::test_explicit_test_endpoint_and_stable_subscription_idempotency`：测试桩补上 `ontology_id`。出处：palantir #3
  - `test_dynamic_alerts.py` 的 `test_start_action_full_flow` / `_order_approver` / `_errors` 准备步骤报错（`grant_fixture_roles` 在空库找不到 U010001 / R-01；`test_palantir_machine`、`test_dynamic_actions` 用同一夹具）：先灌用户种子再授角色。出处：dynamic #6、xiaobai #10
  - `test_dynamic_rules.py::test_hits_paging_and_detail`：查清排序，必要时加第二排序键。出处：dynamic #5
  - `test_dynamic_seed` 2 条、`test_conventions::test_job_stubs_registered`、`test_integration_writeback_calls` 的 TypeError。出处：xiaobai #10
  - `test_admin_users.py` 3 条、`test_audit_logs.py`（多了 `ontologyId: None`）、`test_admin_roles.py::test_list_and_get_match_mock`、`test_admin_audit_export.py::test_export_migration_roundtrip_and_metadata`（列注释）。先核对 mock 和接口哪边对，**不改 mock 既有值**；只能改 mock 才能修的，列出来交老板。出处：xiaobai #6–#9
  - 问数旧 POST 测试 403（测试用户在本体 1 是 viewer）、`test_qa_dynamic_semantic.py::test_single_object_summary_attached` 单独跑失败（加登记受治理表的夹具）。出处：qa #9
  - 迁移往返（`test_static.py::test_migration_round_trip`、`test_migrations.py` 3 条、`test_dynamic_migration.py` 往返）：按 `decisions-pending.md` 第 10 条的默认做法处理。出处：static #4、xiaobai #5、dynamic #7
  - 前端检查脚本：`check-static-pages.mjs` 补 `crErr` 桩；`check-context-integration`、`check-qa-integration`、`check-qa-session-context`、`check-real-object-resolver`、`check-object-ask`、`check-object-behavior`、`check-real-data` 逐个修。出处：static #5
- 涉及：`backend/tests/`、`tests/use_ops_support`、`backend/app/modules/event/delivery.py`、`frontend/scripts/check-*.mjs`。
- 验收：在全新私有测试库上 `pytest -q` 全部通过（skip 的都要写原因）；`node scripts/check-*.mjs` 退出码全为 0；`npx vue-tsc --noEmit`、`npm run build` 通过。

### P0-2 演示主线在真实环境跑通　☐

- 内容：
  1. 给开发库 `rengu` **追加** `demo_mainline` 种子（R-09 / R-10 角色、U010011 / U010812 成员、AL-0139 处理人、EXT-WEBHOOK 通道、ACT-0552 已撤回），以及问数演示种子。只追加，不重灌。出处：xiaobai #3、dynamic #4、qa #3
  2. 本地环境变量：`RENGU_INTEGRATION_ADAPTERS` 加 `1/EXT-WEBHOOK`（`submit_path`、`actions:["request_replenish"]`、`signing_secret`），`RENGU_INTEGRATION_REAL_CONNECT=true`。出处：palantir #4、xiaobai #4
  3. 起一个本地 HTTPS 模拟接收端（种子地址 https://127.0.0.1:8443），验签后回 `{"status":"SUCCESS","receiptId":…}`。出处：palantir #4
  4. 跑派发任务 `dispatch_queued`；起动态 worker `python -m app.modules.dynamic.worker`（`rules`、`qa_pages` 队列）和问数评测 worker `python -m app.modules.qa.eval.worker`（`qa-eval` 队列）。出处：dynamic #4、qa #3
  5. 给问数只读账号授 `rengu_ontology_scoped`，worker 访问 procrastinate 表的 GRANT 补齐。出处：qa #2
  6. 从前端点一遍：周晓峰认领 AL-20261025-0139 → 点「生成建议」（种子告警不会自动评估）→ 发起 `request_replenish` → 姚娜（U010812）审批 → 接收端验签通过，收到 `declaredEdits` → 动作变「已对账」→ 决策日志有记录。出处：dynamic #4、#11、palantir #4、xiaobai #4
- 涉及：`app/modules/admin/seeds/demo_mainline.py`、`seeds/extra/system/DEMO_MAINLINE.json`、`app/modules/integration/worker.py`、`app/modules/dynamic/worker.py`、`app/modules/qa/readonly.py`；action-event-api §14、§18；palantir.md §2。
- 验收：`GET /action-runs/ACT-20261025-0552` 显示已撤回；`GET /alerts/AL-20261025-0139` 无关联动作；前端整条主线走通，`GET /decisions` 有记录；问数问一题不再 503；`procrastinate_periodic_defers` 里有 `qa.page.sweep`。把操作步骤和结果写进本文件第 6 节。

### P1-1 写了未测的功能逐项验证　◐

每项按出处里写的验证方式补测试或手工核对；发现缺陷就修。

- 动态：`decide_pending` 整链和失败分支（#12）、多比较条件参数表达式（#13）、维度对象属性读取（#14）、前端 AI 处置建议面板和动作中心预填（#15）、QA 页面任务挂载（#16）、服务账号名称（#17）、dynamic-r4 / r5 / r6 其余未测项（#18–#20）、事件规则告警下一次 sweep 才评估（#10）。出处：dynamic.md
- 静态：分面按角色范围的端到端测试（#6）；核对 writeback 注释和代码（#7）；补一条测试确认变更单写权限是按目标页还是统一 `releases:manage`（#8）。出处：static.md
- Palantir：时间线显示种子里的 `POST /praybill/save`，外观小问题（#4）。出处：palantir.md
- 问数：前端展示多对象动态摘要（#7）。页面订阅摘要接入通知（#1）要等 `decisions-pending.md` 第 15 条定了再做。出处：qa.md
- 验收：每项有对应测试或写明手工核对结果；`pytest -q` 仍全绿。

### P1-2 问数剩余错题和过拟合检查　☐

- 内容：
  - 100 题里仍错 006、012、015、048（qa.md 2.0d）；048、012 是业务口径，按已发布口径修正标准答案，见 `decisions-pending.md` 附录。
  - 自动出题抽查 7/20 的修复（qa #10 的②–⑤）：出题跳过 `refuse.json` 里没数据的指标，或判分把「无数据拒答」算对；作答列名用维度显示名；查 `ship_avg`（平均出库时长）的 SQL 解析失败；动作题允许反问对象。①（只给指标名要不要追问）见 `decisions-pending.md` 第 9 条。
- 涉及：`app/modules/qa/guards.py`、`chain.py`、`eval_gen.py`、`eval/job.py`、`seeds/extra/qa/caliber/1/refuse.json`、`eval/auto.py`。
- 验收：重跑 `TAG=_px N=20 python -m app.modules.qa.eval.auto` 和 100 题 `eval.run all`：自动题明显上升，100 题不低于 96。出处：qa #8、#10

### P2 最后统一 ruff 和格式化　☐

- 内容：所有任务合并后一次性做。`ff73fe1` 上实测 `ruff check .` 121 个错误、`ruff format --check .` 45 个文件要重排（包括 `app/modules/dynamic/rule_runtime.py` 的 E501）。格式化单独一个提交，不夹带逻辑改动。
- 验收：`ruff check .`、`ruff format --check .` 都通过；`pytest -q`、`npm run build` 仍通过。出处：dynamic #8、codex-index.md 第 3 节

## 4. 待老板定的事项

以 `decisions-pending.md` 为准（共 16 条）。状态是「待定」的，按每条的「未定前 Codex 的默认做法」做，默认都是最保守、可回退的做法；改成「已定」的，按老板的决定做。默认做法写「不做」的，老板确认前不要动。

另外明确延后、本期不做的：入站静态令牌下线和签名校验（palantir #6）、palantir #8 其余缺口、`VITE_DYNAMIC_CR_WRITE` 默认仍关（dynamic #21）、qa #11 和 qa.md 2.0.1 表里的剩余项、static #9 到 #14。

## 5. 交付方式

- 直接在 main 上提交，一组任务一个或几个提交，不 force push。
- 每完成一组，更新第 3 节的状态标记，并在第 6 节记一行：日期（UTC+8）、提交号、测试结果。
- 碰到 `decisions-pending.md` 里默认「不做」的、或者要改既有 mock / 种子值的，停下来，写进第 6 节等老板定。

## 6. 进度记录

| 日期（UTC+8） | 任务 | 提交 | 结果 / 备注 |
|---|---|---|---|
| 2026-10-27 | P0-1 定点修复／P1-1 部分闭环 | 待统一收口 | 三组修复草稿恢复、获准来源指标试算、动作策略／复核、函数密钥限制、页面站内通知及刷新订阅；独立测试库分组验证和实际隔离UI证据见[本轮汇总](../../../../docs/26-10-27-fixes-summary.md)。不是全量pytest，旧423记录未逐条核销，故P0-1仍◐。 |
| 2026-10-27 | P0-2 部署与主线边界 | 待统一收口 | 主库备份后增量至dynamic_0030，API和四worker已运行；未追加demo_mainline种子，未启用外部通道／真实外写，未跑完整告警到外部对账主线，故保持☐。本轮不得按旧清单再次seed。 |
| 2026-10-27 | P1-2 模型评测／其余验证 | 未执行 | 未跑真实LLM、自动出题20题或100题评测；旧分数不是本轮结果。迁移往返、全仓格式化及未覆盖功能仍未验收，不因定点绿色改为全完成。 |
