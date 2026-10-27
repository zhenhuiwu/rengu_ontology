# Codex 未完成清单汇总索引

第一档代码已推送到 `06d7a15`（GitHub main）。各家的详细条目写在各自 handoff 文件末尾的「未完成清单（交 Codex）」一节，每条都有是什么、为什么没做、涉及文件和怎么验证；本文件只做去重汇总和指路，细节以原文为准。

来源文件（都在 `backend/docs/backend/handoff/`）：`palantir.md`（动作 / 事件 / 集成）、`dynamic.md`（动态）、`static.md`（静态）、`xiaobai.md`（admin / data / 种子）、`qa.md`（问数）。

## 1. 待老板定（Codex 不要自行开工）

1. 提交条件改由确认人复核（palantir-t7 已生效；不同意就改回按审批人）—— palantir.md #1、dynamic.md #2
2. humanChecks / declaredEdits 在动态建议面板、启动确认框显示（后端 `:validate`、`ActionRunDetail` 还要补字段）—— dynamic.md #1、palantir.md #5
3. 问数动作确认卡怎么展示 humanChecks / declaredEdits，是否要逐条勾选才能暂存 —— qa.md #6
4. 推演 `:run` / stage-plan 保持 503，等真实预测模型再接 —— dynamic.md #3、#23
5. MetricFlow 时间表 `metricflow_time_spine`（没有它累计、补零查询用不了）—— xiaobai.md #1、qa.md #5
6. 静态 #13 草稿与定义分开存放（只有设计，没写代码）—— static.md #1
7. 种子补季度维度值（要改既有种子值）—— static.md #2
8. 事件载荷字段加 enum（谁来加、列的形状）—— static.md #3、palantir.md #7
9. 问数“只给指标名就追问”规则：反问还是按默认范围直接答 —— qa.md #10①
10. 迁移往返测试的处理方式：改测试只往返到可降级版本，还是给 admin_0025 / notify_0022 补无损降级 —— static.md #4、xiaobai.md #5、dynamic.md #7
11. 演示环境在哪里配、由谁配（见第 2 节）—— palantir.md #4、dynamic.md #4
12. 其他较小的待定项：每日自动评测的发起身份和模型额度（qa.md #4）、048 客单价分母口径（qa.md #8，要静态定）、跨对象 / 带参指标规则（R-012、R-019）的执行口径（dynamic.md #9）、动作中心审批界面要不要做（dynamic.md #22）、演示服务账号「补货助手」（可选，xiaobai.md #2）

## 2. 演示环境要配的（环境问题，不在代码里）

- `RENGU_INTEGRATION_ADAPTERS` 加 `1/EXT-WEBHOOK`：`submit_path`、`actions:["request_replenish"]`、`signing_secret`（可用 fernet$ 加密，密钥不入库不入代码）—— palantir.md #4、xiaobai.md #4
- `RENGU_INTEGRATION_REAL_CONNECT=true`
- HTTPS 接收端（种子地址 https://127.0.0.1:8443），回 `{"status":"SUCCESS","receiptId":…}`
- 派发任务 `dispatch_queued` 在跑
- 动态 worker `python -m app.modules.dynamic.worker` 在跑，监听 `rules` 和 `qa_pages` 队列（否则不 sweep、不自动生成建议、问数页面不定时刷新）；问数评测 worker `python -m app.modules.qa.eval.worker`（`qa-eval` 队列）—— dynamic.md #4、qa.md #3
- 开发库 `rengu` 灌 `demo_mainline` 种子和问数演示种子。注意 `rengu` 是 Codex 的快照，只追加，不要覆盖既有数据；`demo_mainline` 排在 dynamic 之后，可重复跑 —— xiaobai.md #3、dynamic.md #4、qa.md #3
- 问数只读账号要授 `rengu_ontology_scoped`：`GRANT rengu_ontology_scoped TO <qa 只读账号>;`，否则问一题 503；worker 访问 procrastinate 表也要 GRANT —— qa.md #2
- 静态无专属配置，只需确认 `VITE_API_REAL_MODULES` 含 `static`、`demo_admin_codes` 第一个人在 sys_user 里有效 —— static.md E
- 前端完整演练（还没在真实环境做过）：周晓峰认领 AL-20261025-0139 → 生成建议（种子告警要手点「生成建议」）→ 发起 `request_replenish` → 姚娜（U010812）审批 → 接收端验签通过 → 已对账 → 决策日志 —— dynamic.md #4、#11、xiaobai.md #4

## 3. 已知测试失败（Codex 修复）

除特别说明外，都在 master 上本来就失败，不是第一档最后几轮改出来的。

