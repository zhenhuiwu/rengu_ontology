# 使用区运行：告警与动作针对测试（2026-10-27）

基线 `9d31149`。本轮用户已明确授权完整针对测试，替代上一轮仅静态检查的限制。这里只测试动态告警/动作及其执行、权限、分页边界，不包含 QA、对象探索、ERP/Kafka 或预测模型。

## 环境与约束

- 只创建、迁移、写入 `rengu_test_use_ops_20261027`。开发库 `rengu_ontology` 仅用于启动器读取数据库目录；`auto_ontology` 未访问。
- 从 ignored `.env` 内存读取本机 5433 数据库地址，凭据不写入文档或命令。共用 `backend/scripts/run_use_tests.py` 验证源库名、本机地址、固定测试库名，并验证当前登录者已经直接属于 `rengu_ontology_scoped`，才允许既有迁移执行幂等授权。未增加集群角色或主库授权。
- 仅本轮加 `-p tests.use_ops_support`：真实数据库 session 绑定本体 1，按迁移同式装配测试用户的真实成员/角色绑定。不替换身份、权限、RLS 或公共 conftest。实际被测 HTTP 使用 ASGITransport；普通同步/异步 HTTP transport 一律 fail-fast，仅显式 MockTransport/测试适配器可用。
- 没有启动生产 worker、调用外部提供者、修改开发库业务记录或权限。独立测试库中的人工夹具与受控网关响应不代表生产执行结果。

## 最终结果

最终完整目标集 **68 passed，0 failed，0 error，0 skipped，0 xfail**，耗时 **277.29 秒**。只有既有 `pytest_asyncio` 的 `event_loop_policy` fixture 覆写弃用警告。结果文件：[JUnit](use-tests-ops-results.xml)。没有把重叠小组的通过数相加。

共 12 个文件：告警 14、动作 12、认证 3、未配置 2、分页性能 2、完整映射 1、动作 checker 6、动作目录 4、对账 9、upsert 1、新运行链 11、动作编号/日志并发 3。`test_integration_writeback_calls.py` 仅同步 fixture grant 入口，未纳入这 68 项，不能称该文件整组通过。

新运行链验证真实 RLS/行权限的列表、计数、直接详情和目标分页；稳定用户编号真正保存与同名/停用/只读/无成员/实例范围拒绝；批量关闭跳过在途且 missing-ID 整体回滚；预检不创建 run/log/event/call 或业务写入；手动 UUID 重放、冲突、伪来源拒绝；真实本地确认修改 Store.is_test、仅生成一次完成事件、重复确认 409；跨对象参数/摘要/日志隐藏和写入拒绝；第二本体空列表/候选、显式错误路由不回退；queued/claimed 的真实阶段与人工对账 409。nullable 外部系统关联的执行定义在真实 PG 锁查询中通过，保留 `FOR UPDATE OF ActionType`，不将外连接可空侧一起锁定。

## 已确认的产品修复

1. **共享审计 upsert 错误**：`PostgresAdapter.upsert` 仅凭存在 `ontology_id` 列推断领域表，错误为共享 `AuditLog` 生成没有唯一约束的复合冲突键。修复与 Base 使用相同的领域模块边界；追溯列不改变共享唯一键。已追踪 seed loader 两条真实调用。新增真实 PG 回归验证共享审计跨本体按全局 code 幂等、不隐式注入本体，以及 scoped ExtSystem 同 code 分属两个本体。
2. **列表权限核验 N+1**：告警维度和动作引用逐条查库，随页面记录增长。改为请求内收集当前页引用，按对象类型、每批最多 1000 个调用原 `scoped_instances`；动作定义及子定义每页批读，绑定/操作集合只在当前请求缓存。每条记录仍核结构、版本、声明、引用格式和行权限；不能证明可见就隐藏参数、摘要、日志及原因。没有全局权限缓存或绕开 RLS。
3. **执行通道错误分类**：实际未配置外部提交适配器曾返回对账不可用码。`submit_adapter` 最小转换为 `ACTION_EXECUTOR_UNAVAILABLE` / 503，仍不保存成功结果、不调用外部系统。
4. **整数规则版本写入文本列**：告警旧版本缺失时回退到 `Rule.version` 整数，`create_run` 写 varchar 触发真实 PG 500。唯一创建入口仅把整数转字符串；既有 `v3` 等文本不变。实际告警发起、审批、免审和失败回滚回归通过。
5. **前端预检错误丢失**：动作表单原来只有瞬时泛化 toast。现在保留后端字段/条件具体错误为 inline alert，输入或上下文改变清除；暂存仍自行重验，不强制先预检。
6. **本地免审动作的错误外部文案**：确认卡不再承诺先审批、写回 null 或固定 15 分钟对账；按对应版本免审配置显示准确提示，可空外部系统显示“—”。新成功日志只有实际外部系统和单号才带外部回执，否则为“动作执行成功”；历史日志不改。本地真实 PG 回归同时核业务布尔值、单一事件及日志没有 None/外部单号。

