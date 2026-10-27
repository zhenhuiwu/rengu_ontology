# 管理、通知与集成修复记录（2026-10-27）

基线 `8d15cb9`。用户本轮要求解决未完成项及测试暴露的问题。本组只负责管理、通知、集成/事件、函数密钥边界；没有改主库种子、旧业务值、真实外部通道、模型或生产登录。

## 产品修复

### 页面订阅站内通知

- 复用 `qa.page.subscription` 的真实 outbox。`jobs.dispatch_outbox` 已有实现，当前标准事件 worker 把同名任务注册到 **event-delivery** 队列、每分钟周期、独立锁；不要求另起 outbox 服务。兼容 Procrastinate 的 timestamp 参数。
- 派发锁定到期记录并按 payload.ontologyId 明确分空间；每条采用保存点，部分失败回滚，保留安全错误码/尝试数并退避重试。未消费别的 outbox topic。
- 发送前按当前在用用户、真实本体成员、qa-pages 功能权限、当前页面共享可见性、当前启用的站内订阅及收件人重新取交集。只通知“页面已更新，请打开查看当前权限结果”，不转发拥有者的 text/cache/旧权限摘要。
- inbox、未读数和已读操作再次按当前页面可见性裁剪；取消共享后不再显示标题或计数。点击通知以 `page_id` 和预览页签进入真实 MyPages。
- `notify_0028` 仅扩展来源：旧 CR 两个引用改可空，新增 source_kind/source_outbox_id/target_code、互斥来源约束、按本体/outbox/收件人唯一；旧 CR 数据保留，真实 CR 通知继续可读。未回填旧页面通知。
- 外部邮件/企业微信不伪报成功，未配置时保留失败待处理；本轮只实现站内通知。

### 函数按 API key 单项白名单

- `admin_0029` 为共享 ApiKey 只增加可空 JSON function_types，不更改存量 scopes、ontologyIds 或秘密。`null` 保留原 functions:invoke 行为；`[]` 全部拒绝；列表只允许列出的函数业务 code。
- 两条执行路径 `/functions/{code}:test`、`/machine/functions/{code}:invoke` 与机器工具目录使用同一白名单。仍取当前 principal 权限、当前本体成员/绑定及发布状态；没有跨本体回退。
- 按动作/事件的既有合同，函数 code 只在密钥已经授权的 ontologyIds 范围内解释。新签发仍固定当前空间；后来加入别的空间不会扩大旧 key。若存量 key 已明确授权多个空间，编号白名单适用于这些已授权空间，而不是自动新增空间授权。
- 新建/复制、详情和调整授权面板增加函数白名单及绑定本体展示。已有列表只能收窄，扩大或 list→null 需新 key；移除 functions:invoke 后不能再调用函数。保存失败保留表单，界面不回传秘密。

### 副作用人工处置与逐次尝试

- 新增按执行记录的后台真分页列表、净化详情/尝试记录与“重新排队”接口及独立 ActionEffects 面板。
- 读取复用当前执行记录实例范围及引用可见性，返回状态/尝试/安全错误码，不返回原载荷、配置 URL 或 token。共享 outbox 的读取/派发明确加 ontologyId，不能跨本体取行。
- 人工重新排队要求动作中心与外部系统管理权限，锁定并校验预期 attempts/state，复核当前操作人目标权限及原执行人的当前授权/原定义/映射，保留原幂等键和逐次记录，并追加审计。
- 仅确未发送的 rejected 或有明确429/5xx可重试证据的 failed 可重新排队；unknown、claimed、queued/retrying、成功、无安全证据的失败拒绝。接口仅排队，不立即外写。
- **global REAL_CONNECT kill-switch 没有放宽**。未配置适配器时重新排队也会真实503；不能为界面成功绕过产品安全条件。
- 为运行组人工核对闭环，preflight 增加可选 review 字典，复用同一次真实 checker 结果回传 humanChecks/declaredEdits；旧调用仍返回原定义。review_snapshot 由运行组 dynamic_0030 实现。

## 测试契约修正与排障

旧 `f3b8119` 全仓基线有423个失败记录，本组按文件归属解析其中74条（含admin/audit/event/integration），**不把旧结果当8d15cb9当前必现，也不声称423条全清**。原记录与原18项本地Webhook端到端证据均保留，未覆盖或删除。

