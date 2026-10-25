# 智能问数：后台接口、操作流程与种子数据（v0.1 草案）

> 依据：`/workspace/ui-vue` master（`f54db65`）的前端代码与 mock，只读，没有改源码。
> 范围：问数工作台、我的页面与模板库、对象搜索、对象详情（含「带范围提问」浮层）、问数调优（4 个页签），共 **5 个路由**。
> 编号：页面 R1–R5；接口 W(工作台)/P(页面)/O(对象)/T(调优)/X(外部模块)。
> 标注：**[推断]** 表示代码里没有直接体现，是按页面行为推出来的；**[第二期]** 表示前端显示「暂未开放」。
> 前缀统一用 `/api/qa/v1`，跟 `static-api.md` 的 `/api/ontology/v1` 同一种写法。动作、变更单、用户和权限这几块归别的模块，只写调用关系，见第 8 节。
> 技术栈按 `/workspace/ontology-xiaobai/Python技术栈调研.md`：FastAPI、PostgreSQL + pgvector、口径编译器 + sqlglot，行级条件在编译后、执行前注入。
> **总原则：后台跟前端交互一一对应。** 每个按钮都对应第 7 节里的一个接口和一个状态变化。确认卡上的数字、悬停原因、置灰原因都由后台返回，前端不再自己算。

---

## 0. 页面与接口总览

| 页 | 路由 | 前端文件 | 一句话 | 接口数 |
|---|---|---|---|---|
| R1 | `/use/qa/workbench` | `pages/qa/Workbench.vue` + `components/*` + `useQaSession.ts` | 会话式问数：流式回答、澄清、权限拦截、赞踩、固定到页面、对右栏对象发起动作 | 16 |
| R2 | `/use/qa/pages` | `pages/qa/MyPages.vue` + `pages/*`（PageDrawer、TemplateDrawer、UseTemplatePop、CardPreview） | 把回答卡片整理成页面：定时刷新、订阅、共享；从模板新建 | 12 |
| R3 | `/use/objects/search` | `pages/objects/ObjectSearch.vue` | 按类型、关键词、分面查对象，导出，用这批对象提问 | 4 |
| R4 | `/use/objects/detail` | `pages/objects/ObjectDetail.vue`、`parts/ObjAskModal.vue` | 对象概览、关联、时间线、动作记录、可执行动作，对它提问 | 5 |
| R5 | `/build/ontology/qa-tuning` | `pages/ontology/QaTuning.vue` + `qa-tuning/*`（CertTab、EvalTab、FeedbackTab、FreqTab、state.ts） | 认证问答、评测题库、反馈与纠错、高频问题推荐 | 20 |
| | | | **合计** | **57**（另外调用其他模块 5 个，见第 8 节） |

---

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 分页 | `page`（从 1 开始）、`pageSize`；响应 `{items, total, page, pageSize}`。前端 simple 分页（‹ 1/N ›）也要返回 `total`。筛选或搜索变化后前端回到第 1 页。默认条数见各接口。 |
| 时间 | ISO 8601，带 `+08:00`。mock 里的 `10-25`、`今天` 这类显示格式由前端自己格式化。 |
| 人员 | 返回 `xxxId` 加显示名（如 `ownerId`、`owner`）。mock 里只有姓名（`王敏`），种子数据按姓名映射到用户表（第 9 节）。 |
| 枚举 | 存英文码，响应里同时带中文 `label`，前端照旧显示中文。对照表见 1.3。 |
| 语义计划 `SemPlan` | `{metric, dims[], time, filter?, sort?}`，跟 `mock/qa-tuning.ts` 的 `SemPlan` 一致。回答依据、认证问答、评测期望、高频问题、页面卡片**共用这一个结构**；`planText` 的拼接规则搬到后台，响应带 `planText`。 |
| 指标引用 | 统一写 `metric@version`（如 `repeat_sales@v3`），响应带 `metricLabel`（`复购客户销售额（不含税） v3`）。 |
| 错误体 | `{code, message, details}`；`message` 可以直接用作悬停文字或确认卡上的阻断原因。 |
| 审计 | 提问、导出、动作、共享、权限申请，以及预览身份下的所有读写尝试，都写全局审计日志。 |

### 1.1 只读规则（两种，后台都要强制，不能只靠前端置灰）

| 状态 | 前端表现（`useReadonly` / `usePreview`） | 后台规则 |
|---|---|---|
| **历史版本** `?ver=v2026.10.1` | 所有写按钮置灰，悬停显示「正在查看历史版本 x，只读」。工作台的口径框显示「口径：按该版本默认」。已回答的消息保留各自的依据，不改写。 | 读接口带 `release=ver`。写接口只要带了非当前版本，就返回 `READONLY_HISTORY`。问数调优按该版本读认证问答和评测题 [推断]。 |
| **以此用户预览** `?previewAs=U010231` | 写按钮全部置灰（「正在以 XXX 的权限预览，只读」）。左侧导航只显示该用户有权限的页面。对象和卡片按该用户的行级范围显示。 | 请求头 `X-Preview-As: <userId>`，只有有「角色与权限 · 管理」权限的人能用。**数据身份 = 被预览人**（行级条件、页面可见性都按他算），**操作人 = 管理员**（写审计）。所有写接口返回 `READONLY_PREVIEW`。 |

**现在前端两处不一致，需要定一个口径**：工作台在预览时不能追问（`QaComposer` 发送置灰）；对象搜索和对象详情的「提问」在预览时可以用（`ObjAskModal`：「提问是只读操作」）。建议后台两种都支持：预览身份下提问，用被预览人的权限回答，**不落会话、不进高频统计**，再由用户定前端用哪种。

### 1.2 错误码