### 性能证据

受控同一身份、同一数据集分别取 5/13 条：告警含维度 SELECT 修前 **27/35**，修后 **23/23**；结构化动作修后 **42/42**。另一个不含维度的列表预算为 **22/22**，固定授权查询 7 次。断言核页面增长不超过固定余量、动作元数据子表每次请求各至多一次，以及实际 LIMIT/OFFSET，没有单纯放宽旧阈值。一次采样告警冷/热耗时 875.2/68.5ms，动作 204.3/96.7ms；这只是本机采样，不作为通用延迟承诺。

核心 agent 限定只读复核批量实现：仍复用当前本体、受控映射、真实行谓词、业务编号唯一性，未知引用拒绝；执行侧锁和 queued/claimed 拒绝不变。该结论是源码复核，实际测试结果另由上述 68 项提供。

## 失败分类与复测过程

保留失败记录，不靠剔除旧失败得到绿色结果：

- 初始 3 项均在 setup 因共享 AuditLog upsert 报 `InvalidColumnReferenceError`；修复后选 4 项为 **3 passed / 1 failed**，剩余是旧测试无参数调用现需 session/user 的 `get_submitter`。改为显式核 UnconfiguredAdapters 拒绝和默认真实 DatabaseActionSubmitter。
- 早期广集被中止，没有完整计数，不能算通过。单独复现旧告警详情 false：夹具先以空角色建立 viewer，之后补角色却未同步成员。只在实际授角色后按迁移逻辑装配成员，不改产品 auth。
- 完整旧 9 文件诊断轮 **25 passed / 28 failed，600.78s**。失败涉及旧自然语言摘要/伪来源可执行、固定 seed 总数、旧目录签名、没有真实成员/角色绑定、无定义/系统/actor 的回调夹具、固定旧认证查询数。按当前契约重建等效用例；动作 12 个原用例仍保留，分支结果使用显式测试适配器，另有真实默认本地执行正向覆盖。没有放宽 auth 或用跳过替代。
- 新链首次 **10 passed，12.94s**；增加页增长检查后实测告警 27→35，确认为 N+1 并修复。不是仅更新性能断言。
- 后续快组 **20 passed / 1 failed**，定位未配置通道错误码并修复；其后运行链/动作/目录/checker **33 passed，27.95s**。
- 性能/对账组 **11 passed / 1 fixture error**，原因是告警文件导入 alert_runtime 未一并导入其 runtime fixture；补齐所属测试导入。实际告警正向随后暴露整数版本 500，修复产品代码。
- 本地日志、告警发起三项、编号三项最后小组 **7 passed，7.64s**；再执行一次完整目标集得到 **68 passed，277.29s**。

技术编号回归改为在唯一 ops 测试库内建立各自工作空间，覆盖回滚编号不复用、连接池占满可分号、父记录锁保护纯日志并发；不再创建任意随机数据库。真实独立事务保留该测试库内的编号/日志证据，不声称全部记录均回滚。技术计数器清理明确限定唯一测试库及本体 1，没有 DROP/TRUNCATE 既有基线。

## 命令记录

均在 `backend` 运行，未在命令中写凭据：