- use_ops_support 原 transport 拦截同时拒绝本地 receiver；现仅允许该测试 receiver fixture 显式登记的精确 URL、POST、127.0.0.1，其余 HTTP 仍失败。没有泛放 loopback/SSRF。
- event boundary 桩补真实必要 ontology_id；effect 桩补 payload.ontologyId。签名、重试和稳定幂等键原有测试恢复执行，不改产品授权。
- 用户部门候选已是实际组织+存量部门，自由部门文本是现合同；旧固定7项/将合法一级组织当无效的断言已修正，仍验证空部门/格式与实际角色。未更改 mock/seed 业务值。
- 用户停用保护使用独立只读连接汇总本体待办。旧测试未提交CR，该连接正确看不到；新增仅限 rengu_fix_admin_20261027 的 committed approval fixture，创建真实已提交来源并在测试后清理自己记录，不修改产品读取边界或伪造0待办。
- 审计期望补 ontologyId:null，角色范围期望补 objectTypeCode:null；不删除产品追溯字段或改变权限求绿。
- 新副作用列表开发时回归发现未发送项 dispatched_at=None 被无条件格式化而500，已按可空字段返回null；同组真实PG接口回归通过。前两轮新fixture的枚举名与异步关系未加载错误属于测试准备错误，已改准确枚举/显式加载。

## 数据库与执行边界

针对性测试只写 `rengu_fix_admin_20261027`，通过现有安全 runner 在内存读取 ignored .env、验证5433来源和已有直接runtime-role成员关系。没有主库seed/清理/权限修改、迁移往返、真实 provider 或外部业务写入。

新增迁移链：`dynamic_0027 → notify_0028 → admin_0029 → dynamic_0030`。本组独立测试库已实际增量升级。ROOT/B已完成主库完整备份（before-fixes-0030-20261027-213845.dump，1,870,559 bytes，pg_restore --list及SHA256核验），再实际增量升级0027→0030并统一API/4worker启动，记录数前后一致；没有主库恢复/迁移往返或seed。详细部署证据见[运行组报告](26-10-27-fixes-qa-dynamic.md)。其后QA配置新接线还需ROOT最终加载，不以第一次启动代替后续代码已加载。

## 已执行验证

工作目录 backend：

```powershell
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_fix_admin_20261027 -- -p tests.use_ops_support tests/test_subscription_notifications.py tests/test_function_key_allowlist.py tests/test_effect_management.py tests/test_api_key_scopes.py tests/test_palantir_webhook.py tests/test_event_delivery_boundary.py tests/test_admin_users.py tests/test_admin_pending_approvals.py tests/test_audit_logs.py tests/test_admin_roles.py::test_list_and_get_match_mock -q --tb=line --show-capture=no --maxfail=5
```

最终同一轮 **92 passed，0 failed/error，44.56s**，一个既有pytest_asyncio event_loop_policy弃用warning，无skip。包含：

- 通知8项：真实来源/CR兼容、幂等、撤权前后、收件人移除、跨本体、外部渠道保留、部分失败回滚重试、周期签名与实际标准worker注册task→真实PG通知。
- 函数3项：真实纯计算执行两入口/工具目录、null/空/收窄、后加入空间不扩大key、未发布machine拒绝。
- 副作用6项：真实当前权限/源映射复核、安全入队/稳定幂等和审计、unknown/claimed/成功/非重试失败拒绝、跨本体及撤权。
- 原APIkey scopes、Webhook签名/退避/记录与event boundary，以及用户/待办/审计/角色代表回归。

前序10/20/40项轮次和各定点与此轮重叠，不相加。补充三个既有文件独立运行：

```powershell
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_fix_admin_20261027 -- -p tests.use_ops_support tests/test_admin_imports.py tests/test_admin_tenant_settings.py tests/test_integration_reconcile.py -q --tb=line --show-capture=no --maxfail=4
```

**52 passed，25.55s，1个既有warning**，与上面92项分组/轮次分开记录，不冒称同一轮144项全绿。这三文件未为求绿改业务值，旧f3b失败在当前基线及正确测试上下文下不再复现；不把它们归为本组新增产品修复。

其后仅定点补跑：`tests/test_function_key_allowlist.py` **4 passed，2.85s，1个既有warning**，其中新增服务账号真实principal执行允许项/拒绝项及撤销后401，其余3项与92项重叠；`tests/test_integration_code_lock.py` **1 passed，0.46s，1个既有warning**，只修独立测试Session未设置本体上下文的fixture，产品编号锁逻辑未改。两次均沿上面同一安全runner、同一独立admin测试库、`-p tests.use_ops_support -q --tb=line --show-capture=no`执行，不写成完整93/145项重跑。

第一轮统一前端生产构建由ROOT执行：含vue-tsc，4488模块，Vite37.97s，exit0；compiled index-CzfChHqh.js已确认包含通知 page_id/ptab=preview 最后修正。所属31个Python文件AST通过、限定Ruff F/I通过、限定diffcheck通过（既有LF/CRLF提示）。该构建不覆盖后续ActionEffects操作错误/时间格式与B订阅配置修复，须另记最后统一构建；不能用构建代替接口回归。

## 隔离 UI 验证环境与已确认事实