| code | HTTP | 前端表现 |
|---|---|---|
| `READONLY_HISTORY` / `READONLY_PREVIEW` | 403 | 悬停原因 |
| `OUT_OF_SCOPE` | 403 | 对象详情整页「XXX 无权查看门店「…」」；抽屉直接打开时被拦下并提示 |
| `NOT_FOUND` | 404 | 「没有找到对象 kind:id」 |
| `NAME_CONFLICT` | 409 | 「已有同名页面「…」」「已有认证问答「…」」（warning） |
| `CERT_NO_PLAN` | 422 | 「转为认证问答」置灰，悬停显示原因（还没有可用的语义计划 / 本体里还没有对应指标） |
| `ACTION_BLOCKED` | 409 | 动作「发起」置灰，悬停显示 `blocked` 原因 |
| `EXPORT_FORBIDDEN` | 403 | 共享页「允许导出」关闭，或处于预览 |
| `QUERY_TIMEOUT` / `DATASOURCE_DELAY` | 504/503 | 回答失败；页面「刷新失败」，保留上次结果 |
| `LLM_UNAVAILABLE` | 503 | 回答失败，可重试 |
| `VALIDATION` | 422 | 表单校验 |

**说明**：权限拦截、结果为空、需要澄清**不算 HTTP 错误**，是回答的一种结果状态（`answer.status`），见 3.2。

### 1.3 枚举对照（中文 = 前端显示）

| 枚举 | 码 → 中文 |
|---|---|
| 回答状态 `answer.status` | `answered` 正常、`clarify` 已按默认口径（带澄清）、`denied` 权限拦截、`empty` 结果为空、`failed` 失败 |
| 动作状态 | `ready` 发起、`pending_confirm` 待确认、`pending_approval` 待审批、`running` 执行中、`succeeded` 成功、`failed` 失败、`unknown` 结果未知 · 待对账 |
| 时间线 level | `normal` 灰、`warn` 橙、`unknown` 橙 + 问号、`error` 红（`done` = normal） |
| 页面状态 | `ok` 正常、`refresh_failed` 刷新失败、`caliber_pending` 口径待更新 |
| 共享方式 | `private` 仅自己、`members` 指定成员、`dept` 所在部门；成员权限 `view` 可查看、`edit` 可编辑 |
| 认证状态 | `pending` 待认证、`certified` 已认证、`recert` 待重新认证、`disabled` 已停用 |
| 评测运行状态 | `queued` 待运行、`running` 运行中、`done` 已完成 |
| 反馈状态 | `open` 待处理、`to_synonym` 已转同义词、`to_cert` 已转认证问答、`await_release` 待发布生效、`ignored` 已忽略 |
| 失败类型 | `metric_not_recalled` 未召回指标、`wrong_metric` 指标选错、`dim_mismatch` 维度不匹配、`time_parse` 时间解析错误、`missing_filter` 过滤条件缺失 |

---

## 2. R1 问数工作台 `/use/qa/workbench`

**用途**：用对话问数。左栏是会话，中间是对话和输入框，右栏是本轮引用的对象、它的状态时间线和可执行动作。

### 2.1 接口

| # | 方法 路径 | 入参 | 出参 / 规则 | mock 来源 |
|---|---|---|---|---|
| W1 | `GET /sessions` | `q`、`range=all\|today\|7d\|30d`、`page`、`pageSize=12` | `QaSession{id,title,date}`，按时间倒序 | `mock/qa.ts` `QA_SESSION_LIST` |
| W2 | `POST /sessions` | `{scope, caliber}` | 新会话 `title='新会话'`；第一问发出后，后台把标题设成问题的前 20 个字 | `useQaSession.newSession` |
| W3 | `GET /sessions/{sid}` | `before?`（游标）、`limit=20` | `{id,title,date,scope,caliber,ownerId, turns: Turn[], nextCursor}`；Turn 见 3.3 | `QaConversation.vue` 里写死的 4 轮 [需搬进 mock，见第 9 节] |
| W4 | `PATCH /sessions/{sid}` | `{title?}` / `{scope?, caliber?}` | 改范围或口径时，后台追加一条系统轮次「从这里开始按 大区 = X、口径 = Y 回答」，之前的答案保留各自的依据 | `Workbench.vue` `tips`、`QA_SCOPE_OPTIONS`、`QA_CALIBER_OPTIONS` |
| W5 | `DELETE /sessions/{sid}` | — | 204；已固定到页面的卡片不受影响（卡片存的是自己那份语义计划，不外键级联） | `QaSessionPanel.doDelete` |
| W6 | `POST /sessions/{sid}/turns` | `{question, refs?: ObjRef[], clientId}`（范围和口径取会话上的值） | 202 `{turnId, streamUrl}`。追问词、常用问题「提问」、「改查 10 月」都走这个接口 | `useQaSession.send` |
| W7 | `GET /turns/{tid}/stream` | `Last-Event-ID` | **SSE**，见 3.1 | `QaAnswer.vue`、`QaStoreAnswer.vue` |
| W8 | `POST /turns/{tid}/cancel` | — | 中止生成 [推断：前端还没有停止按钮] | — |
| W9 | `POST /turns/{tid}/recalc` | `{caliber: ex\|in\|both}` | 澄清条里切换「不含税 / 含税 / 两者对比」：用同一份语义计划只换指标，**同步返回** `Answer`（不流式），记成这一轮的一个变体 | `QaAnswer` `caliber` 单选 |
| W10 | `GET /turns/{tid}/result` | `page`、`pageSize=10` | `{columns, items, total, summary}`；超过 10 行才分页；`summary` 按**全部数据**算，不是只算当前页 | `QaStoreAnswer`（`QA_EC_STORES` 38 行） |
| W11 | `GET /turns/{tid}/export` | `format=xlsx\|csv` | 文件，导出全部行，写审计；预览时返回 `EXPORT_FORBIDDEN` | `QaStoreAnswer.onExport` |
| W12 | `PUT /turns/{tid}/vote` | `{vote: up\|down\|null, reason?}` | 赞和踩只能选一个，再点一次取消（传 `null`）。**踩**会在问数调优生成一条 `open` 反馈，取消踩时如果还没被处理就撤掉这条反馈 [推断] | `QaAnswer.onVote` |
| W13 | `GET /favorites` | `q`、`page`、`pageSize=10` | `QaFavorite{id,question,uses,lastUsed}`；左栏贴底取前 3 条 | `QA_FAVORITE_LIST` |
| W14 | `POST /favorites` | `{question}` | [推断：前端还没有「加入常用」的入口；`uses` 由提问时累加] | — |
| W15 | `DELETE /favorites/{id}` | — | 「移出」 | `QaListDrawer.remove` |
| W16 | `POST /permission-requests` | `{scope, metric, reason(≤200, 必填), duration: 30\|90\|180, sourceTurnId}` | `{id, status:'pending', approver}`；审批人按申请范围路由给数据负责人；这一轮改成「已申请 · 待审批」 | `QA_APPLY`、`QaApplyDrawer` |

