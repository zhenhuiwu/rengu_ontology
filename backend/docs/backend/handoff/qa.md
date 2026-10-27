# 问数（qa）交接

- 10-26 QA性能/真分页收尾：W1/T3/T9/T12/T13/T18均保留SQL count+LIMIT/OFFSET；前端直接请求page/size/filter并接收后端收拢页码。修复列表加载先清total=0导致公共分页自动跳回第一页；Cert/Feedback/Freq不再串行等待额外T1统计，评测仅加载当前可见列表，页签/视图切换标签不展示统计数字，分页total保留。列表指标目录仅读轻量字段且跳过维度图（独立库实测5条SQL降至2条），T2维度层级直接投影查询，不加载对象/属性关系。共享会话详情GET可取消，身份/版本/预览及选中会话改变后立即隐藏旧详情，迟到详情/提问/创建/范围PATCH不得覆写当前会话或恢复旧输入；不自动新建，不调用模型。侧栏按实际可用高度/行高自适应1..200条，simple分页与底部入口保留。验证见test_qa_pagination_cost.py、check-qa-session-context.mjs、check-qa-session-layout.mjs、check-qa-eval-real.mjs。


- 正式清单F持久评测：`qa_0006`增加内部queue_job_id、execution_context及失败状态/活跃任务唯一约束。T11同请求事务经Procrastinate版本化SQL函数原子入队，202真实待运行；只接受拥有的sessionId，冻结group/scope/caliber，worker重新解析用户功能权限，保留原有SQL守卫。scope是提示上下文，本组未把自由文本当作SQL权限谓词，也未补全全局row_filter。无模型Key/队列schema明确503，全局活跃任务409。独立入口`python -m app.modules.qa.eval.worker`（先`--install-schema`），完整ORM注册、Windows Selector loop、仅QA业务队列/无其他空桩。中断/关闭恢复为失败，保留已提交进度与失败行；queued不误判，旧终态交付noop，不自动重跑。
- 权限申请W16：history/preview一律403 READONLY，owned来源可为OUT_OF_SCOPE；无真实审批路由时approver=null，保留待审批记录等待分配。scope/object/reason/sourceTurnId拒NUL及非法Unicode，空白必填项422。W10/W11仅读已保存answer.table，旧store卡无表不再从QA_EC_STORES拼行，422 TURN_NO_RESULT；空表保留真实表头。
- 静态回滚引用公开查询`app.contracts.qa.has_metric_references(session, code)`：7表metric_code与sem_plan/expect.metric精确匹配（含旧code别名、停用/历史行），不猜自然语言/SQL。QA variant_index(session,ver=None)现转发static.explorer.metric_variant_index，来源/目标同版已发布且显式variants，历史缺字段为空。

- 旧对象 O2–O7/O9 真实化收尾：业务列表只读 biz 表，不再在响应时追加 OBJ_EXTRA_STORES；已落库的 95 家种子门店继续保留，sales 无真实指标来源为 null/导出空单元格。去掉随机销售、常售商品、假区域对象、伪历史和推测支付/取消时间。O5门店只保留当前可见订单的可验证汇总（caliber=null、analyticsConfigured=false，明确未绑定指标）；O7仅订单创建/出库已有时间，其他历史空且configured=false。region未绑定返回503 DATA_NOT_CONFIGURED，O9无真实发起接线返回[]。保留原行级SQL过滤，存在但范围外仍403，不存在404。
- O4 scoped-ask 仅保留明确的基表汇总：订单问题「合计/实付金额合计/订单汇总」、商品问题「商品汇总/平均零售价」；去重已选ID、空ID集合不回落为全量。其他销售比较、历史、排名、预测/范围问答返回503 DATA_NOT_CONFIGURED，不生成答案或零结果，不新增LLM/指标引擎。ObjAskModal持续显示该错误、保留输入，并阻止身份/预览切换和关闭后的迟到答案。
- 验证：独立测试库 test_qa_objects.py + test_qa_objects_db_only.py + test_qa_explore.py 共37 passed；前端check-object-ask.mjs及typecheck通过。未删开发库数据、未改mock值。

- T2/catalog 真实目录：当前指标名称/版本/CR来自 `metric` 与真实未发布且非驳回 `change_request`；维度来自已发布 `dimension` 层级及指标 `dims`。`?ver` 仅取该发布快照的 metrics/dimensions 节，未知版本404、缺节为空，不借当前。保留9个旧plan键及6个code别名，T2增量返回code供真实指标抽屉；新指标（如M01）无需迁移/改种子。非vN版本保持文本显示，原整型metric_version为空。T3/T4/T10/T12–T14/T18解释使用同请求目录，新建/认证绑定真实code/版本；缺指标读时标不可用、认证422。
- T11判分每次运行加载当前DB目录一次，缺指标直接记“期望指标不可用”失败且不调用LLM，不从旧catalog补答案。仍是列名/维度启发式，**不是金标结果比对**，不能验证时间范围/数值正确性；现有SQL金标题库离线工具另行使用，不在此组扩建判分器。`metric.def_rows`原种子模板不被推定为可靠SQL事实。
- W17的scopes/calibers沿用真实qa_session_option记录，metric从真实复购指标读取，缺记录为空；前端qaScope真实模式初始/失败为空、静态失败不pick残留DIM_ITEMS。调优页所有指标引用/确认/计划显示走请求上下文T2目录，未知键不崩、不回演示名版本；真实CR标签没有已加载详情时只显示编号，不伪称草稿或用mock详情。固定时间快捷词/状态枚举/格式函数是输入与显示规则，保留。

## 2026-10-26 本地开发补充

- 评测元数据真实化：T1版本和T11运行版本标签读取数据库发布记录，无发布记录为空；无统一业务水位，T1 `dataAsOf` 为空、页面显示未知。T12分组/题数取实际题库聚合，T11只接受实际有题的允许分组，前端运行确认题数来自API，不再取固定EVAL_QS。去除固定CR回放提示和预计3分钟文案；选项加载失败不显示默认演示指标，评测列表/详情加上下文与请求序号防迟到覆盖。T2指标目录/语义计划解释已按下述目录改为DB读取。

- 评测启动恢复已实现（`qa_0005`）：内部 `worker_id` 和 lifespan 进程会话锁保护已提交但尚未后台调度的任务；每次执行再持有运行锁，兼容旧的无归属记录。孤儿运行保留已提交进度并标记中断，活 worker 的任务跳过；正常关闭取消任务、释放进程锁后收尾。每个 API worker 固定占用 1 条主库连接，部署须运行 lifespan。中断任务仍需手动重跑，每日调度和持久队列未接。
- 会话、页面、反馈、申请、认证问答编号已加事务锁；轮次和卡片使用会话/页面局部锁，避免并发生成重复编号。
- 分面只在后台不可达时回退，业务错误保留；搜索/导出拒绝 NUL，排序只接受一个倒序前缀。
- 我的页面末页删除后收拢页码，并防止旧列表请求覆盖新筛选。当前进展和验证结果见仓库 `docs/development-progress.md`。

> 分支 `qa-joint`（本地，未推送）。基线 = merge `80ac80c`（qa-reuse 最终 `740a554` + pde-integ `215819d`，两条链都是原提交，哈希一致）；整段 = `b688d5d..qa-joint`。截至 2026-10-26 01:25（Asia/Shanghai）。
> **qa-tuning 增量**（分支 `qa-tuning`，基线 master `bb47a5c`，本地未推送，截至 2026-10-26 Asia/Shanghai）：R5 问数调优评测题库 / 反馈与纠错页签接真后端，见 1.3；T15 新建变更单分支等 static-r3。
> 接口契约：`docs/backend/qa-api.md`（各节「联调实现」段）；评测：`docs/backend/qa-eval.md`。[推断] 标的是前端/文档没写、这里自己定的规则。
> 前端开关：`VITE_API_REAL_MODULES` 含 `qa`（动作卡还需 `dynamic`）才走真接口，否则全部 mock。没接完的页面 / 页签在真接口模式下也仍走 mock，测试和构建不受影响。

## 0a. qa-pages 分支：通用对象浏览 + 工作台范围口径（2026-10-26，本地未推送，下一轮用）
> 基于 master `770f6d7`；qa 身份提交。设计 `backend/docs/backend/generic-object-explorer.md`，接口 `qa-api.md` §5.1（E1–E3、O4 `query`）。
> 页面：`/use/objects/search?type=&kw=&f=&sort=&page=`、`/use/objects/detail?type=&id=&tab=` 整页改成元数据驱动（objMeta.ts / objRows.ts），工作台范围口径改成 qaScope.ts。

