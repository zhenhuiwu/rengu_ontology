# 问数（qa）交接

> 分支 `qa-joint`（本地，未推送）。基线 = merge `80ac80c`（qa-reuse 最终 `740a554` + pde-integ `215819d`，两条链都是原提交，哈希一致）；整段 = `b688d5d..qa-joint`。截至 2026-10-26 01:25（Asia/Shanghai）。
> **qa-tuning 增量**（分支 `qa-tuning`，基线 master `bb47a5c`，本地未推送，截至 2026-10-26 Asia/Shanghai）：R5 问数调优评测题库 / 反馈与纠错页签接真后端，见 1.3；T15 新建变更单分支等 static-r3。
> 接口契约：`docs/backend/qa-api.md`（各节「联调实现」段）；评测：`docs/backend/qa-eval.md`。[推断] 标的是前端/文档没写、这里自己定的规则。
> 前端开关：`VITE_API_REAL_MODULES` 含 `qa`（动作卡还需 `dynamic`）才走真接口，否则全部 mock。没接完的页面 / 页签在真接口模式下也仍走 mock，测试和构建不受影响。

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
- **后端**：T11 `POST /tuning/eval/runs`（202，进程内 asyncio 后台任务 `app/modules/qa/eval/job.py`，复用 `chain.answer` 在只读回滚事务里逐题跑、按回答形状判分写 `qa_eval_fail` / `last_result`；真 qwen-flash 冒烟 2 题通过链路）；T15 `:to-synonym`（只做“词已被变更单覆盖”的关联分支，没覆盖 → 409 `CR_API_PENDING`）；T16 `:to-cert`（加同问法 / 新建并认证）；T17 `:ignore` / `:reopen`；T13 列表返回 T14 详情字段。规则与 [推断] 见 `qa-api.md` §7「联调实现」。无新依赖、无迁移。测试 `backend/tests/test_qa_tuning_eval.py`（FakeLLM / runner 打桩）。
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

| 范围 | 状态 | 还差什么 | 接口 / 文件 |
|---|---|---|---|
| R1 流式回答 | 未做 | 前端 W6 同步等 202 里的 `turn`，没接 SSE；后端 W7 未实现流 | W7 `GET /turns/{tid}/stream`（`qa-api.md` §3）；`src/pages/qa/useQaSession.ts` `sendReal`、`QaAnswer.vue` / `QaStoreAnswer.vue` |
| R1 固定到页面 | mock | 弹窗 `QaPinModal` 仍写死卡名、页面列表取 mock、不调后端；后端 P2 / P7 未实现；固定后的卡要在「我的页面」显示，而 R2 仍是 mock，所以单接这一步也不算完成 | P2 `POST /pages`、P7 `POST /pages/{pid}/cards`；`src/pages/qa/Workbench.vue`（`QaPinModal`） |
| R1 建议动作对象 | 部分 | 动作卡对象固定为客户群 `SEG-EC-60D`、参数是卡片文案；影响 / 预算 / 提交条件三行仍是前端文案（stage 无返回）[推断] | X1 `action-runs:stage`；`Workbench.vue` `runAction` |
| R1 反问追问 | 部分 | 链路无多轮上下文，追问句由前端启发式拼接 [推断]；所选项不回写 `clarify.chosen` [推断] | `QaConversation.vue` `followUp` / `chosenOf` |
| R1 第二期 | 未做 | 停止生成、加入常用 | W8 `:cancel`、W14 `POST /favorites` |
| R5 问数调优 · T15 新建 / 加入变更单 | 部分（等 static-r3） | 词未被任何变更单覆盖时要调静态 X3 写接口建 / 加入变更单草稿，static-r3 未合入，后端返回 409 `CR_API_PENDING`、前端提示；接法：`router.py feedback_to_synonym` 里 `SynonymCrPending` 处改调 X3（格式见 `handoff/static.md`），再写 `qa_tuned_synonym` 并按“已覆盖”分支改同词反馈；前端 `FeedbackTab.vue toSynonym` 的 live 分支改为打开发起变更窗（mock 的 `onStartChange` 已有 UI） | `router.py`、`FeedbackTab.vue` |
| R5 · T11 后台任务 | 部分 | 进程内 asyncio，重启丢任务（运行停在「运行中」）；判分是启发式（`时间解析错误` 判不出）[推断]；每日 02:00 定时未接 | `eval/job.py`；接 procrastinate 时包 `_background` |
| R2 我的页面与模板库 `/use/qa/pages` | 未开始，mock | 前端未接；后端只有读接口 P1 / P3 / P11 / P12，写接口 P2、P4–P10 未实现 | P1–P12；`src/pages/qa/MyPages.vue`、`src/pages/qa/pages/*` |
| R3 对象搜索 `/use/objects/search` | 未开始，mock | 前端未接；后端只有 O1 类型列表，O2–O4 未实现 | O1–O4；`src/pages/objects/ObjectSearch.vue` |
| R4 对象详情 `/use/objects/detail` | 未开始，mock | 前端未接；后端 O5–O7、O9 未实现（动作记录走动态 X7） | O5–O7、O9、O4、X7；`src/pages/objects/ObjectDetail.vue`、`parts/ObjAskModal.vue` |
| W11 下载 | 临时 | `apiClient` 没有拿 blob 的方法，`src/api/qa.ts` 里留了一个只做 GET 下载的小工具 | 等 PDE 在 `src/api/http.ts` 加下载方法后改回 |
| 连不上后台回退 mock（剩余） | 部分 | 已覆盖见 1.1。没覆盖：工作台的 W12 投票 / W9 重算 / W10 门店表 / W11 导出等卡片内读接口（`QaAnswer.vue` / `QaStoreAnswer.vue`，回退后卡片本来就是 mock 不再发请求，只有回退前已渲染的真卡片会照旧报错）；问数调优评测 / 反馈页签已接回退（qa-tuning）。`backend.down` 是全站共用的：别的模块先判定后台没起来，问数页也会直接走 mock。 |

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