右栏（当前对象摘要、状态时间线、可执行动作）用 R4 的 `O5/O7/O9`（`kind=region`）。抽屉 `?drawer=region:REG-EC` 是公共的 DetailDrawer，归 PDE。「我的页面 · 全部」抽屉和「固定到页面」下拉用 `P1 GET /pages?view=brief`；「固定」用 `P7`；「把会话整理成页面」跳到 `/use/qa/pages?newName=标题`，用 `P2` 新建。

**分享会话**：前端只复制链接 `?session=sid`。别人打开时用 W3，**按打开人自己的权限重新执行每一轮的语义计划**，超出他范围的轮次返回 `denied`；拥有者自己看时用快照 [推断]。预览期间「分享」置灰。

### 2.2 一次提问的流水线

```
W6 → nlu（实体链接、时间解析、召回本体和认证问答；命中认证问答就直接用它的语义计划）
   → intent（大模型填 SemPlan，用 JSON Schema 约束，校验不通过就修复）
   → 澄清判断（多口径时按默认口径回答，同时下发 clarify）
   → 权限预判（问题里明确的范围超出用户行级范围时：直接 denied，不执行，也不透露那个范围有没有数据）
   → compiler（SemPlan → sqlglot AST → 目标库方言）
   → security（行级谓词注入 AST；预览时用被预览人的谓词）
   → executor（只读账号 + 只允许 SELECT + 超时 + 行数上限）
   → 0 行时返回 empty，带解释，例如「该门店 2026-10-18 开业」；能给改写建议就带上
   → 解释（结论文字、KPI、图表、归因、引用对象、追问词、建议动作）
   → 写 qa_turn + query_log（给高频统计和一致率用）
```

---

## 3. 流式回答（SSE）

### 3.1 事件

`GET /turns/{tid}/stream`，`Content-Type: text/event-stream`。每个事件都带 `id`（序号），断线后用 `Last-Event-ID` 续传；回答完成后再连，就一次性回放全部事件。

| event | data | 前端渲染位置（`QaAnswer.vue`） |
|---|---|---|
| `status` | `{stage: understanding\|linking\|compiling\|permission\|executing\|explaining, text}` | 加载中的提示 [推断：前端还没有加载态] |
| `meta` | `{metricLabel, answeredVersion, dataAsOf, certifiedId?}` | 卡头那一行灰字「复购客户销售额（不含税） v3 · 按 v2026.10.3 回答 · 数据截至 …」 |
| `basis` | `{ontologyVersion, metric, filters:"大区 = 华东、客户分群 = 复购客户", dims:"省份", caliberCheck:"已出库、剔除退款；口径校验通过", permission:"已按行级权限过滤", plan: SemPlan}` | 「依据 ▾」展开后的表 |
| `clarify` | `{term:"销售额", chosen:"ex", options:[{value:"ex",label:"不含税"},{value:"in",label:"含税"},{value:"both",label:"两者对比"}]}` | 澄清条，切换选项调 W9 |
| `sql` | `{dialect, sql}` | **普通用户不下发**，只给有「问数调优」权限的人或调试模式用。路线 B 规定问数页不展示 SQL |
| `text` | `{delta}` 增量 / `{full}` 最终 | 结论段落 |
| `kpi` | `[{label:"销售额", value:"¥1,286.0 万"}, {label:"环比", value:"−8.4%"}, …]` | 左上的 descriptions |
| `chart` | `{id, title:"近 6 个月趋势", unit:"万元", kind: line\|bar, x:[…], series:[{name, data}], markLine?:{label:"省均", value}}` | 趋势图；「按省份拆分」的图表视图 |
| `table` | `{id, title:"按省份拆分", columns:[{key,title,unit?,align?}], items, total, summary, paged}` | 表格；`total > 10` 时 `paged=true`，只发第一页，翻页调 W10 |
| `insight` | `{title:"下降原因：…", items:[{name, value, delta}]}` | 「查看原因」弹层 [推断：归因放在这里] |
| `citations` | `{objects:[{kind,id,name,status}], metrics:[{id,version,label}]}` | 右栏「当前对象」（`本轮回答引用`） |
| `suggest` | `{followUps:["按门店等级再拆一下", …], actions:[{actionKey, target:{kind,id}, name}]}` | 输入框下的追问词（现在写死在 `QaComposer`）；「建议动作」按钮 |
| `denied` | `{asked:"大区 = 华南", allowed:"大区 = 华东", apply:{scope, object, approver, durations:[30,90,180]}}` | 「你问的华南大区不在你的权限范围内」+「权限拦截」标签 +「申请权限」 |
| `empty` | `{reason:"该门店 2026-10-18 开业，9 月尚无订单", rewrite?:"…改查 2026 年 10 月"}` | 「没有符合条件的记录（0 行）」+「结果为空」+「改查 10 月」（把改写填进输入框） |
| `error` | `{code, message, retryable}` | 失败提示 |
| `done` | `{turnId, status: answered\|clarify\|denied\|empty\|failed, elapsedMs}` | 结束 |

### 3.2 结果状态与界面

| status | 是否执行 SQL | 界面 | 可用操作 |
|---|---|---|---|
| `answered` / `clarify` | 是 | 完整回答卡 | 查看原因、建议动作、固定到页面、复制、赞踩、切换口径 |
| `denied` | 否 | 锁图标一行 | 申请权限 → 已申请 · 待审批 |
| `empty` | 是（0 行） | 收件箱图标一行 | 改查建议 |
| `failed` | 失败 | 错误 | 重试（就是再调一次 W6） [推断] |

### 3.3 Turn（W3 返回，也是落库的快照）