**未完成 / 待别人**
| 项 | 状态 | 说明 |
|---|---|---|
| 静态 meta / facets 带关键词 | 过渡 | 已接真 `/object-types/{id}/meta`（+`/properties` 合并）和 `/facets`（mock 只在后台不可达时读兜底，写不兜底）；静态 `/facets` 不收 kw，带关键词时用后端 E1 顺带的过渡 `facets`（`explore_api._facets`、`withFacets`）[推断]，静态支持 kw 后删 |
| 属性标志 | 已接静态 | E1 `flags()` 读静态 property 列（searchable / sortable、filterable 用 `static.explorer.facetable`），与 meta / facets 同一套；静态 seed 里区间属性（store.opened_at、order.created_at）没开 filterable，所以页面上没有这两项筛选，要的话在静态 `explorer_meta.json` 打开；前端 `metaFromProps` 默认规则只在后台不可达的 mock 兜底用 |
| 指标口径 | 已接静态 | 口径 = 对象 meta.metrics[].variants[].label 并集（静态按（不含税）/（含税）后缀推断）；真 meta 没有变体时用 W17 选项，后台不可达用 mock |
| 行级范围 `GET /api/admin/v1/me/row-scopes` | 等本体小白 | 未上 master；返回形状按 `[{dimension?, values[]}]` 或 `{items}` 宽松解析 [推断]；拿不到时默认取预览范围或第一个选项 |
| 后端 SessionCaliber | 已改 | `qa_session.caliber` 改自由文本 varchar(32) 可空（迁移 `qa_0004`，down_revision `static_0003`：去 CHECK、ex→不含税 / in→含税）；W2/W4 strip、空 → null（W4 不传 = 不改），静态指标有口径变体时只收变体标签（不含税 / 含税 总是收），没有变体时不校验；W6 把会话口径映射成指标变体（点名每个指标用哪个 api）进提示词“会话口径”段，没对上变体的 不含税 / 含税 按旧的金额口径一句话提示 [推断]；评测（T11 / eval run）不带会话口径 |
| 会话 scope 值 | [推断] | 选项值是维度取值名（如“华东大区”），旧会话的“华东”按同义词归一显示，用户再改时 PATCH 成维度取值名；后台链路按自由文本用 |
| O4 / mock answerFor | 部分 | 用一批对象提问只支持 store / product / order；其他类型的提问按钮仍可点，回答走兜底 |
| 动作执行 | 未接 | 对象详情真接口模式下「发起」置灰“动作执行接口还没接入”，不假装成功；演示模式明确写“演示数据” |
| mock 绑定 | 部分 | `objRows.ts MOCK_BINDINGS` 只有门店 / 商品 / 订单；门店 region_code 在 mock 未绑定（显示 --） |
| 工作台其他演示文案 | 未改 | 回答卡含税切换、模板弹窗默认范围、右栏动作卡仍是演示数据文案（不在本轮范围） |

## 0. qa-cardfb 分支（2026-10-26，本地未推送）
> 分支 `qa-cardfb`，基于 master `3ece2fa`；提交 `3ece2fa..qa-cardfb`（qa 身份）。无新迁移。本节覆盖下面第 1–4 节中 R2 / 回答卡回退几行。对象搜索 / 详情的通用化在 `qa-pages` 分支，下一轮提交。

**已完成**
- 回答卡回退（`e4b02ef`）：口径重算 / 门店表翻页在后台没起来时回退 mock（`src/pages/qa/qaFallback.ts`、`QaAnswer.vue`、`QaStoreAnswer.vue`）。
- 写操作不回退（PDE 阻塞修复）：赞踩、导出、我的页面新建 / 重命名 / 删除 / 刷新、卡片调整 / 刷新订阅 / 共享、固定到页面在开关 qa 为真时一律调真接口，失败（含后台未启动）只报错，不改演示数据、不假装成功，也不走 `fallBackIfDown`（`qaWriteReal` / `qaWriteFail`）。开关为假（纯演示）时照旧走 mock。
- R2 我的页面（`4f8df2f` 后端、`b3fb3f9` 前端）：后端 P2、P4–P10（`pages_api.py`，含 P7 固定到页面），前端 `src/api/qaPages.ts` + `MyPages.vue`、`pages/PageDrawer.vue`、`components/QaPinModal.vue`（Workbench / QaConversation 传 `turnId`）。实现细节和 [推断] 见 `qa-api.md` §4「联调实现（qa-pages）」。
- 测试：`tests/test_qa_pages_write.py`。

**未完成**
- P10 共享成员：前端候选人大多不在 `sys_user` → 422 `USER_NOT_FOUND`；要 admin 在 `seeds/extra/system/SYS_USERS.json` 补人。
- P3 卡片 `denied` 前端没有分支（`CardPreview.vue`）。

**代码在哪**：后端 `backend/app/modules/qa/pages_api.py`（`router.py` 末尾一处 include；`repo.py PageRepo` 列表排序一处）；前端 `src/api/qaPages.ts`、`src/pages/qa/MyPages.vue`、`src/pages/qa/pages/*`。

- 固定到页面（Palantir 评审修复）：卡片标题按 P7 上限截到 60 字（`pinTitle`，超长末位换「…」）；「新建页面」路径 P7 失败时删掉刚建的空页面（避免空页和重试 409 同名）；新建不再写死范围「华东大区」，交给后台默认 [推断]；P7 全空白标题返回 409 `EMPTY_INPUT`（同 P2 / P4）；`RefreshResult` 去掉 P6 不返回的 `id`。
- 下一轮：P2 / 共享的 id 锁定（并发新建取号）；`MyPages` 删除后计数和回到上一页（删掉最后一页唯一一条时）。

**下一步**：admin 补用户种子；`CardPreview` 补 denied 分支；上面两条下一轮项。

## 1. 已完成

### 1.1 页面联调（真后端 + 真 qwen-flash 端到端验过，Playwright 脚本见第 3 节）
- **R1 问数工作台 `/use/qa/workbench`（除 SSE 外）**：W1 会话列表（搜索 / 时间筛选 / 服务端分页）、W2 新建、W3 切会话、W4 重命名 / 改范围口径（追加系统轮次）、W5 删除、W6 提问（同步 202 结果）、W9 口径重算、W10 门店表 / 链路表格翻页、W11 导出 xlsx/csv、W12 赞踩（踩 → 生成反馈）、W13 常用问题、W15 移出常用、W16 申请权限（刷新保留「已申请 · 待审批」）、P1 我的页面抽屉（只读列表）、X1/X2/X2b 建议动作确认卡（动态 action-runs stage / confirm / withdraw）。
- **反问卡 / 拒答（qa-reuse a35d3b6 起的链路，`QaAnswer.vue` 已读 `answer.clarify`）**：`answer.card='clarify'` 读 `answer.clarify` 渲染选项，点选项发一条自包含追问（EV-Q-064 / 065 验过，「哪个门店表现最好？」+「销售额最高」→「哪个门店销售额最高？」）；刷新后按后续轮次反推已选项。`kind=denied` 按 `code` 渲染（锁图标 + tag），「申请权限」只给 `NO_PERMISSION`（NO_DATA / NOT_SELECT 验过无入口；手机号、关行级权限两题验过有入口且 W16 通过）。
- **R5 问数调优 · 认证问答页签**：T3 列表、T4 抽屉、T5 新建、T6 认证（有未发布 CR → 待重新认证）、T7 启停、T8 同问法；T1 只取待重新认证 / 未认证计数。
- **R5 · 高频问题页签**：T18 列表、T19 一键认证（已关联认证 → 409）。
- **R5 · 新建认证问答选项**：指标 / 维度 / 时间下拉取 T2（真接口），新建走 T5。
- **R1 范围 / 口径下拉**：取 W17。**追问条**：读最后一轮回答的 `answer.suggest.followUps`（链路目前只在反问轮给，等于反问选项），点了按反问选项同样拼追问句；没有就隐藏（不再显示写死的 3 条）[推断]。
- **评审跟进（本体小白，01:3x）**：删掉最后一个会话后清空对话区、显示空状态（`QaSessionPanel.vue` / `QaConversation.vue`）；`?confirm=1` 刷新不再重复暂存动作——暂存后把 `run=<编号>` 写进 URL，刷新时 GET `/action-runs/{code}` 复用，已确认 / 撤回就去掉参数不弹卡（`Workbench.vue`，`qa.ts getAction`）。
- **连不上后台回退 mock**（复用 `src/api/fallback.ts`，同 c988cf5 / 2c6eae4 / StaticGate；问数侧薄封装 `src/pages/qa/qaFallback.ts` 给出 `qaLive`）：开关 qa 为真时，读接口遇到 `isBackendDown(e)` → `backend.down=true`，`qaLive` 变 false，页面改走 mock 渲染，只提示一次 `后台未启动，暂时显示演示数据`；其余错误照旧提示；写操作仍按开关走真接口并照旧报错（不假装成功）。覆盖：工作台会话列表 / 当前会话 / 对话区 / 追问条 / 范围口径选项 / 左栏常用与我的页面 / 常用与我的页面抽屉；问数调优页签计数、T2 选项、认证问答列表与抽屉、高频问题列表。回退在本次会话内保持，后台起来后刷新恢复 [推断]。手工验过：停 8061 后两页都显示演示数据 + 提示一次、无报错；起回后恢复真数据。
- 后端：`backend/app/modules/qa/{router,service,repo,schemas}.py` 对应实现，测试 `backend/tests/test_qa*.py`。最终检查（01:27，变基到 `740a554` 后，RENGU_TEST_DB=rengu_test_qa_joint）：pytest 408 passed / 1 skipped，ruff check + format、alembic check、`npm run build` 通过；页面单查（真后端：反问卡 → 追问条 → 追问、W17 范围下拉、T2 时间选项）无报错。