ROOT明确授权复用 `rengu_test_use_qa_20261027`，先升级到0030，再仅追加UICHECK-FIX前缀记录；原4页/1模板/2次本地动作基线已在提交前后校验保持。首轮准备时对ontology-owner的过严断言中止并回滚；核实U1实际editor但真实功能管理权限齐全后，只调整测试前置，不改任何旧角色或成员。

- 通知id1，页面 UICHECK-FIX-PAGE，只有合法页面更新提示。
- 展示用key AK-UICHECK-FIX，两个函数 UICHECK-FIX-FN-A/B；随机token仅hash，不输出明文。
- 独立测试动作 UICHECK-FIX-ACTION/v1，显式confirm/stage策略，新专属测试role仅该动作/target。执行展示记录 UICHECK-FIX-RUN、失败effect2及unknown effect3为明确人工构造的验证场景，**不是实际外部503/超时或动作执行证据**。
- 8001加载B最终QA代码后的当前PID52188/session11333，无reload/worker；重启只加载应用，没有reseed或再调用fixture准备。HTTP transports全部deny，外部和provider flags关闭。精确写白名单仅该key收窄PATCH、该通知本人read、该失败effect retry及下述单页schedule PUT；其他写均403。预期retry为配置未启用的真实503，不能写成成功入队。

ROOT已实际将展示key移除FN-B保存，刷新详情仅保留FN-A；未改主库或旧role。铃铛实际1条通知，点击真实read并正确跳转page_id=UICHECK-FIX-PAGE&ptab=preview，badge消失，截图在frontend/shots/fixes-20261027。

该次UI同时发现QA配置端原有未闭环：PageDrawer概况/计划面板硬编码“未启用”，service.page_view_out能力常量false，PUT schedule主动拒绝sub.on。fixture的订阅表真实enabled=true，并非展示JSON没同步。ROOT明确授权QA运行组补齐真实配置接线，最终定点25/25通过后才重新加载测试API；没有为截图伪改状态。

ROOT实际effect重试返回503且未发送；首版组件把操作错误并入加载error，导致两条记录被v-if分支隐藏，并直接显示env诊断文字；尝试时间也直接显示UTC ISO。已按真实反馈修复：独立operationError、失败保留已加载items/total并独立渲染，按code/status映射业务语，使用项目已有dayjs及已安装utc插件统一显示UTC+8。API/DB原UTC记录不改，global kill-switch不改。

`node scripts/check-action-effects.mjs` 的实际SFC状态与渲染回归通过：拒绝后仍有2条/total2、明确失败+结果未知、业务提示、无RENGU/env文字、07:42UTC显示15:42。ROOT随后真实复测通过：503后2条记录和total2仍在，unknown按钮disabled，业务提示“当前执行通道不可用，未重新排队，请联系管理员”，DOM无RENGU_INTEGRATION_；实际CSS1707×960、scrollWidth1707无横溢。[修后截图](../frontend/shots/fixes-20261027/effect-retry-preserves-records.png)。这不是1920/2560两档视觉全验。

按ROOT要求另用强制只读连接核QA测试库：key.function_types恰[UICHECK-FIX-FN-A]，notification1.read_at非空；effect2=failed/attempts1，effect3=unknown/attempts1，manualRetries均0；对应UICHECK-FIX-RUN与该空间WritebackCall均0。未改这些验收状态。失败effect的预置503尝试是fixture，实际UI重试被本地配置守卫503拒绝；两者不能混称真实接收端失败/成功投递。

为B继续配置闭环，ROOT授权只给UICHECK-FIX-PAGE追加一张真实卡：从既有非seed、done、当前发布的QA-UI-SOURCE-T001取得turn_plan，经execute_cards真实只读Guard计算cache及当前身份指纹；填该fixture规范手动time/onFail。未改旧4页/1模板/2run、key收窄结果、已读标记或effect记录。testserver另加仅该页schedule PUT（手动/每天有限时刻、inbox/收件人仅本人、保留旧结果）的精确白名单。

ROOT随后实际保存每天08:00、站内信、本人订阅，toast成功；整页reload仍恢复原配置，列表与Drawer显示一致。测试时钟固定10/25，显示下次10/26 08:00；不是拿真实日历误判过期。之后使用只读事务、降权角色和scope1核验：该页refresh为每天08:00/保留上次结果，next_run为`2026-10-26T00:00:00Z`（Asia/Shanghai `2026-10-26 08:00:00`）；订阅enabled=true、channel=inbox、recipients=[王敏]、send_when=after_refresh、only_changed=false。key仍仅FN-A，notice1仍已读，effect2/3分别failed/unknown且attempts各1、manualRetries各0，空间WritebackCall仍0。没有修改已保存配置。旧email配置列表状态标注另由QA组收尾；不把它算作本组外部邮件投递能力。本轮无测试worker，故配置保存恢复与任务/通知的针对性PG回归分别记录，不声称已经等真实每日到点消费。