`{id, time, role: user|system|answer, question?, systemText?, answer?: {status, meta, basis, clarify, text, kpis, charts[], tables[], insight, citations, suggest, denied?, empty?, vote, permissionRequest?:{id,status}}}`。快照里存的是**回答时的结果**，查看历史版本时不改写。

---

## 4. R2 我的页面与模板库 `/use/qa/pages`

**用途**：管理固定下来的回答卡片页面（刷新、订阅、共享），以及从模板新建页面。

| # | 方法 路径 | 入参 | 出参 / 规则 | mock 来源 |
|---|---|---|---|---|
| P1 | `GET /pages` | `kw`、`sourceKind=workbench\|template\|manual`、`share=shared\|private`、`status`、`page`、`pageSize=15`、`view=brief\|full` | `QaPageFull` 列表（brief 只返回 `{id,name,cards,updatedAt,shared}`，即 `QaPage`）+ `fromWorkbenchCount`。只返回我的页面；预览时返回被预览人自己的页面加共享给他的 | `mock/qa-pages.ts` `MY_PAGES`、`OTHER_PAGES`；`mock/qa.ts` `QA_PAGE_LIST` |
| P2 | `POST /pages` | `{name(≤30), scope}`，或者 `{name, scope, template:{id, caliber, refresh}}` | 新建空白页面或从模板新建；从模板新建时 `status` 按卡片是否 `pending` 定为 `caliber_pending` 或 `ok`；重名返回 `NAME_CONFLICT` | `createBlank`、`fromTemplate` |
| P3 | `GET /pages/{pid}` | — | `QaPageFull`：`{id,name,owner,scope,source,sourceKind,cards:PageCard[],dataAsOf,lastRefresh,updatedAt,refresh,sub,share,status,statusNote,views,calibers[{label,pending}]}`。**卡片数据按查看人的权限裁剪**，范围外的卡片返回 `{denied:true}`，显示「无权限」 | `QaPageFull`、`PageCard` |
| P4 | `PATCH /pages/{pid}` | `{name}` | 重名返回 `NAME_CONFLICT` | `rename` |
| P5 | `DELETE /pages/{pid}` | — | 停掉定时刷新和订阅；被共享人从此打不开；问数会话里的原始回答不受影响。确认卡上的字段（卡片数、共享情况、订阅情况）用 P3 的返回 | `remove` |
| P6 | `POST /pages/{pid}/refresh` | — | 30 秒内同步返回 `{lastRefresh, dataAsOf, status, statusNote}`，超时返回 202 `{jobId}`。刷新成功后从「刷新失败」恢复成 `ok` 或 `caliber_pending` | `refresh` |
| P7 | `POST /pages/{pid}/cards` | `{turnId, tableId?/chartId?, title}`，或者 `pid=new` + `{name}` | 把这一轮回答的**语义计划和 `metric@version` 冻结**成卡片：「按当前依据定时刷新，口径和范围不随会话变化」 | `QaPinModal` |
| P8 | `PUT /pages/{pid}/cards` | `{order: cardKey[]}` | 没出现在 `order` 里的卡片就是被移除的；返回 `{cards, removed[]}` | `PageDrawer.saveCards` |
| P9 | `PUT /pages/{pid}/schedule` | `{refresh: RefreshPlan, sub: Subscription}` | 返回 `{refresh, sub, nextRun}`；`onFail=keep_last\|mark_failed_notify`；订阅渠道：邮件、企业微信、站内信 | `RefreshPlan`、`Subscription` |
| P10 | `PUT /pages/{pid}/share` | `{mode, members:[{userId, perm}], link, allowExport}` | 返回 `ShareSetting` 和 `linkUrl`；有新增可见人时，确认卡写「新增可见人：xx」 | `ShareSetting`、`saveShare` |
| P11 | `GET /templates` | `kw`、`scene`、`page`、`pageSize=15` | `QaTemplate{id,name,scene,cards,provider,uses,updatedAt,official,desc}` | `PAGE_TEMPLATES` |
| P12 | `GET /templates/{id}` | — | 带全部卡片，前端每页显示 6 张 | `TemplateDrawer` |

**卡片取数**：缓存 key = `(cardKey, metric@version, 权限指纹)`，权限指纹是查看人行级谓词的哈希。拥有者的定时刷新按拥有者的权限算；被共享人和订阅接收人**各按自己的权限**算 [推断：前端写的是「看到的数据按各自的数据权限裁剪」]。卡片引用的指标有未发布的 CR 时，`pending` 写 CR 说明，页面状态变成 `caliber_pending`，由静态模块的发布事件触发。

选接收人和成员用 PDE 的 `GET /users?kw`（第 8 节）。

---

## 5. R3 对象搜索 `/use/objects/search`

**用途**：选类型、输关键词、勾分面查对象，导出结果，或者用这批对象直接提问。

| # | 方法 路径 | 入参 | 出参 / 规则 | mock 来源 |
|---|---|---|---|---|
| O1 | `GET /objects/types` | — | `[{kind,label,unit,kwPlaceholder,facets:[{key,label,order?}],columns:[{key,title,num?,width?}]}]` | `mock/objects-qa.ts` `OBJ_TYPES` |
| O2 | `POST /objects/{kind}/search` | `{kw, facets:{region:["华东一区"],…}, page, pageSize=16}` | `{items:[{id,title,cells:{key:value}}], total, facets:[{key,label,vals:[{v,n}]}], scopeText}`。**分面计数** = 关键词和其他分面都生效后的数量，本分面不参与。行和计数都只含行级范围内的数据 | `ObjectSearch.facetValues`；`stores.ts`、`products.ts`、`orders.ts`、`OBJ_EXTRA_STORES` |
| O3 | `POST /objects/{kind}/export` | `{kw, facets, ids?}` | CSV（UTF-8 BOM）；传了 `ids` 就导出所选，否则导出当前筛选结果；预览时返回 `EXPORT_FORBIDDEN` | `exportCsv` |
| O4 | `POST /scoped-ask` | `{scope:{kind,label,filter?:{kw,facets}, ids?, single?:{kind,id,title}}, question}` | `AskAnswer{text, basis, rows:[{id,kind,name,value,note}], rowsTitle?}`。**范围只传筛选条件**，没勾选时不要传全部 id（订单有 1286 条）。预览时也能问，见 1.1。用的是 2.2 同一条流水线，结果是同步 JSON | `answerFor`、`ObjAskModal` |