### 1.3 R5 问数调优 · 评测题库 / 反馈与纠错（qa-tuning）
- **后端**：T11 `POST /tuning/eval/runs`（202，进程内 asyncio 后台任务 `app/modules/qa/eval/job.py`，复用 `chain.answer` 在只读回滚事务里逐题跑、按回答形状判分写 `qa_eval_fail` / `last_result`；真 qwen-flash 冒烟 2 题通过链路）；T15 `:to-synonym`（已覆盖 → 关联；没覆盖 → qa-pages 起调静态 X3 建 / 加入草稿变更单，target kind=knowledge）；T16 `:to-cert`（加同问法 / 新建并认证）；T17 `:ignore` / `:reopen`；T13 列表返回 T14 详情字段。规则与 [推断] 见 `qa-api.md` §7「联调实现」。无新依赖、无迁移。测试 `backend/tests/test_qa_tuning_eval.py`（FakeLLM / runner 打桩）。
- **前端**：`EvalTab.vue`（T9 / T10 / T12，有运行中时 3 秒轮询）、`FeedbackTab.vue`（T13–T17）、`QaTuning.vue`（四个计数都取 T1、「运行评测」走 T11 并跳到新运行，未配 LLM 提示 503）；`src/api/qa.ts` 只追加了 `QaEvalRun` / `QaEvalRunDetail` / `QaFeedback` 等类型和 10 个函数。真接口模式下：转同义词遇到未覆盖的词提示「需要新建变更单…」，不弹发起变更窗；连不上后台仍回退 mock。

### 1.4 qa-tuning-fix（PDE 合并审意见，基线 master `e8d9c1a`）
- ① ER- 编号：取号前拿事务级咨询锁（`router.next_run_code`），并发不撞号。并发互斥（同时只能有一个运行中）**未做**，待产品定。
- ④ `:reopen` 撤销 `:to-cert` 加的同问法（新列 `qa_feedback.cert_variant_id`，迁移 `qa_0003` → `action_0002`）；也接受从 `已转认证问答` 重开 [推断]；新建的认证问答保留 [推断]。
- ⑤ T11 改经 `BackgroundTasks` 调度（请求事务提交后才起任务），去掉后台任务里 5 秒轮询。
- 未改：② 重启后运行卡「运行中」（见第 2 节）；③ `/feedbacks` 列表逐行构建详情（N+1，每页 ≤ 200 行，后续按批预取 CR / 认证 / 计划）；⑥ service 跨模块只读引用 `static.ChangeRequest`，等 static 提供查询函数再换。

### 1.2 链路（qa-reuse 最终 `740a554`，在 `a39b314` 上；chain.py / llm.py / test_qa_chain.py / eval/ 归 qa-reuse，本分支未改）
- **LLM 调用**：`llm.py` 用 openai SDK（`openai>=1.50`）的 `AsyncOpenAI` 调 DashScope 兼容接口（qwen-flash）；密钥只从环境变量读（本机 `/home/box/.qa_dashscope.env`，不进仓库）；CI 用 FakeLLM。
- **Prompt**：`chain.py` 照 Vanna `get_sql_prompt` 结构（初始说明 + ===Tables + ===Additional Context + ===Response Guidelines）和 `extract_sql` 围栏规则（MIT）；DDL 用 SQLAlchemy 把 ORM 编译成 CREATE TABLE + COMMENT，只给业务列；PG 方言规则、Style 段、样例值、「Today's date」摘自 NVIDIA auto-ontology（Apache-2.0，本地 `/workspace/tmp/auto-ontology`，未入库）。
- **SQL 安全**：sqlglot AST 校验单条 SELECT（`app/common/sql_guard`）、外包 `LIMIT`、行级过滤注入；执行报错回喂一次（`FIX_RETRIES=1`）；`statement_timeout` → 504 `QUERY_TIMEOUT`；网关拒绝 → 403 `SQL_REJECTED`（不重试、不落轮次）。
- **意图三选一**（同一次 LLM 调用）：SQL / 反问 JSON / 拒答 JSON，形状见 `qa-api.md` §2.3。
- **口径数据**：`backend/seeds/extra/qa/caliber/{本体}/notes.json`（销售额 = 行含税 ÷ 1.13、按出库时间、剔除测试单；有效订单数 / 单量 = 状态 ≠ 已取消、按出库时间；含税销售额、订单数、销量、出库及时率、优惠金额…）、`refuse.json`（拒答清单 → code + 预检 keywords）、`context.json`（本体级上下文补充）、`metric_codes.json`（旧指标 code 映射）。
- **评测**：题库 `backend/seeds/extra/qa/eval/questions.json`（100 题，v0.3，说明 `docs/backend/qa-eval.md`）；工具 `backend/app/modules/qa/eval/`（`setup_db` 建评测库并灌种子、`run` 跑题判分写 `results/`、`ctx` 打印 prompt；不进 pytest、应用不 import，key 只从环境读）。
- **`a39b314` / `740a554` 新增**：sqlglot 口径校验 + 一次纠正重试、收紧反问规则；值锚点、0 行字面量修复、时间上界截断（思路取自 NVIDIA auto-ontology，Apache-2.0）。

## 2. 未完成

### 2.0 qa-p1（10-27，基线 master `283bd89` + `5e3282e`）已做
- **去零售写死**：可问表 / 时间列 / 名称列 / 度量词全部从 sql_guard 受治理表推出（`chain.qa_source`）；对象按 `object_type.code` 对上受治理表，关联只给 join 落在可问表上的；维度对象 = 可问对象 + `caliber/{本体}/context.json` 的 `dimObjects`；本体没有受治理表 → 503 `DATA_NOT_CONFIGURED`（chain 与 O4 `objects_api` 同）。提示词通用化（「{本体名}」数据分析助手，示例不再写门店 / 大区 / 品类）。口径、拒答、绑定、旧指标 code 都改成按本体分目录：`seeds/extra/qa/caliber/{本体}/`、`seeds/extra/qa/objects/{本体}/bindings.json`；`catalog.metric_key` 必须带本体。新建会话话术「按 大区 = …」→「按 范围 = …」。
- **只读连接收窄**：`app/modules/qa/readonly.py` `scope_readonly_conn`：连接账号是 `rengu_ontology_scoped` 成员就 `SET LOCAL ROLE rengu_ontology_scoped`，否则若是 superuser / bypassrls 就拒（503），再设 `rengu.ontology_id`；chain `get_qa_conn`、`eval/job.py`、`object_source.py` 都走它。**部署须给问数只读账号 `GRANT rengu_ontology_scoped TO <qa 只读账号>`**。
- **拒答 / 反问加强（通用，不调模型的确定性预检）**：`refuse.json` 每条带 `keywords`，命中直接拒答（多条命中取最严：NOT_SELECT > NO_PERMISSION > OUT_OF_SCOPE > NO_DATA）；名称简称（“X + 数据推出的通用后缀”，如「南京店」）对上多个对象 → 反问候选；通用评价词（业绩 / 表现 / 怎么样 / 最大…）没带任何已知指标说法 → 反问指标；提示词补了「会话范围外 → OUT_OF_SCOPE」「维度取值不存在 → OUT_OF_SCOPE」「像已有取值的别称 → 反问」「英文泛称 → 反问」。`eval/run.py` 现把题目 `asScope` 当会话范围传入（与 76/100 基线这几题不完全可比）。
- **已验证（qa-p3 实跑，见下方「最近评测分」）**：离线核对 100 题的拒答 keywords 只剩 080 / 081 未命中（靠提示词 + asScope）。keywords 是按题库调的，有过拟合风险，计划加 101+ 留出题（只拒答 / 反问）单独计分。
- **测试**：本轮相关 qa 测试与基线 `54f39d1` 失败集合相同（基线已有 27 failed / 4 errors，多为测试用户在本体 1 是 viewer、POST 被 403「当前本体工作空间为只读权限」，非本轮引入）；新增的预检 / qa_source 单测通过。

