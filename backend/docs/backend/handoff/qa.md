# 问数（qa）交接

> 分支 `qa-joint`（本地，未推送）。基线 = merge `6cfe63f`（qa-reuse `a35d3b6` + pde-integ `215819d`，两条链哈希都是原提交）。截至 2026-10-26 01:15（Asia/Shanghai）。
> 接口契约：`docs/backend/qa-api.md`（各节「联调实现」段）；评测：`docs/backend/qa-eval.md`。[推断] 标的是前端/文档没写、这里自己定的规则。
> 前端开关：`VITE_API_REAL_MODULES` 含 `qa`（动作卡还需 `dynamic`）才走真接口，否则全部 mock。没接完的页面 / 页签在真接口模式下也仍走 mock，测试和构建不受影响。

## 1. 已完成

### 1.1 页面联调（真后端 + 真 qwen-flash 端到端验过，Playwright 脚本见第 3 节）
- **R1 问数工作台 `/use/qa/workbench`（除 SSE 外）**：W1 会话列表（搜索 / 时间筛选 / 服务端分页）、W2 新建、W3 切会话、W4 重命名 / 改范围口径（追加系统轮次）、W5 删除、W6 提问（同步 202 结果）、W9 口径重算、W10 门店表 / 链路表格翻页、W11 导出 xlsx/csv、W12 赞踩（踩 → 生成反馈）、W13 常用问题、W15 移出常用、W16 申请权限（刷新保留「已申请 · 待审批」）、P1 我的页面抽屉（只读列表）、X1/X2/X2b 建议动作确认卡（动态 action-runs stage / confirm / withdraw）。
- **反问卡 / 拒答（qa-reuse a35d3b6 新链路）**：`answer.card='clarify'` 读 `answer.clarify` 渲染选项，点选项发一条自包含追问（EV-Q-064 / 065 验过，「哪个门店表现最好？」+「销售额最高」→「哪个门店销售额最高？」）；刷新后按后续轮次反推已选项。`kind=denied` 按 `code` 渲染（锁图标 + tag），「申请权限」只给 `NO_PERMISSION`（NO_DATA / NOT_SELECT 验过无入口；手机号、关行级权限两题验过有入口且 W16 通过）。
- **R5 问数调优 · 认证问答页签**：T3 列表、T4 抽屉、T5 新建、T6 认证（有未发布 CR → 待重新认证）、T7 启停、T8 同问法；T1 只取待重新认证 / 未认证计数。
- **R5 · 高频问题页签**：T18 列表、T19 一键认证（已关联认证 → 409）。
- 后端：`backend/app/modules/qa/{router,service,repo,schemas}.py` 对应实现，测试 `backend/tests/test_qa*.py`。最近一次全量检查（01:11，反问 / 拒答提交前）：pytest 403 passed / 1 skipped，ruff check + format、alembic check、`npm run build` 通过。

### 1.2 链路（qa-reuse，`a35d3b6`；chain.py / llm.py / test_qa_chain.py 归 qa-reuse，本分支未改）
- **LLM 调用**：`llm.py` 用 openai SDK（`openai>=1.50`）的 `AsyncOpenAI` 调 DashScope 兼容接口（qwen-flash）；密钥只从环境变量读（本机 `/home/box/.qa_dashscope.env`，不进仓库）；CI 用 FakeLLM。
- **Prompt**：`chain.py` 照 Vanna `get_sql_prompt` 结构（初始说明 + ===Tables + ===Additional Context + ===Response Guidelines）和 `extract_sql` 围栏规则（MIT）；DDL 用 SQLAlchemy 把 ORM 编译成 CREATE TABLE + COMMENT，只给业务列；PG 方言规则、Style 段、样例值、「Today's date」摘自 NVIDIA auto-ontology（Apache-2.0，本地 `/workspace/tmp/auto-ontology`，未入库）。
- **SQL 安全**：sqlglot AST 校验单条 SELECT（`app/common/sql_guard`）、外包 `LIMIT`、行级过滤注入；执行报错回喂一次（`FIX_RETRIES=1`）；`statement_timeout` → 504 `QUERY_TIMEOUT`；网关拒绝 → 403 `SQL_REJECTED`（不重试、不落轮次）。
- **意图三选一**（同一次 LLM 调用）：SQL / 反问 JSON / 拒答 JSON，形状见 `qa-api.md` §2.3。
- **口径数据**：`backend/seeds/extra/qa/caliber/notes.json`（销售额 = 行含税 ÷ 1.13、按出库时间、剔除测试单；有效订单数 / 单量 = 状态 ≠ 已取消、按出库时间；含税销售额、订单数、销量、出库及时率、优惠金额…）、`refuse.json`（拒答清单 → code）。
- **评测**：题库 `backend/seeds/extra/qa/eval/questions.json`（100 题，v0.3，说明 `docs/backend/qa-eval.md`）。

