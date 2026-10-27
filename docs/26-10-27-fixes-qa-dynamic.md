# QA 与动态运行问题修复记录（2026-10-27）

基线：`8d15cb9`。本轮以现有业务权限和独立本体边界为准，修复复核信息缺失、前端摘要未呈现及过时测试准备。未调用真实模型、外部动作或评测队列，未写开发库业务数据，未修改 R-012/R-019 定义。

## 本轮改动

| 范围 | 行为与边界 |
| --- | --- |
| 动作预检 | `:validate` 返回真实 `humanChecks`、`declaredEdits`，沿用现有条件及执行配置预检，不创建动作、日志、事件或外部调用。 |
| 运行详情 | 暂存时保存与动作版本、当前目标及参数相符的复核快照，重新打开仍可查看；读取不重新执行检查。当前身份无法核验全部引用对象时，复核列表与原参数一起隐藏。历史记录没有快照时明确显示未记录，不推算旧事实。 |
| 告警、动作、QA 界面 | 建议卡、手动预检、启动确认、详情及审批显示人工复核项和外部声明效果；展示不等于执行完成，不新增强制勾选。动作详情接入同组协作的真实副作用组件。 |
| 多对象动态摘要 | QA 对话显示结构化对象面板，包含告警、规则命中、生命周期、动作数量。保留真实零值；无权限显示不可查看，不把缺失当零，不添加演示结果。 |
| 策略只读元数据 | 动作详情返回安全生效策略、原始存储值 `policyStored` 和 `policyStatus`，区分未配置、有效、旧非法值。历史视图取自身快照，非策略发布沿用原值；仅显式策略变更严格校验。友好编辑控件由静态组接入。 |
| 测试基础 | 运行器仅新增三个固定隔离库名；动态夹具补基本用户/角色准备；QA 显式授权后同步真实本体成员；分享正向用例仅给该页面拥有者编辑身份；结构识别的测试建表只在隔离库 setup 事务临时使用原连接角色，运行读取恢复受限角色。 |

数据库变更：`dynamic_0030` → `admin_0029`，仅向已有受本体隔离保护的 `action_run` 增加可空 JSONB `review_snapshot`。不回填历史记录，不新增种子或授权；降级明确拒绝丢弃运行历史。主库升级、备份、重启由主会话统一处理，本组未部署。

## 旧失败基线与本轮诊断

旧文件 `backend/tests/e2e/results/baseline-f3b8119.json` 属于拉取 107 个提交之前的版本：336 failed、806 passed、87 errors、1 skipped，共 423 条失败记录。它不是当前版本的执行结果，本轮保留原文件和日志。

按旧日志签名粗分：169 条权限拒绝、84 条其它断言/异常、44 条响应键缺失、37 条版本种子 UPDATE、29 条基本用户/角色未准备、23 条 RLS、15 条受限角色建表、11 条自然键冲突、7 条主动禁止破坏性降级、3 条旧签名/导入、1 条环境缺失。这是定位线索，不表示每组已全部修复。

确认的区分：

- `test_seed_is_idempotent` 当前代表用例已通过，旧 37 条所属问题未复现，没有为它修改产品 upsert。
- 结构识别测试直接用运行受限角色执行 `CREATE SCHEMA`，是夹具准备不符合当前权限边界；修复仅在测试库建表准备，不放宽产品角色。
- QA 分享正向测试只有页面功能权限、缺本体编辑身份时返回 403 正确；补的是该拥有者的明确测试身份，保留共享 viewer、行范围、来源不明旧结果等拒绝断言。
- 规则命中列表已有时间/id 稳定排序，旧断言硬比 JSON 插入序，时间相同的记录顺序不同。等效用例改验完整集合、时间倒序、重复请求稳定及跨页无重复，没有修改产品排序或放宽数量/耗时断言。
- 新复核测试首次缺导入依赖夹具 `dyn`，已补显式导入；未将其作为产品问题。
- 旧非法策略的发布 guard 曾直接读取原始 `mode`，可能阻断无关编辑，非对象值还会触发异常。现改按安全生效策略检查自动执行门槛，同时原样保留旧值；两个新增真实 PG 用例覆盖非法对象和字符串，合法自动执行仍要求免审。
- 单对象摘要夹具现在显式登记已知治理表并加载当前本体，不依靠此前 HTTP 请求的导入顺序；该用例新进程单独运行通过。没有为测试添加新业务映射。

## 实际检查轮次

所有后端写验证只使用 `rengu_fix_qa_20261027`。运行器在内存读取本地连接配置、校验源库及固定目标库，未输出凭据；不创建角色、不更改主库授权、不 DROP 已有库。外部网络通过测试插件阻断，仅现有用例显式受控接收器可用。