### 2.0a qa-p2（10-27，基线 master `f0a7df3`，迁移头 `admin_0026`，本批无新迁移）已做
- **W7 流式**：`POST /sessions/{sid}/turns:stream`（实时 `status` + 回答事件 + `done`）、`GET /turns/{tid}/stream`（`Last-Event-ID` 续传 / 回放）；事件先写 `qa_turn_event` 再推（`stream.py`）。前端 `useQaSession.ts` 先走流，失败回退 W6。
- **动作意图 + 确认卡**：候选动作和执行策略取自能力目录 `static.capabilities` 的 actions 节（`intent.action_catalog`），dry-run 只读校验，卡片带 `policy` / `canStage`；`POST /turns/{tid}/action:stage` 只暂存到动作中心待确认，**从不调 `apply_policy`、不自动执行**（`auto` 也一样），suggest → 409，AI 不允许 → 403。
- **告警引用（只读）**：`AlertService.search` 进程内调用，按 alerts:view 门控，进 prompt + `answer.alerts` → SSE `citations`。
- **MCP**：`POST /api/qa/v1/mcp`（入口 meta:read）。问数专属 `qa_ask` / `metric_query`；`object_query` 转发平台 `/machine/objects:query`；动作 / 事件 / 函数工具直接用平台 `machine.tools.export`（同 `/machine/tools?format=mcp`）的清单并按 binding 转发（带 Idempotency-Key），不重复实现。告警引用要 `dynamic:read`。详见 `qa-api.md` §2.4。
- **动作意图离线核对**：`test_qa_intent_eval.py` 用 palantir 20 题集核对问数意图解析与策略卡（不调真模型）。注意：策略 `callers: [人]`（如 call-07）不含 AI → 问数卡 `canStage=false`，只能到动作中心手动发起。
- **测试**：`test_qa_stream.py`、`test_qa_mcp.py`、`test_qa_intent_eval.py`（新）+ `test_qa_chain.py` / `test_qa_session_write.py` / `test_qa_turn_actions.py` / `test_qa_explore.py`；`test_qa_chain.py::test_no_key_503`、`::test_caliber_check_feeds_back_once` 两条基线即失败（本批未改相关代码）。没跑真模型评测。

### 2.0b qa-p3（10-27，基线 master `343ba5f`，迁移头 `dynamic_0027`，本批无新迁移）已做
- **test_qa_chain 旧断言**：跟上 p1/p2 的通用化话术与 503 行为，原两条基线失败已修。
- **决策评估接进动作意图（tier-1 演示的问数段）**（每次都现评估、以 AI 身份记 decision_log；未复用 dynamic-r7 的告警已存决策 `GET /alerts/{code}/decision`，以后可先读已存的再评估）：`dynamic_ctx.py` 进程内调 `DecisionService.evaluate`（同 `POST /decisions:evaluate`），caller = AI + 会话 + 本轮编号（`aiRoundId` 是**预测的轮次编号** [推断]，MCP 调用为 `MCP-<hex>`），动态侧写 `decision_log`；确认卡列排序后的建议（`answer.action.decision`），模型补的参数（如数量）只填决策没填的，填了就按补全后的参数重新 dry-run。问题里带编号（`R-012-…` 这类 ID 片段）也拿来搜告警，点名的告警排前。决策没建议 / 读失败 → 退回能力目录 + dry-run。
- **`POST /turns/{tid}/action:stage` 对齐动态 stage**：`params` 一律转成 JSON 对象字符串（对象 / JSON 串 / 卡上的「k=v，k2=v2」都收，格式不对 422）——动态 checker 要求结构化参数；`source` 带 `alert_id`（卡上 `decision.alertId`），提交条件才有告警上下文；同一告警已有动作（幂等键 `act-<告警编号>`，含种子 / 规则暂存的）→ 同类型直接 200 返回已有那条，不新建；不同类型 → 409「告警 X 已有动作记录 Y，请到动作中心处理」。
- **MetricFlow 链路**：`semantic.py`：可试算的已发布指标（`GET /exports/metricflow` 同一导出）进 prompt，模型只出 `{"metric": {...}}`，SQL 由小白的 `metricflow_engine` 编译，仍走 sql_guard；编不出 → 回喂一次退回 SQL 路径。回答带 `metricQuery`。**引擎缺口（未改引擎，找小白）**：无月 / 周粒度（只有 day / none）；不支持跨语义模型维度；过滤无 IN / LIKE；`query_metrics` 用 `session.connection()`（问数要在只读连接上，只能自己 compile 再执行）；schema 写死 public；每题都要现导出一遍，成本高；没有 time-spine 表，累计 / 补零类查询不可用（编不出 → 退回 SQL 路径）。xiaobai-p2（master `4277bd2`）新增的 `metric_query(session, user, …)` 入口走 `session.connection()`，问数仍用 `compile_query`（默认 builtin 后端）+ 自己的只读连接 `run_guarded`；要切 MetricFlow 后端需引擎支持传入连接或 backend 参数（注意问数 MCP 工具也叫 `metric_query`，导入时改名）。
- **单对象动态摘要**：问题只点名一个对象实例时取 `ObjectDynamicSummary`（同 `…/objects/{objId}/dynamic-summary`），进 prompt + `answer.dynamicSummary` + citations。
- **我的页面定时刷新**：`page_schedule.py` 按动态的 hook 契约：`register(app)` 登记任务 `qa.page.sweep`（队列 `qa_pages`，锁同名，每分钟周期，参数 `timestamp`），逐工作空间刷到点的页面（每批 20），以页面所有者身份执行（所有者没了按 `QA_PAGE_OWNER_MISSING` 记为刷新失败），单页失败只回滚这一页、照常排下次，不拖累其他页面；「刷新后」订阅到期只记审计事件 `qa.page.subscription.due`（`app.common.audit.audit` 目前只写日志、不落库；推送通道和「每天 / 每周」摘要未做）。
- **按本体自动出评测题**：`eval_gen.py`，挂在问数评测 worker（`python -m app.modules.qa.eval.worker`）上的每日周期任务 `qa.eval.generate`（cron `30 2 * * *`，队列 `qa-eval`）。题目全由本体元数据拼：已发布指标 →「指标名」「按维度看指标名」；能力目录里的动作 →「给一个对象发起动作」（身份 = 本空间最近一次评测发起人，没有就不出动作题）。写 `qa_eval_question`（`meta.auto/ontologyId/source`，按题目去重，不动人工题）。表按本体 RLS 隔离（唯一键是 本体 + 题目），评测运行另按 `meta.ontologyId` 兜底过滤；动作题按「回答是不是该动作的建议卡」判。
- **测试**（私有库 `rengu_test_qa_p3`）：新增 `test_qa_dynamic_semantic.py`、`test_qa_page_schedule.py`、`test_qa_tier1_demo.py`（真种子 + 真动态服务 + FakeLLM：告警 → 决策评估 → 确认卡 → stage）、`test_qa_eval_gen.py`。

**动态 hook**：已随 dynamic-r7（master `52e1b9b`）合入：`dynamic/worker.py` `_mount_qa` 调 `page_schedule.register(app)`（import 失败只告警跳过），`run_worker_async(queues=[QUEUE, QA_QUEUE])`，`QA_QUEUE == page_schedule.QUEUE == "qa_pages"`。改名 / 改队列要两边一起改。

**演示环境配置（不是 qa 代码）**：
1. 种子里 `request_replenish` 的审批是一段说明文字 → `approvalConfigured=false`，stage 报 `ACTION_APPROVAL_NOT_CONFIGURED`；演示前要把审批配成一个本体成员（测试里用 `U010001`）。
2. 种子 `request_replenish` 的提交条件有一部分解析不了（动态 / 动作种子问题）。
3. 种子 / 规则已为同一告警暂存过补货 → 问数「发起动作」返回那一条（200），不会再建。

