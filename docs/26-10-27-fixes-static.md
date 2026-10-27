# 构建/static 定点修复（2026-10-27）

基于 `8d15cb9`，保留共享工作区其他组修改。本轮不改主库业务、seed/mock值或原空间2 CR0001，不调用模型/外部源，不提交。后端写入验证仅使用本机独立库 `rengu_fix_static_20261027`，由共用受限runner设置数据库与关闭外部执行；没有迁移往返或全量pytest。

## 产品修复

### 新建草稿误读已发布详情

关系页在异步CR头尚未返回时，只依赖选中ID发起 published detail 请求，尚无已发布定义的新关系会误报404；CR头到达后旧watch又没有重跑依赖。现先等草稿身份及CR头，再判断 `isNew`；加载态/错误/目标身份进入请求失效条件。对象、指标、维度、知识的相同详情加载链同步收紧，新目标只恢复已保存CR项，不借已发布基线。DefinitionEditor把 `newTarget` 的迟到变化纳入重载条件。

`check-static-pages.mjs` 使用实际五个Body脚本覆盖延迟CR头→新目标→草稿恢复，确认不请求不存在的 published detail；保留分页不换选中项、未保存输入、完整属性基线、空本体无演示回退等原断言。

ROOT已在主界面**只读打开原空间2 CR0001/L0001**，恢复未命名草稿，无“关系类型不存在”红错误，console errors=[]；未新建、保存或修改该CR，结束恢复空间1并关闭临时页。截图：`frontend/shots/fixes-20261027/relation-draft-restored.png`。

### 单对象指标复用获准只读来源

`metric_trial.py` 的 readiness 与执行编译复用同一显式工作空间/对象来源绑定，只解析该指标实际引用的对象。现支持管理员已配置的 `object_source` 白名单、真实源列/类型与公开属性，而不要求给新领域写零售 `backing_column`。复用对象Bound的公开主键/字段保护与专用只读连接：非super/nonBYPASS、只读事务、可信ontology GUC、源RLS、语句超时，再叠加相同 `apply_row_filter`。

旧内置受治理表继续走 `run_guarded`，没有改公共权限。显式绑定但URL/映射缺失时拒绝，不能回退同名零售表；无关对象的坏主键不会拖累本指标。历史映射不足、跨对象聚合/过滤仍明确拒绝，不猜税率、不替换字段别名、不省略过滤，也不修改主库15个指标。

当前MetricFlow语义执行器仍针对本地受治理连接。专用来源指标可试算，但导出列入 `skipped / SEMANTIC_SOURCE_UNSUPPORTED`，避免生成误指向本地同名表的SQL；不声称外源语义层已完成。

### 动作策略友好编辑

与runtime组确认合同后，使用其动作详情的 `policyStored` 原始值和 `policyStatus`：概览明确未配置/未记录/旧非法状态，未取得策略基线时编辑禁用。仅用户显式编辑并确认才产生 `policy` 段、单行 key=`policy`、after为策略dict；仍需保存CR、审核、发布。非策略变更不生成policy项，原值绕过通用clean保留null/未知键；后端保留原策略由runtime组负责。

`ActionPolicyEditor.vue` 提供suggest/confirm/auto、调用方、每日/每调用方限额、金额参数与上限、对象编号及超限stage控件，和折叠JSON双向互通。显式配置从confirm开始，auto必须填写每日限额；不默认开启auto、不制造评测通过时间。原非法未知键不静默丢弃；若要替换，必须由用户明确修改JSON并通过严格校验。取消仅放弃本地表单，没有实际动作执行/策略发布。

## 旧测试契约与夹具修复

- `test_metric_versions` 授予实际角色后补齐既有workspace成员映射；旧失败来自viewer/未映射夹具，不放开生产鉴权。
- 新版本definition已包含 `execDef`，旧测试只断言 `FIELDS` 已过期；更新为同时断言执行元数据保留。
- 静态Body检查补workflow加载态、crErr及新指标元数据桩；对象行为检查加载实际graphEdges纯模块；范围提问桩改用当前qaReal；对象元数据检查不再要求使用区请求构建 `/properties`，并保留当前 `/meta` 的403/404/网络错误拒绝断言。
- 旧 `baseline-f3b8119.json`/JUnit 仅作根因索引，不把其336 failed/87 error等旧数当本轮结果；抽样指标版本失败对应上述真实成员夹具和execDef契约漂移。未删除或重写旧成果。

## 准确验证记录

命令工作目录 `backend`，每轮均通过受限runner，非默认conftest数据库：

```powershell
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_fix_static_20261027 -- -p tests.use_objects_support tests/test_static_metric_source.py tests/test_static_trials.py tests/test_metric_variants.py tests/test_metric_versions.py --tb=short --show-capture=no -q --maxfail=4
```

首轮 **23 passed / 3 failed / 1 error，38.17s**：指标版本夹具未刷新成员映射、新增源测试表重复创建。真实源第一个聚合用例已通过；修准备而非放宽产品。

```powershell
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_fix_static_20261027 -- -p tests.use_objects_support tests/test_static_metric_source.py tests/test_static_trials.py tests/test_metric_variants.py tests/test_metric_versions.py tests/test_static_build_writes.py tests/test_static_cr_write.py --tb=short --show-capture=no -q --maxfail=5
```

第二轮 **63 passed / 1 failed，82.17s**：唯一失败为旧execDef断言，按真实版本结构修正。