| 轮次 | 结果 | 用途 |
| --- | --- | --- |
| stage 来源、告警启动、单对象摘要代表 | 3 passed，11.87s | 先确认当前调用链可用。 |
| seed 幂等 + 结构识别代表 | 1 passed、1 fixture error，2.35s | 区分旧 seed 问题与受限角色建表夹具。 |
| 新复核用例初跑 | 1 fixture error | 缺 `dyn` 导入；修复后重跑。 |
| 新复核两条 + 结构识别代表 | 3 passed，12.59s | 无写预检、重载快照、历史未知、不可见跨对象引用。 |
| QA 动态语义 + 结构识别全文件 + 规则命中代表 | 26 passed、1 failed、1 skipped，43.70s | 唯一失败为旧插入序断言；跳过项保留原环境条件。 |
| review + QA API + 策略小组，遇首错停 | 22 passed、1 failed，93.99s | 首错为分享正向用例缺本体编辑身份。 |
| 分享正向代表 + 策略 + 决策 | 26 passed，54.39s | 针对前述问题复测，策略/额度/拒绝/决策原行为保留。 |
| 最终针对性合集 | 98 passed、1 skipped，227.93s | JUnit：`backend/testlogs/fixes-20261027-qa-dynamic.xml`；不是全仓回归，也不是真实模型评测。 |
| 旧非法策略发布 guard 收尾 | 6 passed，15.94s | 两条新增保留原值用例，加合法策略发布及非法发布原子拒绝；JUnit：`backend/testlogs/fixes-20261027-qa-policy-legacy.xml`。 |
| 单对象摘要单独运行 | 1 passed，8.24s | 新进程只选该用例，确认不需要其它测试先发请求。 |

最终合集加收尾共 100 个不同 case 有通过记录；重复复测不相加。1 个实连 LLM 用例跳过的原因是**隔离测试环境主动禁用真实模型凭据，故实连 LLM 用例未运行**，不表示用户本机缺少密钥。

标准命令前缀（在 `backend`）：

```powershell
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_fix_qa_20261027 -- -p tests.use_ops_support
```

最终选择：`tests/test_dynamic_review.py tests/test_qa_api.py tests/test_qa_dynamic_semantic.py tests/test_palantir_policy.py tests/test_dynamic_decisions.py tests/test_use_ops_runtime.py tests/test_dynamic_rules.py::test_hits_paging_and_detail tests/test_auto_ontology.py tests/test_versioning.py::test_seed_is_idempotent --tb=short --show-capture=no -q --junitxml=testlogs/fixes-20261027-qa-dynamic.xml`。

轻量检查：本组后端修改 Ruff `--select F,I` 通过；前端 `node scripts/check-action-review.mjs` 使用真实 Vue 编译和服务端渲染，5 个场景通过（人工清单、声明效果、转义、未知/受限隐藏、多对象零值与不可见状态），无服务调用。静态组统一执行 `npm run typecheck`，exit 0，覆盖本组新增组件。之后手动预检仅补了输入/身份/本体变化时丢弃迟到响应的保护，主会话最终 build（含 vue-tsc）已通过并覆盖此小改。

## 尚未完成与验证限制

- 通用跨对象规则、指标时间窗及历史值函数没有绑定完整的执行口径。当前 `rule_runtime.Expression` 只读取明确绑定的当前对象属性，不能通过猜测关联/聚合/参数含义激活 R-012/R-019；保留明确不可执行结果。动作参数预填、当前对象有限条件、受控提交仍按已有契约执行。
- 情景推演仍需真实预测模型，保留不可用响应；本轮没有添加示意预测引擎。
- 旧自动出题 7/20、远端报告 96/100 均未在本轮重跑。没有消费现有 QA 评测队列，没有修改业务标准答案或声称分数提升；出题/判分口径冲突仍需另行处理。
- 本轮未逐项重跑旧 423 条失败、未全仓 pytest、未做集成/迁移升降级往返。现有 pytest-asyncio `event_loop_policy` 弃用警告单列，不等于产品失败。
- 产品前端实际检查由主会话进行；本组渲染检查不等同于真实外部动作、非空通知或真实模型的端到端验收。

## 主库部署事实（2026-10-27 21:38–21:40，Asia/Shanghai）

只操作本地 `localhost:5433/rengu_ontology`。审阅确认唯一 head 为 `dynamic_0030`，三项迁移仅放宽通知来源并增加约束/字段、增加 API key 可空函数限制字段、增加动作可空复核快照；无删除/种子/历史业务改写。完成完整 custom-format 备份、`pg_restore --list` 可读性和 SHA256 核验后，核对旧进程命令，精确停止旧主 API 与四个 worker，再执行 `alembic upgrade head`。