### 2.0c qa-p4（10-27，基线 master `f146379`，本批无新迁移）已做：准确率
全部是**通用、本体驱动**的能力，代码里没有零售词；按本体的提示放 `seeds/extra/qa/caliber/{本体}/`（本批只给本体 1 加了 `context.json` 的 `timeHints.shipped_at += 履约`、`hierarchyLevel {"product.category": 1}`，`notes.json` 销售额别名 `sales`）。
- **取值索引 `value_index.py`**（思路参考 WrenAI / Vanna / SQLBot 的取值检索，未拷代码）：主源是静态 `semantic_map`（已发布快照的维度取值 + 同义词），没覆盖的列用本库取值（受治理表的低基数文本列 / 枚举标签 → code / 路径列 `a/b/c` 各级段）兜底；题目最长优先匹配 → `col = 值` 进 prompt「取值匹配」。`semantic_map` 里有、库里没有的官方取值 → 确定性拒答 OUT_OF_SCOPE；会话范围同列别的取值 → OUT_OF_SCOPE（修 080 / 081）。
- **层级**：路径列自动识别（值里带分隔符），没指明层级按 `hierarchyLevel`（默认第 1 级）`split_part` 汇总，答复说明「按第 n 级」；表内自关联只看顶层。`sqlfix.path_top_level` 把模型选的整列分组改成默认层级。
- **指标定义**：口径裁定 + 指标层定义进 prompt；只说了指标没时间 / 维度 / 取值 → 反问范围；未定义的评价词 → 反问指标。口径裁定 / 指标 / 会话口径里写到的枚举 code 是「必需过滤」，不会被「题目没提的枚举条件」清理掉。
- **确定性反问（不调模型）** `guards.py`：题里同时点两种时间说法（列注释「X时间」+ `timeHints`）→ 反问时间口径（068）；像某列取值但对不上 → 反问候选（058）；一个泛称能落到多个层级 / 维度 → 反问层级（070）；英文 / 泛称只带指标 → 反问（066）。
- **确定性 SQL 修正** `sqlfix.py`（sqlglot）：LIKE → 等值；占位取值（`--` / 未知）剔除；题目没给时间去掉时间窗；题目点名某时间列（「下单的」）而时间窗落在同表另一时间列 → 改到点名的列（口径名 / 枚举标签里的字不算点名）；最外层日 / 周 / 月时间桶显示成日期；按对象名称分组或列明细时补业务编号列；会话范围注入每个用到该表的 SELECT。扇出检查（上层列 SUM/AVG + join 明细、`FROM 上层` 的 COUNT(*)、「明细合计 / COUNT(*)」）回喂一次。
- **模型反问已有默认的事 → 回喂一次让它直接答**（`guards.overrule_clarify`）：选项是层级名、会话范围列的取值、或（题目点了口径时）同组口径说法。
- **测试**（私有库 `rengu_test_qa_p4t`；评测库 `rengu_test_qa_p4` 已灌种子，ontology_release 只追加触发器会让测试夹具失败，所以测试用另一个库）：新增 `tests/test_qa_value_index.py`（虚构车队本体，纯单元）；`test_qa_chain.py` 改 3 条预期（LLM 反问题改问法、time_hints 多了「履约」、明细列表多了 `code` 列）+ 新增确定性反问用例。`test_qa_value_index / test_qa_chain / test_qa_mcp / test_qa_stream / test_qa_tier1_demo` 全过。
- **评测（真模型，一次，`summary_p4.json`）**：**90 / 100**（qa-p3 77）：作答 64/74（57）、拒答 16/16（14）、反问 10/10（6）。
  - 作答失败 10 题：006（模型反问品类层级）、025（模型在范围内反问「哪个区域」）、095（模型反问含税 / 不含税）——三题都已由 `overrule_clarify` 处理，**未经真模型复测**；020（列门店用了 `store_no` 不是 `code`）、046（「昨天下单」时间窗用了出库时间）、100（`date_trunc('day')` 显示成 UTC 16:00）——评测后加的确定性修正，用评测存下的 SQL 回放已对；012（gold 不剔除已取消单，与口径裁定「优惠金额」不一致，题库问题）；015（SQL_REJECTED，模型偶发，p3 / 回放均过）；048（客单价分母：gold = 有效订单数 ≠ 已取消，模型用了明细行 COUNT(*)，现在扇出能拦但改完分母口径仍可能不对）；052（含税销售额 SUM(o.amount) join 明细扇出，回喂后模型没改；需要确定性改写或指标层直出）。
  - 评测后修正按存档 SQL 回放：作答 67/74（020 / 046 / 100 转对、无回退）；加上 006 / 025 / 095 若回喂生效可到 70/74。下一轮真模型评测核实。
- **静态 `semantic_map` 缺的字段（已列给静态，不在 qa 写死）**：dimValue 显式 `level`（现在只能从 id 前缀猜）、父取值、状态、计数；dimension 的 `levels`（有序 api / name / 来源列）、`qa.defaultLevel`、`qa.unspecified`、`qa.nullAs`、对象 / 属性 / 物理列绑定；metric 的 execDef（timeField、defaultFilter）、公式、状态、描述、同义词（现为空）；property 的 api ≠ 物理列（如 region_code vs big_region），缺物理绑定。

### 2.0d qa-p5（10-27，基线 master `2300d48`，本批无新迁移）已做
- **扇出确定性改写**（052 类，`sqlfix.preaggregate_children`，sqlglot AST，按外键元数据、不认业务词）：同一 SELECT 里 join 了上层表和它的明细表，且对上层列做 SUM / AVG（或 `FROM 上层` 的 COUNT(*)）时，把叶子明细表换成按 join 键先汇总的派生表，明细侧聚合改成对汇总列再聚合（SUM→SUM、COUNT→COALESCE(SUM)、MIN/MAX 不变、AVG→ΣS/ΣN）；只在改完该 SELECT 不再扇出时生效，做不到原样不动，再由 qa-p4 的扇出检查回喂一次兜底（`chain.py` 先改写、后检查）。没走指标层直出（MetricFlow 缺口见 2.0b）。
- **订阅摘要推送**（`page_schedule.send_digests`，随每分钟 `qa.page.sweep` 跑）：「每天 09:00 / 每周一 09:00」（`display_tz`）每个时段出一条摘要（标题 / 每张卡的值或前几行 / 数据截至 / 链接），「每次刷新后」订阅在刷新成功后出一条（`only_changed` 时结果没变不出；摘要时段同理按指纹去重）。问数自己没有投递通道，一律写事务性 outbox（`app.common.outbox.emit`，主题 `qa.page.subscription`，`event_id` 带页面 + 订阅 + 时段键，多实例只出一条；时段后 3 小时内补发）。收件人只认页面所有者和共享成员（姓名 → 用户编号，对不上的放 `unresolved`）。**真正投递要通知侧改**，见文末清单第 1 条。
- **每日自动跑评测**（`eval/daily.py`，周期任务 `qa.eval.daily`，cron `0 3 * * *`，晚于 02:30 出题；登记在评测 worker `python -m app.modules.qa.eval.worker`，队列 `qa-eval`）：逐工作空间建一条 `qa_eval_run`（触发方式「每日自动」）并在同一事务入队，由既有 `qa.eval.run` 执行、结果照常写 `qa_eval_run` / `qa_eval_fail` / 每题 last_result；同一天已有运行或已排队 → 跳过；发起身份 = 本空间最近一次评测发起人（须仍有 qa-tuning 管理权），没有就跳过并记日志。worker 启动时若有 `rengu_ontology_scoped` 角色会补 GRANT（procrastinate 表）。**已登记，未对真模型跑过**。
- **静态 `semantic_map` 新字段（static-t7）已接**（`value_index.py`、`sqlfix.path_top_level`、`chain._metric_meta / _semantic_props`）：维度先按 `levels[].table/column` / `binding` 物理绑定对上受治理列，层级按 `levels` 顺序，默认层级 = `defaultLevel`（`context.json` 的 `hierarchyLevel` 仍可覆盖）；`dimValue.level / levelNo / parentValue` 直接用，**不再按 id 前缀猜**；`property.column` 对物理列；`metric.synonyms / desc` 进指标提示。快照没有这些字段的本体才退回旧的重合 / id 前缀推断。
- **多对象动态摘要**（`dynamic_ctx.named_objects / result_objects / objects_summary`）：点名多个对象或表格结果带对象业务编号 → 最多扫 10 个，只留有未关闭告警的，按告警数取前 3，`answer.dynamicSummaries` + citations + 表格文字末尾「动态提示：…」。
- **palantir-t7 humanChecks / declaredEdits**（`intent.py`、`dynamic_ctx._check_of`、`chain.py`）：动作卡 `dryRun.humanChecks`（不算失败，文字提示「另有 N 项提交条件需确认人人工核对」）、`dryRun.declaredEdits` + `action.declaredSummary`（「由外部系统承担（本地不执行）：…」，进程内调 `integration.action_edits.declared_writeback`）；决策评估建议也带。SSE `suggest` 事件即 `answer.action`，字段同。前端确认卡**未改**（见清单）。
- **评测（真模型 qwen-flash，一次，私有库 `rengu_test_qa_p5e` 用 `setup_db` 新建，含 static-t7 字段；`summary_p5.json` / `result_all_p5.json`）**：**96 / 100**（qa-p4 90）：作答 **70/74**（64）、拒答 16/16、反问 10/10；相对 p4 无回退。
  - 核实项：025、095 转对（`overrule_clarify` 生效）；006 不再反问，但只返回「乳品」1 行（gold 乳品 / 粮油调味各 3），仍错；p4 回放的 020 / 046 / 100 真模型也对（67/74 已达成）；052 由扇出改写转对（说明「明细表 order_line 先按关联键汇总再关联」，数值与 gold 一致）。
  - 仍错 4 题：006（见上）、012（gold 不剔除已取消，与口径裁定冲突，题库问题）、015（模型偶发 SQL_REJECTED）、048（客单价分母口径，见清单第 8 条）。
  - **自动出题过拟合抽查**（新工具 `eval/auto.py`，按本体元数据现拼题、不写库，确定性抽 20 题：指标 15 / 动作 5，池子 25 / 10；`summary_auto_p5.json`）：**7 / 20**（指标 4/15、动作 3/5）。100 题 96 分而自动题 7/20，说明有过拟合 / 规则冲突，见清单第 10 条。