---

## 6. R4 对象详情 `/use/objects/detail?kind&id&tab`

**用途**：看一个对象的属性、关键指标、关联对象、时间线、动作记录，对它发起动作或提问。问数工作台的右栏也用这里的接口。

| # | 方法 路径 | 入参 | 出参 / 规则 | mock 来源 |
|---|---|---|---|---|
| O5 | `GET /objects/{kind}/{id}` | — | `{kind,kindLabel,id,code,title,status, groups:[{title,fields:[{label,value}]}], kpis:[{label,value,caliber}], quickQuestions[], scopeLabel}`；没找到返回 `NOT_FOUND`，不在范围内返回 `OUT_OF_SCOPE`。大区对象额外返回 `owner,provinces,stores,sales,repeatShare,parent` | `objectRegistry.resolveObject`、`FIELD_GROUPS`、`caliberOf`、`storeExtraKpi`、`quickQuestions`；`REGION_EC` |
| O6 | `GET /objects/{kind}/{id}/related` | `group?`、`page`、`pageSize=8` | `[{key,title,total,items:[{kind,id,label,note,denied?}]}]`；每组各自分页；没权限的对象类型整组返回 `denied`，不给计数 | `relationGroups`、`QA_REGION_RELATED` |
| O7 | `GET /objects/{kind}/{id}/timeline` | `page`、`pageSize=8`（右栏取前 6 条） | `TlItem{time,text,desc,status}` / 右栏 `{level,title,time,desc}`；`unknown` 统一补一行「结果未知 · 待对账」 | `extraTimeline`、`QA_TIMELINE`、`QA_REGION_TIMELINE`、`QA_UNKNOWN_LABEL` |
| O8 | `GET /objects/{kind}/{id}/action-records` | `page`、`pageSize=14` | `ActRecord{id,action,by,approver,time,result}` | `actionRecords` |
| O9 | `GET /objects/{kind}/{id}/actions` | — | `ObjAction{key,name,desc,fields,impact,irreversible,blocked?,approver?, state, stateTip}`；`state` 见 1.3；`stateTip` 是悬停文字（如「王敏 10-25 15:36 提交 · 待 赵磊 审批」） | `objActions`；`QaObjectPanel` 写死的 3 个动作 |

点「发起」后，确认卡的内容和提交都走动态模块（第 8 节 X1、X2）。确认卡打开时这一行显示「待确认」；取消就恢复成「发起」；提交后变成「待审批」或「执行中」。「对它提问」用 O4（`single`）。

---

## 7. R5 问数调优 `/build/ontology/qa-tuning?tab=cert|eval|feedback|freq`

**用途**：维护认证问答，跑评测，处理用户的踩和纠错，把高频问题设成认证问答。

| # | 方法 路径 | 入参 | 出参 / 规则 | mock 来源 |
|---|---|---|---|---|
| T1 | `GET /tuning/summary` | `release?` | `{version, dataAsOf, recertCount, openFeedback, uncertFreq, evalTotal}`，即页签上的计数 | `QaTuning.vue` |
| T2 | `GET /tuning/plan-options` | — | `{metrics:[{key,name,version,cr?}], dims[], times[]}`；`cr` 是影响该指标的未发布 CR | `T_METRICS`、`DIMS`、`TIMES`（`QaTuning.vue`） |
| **认证问答** |||||
| T3 | `GET /tuning/certs` | `kw`（同时匹配同问法）、`status`、`metric`、`page`、`pageSize=15` | `CertQa{id,question,variants,plan,planText,status,cr?,certifier,certifiedAt,hits,source,note?}`；排序：待重新认证 › 待认证 › 已认证 › 已停用 | `CERT_QAS` |
| T4 | `GET /tuning/certs/{id}` | — | 抽屉内容 | `CertTab` |
| T5 | `POST /tuning/certs` | `{question(≤40), plan}` | **新建后是 `pending`（待认证）**，不会直接变成已认证；重复返回 `NAME_CONFLICT` | `createCert` |
| T6 | `POST /tuning/certs/{id}:certify` | — | 指标有未发布的 CR 时：按当前版本锁定，`status=recert`，`note` 写「CR 发布后需再次认证」；没有就是 `certified`。相同或相近的问题以后直接按它回答，并标注「已认证」 | `certify` |
| T7 | `PATCH /tuning/certs/{id}` | `{enabled: bool}` | 停用：回到常规理解流程；启用：恢复为 `certified` | `toggle` |
| T8 | `PUT /tuning/certs/{id}/variants` | `{variants: string[]}` | 加或删同问法；重复返回 `NAME_CONFLICT` | `addVariant`、`removeVariant` |
| **评测题库** |||||
| T9 | `GET /tuning/eval/runs` | `trigger`、`page`、`pageSize=15` | `EvalRun{id,time,trigger,version,scope,total,passed,passRate,failCount,status,operator,cr?,crs?,note?}` | `EVAL_RUNS` |
| T10 | `GET /tuning/eval/runs/{id}` | `page`、`pageSize=12` | 加上 `fails: FailCase{qid,question,expect,actual,kind}` 分页、`kindStats[{kind,n}]`；待运行的回放额外返回 `replayQuestions` 和每张 CR 的提示 | `FailCase`、`REPLAY_HINT` |
| T11 | `POST /tuning/eval/runs` | `{group: all\|销售\|退款\|…}` | 202，返回 `running` 状态的 EvalRun；后台任务（procrastinate）逐题生成语义计划，跟期望比对，**不改任何口径** | `startRun` |
| T12 | `GET /tuning/eval/questions` | `group`、`last=pass\|fail`、`kw`、`page`、`pageSize=15` | `EvalQ{id,question,group,expect,expectText,last,cr?}` + `groupCounts` | `EVAL_QS`、`EVAL_GROUPS` |
| **反馈与纠错** |||||
| T13 | `GET /tuning/feedbacks` | `kw`（问题或用户）、`status`、`fixKind=synonym\|cert`、`page`、`pageSize=15` | `Feedback{id,time,user,dept,question,answer,reason,fix:{kind,text,target?},status,resolvedBy?,handler?}`；`open` 排在前面 | `FEEDBACKS` |
| T14 | `GET /tuning/feedbacks/{id}` | — | 加上 `certBlock`（不能转认证问答的原因）、`existingCert?`、`coveredBy?`（已经覆盖这个词的同义词 CR）、`relatedEvalCount` | `certBlock`、`synCovered` |
| T15 | `POST /tuning/feedbacks/{id}:to-synonym` | `{join?: crId, title?}` | 已经被某张 CR 覆盖的：直接关联（CR 已通过 → `await_release`，否则 `to_synonym`）。没覆盖的：调静态模块建或加入变更单草稿（X3），**确认时才分配 CR 号**；**同一个词所有 `open` 的反馈一起改状态**；返回 `{crId, affected[]}` | `toSynonym`、`onStartChange` |
| T16 | `POST /tuning/feedbacks/{id}:to-cert` | — | 已经有同问题的认证问答：把用户原话加成它的同问法；没有：用找到的语义计划新建认证问答，原话也加成同问法。找不到语义计划返回 `CERT_NO_PLAN`；返回 `{certId, mode: new\|variant}` | `toCert`、`planFor` |
| T17 | `POST /tuning/feedbacks/{id}:ignore` / `:reopen` | — | `ignored` ⇄ `open`；重新打开时清空处理人和处理结果 | `ignore`、`reopen` |
| **高频问题推荐** |||||
| T18 | `GET /tuning/freq` | `period=7\|30`、`cert=yes\|no`、`kw`、`page`、`pageSize=15` | `FreqQ{id,rank,question,hits7,hits30,users,plan,planText,consistency,lastAsked,certId?,certStatus?}`；按所选周期的命中次数排名 | `FREQ_QS` |
| T19 | `POST /tuning/freq/{id}:certify` | — | 同 T6，`source='高频问题推荐'`；确认卡显示命中次数，一致率低于 70% 时加一句提醒 | `FreqTab.certify` |
| T20 | `GET /tuning/synonyms` | — | 已经走了变更单的同义词 `{word,target,cr}`，给 T14 判断是否已覆盖 | `state.ts` `tuning.synonyms` |