- **迁移往返**：`test_static.py::test_migration_round_trip`，`test_migrations.py` 3 条（`notify_0022` 命名检查、降级报错），连带 `test_dynamic_migration.py` 往返没真正跑过。原因是 `admin_0025:165`、`notify_0022:67` 有意禁止降级；处理方式待老板定（第 1 节 #10）—— static.md #4、xiaobai.md #5、dynamic.md #7
- **`test_palantir_webhook.py`**：`use_ops_support` 下 8 条失败，外网拦截把 127.0.0.1 本地模拟接收端也拦了（master 06a1202 起就有）—— palantir.md #2
- **`test_event_delivery_boundary.py`**：`test_explicit_test_endpoint_and_stable_subscription_idempotency`，测试桩缺 `ontology_id` —— palantir.md #3
- **`test_dynamic_alerts.py`**：`test_start_action_full_flow` / `_order_approver` / `_errors` 3 个准备步骤报错（`grant_fixture_roles` 在空库找不到 U010001 / R-01；`test_palantir_machine`、`test_dynamic_actions` 同一夹具同样受影响）—— dynamic.md #6、xiaobai.md #10
- **`test_dynamic_rules.py::test_hits_paging_and_detail`**：0126 / 0127 顺序断言 —— dynamic.md #5
- **其他动态 / 集成**：`test_dynamic_seed` 2 条、`test_conventions::test_job_stubs_registered`、`test_integration_writeback_calls` 的 TypeError —— xiaobai.md #10
- **admin**：`test_admin_users.py` 3 条（`test_depts`、`test_import_preview_csv_and_rows`、`test_enable_disable`）；`test_audit_logs.py`（多了 `ontologyId: None`）；`test_admin_roles.py::test_list_and_get_match_mock`（mock 漂移）；`test_admin_audit_export.py::test_export_migration_roundtrip_and_metadata`（列注释）—— xiaobai.md #6–#9
- **问数**：部分旧问数 POST 测试 403，因为测试用户在本体 1 是只读 viewer；`test_qa_dynamic_semantic.py::test_single_object_summary_attached` 单独跑失败、整文件跑通过（顺序依赖）—— qa.md #9
- **前端检查脚本**：`check-static-pages.mjs`（缺 `crErr` 桩，补一行即可），以及 `check-context-integration`、`check-qa-integration`、`check-qa-session-context`、`check-real-object-resolver`、`check-object-ask`、`check-object-behavior`、`check-real-data` —— static.md #5
- **Ruff / 格式化**：放到最后统一做。`06d7a15` 上实测 `ruff check .` 121 个错误、`ruff format --check .` 45 个文件未格式化（之前汇总时是 183 个错误、50 个文件）；其中 `app/modules/dynamic/rule_runtime.py` 的 E501 见 dynamic.md #8

## 4. 写了未测 / 功能缺口 / 运行限制（按家指路）

**Palantir（palantir.md）**
- 下线入站静态令牌、加入站签名校验，延后到接入方都换成 API 密钥（#6）
- 副作用人工重试、调用记录逐次尝试、策略前端编辑器、`evalPassedAt`、函数按密钥白名单等，不在第一档（#8，细节见 §5）
- 时间线显示种子里的 `POST /praybill/save`，外观小问题（#4）

**动态（dynamic.md）**
- 运行限制：R-012 / R-019 跨对象、带参指标条件执行不了，真实库存事件不会产生新告警（#9）；事件规则告警要等下一次 sweep 才评估（#10）；种子告警要手点「生成建议」（#11）
- 写了未测：`decide_pending` 整链和失败分支（#12）、多比较条件参数表达式（#13）、维度对象属性读取（#14）、前端 AI 处置建议面板和动作中心预填（#15）、QA 页面任务挂载（#16）、服务账号名称（#17）、dynamic-r4 / r5 / r6 其余未测项（#18–#20）
- 旧遗留：`VITE_DYNAMIC_CR_WRITE` 默认关（#21）、推演模板没有管理接口（#23）、生命周期草稿残留和 `/lifecycle/changes` 行级裁剪（#24）

**静态（static.md）**
- 分面按角色范围的端到端测试缺一条（#6）
- 待核：writeback 注释与代码不一致（#7）；变更单写权限是统一 `releases:manage` 还是按目标页，文档前后矛盾（#8）
- 其他负责人的遗留和设计限制（#9、#10）；属性级权限、分支、跨本体模板、统一同义词表延后到二、三期（#11–#14）

**本体小白（xiaobai.md）**
- 演示服务账号种子未做（#2）
- 达梦、金仓、MySQL 驱动和正式登录 / 单点登录等属于第二、三档，不做（#11）

**问数（qa.md）**
- 页面订阅摘要已写进 outbox 但没人消费：`dispatch_outbox` 是空桩，通知侧要订阅，`notification` 的两个外键要改可空（#1）
- 每日自动评测已登记，没在真模型上跑过（#4）
- 前端没展示多对象动态摘要（#7）
- 自动出题抽查 7/20：出题没排除无数据指标、仓库列名、平均出库时长 SQL 解析失败、动作题判分过严（#10②–⑤）；100 题里仍错 006、012、015、048（qa.md 2.0d 评测一节）
- 其他见 qa.md 2.0.1 表（#11）