- **接口文档**：`frontend/docs/backend/qa-api.md` 补 `dynamicSummaries`、humanChecks / declaredEdits、取值层级来源三条。
- **测试**（私有库 `rengu_test_qa_p5t`）：新增 / 改 `test_qa_value_index.py`（t7 字段绑定、属性物理列）、`test_qa_eval_gen.py`（每日运行）、`test_qa_eval_queue.py`、`test_qa_page_schedule.py`（摘要时段 / 去重 / 收件人）、`test_qa_dynamic_semantic.py`（多对象摘要、humanChecks / declaredEdits 卡）。结果见本节末与清单「已知测试失败」。

### 2.0.1 剩余（按优先级）
| 项 | 说明 |
|---|---|
| 订阅推送 | 摘要内容 + outbox 事件已做（qa-p5）；实际投递等通知侧（见文末清单 1） |
| 自动评测 | 自动出题（qa-p3）+ 每日自动跑（qa-p5，已登记未实跑）；101+ 留出题未加 |
| MetricFlow 引擎缺口 | 已接链路（qa-p3）；引擎缺口见 2.0b，找小白 |
| MCP SDK | MCP 现在是自写的最小 JSON-RPC（够 tools 用）；官方 `mcp` Python SDK（MIT）未装，`pyproject.toml` 不归 qa，要换 SDK / 加 streamable-HTTP 会话、SSE 通知时找 PDE 加依赖 |
| 动态摘要 | 单对象（qa-p3）+ 多对象前 3（qa-p5）已做；前端未展示 `dynamicSummaries` |
| 旧 X1 stageAction 路径 | 前端旧的非建议动作路径 `POST /action-runs:stage` 用 `source.type='问数'`，动态路由只收 `手动` → 被拒（既有问题）；新路径走 W7a |
| 达梦 / 金仓方言 | sqlglot 方言、DDL 编译与 statement_timeout 适配 |
| 测试夹具 | 测试用户在本体 1 是 viewer，大量 qa POST 测试 403（基线即如此），要与 auth / ontology owner 对齐夹具 |
| **sql_guard 本体 1 写死（归治理）** | `app/common/sql_guard.governed_tables` 只对 `ontology_id == 1` 返回受治理表，其他本体一律空 → qa 返回 `DATA_NOT_CONFIGURED`。要按本体登记受治理表，由治理 owner 改，qa 不动 |

> 截至 qa-pages `c7169d2`（基线 master `b475283`；静态 `58ad8d7` 已在 master）。已做完、不再列：R1 固定到页面（qa-cardfb，P7）、R2 我的页面（P2、P4–P10 + 前端）、R3 对象搜索 / R4 对象详情（通用化，读真 `/meta`、`/facets`）、连不上后台回退 mock。

| 项 | 还差什么 | 接口 / 文件 |
|---|---|---|
| T11 评测任务 | 进程内 asyncio，重启丢任务；每日定时未接；评测不带会话口径 | `eval/job.py` |
| 行级范围 | row-scopes 接口未上 master：工作台「范围」默认值靠前端推断（话术已改「范围」，qa-p1） | `router.py`、`Workbench.vue` |
| 按一批对象提问 / 绑定 | O4 和物理绑定只有 store / product / order，其他对象类型 `bound=false` | `seeds/extra/qa/objects/{本体}/bindings.json`、`objects_api.py` |
| 动作执行 | 建议动作只到 stage（W7a），确认 / 执行在动作中心；qa 不执行 | W7a；`Workbench.vue` `runAction` |
| 关键词分面 | E1 顺带的过渡分面要保留到静态 `/facets` 支持关键词 | `explore_api.py` `_facets` |
| 静态分面回落 | `objMeta.ts:156` `loadStaticFacets` 任何错误都回落（含 422），应只在 `isBackendDown` 时回落 | `src/pages/objects/objMeta.ts` |
| 复用静态私有函数 | qa 直接 import 静态 `_bad` / `_scalar`，等静态公开后改名 | `explore_api.py` |
| `Bound.check` | 按 `what` 字符串区分筛选 / 排序，应改成显式参数 | `explore_api.py` |
| 并发 | 页面 P2、会话 S 的 id 生成没锁，并发新建会撞 id 报 500 | `service.py` / `pages` 相关 |
| 我的页面 | 计数单独多一次请求；删除后停在空的后一页，应回退一页 | `MyPages.vue` |
| 注释 | `PageDrawer.vue:11` 注释过时 | `src/pages/qa/pages/PageDrawer.vue` |
| FeedbackTab | `FeedbackTab.vue` 的改动在 qa 允许路径外，待 PDE 确认 | `FeedbackTab.vue` |
| 评测分 | 96 / 100（qa-p5）：作答 70/74、拒答 16/16、反问 10/10；自动题抽查 7/20（见 2.0d、清单 10） | `eval/results/summary_p5.json`、`summary_auto_p5.json` |
| 指标口径变体 | 静态按指标名的（…）后缀推出变体，静态会改成显式字段；qa 按标签匹配，届时核对 | `service.py` `variant_index` |
| 筛选/关键词含 NUL | 值或关键词带 `\u0000` 时 search/export 仍 500（PostgreSQL 拒绝）；应与静态 `_scalar` 一起拦成 422（动态评审非阻塞 ①） | `explore_api.py` |
| 排序键前缀 | 排序键用 `lstrip("-")`，`--key` 也能过；改成只去一个 `-`（动态评审非阻塞 ②） | `explore_api.py` |

### 2.1 链路未完成 / 待裁定
- **最近评测分（qa-p5，真模型，私有库 `rengu_test_qa_p5e`）**：**96 / 100**（作答 70/74、拒答 16/16、反问 10/10），自动题抽查 7/20，见 2.0d。
- **再上次（qa-p4，私有库 `rengu_test_qa_p4`）**：**90 / 100**（作答 64/74、拒答 16/16、反问 10/10），失败题与原因见 2.0c。
- **上上次评测分（qa-p3，真模型，私有库 `rengu_test_qa_p3`，master `4277bd2` 之上）**：**77 / 100**，`backend/seeds/extra/qa/eval/results/summary_p3.json`（作答 57/74、拒答 14/16、反问 6/10）。失败 23 题：作答 006、012、014、016、017、022、024、025、034、042、048、052、062、067、085、091、095；反问 058、066、068、070；拒答 080、081。
  - 与 76/100 不完全可比：① 题目 `asScope` 现在当会话范围传入（3 题，过 1；025 / 081 范围没收窄）；② ontology_0018 后 seed 的 U010001 不再挂角色，评测身份改为「无行级范围的角色里编号最小的成员」（现为 U010245，可用 `QA_EVAL_USER` 覆盖）——第一次跑用了无角色身份，作答 3/74，作废重跑；③ `setup_db` 补了 RLS 作用域、`load_sections()` 与空间 1 成员映射（否则灌种子 / resolve_user 失败）。
  - 作答回落主要在：零售组 / 区域名（024、085「华东N区」返回 0，疑似区域维度取值与 dim_value 对不上）、口径（012 优惠、042/067 GMV 已出库、048 客单价、052 含税）、品类层级（006、016 返回「乳品/常温奶」二级而 gold 为一级）。
- **上一次评测分（真 qwen-flash，`740a554`）**：**76 / 100**，见 `backend/seeds/extra/qa/eval/results/summary.json`（作答 62/74、拒答 9/16、反问 5/10；失败 24 题：EV-Q-012、014、016、022、025、026、042、051、052、058、061、063、066、067、069、071、072、078、080、081、082、084、091、095）。
- **评测工具**：已入库到 `backend/app/modules/qa/eval/`（`/workspace/qa_eval` 是旧的本机副本，可删）。T11 用的是 `eval/job.py`（种子评测题 `qa_eval_question`），离线 100 题工具 `eval/run.py` 不变。
- **链路行为问题（找 qa-reuse）**：
  1. EV-Q-080「华南大区上个月的有效订单数」有时返回反问（「华南大区不存在…是否查询华东」），有时拒答 `OUT_OF_SCOPE`；题库要求拒答且 `applyEntry: true`，但前端只给 `NO_PERMISSION` 申请入口 —— 契约冲突，待定 `OUT_OF_SCOPE` 是否给申请入口。
  2. EV-Q-066「Show me the sales」不反问，直接答销售额（137009.54）。
  3. 只缺时间时 qwen-flash 仍常反问时间（违反「只缺时间不问」）。
  4. 偶发生成 SQL 被网关拒 → 403 `SQL_REJECTED`，前端提示「提问失败」，问题不落库（如「昨天的毛利率」一次 403、重问得 NO_DATA）。
  5. 无多轮上下文：追问依赖前端拼句。