**高频统计 [推断]**：每天从 `query_log` 聚合。问题先归一，再用 pgvector 聚类近义问法。`consistency` = 这一类问题里最常见的那份语义计划占比。只统计正式身份的提问，不统计预览和评测的。

### 7.1 状态流转

```
反馈：踩(W12) → open 待处理
  open → to_synonym 已转同义词（CR 是草稿或审核中）→ await_release 待发布生效（CR 已通过）→ [待定：发布后的终态，第 10 节]
  open → to_cert 已转认证问答
  open → ignored 已忽略 → (reopen) open
认证问答：T5 新建 → pending → T6 → certified；指标有未发布 CR → recert；CR 发布 → recert（事件触发）→ T6 → certified
         certified ⇄ disabled（T7）
评测运行：每天 02:00、手动 T11、CR 审核通过后试跑、整版发布前回放、发布后回放 → queued → running → done
页面：ok ⇄ refresh_failed（保留上次结果）；指标有未发布 CR → caliber_pending → 发布后重算 → ok
```

---

## 8. 跟其他模块的接口（不算进 57 个）

| # | 归谁 | 接口 | 谁在用 |
|---|---|---|---|
| X1 | 动态 | `POST /actions/{actionKey}:preview` `{target}` → 确认卡字段（动作类型、参数、影响、预算、提交条件、审批人、写回、幂等键） | 工作台「建议动作」和右栏「发起」、对象详情「发起」 |
| X2 | 动态 | `POST /actions/submissions` `{actionKey, target, params, idempotencyKey}` → `{recordId, state}`；外部系统超时标记成「结果未知」，进对账，不自动重试 | 同上 |
| X3 | 静态 | `POST /change-requests`、`POST /change-requests/{id}/targets`、`GET /change-requests?status=draft` | T15 转同义词 |
| X4 | 静态 | 发布事件 `release.published{ver, crs}` | 认证问答改成待重新认证、页面口径待更新、评测回放、反馈变成待发布生效 |
| X5 | PDE | `GET /users?kw`、权限审批流、预览会话、审计日志 | P9/P10 选人，W16，1.1 |

---

## 9. 种子数据：把前端 mock 灌进数据库

**约定**：种子脚本放在 `backend/app/modules/qa/seeds/`，**直接从现有前端 mock 文件导入，不另编数据**。迁移跑完后执行统一的 `seed` 命令。

**怎么导入**（mock 是 TS，而且很多是函数生成、用固定随机种子 `seeded()` 算出来的）：
1. `frontend/scripts/dump-mock.ts`：用 esbuild 打包 `src/mock/*` 后用 node 执行，把需要的导出求值，写成 `backend/app/modules/qa/seeds/data/*.json`。我已经在本机验证过，`esbuild --bundle --platform=node --alias:@=./src` 可以直接跑。
2. `seeds/*.py` 读 JSON 写库：中文枚举按 1.3 转成码，人名按 `PEOPLE` / `PEOPLE_T` 映射到用户表，`metric` 显示名映射到静态模块的 `metric@version`。
3. 种子可以重复执行（按业务 id upsert）。dump 出的 JSON 进 git，前端 mock 改了之后重新 dump。

