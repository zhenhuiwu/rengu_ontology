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
- **口径数据**：`backend/seeds/extra/qa/caliber/notes.json`（销售额 = 行含税 ÷ 1.13、按出库时间、剔除测试单；有效订单数 / 单量 = 状态 ≠ 已取消、按出库时间；含税销售额、订单数、销量、出库及时率、优惠金额…）、`refuse.json`（拒答清单 → code）。
- **评测**：题库 `backend/seeds/extra/qa/eval/questions.json`（100 题，v0.3，说明 `docs/backend/qa-eval.md`）；工具 `backend/app/modules/qa/eval/`（`setup_db` 建评测库并灌种子、`run` 跑题判分写 `results/`、`ctx` 打印 prompt；不进 pytest、应用不 import，key 只从环境读）。
- **`a39b314` / `740a554` 新增**：sqlglot 口径校验 + 一次纠正重试、收紧反问规则；值锚点、0 行字面量修复、时间上界截断（思路取自 NVIDIA auto-ontology，Apache-2.0）。

## 2. 未完成

> 截至 qa-pages `c7169d2`（基线 master `b475283`；静态 `58ad8d7` 已在 master）。已做完、不再列：R1 固定到页面（qa-cardfb，P7）、R2 我的页面（P2、P4–P10 + 前端）、R3 对象搜索 / R4 对象详情（通用化，读真 `/meta`、`/facets`）、连不上后台回退 mock。

| 项 | 还差什么 | 接口 / 文件 |
|---|---|---|
| 流式回答 | 后端 W7 未实现，前端同步等 W6 的 `turn` | W7 `GET /turns/{tid}/stream`；`useQaSession.ts` `sendReal` |
| T11 评测任务 | 进程内 asyncio，重启丢任务；每日定时未接；评测不带会话口径 | `eval/job.py` |
| 行级范围 | row-scopes 接口未上 master：工作台「范围」默认值靠前端推断；`router.py:239` 新建会话话术写死「大区」 | `router.py`、`Workbench.vue` |
| 按一批对象提问 / 绑定 | O4 和物理绑定只有 store / product / order，其他对象类型 `bound=false` | `seeds/extra/qa/objects/bindings.json`、`objects_api.py` |
| 动作执行 | 建议动作只到 stage，执行未接 | X1 `action-runs`；`Workbench.vue` `runAction` |
| 关键词分面 | E1 顺带的过渡分面要保留到静态 `/facets` 支持关键词 | `explore_api.py` `_facets` |
| 静态分面回落 | `objMeta.ts:156` `loadStaticFacets` 任何错误都回落（含 422），应只在 `isBackendDown` 时回落 | `src/pages/objects/objMeta.ts` |
| 复用静态私有函数 | qa 直接 import 静态 `_bad` / `_scalar`，等静态公开后改名 | `explore_api.py` |
| `Bound.check` | 按 `what` 字符串区分筛选 / 排序，应改成显式参数 | `explore_api.py` |
| 并发 | 页面 P2、会话 S 的 id 生成没锁，并发新建会撞 id 报 500 | `service.py` / `pages` 相关 |
| 我的页面 | 计数单独多一次请求；删除后停在空的后一页，应回退一页 | `MyPages.vue` |
| 注释 | `PageDrawer.vue:11` 注释过时 | `src/pages/qa/pages/PageDrawer.vue` |
| FeedbackTab | `FeedbackTab.vue` 的改动在 qa 允许路径外，待 PDE 确认 | `FeedbackTab.vue` |
| 评测分 | 76 / 100，拒答 9/16、反问 5/10 偏弱（见 2.1） | `eval/results/summary.json` |
| 指标口径变体 | 静态按指标名的（…）后缀推出变体，静态会改成显式字段；qa 按标签匹配，届时核对 | `service.py` `variant_index` |
| 筛选/关键词含 NUL | 值或关键词带 `\u0000` 时 search/export 仍 500（PostgreSQL 拒绝）；应与静态 `_scalar` 一起拦成 422（动态评审非阻塞 ①） | `explore_api.py` |
| 排序键前缀 | 排序键用 `lstrip("-")`，`--key` 也能过；改成只去一个 `-`（动态评审非阻塞 ②） | `explore_api.py` |