## 2. 未完成

| 范围 | 状态 | 还差什么 | 接口 / 文件 |
|---|---|---|---|
| R1 流式回答 | 未做 | 前端 W6 同步等 202 里的 `turn`，没接 SSE；后端 W7 未实现流 | W7 `GET /turns/{tid}/stream`（`qa-api.md` §3）；`src/pages/qa/useQaSession.ts` `sendReal`、`QaAnswer.vue` / `QaStoreAnswer.vue` |
| R1 固定到页面 | mock | 弹窗 `QaPinModal` 仍写死卡名、不调后端 | P2 `POST /pages`、P7 `POST /pages/{pid}/cards`；`src/pages/qa/Workbench.vue`（`QaPinModal`） |
| R1 追问建议条 | mock | 输入框上方追问 chip 是写死的 3 条，没读 `answer.suggest.followUps` | `src/pages/qa/components/QaComposer.vue`（`followUps`） |
| R1 会话选项 | 未接 | 范围 / 口径下拉仍用前端常量 | W17 `GET /session-options`（`qa.ts` 已有 `getSessionOptions`，未调用） |
| R1 建议动作对象 | 部分 | 动作卡对象固定为客户群 `SEG-EC-60D`、参数是卡片文案；影响 / 预算 / 提交条件三行仍是前端文案（stage 无返回）[推断] | X1 `action-runs:stage`；`Workbench.vue` `runAction` |
| R1 反问追问 | 部分 | 链路无多轮上下文，追问句由前端启发式拼接 [推断]；所选项不回写 `clarify.chosen` [推断] | `QaConversation.vue` `followUp` / `chosenOf` |
| R1 第二期 | 未做 | 停止生成、加入常用 | W8 `:cancel`、W14 `POST /favorites` |
| R5 问数调优（整页**不算完成**） | 部分 | 评测题库页签（T9 运行列表、T10 运行详情、T11 发起评测、T12 题目）、反馈与纠错页签（T13 列表、T14 详情、T15 转同义词 → 静态 X3 变更单、T16 转认证问答、T17 忽略 / 重开）全部仍是 mock；页签角标里评测、反馈计数是 mock；新建认证问答的计划选项没接 T2；T20 同义词未接 | `src/pages/ontology/qa-tuning/EvalTab.vue`、`FeedbackTab.vue`、`QaTuning.vue`（计数）、`CertTab.vue`（T2）；后端 T2 / T9–T17 / T20 路由 |
| R2 我的页面与模板库 `/use/qa/pages` | 未开始，mock | 全部 12 个接口 | P1–P12；`src/pages/qa/MyPages.vue`、`src/pages/qa/pages/*` |
| R3 对象搜索 `/use/objects/search` | 未开始，mock | 类型、搜索、导出、带范围提问 | O1–O4；`src/pages/objects/ObjectSearch.vue` |
| R4 对象详情 `/use/objects/detail` | 未开始，mock | 概览、关联、时间线、可执行动作、对它提问 | O5–O7、O9、O4、X7；`src/pages/objects/ObjectDetail.vue`、`parts/ObjAskModal.vue` |
| W11 下载 | 临时 | `apiClient` 没有拿 blob 的方法，`src/api/qa.ts` 里留了一个只做 GET 下载的小工具 | 等 PDE 在 `src/api/http.ts` 加下载方法后改回 |