| 种子集 | 目标表 | 前端来源（文件 · 导出） | 条数（实测） | 备注 |
|---|---|---|---|---|
| sessions | `qa_session` | `mock/qa.ts` · `QA_SESSION_LIST` | 103 | 拥有者都是王敏 |
| demo_turns | `qa_turn`、`qa_turn_result` | **现在写死在 `QaConversation.vue`、`QaAnswer.vue`、`QaStoreAnswer.vue`**；数据部分来自 `qa.ts` · `QA_PROVINCES`(7)、`QA_TREND`、`QA_TOTAL_*`、`QA_EC_STORES`(38)、`QA_APPLY` | 第一个会话 4 轮 | ⚠ 文字、依据、KPI 这些都在 .vue 里。要做到「不另编数据」，得先把它们搬进 `mock/qa.ts`（`QA_DEMO_TURNS`），这是前端改动，本次没做 |
| favorites | `qa_favorite` | `qa.ts` · `QA_FAVORITE_LIST` | 18 | |
| scope_options | `qa_session_option` | `qa.ts` · `QA_SCOPE_OPTIONS`、`QA_CALIBER_OPTIONS`、`QA_METRIC` | 3 + 2 | 范围其实应该从用户的行级权限推出来 [推断] |
| permission_requests | `qa_permission_request`（或 PDE 的审批表） | `qa.ts` · `QA_APPLY` | 0（只有预填模板） | |
| pages | `qa_page`、`qa_page_card`、`qa_page_share_member`、`qa_page_subscription` | `mock/qa-pages.ts` · `MY_PAGES`、`OTHER_PAGES`（卡片来自 `CARD_LIB`） | 27 + 5 个页面 / 197 张卡片 | 前 13 个页面跟 `QA_PAGE_LIST` 是同一批 |
| templates | `qa_template`、`qa_template_card` | `qa-pages.ts` · `PAGE_TEMPLATES` | 22 / 104 | |
| people | → PDE `sys_user`（只做映射） | `qa-pages.ts` · `PEOPLE`、`OWNER_DEPT`；`qa-tuning.ts` · `PEOPLE_T` | 12 | |
| certs | `qa_cert`、`qa_cert_variant` | `mock/qa-tuning.ts` · `CERT_QAS` | 30 | |
| eval | `qa_eval_question`、`qa_eval_run`、`qa_eval_fail` | `qa-tuning.ts` · `EVAL_QS`、`EVAL_RUNS` | 40 / 21 / 222 | |
| feedbacks | `qa_feedback` | `qa-tuning.ts` · `FEEDBACKS` | 26 | |
| freq | `qa_freq_question`（物化统计） | `qa-tuning.ts` · `FREQ_QS` | 32 | 上线后改成从 `query_log` 聚合 |
| synonyms_link | `qa_synonym_link` | `pages/ontology/qa-tuning/state.ts` · `tuning.synonyms` | 2 | ⚠ 在 .vue 同级的 state.ts 里，不在 mock 目录 |
| tuning_metrics | → 静态 `metric`（只引用） | `qa-tuning.ts` · `T_METRICS`；`qa.ts` · `QA_METRIC` | 9 | ⚠ `repeat_sales` / `repeat_sales_tax` 还不在公共 `metrics.ts` 里 |
| object_views | `qa_object_type_view` | `mock/objects-qa.ts` · `OBJ_TYPES`（facets/columns）、`FIELD_GROUPS`、`KIND_LABEL`、`OBJ_SCOPE` | 3 类 | 函数型字段（`get`）在 dump 时转成 `{key, sourceProperty}` |
| biz_demo | 演示业务库 `biz_demo.store / product / order / store_sales` | `mock/stores.ts` · `ALL_STORES`；`objects-qa.ts` · `OBJ_EXTRA_STORES`、`storeSales`；`products.ts` · `PRODUCTS`；`orders.ts` · `ORDERS` | 门店 95（含扩充 57）/ 商品 12 / 订单 1286 | 这是问数要查的「业务数据」，不是元数据。达梦和金仓测试库也灌这一份 |
| object_timeline / actions | → 动态 `event`、`action_record`、`action_type` | `qa.ts` · `QA_TIMELINE`、`QA_REGION_TIMELINE`、`REGION_EC`；`objects-qa.ts` · `extraTimeline`、`actionRecords`、`objActions` | 6 / 26 / 按对象生成 | 由动态模块的种子负责，这里只核对 id 是否一致 |
| relations | → 静态 `link` + 业务库 | `objects-qa.ts` · `relationGroups`；`qa.ts` · `QA_REGION_RELATED` | 38 | |
| query_log | `qa_query_log` | 没有 mock | 0 | 高频统计和一致率靠它 |

---

## 10. 评审用：按钮 → 接口 → 状态变化