### 2.1 链路未完成 / 待裁定
- **最近评测分（真 qwen-flash，`740a554`）**：**76 / 100**，见 `backend/seeds/extra/qa/eval/results/summary.json`（作答 62/74、拒答 9/16、反问 5/10；失败 24 题：EV-Q-012、014、016、022、025、026、042、051、052、058、061、063、066、067、069、071、072、078、080、081、082、084、091、095）。
- **评测工具**：已入库到 `backend/app/modules/qa/eval/`（`/workspace/qa_eval` 是旧的本机副本，可删）。T11 用的是 `eval/job.py`（种子评测题 `qa_eval_question`），离线 100 题工具 `eval/run.py` 不变。
- **链路行为问题（找 qa-reuse）**：
  1. EV-Q-080「华南大区上个月的有效订单数」有时返回反问（「华南大区不存在…是否查询华东」），有时拒答 `OUT_OF_SCOPE`；题库要求拒答且 `applyEntry: true`，但前端只给 `NO_PERMISSION` 申请入口 —— 契约冲突，待定 `OUT_OF_SCOPE` 是否给申请入口。
  2. EV-Q-066「Show me the sales」不反问，直接答销售额（137009.54）。
  3. 只缺时间时 qwen-flash 仍常反问时间（违反「只缺时间不问」）。
  4. 偶发生成 SQL 被网关拒 → 403 `SQL_REJECTED`，前端提示「提问失败」，问题不落库（如「昨天的毛利率」一次 403、重问得 NO_DATA）。
  5. 无多轮上下文：追问依赖前端拼句。
- **静态本体 / 指标定义待确认**（详见 `qa-eval.md` 待定表）：
  1. 不含税销售额 = 行含税 ÷ 1.13、订单级优惠不分摊 —— qa 已裁定写进 `caliber/notes.json`，静态指标定义页没改，待指标负责人确认。
  2. `store.is_test`：口径写了「门店.是否测试店 = 否」，前端门店属性没有该字段（种子全为否）。
  3. 退款种子缺失，gmv / gmv_tax 退款项按 0；退款率题拒答。
  4. 含税销售额的时间字段与默认过滤（同 gmv？）。
  5. 出库及时率「应出库订单」定义（暂按状态 ∉ {待支付, 已取消}），用 `shipped_at` 代替状态变更时刻（owner 周晓峰）。
  6. `store.big_region` 列名和取值（种子只有「华东」）。
  7. 外卖渠道范围；单量 / 几单 / 卖了多少钱 / 卖得最好等口语说法进 semantic-map。
  8. **种子指标定义页 `def_rows` 是模板照抄**：所有指标共用同一份定义页（基础对象 / 时间字段 / 默认过滤 / 同名默认都一样），要静态 owner 按指标分别填。
  9. **有效订单数：默认过滤与公式冲突**：定义页「默认过滤 已出库」vs 公式「状态 ≠ 已取消」；链路暂以公式为准（qa 裁定写在 `caliber/notes.json`），待静态 owner 定。

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
6. **静态本体**：2.1 节 8 条口径问题找指标负责人 / 本体小白确认后，改 `caliber/notes.json` 和 `qa-eval.md` gold，重跑评测。

## 通用对象搜索评审修复（动态 / 静态，10-26）

- explore_api 筛选值按属性逐个校验，直接复用静态 `explorer._scalar / _bad / facet_kind / facetable`：枚举类只收标量数组（未知枚举值 422），区间类只收 `{min,max}`（数值走列范围检查，timestamp 只给日期上界含当天），不合法一律 422 `VALIDATION_FAILED`，`details.fields[0].field = filters.<key>`（排序是 `sort`）。
- 敏感 / 隐藏属性后台也拦：筛选键须 facetable，排序键须 sortable 且不敏感不隐藏；搜索结果、详情、`boundProps`、导出都不带；关键词只匹配可搜索且可见的属性，没有就忽略关键词（不再拿第一列兜底）。
- 前端：后台不通时导出直接报错，不导出演示数据。
- 未做：`router.py` 新建会话的系统话术写死「按 大区 = …」（应换成范围维度名，待 row-scopes 维度上线再改）[推断]；`objMeta.ts loadStaticFacets` 任何错误都回落过渡分面（422 也回落），可改成只在 `isBackendDown` 时回落。