### 2.1 链路未完成 / 待裁定
- **最近评测分（真 qwen-flash，`/workspace/qa_eval`，未入库）**：`a35d3b6` 之后 qa-reuse 又有 `a39b314`（sqlglot 口径校验 + 一次纠正重试、收紧反问规则，**本分支未包含**）；01:05–01:07 两组结果 `result_all_v5A/B` 75 / 76 分、`result_all_finA2/B2` 74 / 71 分（满分 100；失败主要在作答 13–16、拒答 7–8、反问 4–5）。00:44 的 `result_final_1/2` 是 39 分（意图三选一之前）。结果文件没有记链路版本，以 qa-reuse 报告为准。
- **评测工具未入库**：`/workspace/qa_eval/run_eval.py`（跑题、比对结果集、按 expectedType 判分）、`setup_db.py`（建 `rengu_qa_eval` 库）、`ctx.py`、`fails.txt`、各轮 `result_*.json`。要进 CI 需挪到 `backend/tests/` 或 `backend/scripts/`，FakeLLM 模式下只跑判分逻辑。
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
  8. 种子里所有指标共用同一份定义页（像模板照抄）；有效订单数「默认过滤 已出库」与公式「状态 ≠ 已取消」冲突，链路规则暂以公式为准。

## 3. 代码在哪
- 前端：`src/api/qa.ts`（全部 qa 接口 + 动态 X1/X2）；`src/pages/qa/Workbench.vue`、`useQaSession.ts`、`components/QaConversation.vue`（轮次渲染、反问追问、拒答申请入口）、`components/QaAnswer.vue`（主卡 + 反问卡）、`QaStoreAnswer.vue`、`QaListDrawer.vue`、`QaSessionPanel.vue`；`src/pages/ontology/QaTuning.vue`、`qa-tuning/CertTab.vue`、`FreqTab.vue`（已接）、`EvalTab.vue`、`FeedbackTab.vue`（mock）。
- 后端：`backend/app/modules/qa/`（`router.py` 路由、`service.py` 业务、`repo.py` 查询、`schemas.py`、`models.py`、`catalog.py`；`chain.py` / `llm.py` 链路归 qa-reuse）；迁移 `backend/migrations/versions/qa_*.py`；种子 `backend/app/modules/qa/seeds`、`backend/seeds/extra/qa/{caliber,eval}/`；测试 `backend/tests/test_qa*.py`。
- 文档：`docs/backend/qa-api.md`（§2.1 / §7 联调实现、§12 [推断] 清单）、`docs/backend/qa-eval.md`。
- 提交（`6cfe63f..qa-joint`）：`d312a7f` 工作台 W2/W4/W5/W15、`34bd07a` W16 + app store 版本号、`cb1cec0` W9–W12 / X1 / 常用、`e89b681` qa.ts 改用 apiClient patch/put/delete、`76ce23f` 问数调优（部分：认证、高频）、之后一笔反问卡 / 拒答 + 本交接文档。
- 本机联调脚本（未入库）：`/tmp/qaj/be.sh`（起后端 8061，读密钥 env）、`checks.sh`（pytest / ruff / alembic / build）、`e2e4.py`（工作台回归）、`e2e6.py` / `e2e7.py` / `e2e8.py`（反问、拒答、申请、刷新）。

## 4. 下一步怎么接
1. **合入顺序**：先合 qa-reuse 最新（`a39b314` 起），再把 `qa-joint` 变基上去（只动 `backend/app/modules/qa/` 非链路文件和前端，冲突面小）；合入前跑 `RENGU_TEST_DB=<独立库> uv run pytest`、`ruff check/format --check`、`alembic check`、`npm run build`。
2. **问数调优补完**：后端按 `qa-api.md` §7 实现 T2、T9–T17、T20（T11 可复用 `/workspace/qa_eval/run_eval.py` 的判分逻辑，异步跑完写 `qa_eval_run`；T15 走静态 X3 变更单），前端在 `EvalTab.vue` / `FeedbackTab.vue` 按 `CertTab.vue` 的 `qaReal()` 分支模式接，页签计数改读 T1。整页接完再标完成。
3. **SSE W7**：后端把 chain 的阶段产出成事件（§3.1），前端 `sendReal` 改为 W6 拿 `streamUrl` 后订阅；同步 202 保留作降级。
4. **我的页面 R2、对象页 R3/R4**：按 §4–§6 的接口表逐个接，接口函数加进 `src/api/qa.ts`，页面用 `qaReal()` 切换，未接完的保持 mock。
5. **链路**：EV-Q-080 的 `OUT_OF_SCOPE` 申请入口与 qa-reuse / PDE 定；若链路加多轮上下文，前端 `followUp` 可直接发选项 + 回写 `clarify.chosen`。评测工具入库后接进 T11。
6. **静态本体**：2.1 节 8 条口径问题找指标负责人 / 本体小白确认后，改 `caliber/notes.json` 和 `qa-eval.md` gold，重跑评测。