```powershell
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_test_use_ops_20261027 -- -p tests.use_ops_support tests/test_dynamic_alerts.py::test_list_filters tests/test_dynamic_unconfigured.py tests/test_complete_action_mappings.py --tb=short -q
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_test_use_ops_20261027 -- -p tests.use_ops_support tests/test_use_ops_upsert.py tests/test_dynamic_alerts.py::test_list_filters tests/test_dynamic_unconfigured.py tests/test_complete_action_mappings.py --tb=line --show-capture=no -q
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_test_use_ops_20261027 -- -p tests.use_ops_support tests/test_use_ops_runtime.py -x --tb=short --show-capture=no -q
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_test_use_ops_20261027 -- -p tests.use_ops_support tests/test_dynamic_alerts.py::test_detail -x --tb=short --show-capture=no -q
```

最终完整命令：

```powershell
.venv/Scripts/python.exe scripts/run_use_tests.py --db rengu_test_use_ops_20261027 -- -p tests.use_ops_support tests/test_dynamic_alerts.py tests/test_dynamic_actions.py tests/test_dynamic_auth.py tests/test_dynamic_unconfigured.py tests/test_dynamic_pagination_perf.py tests/test_complete_action_mappings.py tests/test_action_checker.py tests/test_dynamic_catalog_db.py tests/test_integration_reconcile.py tests/test_use_ops_upsert.py tests/test_use_ops_runtime.py tests/test_dynamic_action_numbers.py --tb=short --show-capture=no -q --junitxml=../docs/use-tests-ops-results.xml
```

轻量检查：所属 16 个 Python 文件 `ruff check` 通过（postgres、row_scope、run_visibility、service、ops plugin/新回归、改动的动作/告警/目录/认证/checker/对账/性能/编号测试及 prepare helper）；前端 `npm.cmd run typecheck` 通过；所属 diff 空白检查通过。没有宣称全仓 Ruff。integration/execution.py 的通道错误转换经目标测试和 AST 检查，未以全文件历史风格问题冒充本轮产品失败。

## UI 证据与执行限制

主会话在开发服务只读查看到：动作执行记录 96 条/10 页，默认首页 10 条，翻第 2 页首条变为 ACT0531；未知与对账 26 条/3 页、每页 10 条，ACT0544 显示旧参数不可用且禁止发起对账，页签没有数字。告警 AL0143 触发/条件和稳定编号转派候选已打开取消。上述 UI 证据不代表真实转派、批量关闭、生产暂存/确认、外部执行或对账验证。

主任务另明确授权在 **QA 隔离测试库 `rengu_test_use_qa_20261027`** 走实际浏览器本地闭环。`scripts/qa_test_ops_prepare.py` 由 ops 提供、QA agent 执行，仅准备 `ops_local` / `OPS-UI-LOCAL` / 现有 owner 的精确本地动作权限；断言该唯一 QA 库、不重 seed、不重置既有记录。测试中间件只放行该本地动作的特定路由、目标和身份，其余动作写入拒绝。测试服务 8001/5282 没有 worker、外部绑定或外发。

1. `params={}` 预检 422，表单持续显示“测试标记：缺少必填参数”；改为 `enabled=true` 后错误立即消失，重预检 200 并取消，没有额外 stage。
2. 第一条 `ACT-20261025-0001`：`enabled=true`，stage 201、confirm 200，UI 成功/“本体内部动作已完成”。确认卡准确显示免审、外部系统“—”。QA agent 只读核数据库：目标 true、恰 1 条 succeeded/INTERNAL Run、1 条 ActionExecuted、0 条 WritebackCall，无审批人或外部单号。旧日志中的 None 原样保留。
3. 加载冻结修复后，第二条 `ACT-20261025-0002`：`enabled=false`，stage/confirm 成功，UI 新日志“动作执行成功”，没有 None 或外部号。只读核验最终目标 false、恰 2 条成功 Run（true/false），每条各 1 个 ActionExecuted、总计 2，WritebackCall 仍为 0。没有修改第一条历史日志。
4. 主任务结束测试后 QA agent 已关闭专用 API/前端，确认 8001/5282 无监听；测试记录保留，未清库，未关闭主服务。

这些结果证明本地受控布尔赋值和事件持久化，不代表事件订阅投递、真实外部执行/对账、分布式并发或生产动作通过。自动化重放/锁测试与两次不同参数 UI 确认分开记载。未运行全仓、独立迁移恢复、生产队列或真实外部系统测试。ERP/Kafka、预测与模型提分继续后置。