| 页面 | 按钮 / 交互 | 接口 | 状态变化 |
|---|---|---|---|
| 工作台 | 新建会话 | W2 | 左栏顶部多一条「新会话」，对话区显示空状态 |
| 工作台 | 搜索会话 / 时间筛选 / 翻页 | W1 | 回到第 1 页（翻页除外） |
| 工作台 | 会话 ⋯ → 重命名 | W4 `{title}` | 标题更新 |
| 工作台 | 会话 ⋯ → 删除（二次确认） | W5 | 会话消失，焦点移到第一条；卡片保留 |
| 工作台 | 发送 / Enter / 追问词 / 常用问题「提问」 | W6 + W7 | 追加一轮；第一问把会话标题设成问题的前 20 个字 |
| 工作台 | 修改范围或口径 | W4 `{scope,caliber}` | 插入一条系统提示，下一问开始生效 |
| 工作台 | 澄清条：不含税 / 含税 / 两者对比 | W9 | 结论和指标名切换，明细仍按不含税 |
| 工作台 | 依据 ▾ | 无（basis 已经在 SSE 里） | 展开依据表 |
| 工作台 | 查看原因 | 无（insight） | 弹层 |
| 工作台 | 表格 / 图表切换 | 无 | — |
| 工作台 | 结果表翻页 | W10 | `?tblPage=`，合计行不变 |
| 工作台 | 导出 | W11 | 下载文件，写审计 |
| 工作台 | 赞 / 踩 / 再点一次取消 | W12 | 踩：调优多一条「待处理」反馈 |
| 工作台 | 复制 | 无 | — |
| 工作台 | 固定到页面（已有页面 / 新建页面） | P1 brief + P7 | 页面卡片数 +1 |
| 工作台 | 建议动作 / 右栏「发起」 | X1 → 确认卡 → X2 | 待确认 →（提交）待审批；取消就恢复成「发起」 |
| 工作台 | 申请权限 → 提交申请 | W16 | 这一轮显示「已申请 · 待审批」 |
| 工作台 | 改查 10 月 | 无（把 `empty.rewrite` 填进输入框） | 输入框有内容 |
| 工作台 | 分享 → 复制链接 | 无（打开链接时用 W3，按打开人的权限） | — |
| 工作台 | 把会话整理成页面 | 跳转 → P2 | 我的页面打开新建气泡，名称已填好 |
| 工作台 | 常用问题「管理」→ 移出 | W13 / W15 | 从列表消失 |
| 工作台 | 打开对象详情 | O5/O6/O7（抽屉） | `?drawer=region:REG-EC` |
| 工作台 | 引用对象、⋯ | — | **[第二期]**，置灰 |
| 我的页面 | 新建页面 | P2 | 列表顶部多一行 |
| 我的页面 | 筛选 / 搜索 / 翻页 | P1 | — |
| 我的页面 | 行点击 → 抽屉 | P3 | `?page_id=` |
| 我的页面 | 刷新 / 立即刷新 | P6 | 数据截至时间更新；刷新失败 → 正常或口径待更新 |
| 我的页面 | 重命名 | P4 | 名称更新；重名给 warning |
| 我的页面 | 删除（确认卡，红色） | P5 | 行消失，抽屉关闭 |
| 我的页面 | 调整卡片 → 拖拽 / 移除 → 保存顺序 | P8 | 有移除时先弹确认卡 |
| 我的页面 | 刷新与订阅 → 保存 / 还原 | P9 / 无 | 下次刷新时间更新 |
| 我的页面 | 共享 → 添加成员 / 改权限 / 移除 / 链接 / 导出 → 保存共享设置 | P10 | 有新增可见人时弹确认卡 |
| 我的页面 | 共享 → 复制链接 | 无（`linkUrl`） | — |
| 我的页面 | 模板库：筛选 / 预览 | P11 / P12 | `?tpl=` |
| 我的页面 | 用此模板新建 | P2 `{template}` | 新页面出现，抽屉打开 |
| 对象搜索 | 换类型 / 搜索 / 勾分面 / 清除 / 翻页 | O1 / O2 | 换类型时清掉筛选；分面计数刷新 |
| 对象搜索 | 导出 / 导出所选 | O3 | 下载 CSV |
| 对象搜索 | 用这批对象提问 → 建议问题 / 发送 | O4 | 浮层里出现回答，`?ask=1` |
| 对象搜索 | 行点击 / 完整视图 | O5 | 打开抽屉 / 跳到详情 |
| 对象详情 | 页签切换、关联分组翻页、时间线翻页、记录翻页 | O5–O8 | — |
| 对象详情 | 可执行动作「发起」 | O9 + X1 + X2 | 待确认 → 待审批 / 执行中 |
| 对象详情 | 快捷问题 / 自己输入问题 | O4 `single` | 浮层里出现回答 |
| 问数调优 | 新建认证问答 → 认证 | T2 + T5 | 多一条「待认证」，打开它的抽屉 |
| 问数调优 | 运行评测（选范围 → 确认卡） | T11 | 运行记录多一条「运行中」，跳到 eval 页签 |
| 问数调优 | 认证 / 重新认证 | T6 | 已认证；如果 CR 还没发布，仍是待重新认证，并写说明 |
| 问数调优 | 停用 / 启用（确认卡） | T7 | 已停用 ⇄ 已认证 |
| 问数调优 | 抽屉：添加同问法 / 删除同问法 | T8 | 同问法标签增减 |
| 问数调优 | 运行记录行点击 → 失败用例翻页 | T10 | `?run=` |
| 问数调优 | 题目视图筛选 | T12 | — |
| 问数调优 | 转为同义词（发起变更弹窗：新建 / 加入已有） | T15（+X3） | 同一个词的待处理反馈一起变成已转同义词，或者待发布生效 |
| 问数调优 | 转为认证问答 | T16 | 已转认证问答；生成新的认证问答或同问法 |
| 问数调优 | 忽略 / 重新打开 | T17 | 已忽略 ⇄ 待处理 |
| 问数调优 | 高频：近 7 天 / 近 30 天、筛选 | T18 | 排名重算 |
| 问数调优 | 设为认证问答 | T19 | 「认证」列变成链接，点了跳到 cert 页签 |

---

## 11. 待定问题（只列不答）

1. **大模型部署（外部 API 还是本地 GPU）**：决定 `text` 是逐字流式还是模板拼接；`status` 各阶段的超时；外部模型时**只能发 schema 和元数据，不能发业务行数据**；pgvector 的向量维度和嵌入模型；评测一次「预计 3 分钟」能不能做到；并发上限。
2. **信创 / 元数据库**：元数据库如果用达梦或金仓，pgvector 和 procrastinate（只依赖 PG）都要换掉；国产中间件（如 TongWeb）前面的反向代理要关掉 SSE 缓冲；如果要求 Java 技术栈，FastAPI 方案要重新评估。
3. **是否对外分发**：分发涉及组件许可证；SSO 和多租户；`sql` 事件给不给客户的管理员看；离线安装包里要不要带种子数据。
4. **达梦 / 金仓测试库**：sqlglot 没有原生的达梦方言（只能走 Oracle 兼容模式），金仓要看是 PG 模式还是 Oracle 模式；行级谓词注入和导出要在两种库上各测一遍；`biz_demo` 种子要能灌进两种库。
5. 分享会话、共享页面、订阅都按**查看人**的权限重新执行，查询量会成倍增加，缓存策略要定。
6. 预览身份下能不能提问（1.1 提到的前端不一致）。
7. 反馈在「待发布生效」之后的终态：CR 发布后变成什么（前端没有定义）。
8. 踩的时候要不要让用户填说明（现在没有输入框，`reason` 和 `fix` 只能由后台生成）[推断]。
9. 常用问题是用户手动加入还是按使用次数自动生成。
10. 权限申请走 PDE 的统一审批流，还是问数自己建一张表。
11. 企业微信订阅推送接哪个通道（技术栈调研里只有飞书和钉钉）。