```powershell
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_fix_static_20261027 -- -p tests.use_objects_support tests/test_static_metric_source.py tests/test_metric_versions.py tests/test_metricflow_engine.py --tb=short --show-capture=no -q --maxfail=4
```

最终定点 **28 passed / 2 skipped，34.58s**；skip仅可选MetricFlow依赖未安装，不算通过。各轮均有既有event_loop_policy弃用warning。计数有重叠，不能相加当唯一case总数，也未声称一次全量全绿。

真实PG新源回归覆盖：本地私有源表/RLS与临时非高权只读账号，25条本体1实例及另一本体数据；同对象even过滤实际汇总 **172.25**，对象ACL撤销返回空，super连接拒绝；隐敏字段、缺映射、跨对象、显式不完整绑定不借零售、无关源主键受限不影响本指标；专用来源语义导出准确skipped。结束后SELECT核查临时源schema=0、临时源角色=0，无生产外源实测。

前端以下脚本均有exit0记录（工作目录frontend）：

```text
node scripts/check-static-pages.mjs
node scripts/check-definition-forms.mjs
node scripts/check-static-draft-hydration.mjs
node scripts/check-static-loading.mjs
node scripts/check-overview-integration.mjs
node scripts/check-object-usage-pages.mjs
node scripts/check-object-behavior.mjs
node scripts/check-object-automation.mjs
node scripts/check-object-ask.mjs
node scripts/check-real-object-resolver.mjs
node scripts/check-action-policy-editor.mjs
```

范围提问/行为/解析器三个检查首轮分别因旧qa模块桩、缺graphEdges依赖、旧构建属性分页断言失败；修为当前合同后全部通过。新策略SFC/VM用例覆盖原值不归一化、安全confirm、auto显式每日限额、金额0上限、null可选字段及JSON/控件互通。统一 `npm run typecheck` exit0；Python限定Ruff与diff空白检查通过。ROOT后续统一build与隔离浏览器，不重复已通过28项/typecheck。

实现阶段另有两处被及时捕获的本轮问题，不混入旧契约分类：扩展维度门禁时误用了该页不存在的workflow，被实际Body脚本报ReferenceError，已改为该页实际草稿/CR依赖；新策略模板事件有7个隐式any，已补显式类型后统一typecheck通过。

## 文档与仍保留的边界

`docs/user-guide.md` 最小同步已存在时间字段/默认过滤、7种API-key scope及函数白名单（null兼容未另限、[]全拒绝，已有名单只能收窄）、新关系草稿修复。旧截图保留当时真实含义，不冒称新UI截图。`database/exports/README.md` 明确notify_0022快照不是当前schema，恢复到新库后按所选项目版本安装依赖、`alembic upgrade head`、核对current再启动；未改SQL/哈希/导出时间。

未新增static0031日期表：内置编译器目前仅simple/同模型derived，静态导出及查询合同没有可完整运行的累计/补零协议；可选MF未安装，本轮不安装。仅建spine表不能打通功能，不列假完成。需要另行实现累计定义/请求合同，或受控启用MF后专门验受控spine执行。跨对象指标语义、主库15项业务定义、属性级权限/分支/草稿物理分离、真实预测仍未被本轮改成可用能力。


## 补充：四项旧前端检查脚本收口

仅执行以下四个既有入口（cwd `frontend`），未修改产品源码或B组PageDrawer/动作评审脚本：

```text
node scripts/check-context-integration.mjs
node scripts/check-qa-integration.mjs
node scripts/check-qa-session-context.mjs
node scripts/check-real-data.mjs
```

首轮四项均未通过：context缺当前页面已导入的HTTP/static/qa依赖桩；QA integration仍等待已移除的双请求分面路径而产生未完成top-level await；session只提供旧同步askTurn桩，未提供当前SSE入口；real-data缺currentApiContext及Releases当前版本读取依赖。修正桩及断言后第二轮四入口全部exit0（日志 `backend/testlogs/fix-check-*-round2.log`）。session追加当前/迟到流事件隔离、普通流错误不产生重复同步提问断言后再次单项exit0。没有移除权限安全检查：真实搜索VALIDATION_FAILED/UNAUTHORIZED/FORBIDDEN/NETWORK_ERROR均拒绝、不回退样例；页码/size/关键词/过滤及同请求分面一致性仍被检查；旧身份元数据、会话/创建/抽屉迟到响应、角色元数据失败禁写、已发布scope拒绝/缺失/失败均fail closed。MyPages桩按当前qaWriteReal读写模式更新，关闭模式仍验证清除真实缓存。

`check-real-data`既有入口会自行导入`check-object-usage-pages.mjs`，该传递检查也exit0；本次没有单独重跑此前11项绿色入口，没有后台测试/数据库写入。ROOT后续统一build。

旧f3b8119基线只读对照：原JUnit中本组相关失败记录包括metric_versions 15、static_cr_write 1、static_trials 1，这三个文件已在本报告记录的本轮定点命令中复验（最终metric_versions所在28pass/2skip轮、其余在63pass/1fail轮对应通过；该轮唯一失败为metric_versions旧execDef断言，随后修正并定点通过）。原static 2、static_publish 1、static_writeback 3、object_cr_history 1、object_source_regression 2、qa_explore 5、qa_objects 1、qa_objects_db_only 1，本收口未重跑；不能称其仍为当前失败。当前这些轮次未生成新JUnit，不能冒称逐nodeid新旧JUnit一一核销或全量423失败已解决。