- 迁移前：`dynamic_0027`；迁移后：`dynamic_0030`，顺序为 `notify_0028 → admin_0029 → dynamic_0030`，实际升级成功。
- 备份：`C:\Users\15827\AppData\Local\Temp\rengu-ontology\before-fixes-0030-20261027-213845.dump`，**1,870,559 bytes**。
- SHA256：`6032cf57267b004a7ea8d48b388945adf6e301ee5d78afc4ddbb09bb8f7d0ac8`。未执行主库恢复或升降级往返。
- 外连开关保持：`integration_real_connect=false`、`event_delivery_http_enabled=false`、`RENGU_DS_REAL_CONNECT=false`。未改配置、启用评测/每日模型任务或执行外部动作。

迁移前、升级后及重启后的实际记录数一致：

| 表 | 前后数量 |
| --- | ---: |
| ontology_workspace | 2 |
| object_type / property / link_type / metric | 18 / 211 / 27 / 15 |
| biz_order / order_line | 1286 / 3905 |
| store / product | 95 / 12 |
| action_run / event / alert | 1991 / 1243 / 154 |
| change_request / notification / qa_page / api_key | 19 / 0 / 33 / 0 |

主 API 无 reload：实际 PID **36992**，launcher **46016**，`127.0.0.1:8000`。实际 `GET /healthz` 返回 `{"status":"ok"}`，`GET /readyz` 返回 `{"status":"ok","db":"ok"}`。日志：`%TEMP%/rengu-ontology/backend-fixes-0030-20261027-213930.stdout.log` / `.stderr.log`。启动成功，无异常；仍有既有开发环境数据源临时密钥警告，未改动密钥配置。

四个标准 worker 新进程均存活，日志只有既有主模块应用提示及 Windows 不支持信号处理提示，无异常：

| worker | 实际 PID / launcher | 日志前缀（同 `%TEMP%/rengu-ontology/`） |
| --- | --- | --- |
| integration | 21724 / 11276 | `integration-worker-fixes-0030-20261027-213950` |
| event | 26300 / 27108 | `event-worker-fixes-0030-20261027-213950` |
| data | 31308 / 46412 | `data-worker-fixes-0030-20261027-213950` |
| dynamic | 6952 / 51288 | `dynamic-worker-fixes-0030-20261027-213950` |

各日志前缀对应 `.stdout.log` / `.stderr.log`。event 标准 worker 已按当前代码包含每分钟 `outbox.dispatch`；dynamic 仅正常规则及受控页面刷新，未启动 QA-eval/每日模型评测。迁移前后待执行队列和未分发 outbox 均为 0。前端 PID29132/5280 保持运行；未触碰独立测试服务、其他组测试进程、`auto_ontology` 数据库。主会话已完成前端 build（含 vue-tsc，37.97s、4488 modules、exit 0）；本组没有重复构建或业务测试。

## 实际 UI 发现后的页面刷新/订阅闭环补修

主会话随后在隔离 UI 服务发现：`UICHECK-FIX-PAGE` 的订阅记录已启用且站内通知已收到，但 PageDrawer 硬编码显示“未启用/原设置未发送”；保存接口仍拒绝所有自动刷新和开启订阅。原执行器和通知消费链已存在，这属于真实产品接线缺失，不能把 UI fixture 改成关闭状态掩盖。

本次沿用 `PUT /api/qa/v1/pages/{pid}/schedule` 和原 `refresh/sub` DTO，无新表或迁移：

- 自动刷新只允许包含当前发布依据、公开字段及有效执行定义的页面；保存不执行查询/刷新、不发送通知。实际执行仍复用现有按当前拥有者权限运行的受控只读查询，缺定义/版本过期明确拒绝。
- 拥有者须有当前本体编辑身份和页面权限；共享编辑成员不能修改拥有者的后台任务。历史及预览视图保持只读。
- 支持手动、每小时、每天、每周、每月；校验真实时间、星期、日期，并返回服务端时区与下次刷新时间，不再显示固定演示日期。失败处理仅支持保留上次结果，未启用尚未实现的失败通知承诺。
- 仅允许开启站内信；邮件、企业微信未配置，前后端均不可启用。接收人限当前有效、具页面功能权限的拥有者或已有共享成员；空名单表示拥有者本人，重名及无关收件人拒绝。候选功能角色必须属于当前本体绑定。
- 新建订阅时同步 ORM 关系，避免同一会话仍把已保存记录读成关闭。GET 返回真实 `scheduleActive/subscriptionActive`、`canSchedule`、`nextRun/scheduleTimezone`，不以功能未配置的常量替代数据事实。
- PageDrawer 同一表单用于真实模式，保存/重新读取/还原均使用实际配置；无执行卡片时允许保持手动并配置站内订阅，同时明确自动刷新不可用。表单接收人来自该页面真实拥有者/共享成员，不借用全局演示名单。

新增产品文件：`qa/pages_api.py`、`qa/service.py`、`qa/schemas.py`、`frontend/src/api/qaPages.ts`、`frontend/src/pages/qa/pages/PageDrawer.vue`。新增检查：`backend/tests/test_qa_schedule_crud.py`、`frontend/scripts/check-qa-schedule.mjs`。