- **静态本体 / 指标定义待确认**（详见 `qa-eval.md` 待定表）：
  1. 不含税销售额 = 行含税 ÷ 1.13、订单级优惠不分摊 —— qa 已裁定写进 `caliber/1/notes.json`，静态指标定义页没改，待指标负责人确认。
  2. `store.is_test`：口径写了「门店.是否测试店 = 否」，前端门店属性没有该字段（种子全为否）。
  3. 退款种子缺失，gmv / gmv_tax 退款项按 0；退款率题拒答。
  4. 含税销售额的时间字段与默认过滤（同 gmv？）。
  5. 出库及时率「应出库订单」定义（暂按状态 ∉ {待支付, 已取消}），用 `shipped_at` 代替状态变更时刻（owner 周晓峰）。
  6. `store.big_region` 列名和取值（种子只有「华东」）。
  7. 外卖渠道范围；单量 / 几单 / 卖了多少钱 / 卖得最好等口语说法进 semantic-map。
  8. **种子指标定义页 `def_rows` 是模板照抄**：所有指标共用同一份定义页（基础对象 / 时间字段 / 默认过滤 / 同名默认都一样），要静态 owner 按指标分别填。
  9. **有效订单数：默认过滤与公式冲突**：定义页「默认过滤 已出库」vs 公式「状态 ≠ 已取消」；链路暂以公式为准（qa 裁定写在 `caliber/1/notes.json`），待静态 owner 定。

## 3. 代码在哪
- 前端：`src/api/qa.ts`（全部 qa 接口 + 动态 X1/X2）；`src/pages/qa/Workbench.vue`、`useQaSession.ts`、`components/QaConversation.vue`（轮次渲染、反问追问、拒答申请入口）、`components/QaAnswer.vue`（主卡 + 反问卡）、`QaStoreAnswer.vue`、`QaListDrawer.vue`、`QaSessionPanel.vue`；`src/pages/ontology/QaTuning.vue`、`qa-tuning/CertTab.vue`、`FreqTab.vue`（已接）、`EvalTab.vue`、`FeedbackTab.vue`（mock）。
- 后端：`backend/app/modules/qa/`（`router.py` 路由、`service.py` 业务、`repo.py` 查询、`schemas.py`、`models.py`、`catalog.py`；`chain.py` / `llm.py` 链路归 qa-reuse）；迁移 `backend/migrations/versions/qa_*.py`；种子 `backend/app/modules/qa/seeds`、`backend/seeds/extra/qa/{caliber,eval}/`；测试 `backend/tests/test_qa*.py`。
- 文档：`docs/backend/qa-api.md`（§2.1 / §7 联调实现、§12 [推断] 清单）、`docs/backend/qa-eval.md`。
- 提交（`80ac80c..qa-joint`，均为 qa 身份）：`8319001` 工作台 W2/W4/W5/W15、`6fc9da7` W16 + app store 版本号、`3deb514` W9–W12 / X1 / 常用、`014ea1d` qa.ts 改用 apiClient patch/put/delete、`3c7c54c` 问数调优（部分：认证、高频）、`c0c1d96` 反问卡 / 拒答、`c52ce76` 本交接文档、`0cd5a83` T2 选项 / 追问条 / qa-eval 冲突记录、`7f33ea3` 交接更新，及最后一笔交接定稿。
- 本机联调脚本（未入库）：`/tmp/qaj/be.sh`（起后端 8061，读密钥 env）、`checks.sh`（pytest / ruff / alembic / build）、`e2e4.py`（工作台回归）、`e2e6.py` / `e2e7.py` / `e2e8.py`（反问、拒答、申请、刷新）。

## 4. 下一步怎么接
1. **合入**：`qa-joint` 已在 qa-reuse 最终 `740a554` 上（+ pde-integ `215819d`），直接按 `b688d5d..qa-joint` 合；合入前跑 `RENGU_TEST_DB=<独立库> uv run pytest`、`ruff check/format --check`、`alembic check`、`npm run build`。
2. **问数调优补完**（qa-tuning 已做完除 T15 新建变更单以外的部分，下面是原计划）：后端按 `qa-api.md` §7 补 T11、T15–T17（T9/T10/T12/T13/T14/T20 读接口已有；T11 可复用 `app.modules.qa.eval.run` 的判分逻辑，异步跑完写 `qa_eval_run`；T15 走静态 X3 变更单），前端在 `EvalTab.vue` / `FeedbackTab.vue` 按 `CertTab.vue` 的 `qaReal()` 分支模式接，页签计数改读 T1。整页接完再标完成。
3. **SSE W7**：后端把 chain 的阶段产出成事件（§3.1），前端 `sendReal` 改为 W6 拿 `streamUrl` 后订阅；同步 202 保留作降级。
4. **我的页面 R2、对象页 R3/R4**：按 §4–§6 的接口表逐个接，接口函数加进 `src/api/qa.ts`，页面用 `qaReal()` 切换，未接完的保持 mock。
5. **链路**：EV-Q-080 的 `OUT_OF_SCOPE` 申请入口与 qa-reuse / PDE 定；若链路加多轮上下文，前端 `followUp` 可直接发选项 + 回写 `clarify.chosen`。评测工具入库后接进 T11。
6. **静态本体**：2.1 节 8 条口径问题找指标负责人 / 本体小白确认后，改 `caliber/1/notes.json` 和 `qa-eval.md` gold，重跑评测。

## 通用对象搜索评审修复（动态 / 静态，10-26）

- explore_api 筛选值按属性逐个校验，直接复用静态 `explorer._scalar / _bad / facet_kind / facetable`：枚举类只收标量数组（未知枚举值 422），区间类只收 `{min,max}`（数值走列范围检查，timestamp 只给日期上界含当天），不合法一律 422 `VALIDATION_FAILED`，`details.fields[0].field = filters.<key>`（排序是 `sort`）。
- 敏感 / 隐藏属性后台也拦：筛选键须 facetable，排序键须 sortable 且不敏感不隐藏；搜索结果、详情、`boundProps`、导出都不带；关键词只匹配可搜索且可见的属性，没有就忽略关键词（不再拿第一列兜底）。
- 前端：后台不通时导出直接报错，不导出演示数据。
- 未做：`router.py` 新建会话的系统话术写死「按 大区 = …」（应换成范围维度名，待 row-scopes 维度上线再改）[推断]；`objMeta.ts loadStaticFacets` 任何错误都回落过渡分面（422 也回落），可改成只在 `isBackendDown` 时回落。

## 未完成清单（交 Codex）

> 截至 qa-p5（基线 master `df09e5b`）。分类：**待定** = 要老板 / PDE 拍板；**Codex 修复** = 范围清楚、可直接改；**环境问题** = 部署 / 演示库配置，不是代码。

1. **页面订阅摘要的实际投递（通知侧）**
   - 是什么：问数已按时段把摘要写进 outbox（主题 `qa.page.subscription`，payload 含 `to` 用户编号、`channel` / `channelLabel`、`title` / `text` / `link`、`slot`、`fingerprint`），但没人消费，用户收不到。
   - 为什么没做：Codex 修复（不在 qa 路径）。需要：① `app.jobs.dispatch_outbox` 现在是空桩（`# EXTENSION POINT (PDE)`，返回 0），要实现扫描 + 投给 `outbox.subscribe` 的订阅者；② 通知侧订阅 `qa.page.subscription`，按 `to` 给每人写站内信；③ `notification.source_event_id`（FK `cr_event`）和 `cr_id`（FK `change_request`）都是 NOT NULL，非变更单来源写不进，要改成可空或加通用来源字段（`source_kind` / `source_ref`）+ 新迁移；④ 邮件 / 企业微信通道（通知模块现在只做站内信）。
   - 涉及文件和接口：`app/jobs/__init__.py` `dispatch_outbox`；`app/common/outbox.py` `subscribe`；`app/modules/notifications/models.py`、`service.py`；问数侧 `app/modules/qa/page_schedule.py` `TOPIC` / `_payload` / `send_digests`。
   - 怎么验证：`test_qa_page_schedule.py` 已验 outbox 行；补一条集成测试：建每日订阅的页面 → 推时钟到 09:00 → 跑 `qa.page.sweep` + `outbox.dispatch` → 所有者和共享成员各收到一条通知、同一时段重跑不重复。