ROOT结束隔离UI核查后，已关闭跟踪的测试API session11333（PID52188），只读端口核验8001监听数为0；测试证据数据保留，未再seed或修改产品。临时5282前端由ROOT关闭，主5280/8000与四个worker未由本组启动或停止。

## 本组文件清单与冻结范围

以下均为仓库相对路径；其他组的修改和旧截图删除不归本组。

- 后端产品：`backend/app/common/auth.py`、`backend/app/jobs/__init__.py`、`backend/app/modules/admin/api_keys.py`、`backend/app/modules/admin/models.py`、`backend/app/modules/event/worker.py`、`backend/app/modules/functions/router.py`、`backend/app/modules/integration/action_effects.py`、`backend/app/modules/integration/effect_management.py`、`backend/app/modules/integration/execution.py`、`backend/app/modules/integration/router.py`、`backend/app/modules/machine/router.py`、`backend/app/modules/machine/tools.py`、`backend/app/modules/notifications/models.py`、`backend/app/modules/notifications/router.py`、`backend/app/modules/notifications/service.py`。
- 增量迁移：`backend/migrations/versions/notify_0028_subscription_sources.py`、`backend/migrations/versions/admin_0029_function_allowlist.py`；dynamic_0030归运行组。
- 前端产品：`frontend/src/api/apikeys.ts`、`frontend/src/api/effects.ts`、`frontend/src/api/notifications.ts`、`frontend/src/components/ActionEffects.vue`、`frontend/src/components/NoticeList.vue`、`frontend/src/pages/admin/ApiKeys.vue`、`frontend/src/pages/admin/NotificationSettings.vue`；Actions.vue接入归运行组。
- 测试：`backend/tests/admin_committed_support.py`、`backend/tests/test_subscription_notifications.py`、`backend/tests/test_function_key_allowlist.py`、`backend/tests/test_effect_management.py`、`backend/tests/test_admin_pending_approvals.py`、`backend/tests/test_admin_roles.py`、`backend/tests/test_admin_users.py`、`backend/tests/test_audit_logs.py`、`backend/tests/test_event_delivery_boundary.py`、`backend/tests/test_palantir_webhook.py`、`backend/tests/test_integration_code_lock.py`、`backend/tests/use_ops_support.py`、`frontend/scripts/check-action-effects.mjs`。
- 仅隔离UI准备/服务：`backend/scripts/qa_use_test_server.py`、`backend/scripts/fixes_admin_ui_prepare.py`、`backend/scripts/fixes_admin_ui_server.py`。
- 报告：`docs/26-10-27-fixes-admin-integration.md`；ROOT截图证据位于`frontend/shots/fixes-20261027`，不冒称本组拍摄。

后端产品在向ROOT声明freeze后没有再修改；后续仅ROOT明确授权的ActionEffects前端修正、测试用例/隔离UI helper及报告。QA订阅配置源码由运行组修复并单独加载，不能归为本组暗改后端。

## 明确未扩大范围

入站HMAC/静态token迁移、生产登录/SSO、普通数据库运行账号、外部通知渠道、真实Webhook接收端、国产数据库部署验证、ERP/Kafka、模型调用/预测继续按原约定延期。未跑全仓、迁移往返、生产压力或真实外部链。

## 59acc70后续复核：确认人失效不能切换条件身份

按用户“继续解决未完成问题”的授权，限定复查发现`integration.execution.criteria_user`原先捕获确认人的鉴权AppError并回退当前审批人；确认人停用/撤销本体成员时，可能改用权限不同的审批人检查提交条件，违反既定确认人语义。已最小删除该异常回退，保留原401/403及code，审批人其余功能/对象/操作检查不变；本人和未确认分支保持现行为。

新增`backend/tests/test_confirmation_principal.py`只在`rengu_fix_admin_20261027`真实PG事务中准备测试身份，未mock核心鉴权、未发送HTTP/模型/外部请求。相同命令修前复现2 failed（停用/撤成员未拒绝）/3 passed，修后**5 passed，0 failed，0.44s，1个既有pytest_asyncio弃用warning**：

```powershell
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_fix_admin_20261027 -- tests/test_confirmation_principal.py -q --tb=line --show-capture=no
```

新增覆盖合法确认人、停用、撤成员、本人、未确认5个分支；不将其与此前92/52/4/1轮次拼成新的全量通过数。新测试Ruff F/I与格式检查通过。无新schema、主库写入或主服务重启；主进程尚需ROOT统一安排加载本次修复。统一业务决策见[下一步清单](26-10-27-decisions-next.md)。

A组随后对唯一调用方preflight及新增回归做限定只读交叉复核，未发现新增确定问题；没有因此改源码或重复执行测试。