实际轮次：新 CRUD 初轮 1 failed（首次创建后的关系缓存缺陷），修复后 15 passed（42.90s）；旧调度 runtime 与原 API 代表 9 passed（16.55s）。再补独立配置时区用例，最终定点合集 **25 passed，74.92s**，JUnit：`backend/testlogs/fixes-20261027-qa-schedule.xml`，没有重跑前面的 98 条合集。25 条涵盖 16 条新增 CRUD 与 9 条既有调度/契约用例。

命令：在原安全运行器前缀后选择 `tests/test_qa_schedule_crud.py tests/test_qa_page_schedule.py tests/test_qa_pages_write.py::test_schedule tests/test_qa_pages_write.py::test_next_run_rules --tb=short --show-capture=no -q --junitxml=testlogs/fixes-20261027-qa-schedule.xml`。

前端 `npm run typecheck` exit 0；`node scripts/check-qa-schedule.mjs` 通过保存/还原、真实接收人、服务端下次时间、拥有者/只读限制、迟到响应不跨本体写前端状态，未访问服务或数据库。Ruff F/I 与 diffcheck 通过。主库仍为 `dynamic_0030`，源码冻结后已按主会话确认完成 API/dynamic worker 重启（见下文）；隔离 UI 的真实保存/恢复验收结果尚不能由这些脚本替代。

### 调度闭环源码加载（22:00–22:01，Asia/Shanghai）

主会话明确确认后，核对实际 PID、exe、命令及父子关系，仅停止上轮 API 36992/46016 和 dynamic 6952/51288，启动新主 API **16820 / launcher48080**、dynamic worker **26248 / launcher4160**，API 无 reload。未再次迁移、备份、seed 或修改开关。

新日志位于 `%TEMP%/rengu-ontology/backend-qa-schedule-20261027-220056.stdout.log` / `.stderr.log` 和 `dynamic-worker-qa-schedule-20261027-220056.stdout.log` / `.stderr.log`。API 启动完成，`readyz={status:ok,db:ok}`、`healthz={status:ok}`；dynamic 仅既有主模块/Windows 信号提示，无异常。

主库 `dynamic_0030` 及上述 16 张表数量全部不变，待执行作业和未分发 outbox 仍为 0。只读确认已有页面 `next_run` 非空数量 0、已启用站内信订阅 0，重启未启用任何旧计划或创建通知。integration21724、event26300、data31308 和前端29132/5280 保持存活且未重启；未操作8001/5282测试服务、其他组测试进程或 QA-eval/每日评测。

后续隔离 UI 又发现 MyPages 背景列表仍硬编码订阅“未启用”，与已正确读取的抽屉不一致。仅修 `frontend/src/pages/qa/MyPages.vue` 两个单元格：订阅读取实际 `sub.on/channel/receivers`；刷新显示实际频率，只有非手动且 `scheduleActive=false` 时标注“未排定”。没有修改后台状态、权限或旧计划。新增 `node scripts/check-qa-page-list-schedule.mjs` 直接编译渲染这两段实际表格模板，已通过启用、关闭、无记录、默认拥有者、未排定和手动六种代表状态；未重新运行后台合集或重启服务。实际页面保存/恢复结果以主会话后续 UI 记录为准。

主会话隔离 UI 随后已完成该真实页面保存每日08:00、本人站内信，重新读取后抽屉及列表均恢复正确频率和收件人。另补列表旧配置边界：真实模式仅 `subscriptionActive=true` 且渠道为已支持的站内信时显示启用人数；旧邮件/企业微信设置即使 `sub.on=true` 也标记“未启用”，不修改旧记录。列表模板脚本新增 legacy email/on 但 runtime inactive 代表断言，通过后仍无需后端重测/重启。

## 最终只读 UI 与临时服务关闭

ROOT 最终前端 build（session16089）exit 0，包含 vue-tsc，4490 modules，Vite16.34s，覆盖最后列表启用状态修正。主环境动作中心实际加载22条记录、每页10条，无500或console错误；旧 ACT-20261025-0550 的引用对象仍无法核验，界面保留保护提示，没有绕过权限或补造旧参数。此次实际 viewport 为1280×720，无横向溢出，不代表1920/2560已验。

主规则页实际加载“规则与自动化”、R-007 条件及命中历史，无红提示、console错误或500；只读，没有修改规则或提交动作。结束时 C 按授权关闭隔离测试 API52188/session11333，ROOT关闭临时前端23368，实际8001/5282无监听。隔离库验收记录保留、未重新seed；主8000/API16820、5280/前端29132及四个worker继续运行。此段仅记录上述实际页面与关闭事实，不新增后台测试或外部执行验收。