2. **问数只读账号 / worker 需要 `rengu_ontology_scoped`**
   - 是什么：问数只读连接 `SET LOCAL ROLE rengu_ontology_scoped`（qa-p1），账号不是该角色成员就 503；评测 worker 在这个角色下访问 procrastinate 表需要 GRANT（integration / event worker 也有同样缺口）。
   - 为什么没做：环境问题（部署账号授权，不是 qa 代码）。`eval/worker.py` 启动时如有该角色会补 `GRANT … TO rengu_ontology_scoped`，但给账号授角色仍要 DBA 做。
   - 涉及文件和接口：`app/modules/qa/readonly.py` `scope_readonly_conn`；`app/modules/qa/eval/worker.py` `GRANT_RUNTIME`；procrastinate 表。
   - 怎么验证：演示库执行 `GRANT rengu_ontology_scoped TO <qa 只读账号>;` 后问一题不再 503；起评测 worker，`qa.eval.run` 能取到任务。

3. **演示环境：dev 库 `rengu` 缺新演示种子 + worker 未起**
   - 是什么：dev 库 `rengu` 是 Codex 的真快照，没有 qa-p3 起的演示种子（告警 → 决策 → 确认卡、页面订阅、自动评测题）；带 `qa_pages` 队列的动态 worker、评测 worker（`qa-eval` 队列：`qa.eval.generate` 02:30 / `qa.eval.daily` 03:00 / `qa.eval.run`）都没起。
   - 为什么没做：环境问题（规定 qa 不往 `rengu` 写、不在其上起后台 worker）。
   - 涉及文件和接口：`python -m app.cli seed`（或只灌 qa / 动态演示种子）；`app.modules.dynamic.worker`（`queues=[QUEUE, "qa_pages"]`）；`python -m app.modules.qa.eval.worker`。另见 2.0b「演示环境配置」三条（`request_replenish` 审批人、提交条件、已暂存动作）。
   - 怎么验证：dev 库灌完后 `tests/test_qa_tier1_demo.py` 同样的流程手工走一遍；`procrastinate_periodic_defers` 里出现 `qa.page.sweep` / `qa.eval.daily`。

4. **每日自动评测：已登记，未对真模型跑过**
   - 是什么：`qa.eval.daily` 每天给每个空间建一条评测运行并入队（2.0d）。
   - 为什么没做：待定（要定发起身份和模型额度；按要求本批不在真模型上跑后台任务）。
   - 涉及文件和接口：`app/modules/qa/eval/daily.py`、`eval/worker.py`、`eval/job.py`；表 `qa_eval_run` / `qa_eval_fail` / `qa_eval_question.last_result`；前端 T11 评测页。
   - 怎么验证：私有库起评测 worker，`RENGU_FIXED_NOW` 设到 03:00 后，`qa_eval_run` 多一条「每日自动」并跑完，同日重复触发不新增。

5. **MetricFlow 没有 time spine，累计 / 补零查询不可用**
   - 是什么：指标层直出遇到累计、按日补零类问题编不出，退回 SQL 路径（模型自写，准确率不稳）；扇出类（052）也因此没走指标层直出，用的是 sqlglot 改写。
   - 为什么没做：待定（老板决定，引擎归小白）。
   - 涉及文件和接口：`app/modules/qa/semantic.py`；小白 `metricflow_engine`（另缺月 / 周粒度、跨语义模型维度、IN / LIKE 过滤、传入只读连接）。
   - 怎么验证：引擎有 time spine 后，「近 7 天每天的销售额（没单的天记 0）」走 `metricQuery` 且返回 7 行。

6. **动作确认卡的 humanChecks / declaredEdits 怎么展示**
   - 是什么：后端已在 `answer.action.dryRun.humanChecks`、`action.humanChecks`、`dryRun.declaredEdits`、`action.declaredSummary` 和文字里给出（2.0d），前端确认卡没展示，「人工核对项是否要确认人逐条勾选后才能 stage」也没定。
   - 为什么没做：待定（老板定展示和是否强制勾选），前端随后 Codex 修复。
   - 涉及文件和接口：`app/modules/qa/intent.py` `action_snapshot` / `with_declared`；SSE `suggest`；`POST /turns/{tid}/action:stage`；前端 `src/pages/qa/components/QaAnswer.vue`（动作卡）。
   - 怎么验证：`test_qa_dynamic_semantic.py::test_action_card_shows_human_checks_and_declared_edits` 已验后端；前端用 Webhook 类动作问一题，卡上列出人工核对项与「由外部系统承担」。

7. **前端未展示多对象动态摘要**
   - 是什么：`answer.dynamicSummaries` / SSE `citations.dynamicSummaries` 已有，前端只渲染了单对象 `dynamicSummary`（表格文字末尾的「动态提示」能看到）。
   - 为什么没做：Codex 修复（本批只做后端）。
   - 涉及文件和接口：`QaAnswer.vue` / `QaConversation.vue`、`src/api/qa.ts` 类型；`qa-api.md` 动态摘要两条。
   - 怎么验证：问「列出告警最多的几个门店」类问题，卡片下方出现最多 3 个对象的告警提示。

8. **048 客单价分母口径**
   - 是什么：gold = 有效订单数（≠ 已取消），模型常用明细行 COUNT(*)；扇出改写能把行数改对，但「有效」的过滤口径仍可能不对。
   - 为什么没做：待定（指标定义页「默认过滤 已出库」与公式「≠ 已取消」冲突，见 2.1 静态第 9 条，要静态 owner 定）。
   - 涉及文件和接口：`seeds/extra/qa/caliber/1/notes.json`；静态指标 `execDef`；`sqlfix.preaggregate_children`。
   - 怎么验证：口径定后 `eval.run 48-48` 通过。

9. **已知测试失败 / 注意**
   - 是什么：`test_qa_dynamic_semantic.py::test_single_object_summary_attached` 单独跑会失败（受治理表在第一次请求时才登记，直接调 `dynamic_ctx` 拿不到），整文件跑通过——测试顺序依赖，qa-p3 起就有；另外基线遗留：测试用户在本体 1 是 viewer，部分旧 qa POST 测试 403（见 2.0 / 2.0.1「测试夹具」）。
   - 为什么没做：Codex 修复（夹具问题，不影响功能）。
   - 涉及文件和接口：`tests/test_qa_dynamic_semantic.py`、`tests/conftest.py`、`app/common/sql_guard.governed_tables`。
   - 怎么验证：给该用例加一个先登记受治理表的夹具后，`pytest tests/test_qa_dynamic_semantic.py::test_single_object_summary_attached` 单独通过。本批跑的 10 个 qa 测试文件（tier1_demo、dynamic_semantic、value_index、eval_gen、eval_queue、page_schedule、chain、mcp、stream、intent_eval）在 `2300d48` 上 116 passed（`df09e5b` 只多了文档提交）。

10. **自动出题抽查 7/20：过拟合 / 规则冲突信号**
    - 是什么：真模型跑 20 道按元数据拼的题，13 题失败：① 只说指标名（「销售额（不含税）」「有效订单数」等 4 题）→ 被 qa-p4 的「只说了指标没时间 / 维度 / 取值 → 反问范围」规则反问，而 `job.judge` 要求直接作答（与早先「只缺时间不问」冲突，规则是按 100 题库调的）；② 没数据的指标（退款率 ×2、毛利率 ×2、新增会员数、退款审核动作）→ 正确拒答，但出题没排除无数据指标，判成失败；③「按仓库看出库及时率」列名是 `warehouse` 没用维度名，判维度缺失；④「平均出库时长」模型 SQL 解析失败（SQL_REJECTED 类，真缺陷）；⑤「给一个 store 发起变更门店状态」反问是哪个门店（合理，判分过严）。
    - 为什么没做：待定（①要 PDE 定「只给指标名」该反问还是按默认范围直接答，定了再统一提示词 / `guards` 与 `job.judge`）+ Codex 修复（②出题跳过 refuse.json 里无数据的指标、或判分把「无数据拒答」算过；③作答列名用维度显示名；④查 `ship_avg` 的 SQL；⑤动作题允许「反问对象」）。本批按要求不再加功能。
    - 涉及文件和接口：`app/modules/qa/guards.py`（指标无范围反问）、`chain.py` 提示词、`eval_gen.py`、`eval/job.py` `judge` / `judge_action`、`seeds/extra/qa/caliber/1/refuse.json`；抽查工具 `eval/auto.py`、结果 `seeds/extra/qa/eval/results/result_auto_p5.json`。
    - 怎么验证：改完重跑 `TAG=_px N=20 python -m app.modules.qa.eval.auto` 与 100 题 `eval.run all`，自动题明显上升且 100 题不低于 96。

11. **其它已列未做（见 2.0.1 表）**：101+ 留出题、MCP 官方 SDK、达梦 / 金仓方言、sql_guard 只认本体 1（归治理）、旧 X1 stageAction 路径、并发 id、`explore_api` NUL / 排序键前缀等。分类以 2.0.1 表为准，验证方式写在各行。
