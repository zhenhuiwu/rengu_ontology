# 动作类型与事件类型：页面操作、接口、业务链路与表结构（v0.5.7 草案）

> 分页与性能补充（2026-10-26）：普通列表、参数/载荷、近期事件默认10条，当前定义由SQL COUNT/LIMIT/OFFSET分页；历史只取对应快照，缺少定义节返回空，不借当前定义。前端共享目录仅取轻头，选中类型才加载详情，进入草稿后等完整编辑基线就绪才允许保存。动作执行记录按页请求`/action-runs`，`GET /action-types/{id}/stats`按同一历史执行记录口径提供总体统计（需action-center查看权），不从当前页计算总计。

> 2026-10-26 实现补充：`:simulate`保持不落库，执行真实载荷/过滤校验，返回`{testEventId:null,deliveredTo:[],payloadValid,checks:[{name,matched,message}],message}`；无法执行的过滤条件`matched:null`。没有测试投递器，因此不返回假事件ID或投递目标；`ver/previewAs`均403 READONLY，此处覆盖旧历史白名单。定时对账已持久化，沿既有`action.reconcile`/`reconcile`队列运行；无外部适配配置返回503、不计作查无。外部测试调用只发显式配置的只读GET probe。配置及状态机见`backend/docs/backend/handoff/reconciliation.md`。

> 作者：Palantir专家　　日期：2026-10-25　　版本：v0.5.7（v0.5.7 主键落到每张表：`event_type.code`、各外键标明指向 `id`、统计表补 `id`、按订阅方查投递改用业务编码；v0.5.6 对账状态表去掉旧枚举、`idempotent=false` 人工再开一轮仍停在 manual【推断】；v0.5.5 主键按 PDE 全员统一：代理主键 `id bigint` + `code` 唯一，外键指向 `id`；v0.5.4 `query_by_key` 返回值对齐动态：查到·成功 / 查到·未执行 / 查无；v0.5.3 按 智能问数项目调研员 `b7ffba4` 评审改 10 处：试运行一律 200、重名校验键、`query_by_key` 模拟返回、时间线时间格式、`action_type_rule`、`writeback_call` 一行一次调用、补列、非停用口径、webhook 前缀、master 提交号；v0.5.1 只读错误体加 `details.mode`；v0.5.2 按动态 `b7ffba4` 评审改：执行状态与来源库内码引用动态、`:mark-checked` 不碰执行记录、`writeback_call`/`event_dedup` 改可变表、`TARGETS` 改为 `releases.ts` 的 `DRAFT_TARGETS`、P4 标注、`versionsOf` 引用参数；v0.2 并入 PDE 的 export-mock、调度器、分页、对账结论；v0.3 按 智能问数项目调研员、动态、本体小白 的评审修改：`/api/ontology/v1` 前缀、接口值与前端 mock 一致并加库内值对照、`writeback_call`/`reconcile_task`/`ext_system` 归属、对账结论 succeeded/not_executed、业务键模板、derived 调用清单；v0.4 定下 `writeback_call`/`ext_system` 归 integration、补每个接口的权限（§3.6）、外部系统接口；v0.4.1 按 PDE `e03a575` 改 §7 的导出路径：derived 按函数名分文件，`RUNS`/`EVENTS` 由 runsOf/eventsOf 并集还原；v0.5 按动态的 5 条意见改（§7.1 用实跑核过的 derived 文件名、业务键列 `biz_key`、§9 的 `action_run` 改为引用动态文档、`GET /action-runs` 用 `actionType`、人工对账先查一次改为【推断】）并按 PDE 表规范补 §9.0 和时间字段表 §3.5.1；外部系统接口改前缀 `/api/data/v1`；只读统一 `?ver=`/`?previewAs=` + 403 `READONLY`、`VALIDATION_FAILED`、错误体带 `requestId`；§4.2 补发布快照的 `actionTypes`/`eventTypes` 分段与组装函数登记；尚未发布到 master）
> **团队规则（PDE）**：文档和前端不一致时**以前端为准**；后台不加前端没有的限制。我补的额外限制一律标【推断】，集中列在 §8.2「待用户确认」。
> 依据：master `2e5b002` 的前端代码与 mock，只读，没有改源码。涉及的文件：
> `src/pages/ontology/ActionTypes.vue`、`EventTypes.vue`、`parts/useDraftView.ts`、`parts/StartChangeModal.vue`、`parts/DraftBanner.vue`、`useDraftEdits.ts`、
> `src/components/actionEventResolvers.ts`、`src/mock/actionEvent.ts`、`extsys.ts`、`dynamic.ts`、`paginate.ts`，以及 `composables/usePagedQuery.ts`、`useDrawer.ts`、`useReadonly.ts`、`useUnsavedGuard.ts`、`stores/confirm.ts`。
> 与静态文档 `docs/backend/static-api.md` 共用的部分（变更单、整版发布、`?ver=`、通用错误码）**直接沿用，不重复定义**。
> 标注：【官方】表示 palantir.com/docs 上本人打开核对过的页面；【笔者归纳】表示我们自己的设计取舍；【推断】表示前端没写死、由我补的后台规则；【建议】表示给 本体小白 对齐用的建议，可以改。

---

## 0. 一句话讲清楚（给小白）

- **动作类型**：定义一件“能做的事”，比如“补货申请”。它规定要填哪些参数、什么情况下才能提交、成功后写回哪个外部系统、改本体里的哪些对象，以及之后发什么通知、产生什么事件。
- **动作执行（action run）**：某个人或某条规则真正做了一次这件事，编号 `ACT-YYYYMMDD-NNNN`。
- **事件类型**：定义一种“发生了的事”，比如“订单状态变更”。它规定载荷有哪些字段、按什么去重、投递给哪些订阅方。
- **事件（event）**：真实发生的一条，编号 `EV-…`，会投递给多个订阅方，每个订阅方各有一条**投递记录**。

---

## 1. 模块边界与对外约定

和 动态本体负责人 已经商定（2026-10-25）：

| 归属 | 负责内容 |
|---|---|
| **动态（Dynamic）** | 动作执行的**状态流转**和对应接口：暂存（规则命中后生成待确认动作）、确认、审批、撤回、重新提交、人工对账。`action_run`、`action_run_log` 表由动态负责（seed 也由动态做）。**外部幂等键 `act-…` 在暂存时由动态生成**；重新提交会生成**新的执行记录和新的幂等键**，原记录保留。 |
| **Palantir（本模块）** | 动作类型、事件类型的定义（读接口 + 草稿校验）；外部系统（`ext_system`）及适配器；**业务键模板**（动作类型上的模板，例如 `requestReplenishment:{门店ID}:{SKU}:{日期}`，前端标签“幂等键规则”）；按外部幂等键调用、查询；超时判定；定时对账任务（procrastinate 调度）；事件的接收、去重、投递和投递记录。 |
| **静态（Static）** | 变更单（CR）、草稿增量 `cr_item`、校验、整版发布。动作类型和事件类型的**所有定义改动**都走这条链路，不另做一套。 |
| **本体小白（基础层）** | 本体版本的存储（`ontology_release` 快照）和 `?ver=` 历史版本的读取。本模块的读接口只把 `ver` 参数透传给基础层，自己不存历史快照。 |

### 1.1 本模块提供给动态的内部接口（进程内调用或 `/internal/v1`）

动态对外的接口（只引用，不在本文定义）：暂存 `POST /api/ontology/v1/action-runs:stage`、确认 `POST /api/ontology/v1/action-runs/{id}:confirm`、执行记录 `GET /api/ontology/v1/action-runs` 和 `GET /api/ontology/v1/action-runs/{id}`（动态的**唯一**执行记录接口，返回 `ActionRun`，时间线来自 `action_run_log`；动作类型页的“执行记录”“最近异常”和执行抽屉都调它）。

| 接口 | 入参 | 同步返回 | 说明 |
|---|---|---|---|
| `submit(actionRunId, idemKey)` | 执行记录号和**外部幂等键**；本模块再按 `actionRunId` 读执行时锁定的动作类型版本和参数快照 | `result: 成功 \| 失败 \| 结果未知`，`extRef`（外部回执号，可空），`code`（返回码或失败原因），`retries`，`ms`，`calls[]`（每次调用：attempt、http、ms） | 先校验提交条件，再调用外部系统。**写回提交只发一次、带签名、永不重发**（PDE 定，2026-10-27，以代码为准【仓库】）：外部**明确返回失败**（HTTP 4xx/5xx 或业务失败码，如 ERP 的 `NC-2071 供货仓可用库存不足`）直接返回失败，不自动重试；**超时或无响应**记为结果未知，转对账（§5.2），也不重发。外部系统的 `maxRetry` 只用于副作用 Webhook 出站（§14）。mock 里“失败记录 `retries = maxRetry`”“第 2 次成功”的演示数据与此不一致，以代码为准。成功后的本体编辑和 `ActionExecuted` 见 §4.1。 |
| `query_by_key(idemKey)` | 外部幂等键 | `extStatus: 查到·成功 \| 查到·未执行 \| 查无`（与动态口径一致；「仍无法确认」是人工对账的选项、触发 `reopen_reconcile`，不是返回值），`extRef`，`raw` | 定时对账（本模块的 cron）调这个接口。人工对账前是否先调一次给对账人参考，前端没有（对账弹窗只有结论 + 说明），属于【推断】，见 dynamic-api.md `[推断 P7]`。“查无”也算一次查询。对方不支持幂等查询（`ext_system.idempotent=false`，如短信、企业微信）时不调这个接口，对账任务建好即转人工。 |
| 回调 `on_reconciled(actionRunId, reconcileResult, reconciler)` | 由本模块调用动态 | — | 定时对账有结论时回调。**一律变成“已对账”，不会变成“成功”**；`reconcileResult = succeeded \| not_executed`（界面文案“外部已成功 / 外部未执行”），`reconciler` 为“系统”。`succeeded` 时由**动态**提交本体编辑并发出 `ActionExecuted`（人工对账选“外部已成功”同样处理）。`not_executed` 不提交、不发事件，原记录**不能重新提交**，前端提示“如仍需执行请重新发起”。本模块不写 `action_run`。 |
| `dry_run(actionTypeId, params, view?, cr?, ver?)` | 动作类型、参数；草稿视图带 `view=draft&cr=` | `passed`，`checked`，`failed[{name, fail}]` | 对应“试运行”。只校验提交条件并模拟调用，不写外部系统，不改本体。 |
| `emit(event)` | **完整信封（含生产方生成的 `eventId`）**和载荷 | `eventId`，`deduped: bool` | `eventId` 由**生产方**生成并放在请求里；本模块是消费方，先按 `eventId` 去重（同一个 `eventId` 重发直接返回 `deduped=true`），再按去重键窗口去重。动作成功或对账为外部已成功后，动态调用它发 `ActionExecuted`；结果未知时发 `ActionOutcomeUnknown`；规则、数据同步、外部回调也走这个接口。 |

- **两种键，别混用**：
  - **外部幂等键** `idemKey`，形如 `act-20261025-2134`、`act-al-20261025-0139`：动态在**暂存**时生成，存在 `action_run.idem_key`，作为外部系统的幂等请求头（如 `X-Request-Id`），按它重试和对账。重新发起 = 新执行记录 + 新幂等键。
  - **业务键模板**：动作类型上的模板，如 `requestReplenishment:{门店ID}:{SKU}:{日期}`（前端标签仍叫“幂等键规则”，接口字段沿用 mock 的 `idemKey`，库里叫 `biz_key_template`）。暂存时由动态算出 `action_run.biz_key`（与 `idem_key` 分开存），不发给外部系统；拿它拦截重复暂存是动态的【推断 P4】，前端没有。
- 定时对账：**每 15 分钟**按幂等键查询一次，每轮最多 3 次（“查无”也计 1 次）。查到结果就调 `on_reconciled`，对账人记为“系统”；3 次后仍无法确认的转人工。人工对账选“仍无法确认”时**再开一轮 3 次自动查询**（前端文案：“保持结果未知，继续自动查询”）。
- **调度器**：用 **procrastinate**（PostgreSQL 任务队列，带 cron 和锁）。定时对账（每 15 分钟的 cron，同一条执行记录按 `actionRunId` 加锁）、按订阅方的重投、按保留期删除过期事件，都用它（已定（PDE 汇总团队决定，2026-10-25））。
- **数据归属**：
  - 我的表：`writeback_call`（每次外部调用，`integration` 模块；动态原来的 `ext_call` 已并入这张表）、`reconcile_task`（对账次数、轮次、下次查询时间只放在这里）、`ext_system`（放在 `integration` 模块；PDE 的“外部系统”页通过我的接口读写），以及动作类型、事件类型的定义表和事件、投递表。
  - 动态的表：`action_run`（只由动态写，seed 也由动态做，包括动作中心的 `ACTIONS` 和本模块的 `RUNS`）、`action_run_log`（执行时间线）。
- **seed、dev、test 环境里所有适配器都用 mock 实现**，不连真实外部系统。外部系统页的“测试调用”在 dev/test 也走 mock 适配器；**只有生产环境、经用户授权**才连真实外部系统。seed 进来的“结果未知”执行记录（动作中心 20 条，`RUNS` 里 54 条），mock 适配器的 `query_by_key` 一律返回“查无”，而这些记录 `state=manual`、不会被定时对账扫到，`next_at` 保留导出原值（mock 里没有，所以是空）。这样对账任务不会改动这些记录，页面和现在一模一样（详见 §7.1）。
- 结果未知超过 30 分钟，由“结果未知提醒”这个订阅方通过企业微信通知发起人和确认人（mock：`actionEvent.ts` 的 `subscribersOf('action_outcome_unknown')`）。

### 1.2 代码位置（与 PDE 约定）

```
backend/app/modules/
  action/            动作类型定义读接口、提交条件求值、试运行、submit / query_by_key
    seeds/           从前端 mock 读数据写库（见 §7）
  event/             事件类型定义、事件接收与去重、投递、投递记录、模拟触发
    seeds/
  integration/       ext_system 表与接口（PDE 外部系统页也用）、外部系统适配器（每个 EXT-* 一个 adapter）：超时、重试、限流、幂等请求头、writeback_call
  reconcile/         定时对账任务（procrastinate；可以并入 action，见 §8 待定问题）
```

FastAPI，先用 PostgreSQL，数据库访问留适配层（与 智能问数项目调研员 的建议一致）；任务队列用 procrastinate。迁移跑完后执行统一的 `seed` 命令。

> 下面写到的“已定”都是 PDE 在 2026-10-25 汇总团队讨论后定下的。

---

## 2. 页面操作流程

### 2.1 两页共有的规则

| 规则 | 前端实现 | 后台对应 |
|---|---|---|
| 左侧列表 | 搜索名称和分组（动作）/来源（事件），每页 22 条，‹ 1/N › 简洁分页；改筛选条件后回到第 1 页 | 页码分页，返回 `total` |
| 选中项写在 URL | `?action=` / `?event=`。动作页切换选中项清掉 `rs`、`rpage`、`rsize`、`ppage`、`psize`、`drawer`、`view`；事件页只清 `view`、`fpage`、`fsize`、`epage` 和抽屉，**不清 `range`、`oc`**（近期事件的范围和结果筛选会保留） | — |
| 页签写在 URL | `?tab=`，切换页签会清掉分页参数 | — |
| 已发布版本只读 | 编辑类按钮置灰，悬停提示“已发布版本只读，请先发起变更” | 写接口一律走 CR，直接改返回 `NOT_IN_DRAFT` |
| 发起变更 | 弹窗二选一：**新建变更单**（标题必填，最多 40 字，说明可选），或**加入已有变更单**（下拉选未发布的单）。点“进入草稿”后切到 `?view=draft` | `POST /change-requests` 或 `POST /change-requests/{id}/targets`（静态 §3.1） |
| 有草稿时 | 页头出现“已发布 / 草稿 CR-xxxx”切换。默认看已发布版，因为问数用的是这一版；主按钮变成“继续编辑草稿” | 读接口带 `view=draft&cr=` |
| 草稿横幅 | 显示变更单号、标题、改动数、最后编辑人和时间、“问数仍使用已发布版本”；提供“提交审核”和“放弃草稿” | 改动数由后台返回 |
| 草稿内编辑 | 每张表的行带改动标记（新增/修改/删除）；删除的行加删除线，可以“恢复”。改动先存在本地，点“保存草稿”才写入，点“撤销修改”（`de.revert()`）丢弃本地改动 | `PUT /change-requests/{id}/items`，`section` 与前端 `de.section(name)` 一一对应（见 §3.1、§3.2 的“草稿写入”） |
| 未保存保护 | 有未保存修改时，切换左侧选中项、切回已发布版、离开页面都会弹“有未保存的修改”；关闭浏览器标签页时用浏览器自带提示 | 后台以已保存内容为准，带 `lock_version` |
| 提交审核 | 确认卡显示本对象、本对象改动数、变更单改动数、审核人（mock 写死“本体管理员 陈立军”）。有未保存修改时，卡片上的确定按钮锁住并显示原因。提交后草稿锁定，跳到 `/build/ontology/releases?cr=` | `POST /change-requests/{id}:submit`；锁定后写接口返回 `CR_LOCKED` |
| 放弃草稿 | 红色确认卡：丢弃本对象在草稿里的全部改动（含已保存的） | `DELETE /change-requests/{id}/targets/{kind}/{targetId}` |
| 历史版本 `?ver=` | 整页只读：“新建动作/事件类型”“发起变更”置灰，悬停显示“正在查看历史版本 x，只读”；不显示草稿切换；回滚链接的悬停也显示这个原因 | 读接口带 `ver`，写接口返回 `READONLY` |
| 以此用户预览 | 同样只读；所有确认卡自动锁住，显示“正在以 xx 的权限预览，只读” | `READONLY` |
| 版本历史 | 表格：版本、本体发布、状态（生效中/已替代）、变更说明、发布人、发布时间。**对比**和**回滚**都置灰，悬停“暂未开放” | `GET …/versions`；对比、回滚放第二期 |
| 抽屉 | 点编号在当前页打开抽屉（`?drawer=kind:id`，最多两层），不跳转 | `GET /action-runs/{id}`、`GET /events/{id}`、规则抽屉（动态的接口） |

> 注意：“试运行”和“模拟触发”这两个按钮**只在状态为草稿时置灰**，在 `?ver=` 下仍然可以点（前端没有用 readonly 控制）。后台需要决定这时按哪个版本执行，见 §8 第 8 条。

### 2.2 动作类型页

| 操作 | 前端行为 | 约束 |
|---|---|---|
| 新建动作类型 | 页头按钮 → 发起变更弹窗（目标“新建动作类型”） | 只读时置灰 |
| 试运行 | 确认卡（标签“试运行”）：目标对象、前 5 个必填参数的默认值，说明“只校验提交条件并模拟调用，不会写回外部系统，也不会修改本体对象” → 成功提示“N 项提交条件全部满足” | 草稿状态的动作类型置灰 |
| 概览 | 基本信息：目标对象、负责人、说明、谁可以执行、审批、写回系统、写回接口、幂等键规则、超时、重试；“使用该动作的规则与告警”表（规则、触发方式、使用方式、近期告警及其中待处理数）；提交条件前 5 条；右侧“近 30 天执行”：执行次数、成功率、失败、结果未知·待对账，14 天堆叠柱图（成功/结果未知/失败），最近 4 条异常（失败或结果未知），点编号打开执行抽屉 | 草稿状态的动作类型显示“草稿未发布，暂无执行记录” |
| 参数 | 每页 12 条（`?ppage/psize`），列：参数、API 名、类型、必填、默认值、校验；草稿里可新增/编辑/删除/恢复 | 弹窗必填项：参数名、API 名；API 名重复提示“已存在同名项” |
| 提交条件 | 不分页；列：模板（当前用户/参数/上下文对象）、条件、表达式、不满足时提示 | 必填：条件名、表达式、不满足时提示 |
| 写回与副作用 | 写回配置：外部系统、接口、幂等键（加外部系统的幂等请求头）、超时、重试、超时处理；“编辑写回”弹窗可以选“本体（无外部写回）”，选这一项时接口自动置为“—”并禁用输入。本体编辑表（操作：修改/新建/关联，对象，字段）；副作用表（类型：通知/事件/Webhook，目标，时机，说明）；右侧“执行顺序”时间线 | 外部系统只能选生产环境、未停用的系统（`writebackSystems()`） |
| 执行记录 | 按结果筛选（`?rs=`：全部/待确认/审批中/执行中/成功/失败/结果未知/已对账/已撤回），每页 14 条（`?rpage/rsize`），列：时间、编号、目标对象、参数摘要、发起人（规则显示为规则链接）、确认人、来源、结果、耗时 | 切换筛选回到第 1 页 |
| 执行抽屉 | 标题“动作名 · 对象”；字段：动作类型、版本（**显示动作类型的当前版本**，resolver 取 `a.version`，不是执行时的版本；见 §8.2 U10）、发起人、确认人、来源、审批人、目标对象、参数摘要、外部系统、幂等键、返回、自动重试、时间、关联告警；过程时间线（见 §3.1）；关联：目标对象、发起规则 | — |

“执行顺序”时间线的生成规则（`ActionTypes.vue` 里的 `flow`），后台按这个顺序执行：
1. 规则生成 ⇒ 待确认（只在被规则绑定时出现；确认前可以撤回）
2. 校验提交条件（任一不满足即拒绝）
3. 审批（审批不是“免审”时）
4. 调用外部系统（同步；超时值和幂等键取外部系统配置）
5. 外部明确返回失败 ⇒ 失败（写回提交只发一次，不自动重试【仓库】）
6. 超时或无响应 ⇒ 结果未知·待对账，不重发
7. 提交本体编辑（写回成功后才提交）
8. 执行副作用（异步；失败不影响动作结果）

### 2.3 事件类型页

| 操作 | 前端行为 | 约束 |
|---|---|---|
| 新建事件类型 | 页头按钮 → 发起变更弹窗 | 只读时置灰 |
| 模拟触发 | 确认卡（标签“测试”）：前 4 个载荷字段的示例值，说明“只投递到订阅方的测试通道，不会产生告警、执行动作或写入下游” → 成功提示“已投递 N 个订阅方的测试通道”（N 不含停用的订阅方） | 草稿状态的事件类型置灰 |
| 概览 | 来源、来源说明（规则显示为链接）、触发对象、负责人、说明、载荷摘要（“另含 N 个通用信封字段”）、去重键、去重窗口、投递保证、顺序、保留期、近 7 天条数；订阅方前 4 个；最近事件前 5 条；右侧 14 天柱图，以及 4 个数：近 7 天、处理延迟 P95、去重丢弃、投递失败/部分投递 | 草稿状态显示“草稿未发布，暂无事件” |
| 载荷字段 | 每页 12 条（`?fpage/fsize`）；事件自身字段在前，10 个**信封字段**在后，信封字段不可编辑 | 新增字段时 API 名不能和已有字段（含信封字段）重复；弹窗提示“新增字段建议设为可选；删除或改类型属于不兼容变更，需要所有订阅方确认” |
| 订阅方 | 不分页；列：类型（规则/自动化/通知/外部订阅）、订阅方、过滤条件、收到后、近 7 天投递、状态（启用/停用/异常） | 必填：订阅方、过滤条件、收到后 |
| 近期事件 | 时间范围（`?range=24h\|7d`，默认 7 天）和投递结果筛选（`?oc=`：全部/已投递/已生成告警/已去重/部分投递/投递失败），每页 14 条（`?epage=`）；列：时间、事件、对象、载荷摘要、订阅方数、延迟、结果；分页只显示 ‹ 1/N ›，不显示总数，旁边显示保留期 | 改范围或筛选会回到第 1 页 |
| 事件抽屉 | 字段：事件类型、来源、发生时间、处理延迟、对象、载荷摘要、去重键、投递结果；时间线：接收 → 去重检查（命中则丢弃）→ 投递 N 个订阅方 / 部分投递（1 个超时待重试）/ 投递失败（3 次重试后进入死信队列）；关联：事件对象、产生该事件的规则 | — |

> **事件流分页**（已定（PDE 汇总团队决定，2026-10-25））：近期事件页和其他显示 ‹ 1/N › 的列表一样，用 `page/size`，后台**返回 `total`**，前端只是不显示总数。**不用游标**。只有前端本来就用游标的页面（如审计日志）才用游标。

---

## 3. 接口清单

通用约定（沿用静态 §4）：
- **前缀**：动作类型、事件类型、事件、对账相关接口用 `/api/ontology/v1`（`/api/ontology/v1/action-types/…`、`/event-types/…`、`/events/…`）；**外部系统 `/ext-systems*` 和 `/writeback-calls*` 用 `/api/data/v1`**（PDE 定：外部系统属数据模块，和骨架一致）。代码仍在 `integration/` 目录，归我。下表省略前缀。
- **分页**：页码分页，请求参数 `page`、`size`，响应 `{items, total, page, size}`（与 `usePagedQuery` 一致）。所有显示 ‹ 1/N › 的列表（包括近期事件）都这样，**不用游标**。页码按骨架 `app.common.pagination` 收拢（同前端 `paginate.ts:12-13`）：`page ≤ 0` 当第 1 页（不报 422），超过最后一页返回最后一页，响应里的 `page` 是收拢后的页码，`total = 0` 时 `page = 1`；`size` 仍校验 1–200（超出 422）。默认 `size`：动作 / 事件列表 22，参数 / 载荷字段 12。
- **字段和取值与前端 mock 完全一致**：JSON 用 camelCase（`typeId`、`objLabel`、`latencyMs`……），枚举用 mock 里的中文值（`已发布`、`结果未知`、`部分投递`……）。库里用 snake_case 和英文代码，在 Pydantic 层转换，对照见 §3.5。
- 读接口接受 `ver`（即前端的 `?ver=`，历史版本）、`previewAs`（即“以此用户预览”）、`view=published|draft`、`cr`。**只读状态只看 query 参数 `?ver=` / `?previewAs=`，不用请求头**；带这两个参数的写请求一律 **403 `READONLY`**，`details.mode` 填 `history`（`?ver=`）或 `preview`（`?previewAs=`）（与 PDE/骨架一致）。
- **白名单**：`:dry-run`、`:simulate` 是**不落库的 POST**，列入骨架的只读白名单——带 `?ver=` 时照常执行（前端不置灰）；带 `?previewAs=` 时前端已锁住，后台仍返回 403 `READONLY`（见 §3.3）。
- **错误体**（骨架统一）：`{code, message, details, requestId}`，`message` 可以直接用作悬停文字或确认卡上的阻断原因。字段校验失败统一 **422 `VALIDATION_FAILED`**，`details.fields[{field, message}]`（`field` 用接口的 camelCase 字段名）。
- **动作类接口用冒号风格**：`:dry-run`、`:simulate`、`:test`、`:mark-checked`；动态的 `:stage`、`:confirm`、`:submit`、`:reconcile`，静态的 `:submit`、`:rollback` 同样写法。
- “来源 mock”一列是 seed 数据和接口返回形状的出处（`src/mock/` 下）。

### 3.1 动作类型

2026-10-26 `:dry-run`已实现真实只读条件校验，body.params为结构化JSON（可省略为空，不填sample默认值）。当前读DB，history只读所选release.actionTypes（缺段503不偷当前），view=draft按真实CR未删除行覆盖。响应200 `{passed,checked,failed}`，checked为实际条件数，参数/类型/规则/条件缺失或不支持均failed；不创建ActionRun、不调用外部、不模拟回执。仅本路由允许history，preview仍403，其他真实写接口只读规则不变。

语法支持已声明参数API或唯一完整显示名引用、真实用户code/DB角色、有限数值算术/链式比较、等于/不等于、集合和布尔；对象只读已登记真实非敏感字段，按实际RoleAccess/effective_expr/to_sql约束范围，不借None行级桩或展示范围文字放宽。告警仅真实当前唯一处理人且权限明确时提供上下文。未知AST、CRM/预算查询、自然语言、隐式今天/单位/对象数据不支持即failed；矩阵与预算见backend/docs/backend/handoff/action-checker.md。动态暂存/确认共用同一真实checker，参数摘要须本身为JSON对象，审批与外部503边界保持。

| 方法 路径 | 参数 / 请求体 | 返回（字段名取自 mock） | 分页 | 来源 mock |
|---|---|---|---|---|
| `GET /action-types` | `group`、`kw`（匹配名称）、`page`、`size=22`、`ver` | `items[{id,name,api,group,status,version,target,targetApi,targetKind}]`；`groups[]` | 页码 | `actionEvent.ts` `ACTION_TYPES`、`ACTION_GROUPS` |
| `GET /action-types/{id}` | `ver`、`view`、`cr` | 上面的字段，加 `owner,desc,runners,approval,sysId,system,endpoint,idemKey(业务键模板),timeout,retry,idempotent,usedBy[]`；草稿视图另带 `draft{crId,title,changes,owner,updatedAt,locked}` | — | `ACTION_TYPES`（已含 `withSystem` 合并的系统字段）、`releases.ts` `DRAFT_TARGETS`、`DRAFTS` |
| `GET /action-types/{id}/params` | `page`、`size=12`、`ver`、`view`、`cr` | `items[{name,api,type,required,def,rule,mark?}]` | 页码 | `paramsOf()` |
| `GET /action-types/{id}/submission-criteria` | `ver`、`view`、`cr` | `items[{kind,name,expr,fail,mark?}]` | 不分页 | `condsOf()` |
| `GET /action-types/{id}/writeback` | `ver`、`view`、`cr` | `config{sysId,endpoint,idemKey,mark?}`、`edits[{op,obj,field,mark?}]`、`effects[{kind,target,when,note,mark?}]`、`flow[{text,desc,status}]` | — | `writebackOf()`、`extsys.ts` `EXT_SYSTEMS` |
| `GET /action-types/{id}/used-by` | `ver` | `items[{ruleId,ruleName,trigger,mode,alerts,openAlerts}]` | 不分页 | `action_type_rule`（seed 取 `ACTION_TYPES[].usedBy`）；规则名、告警数关联 `dynamic.ts` `RULES`/`ALERTS`、`RULE_CONFIRMER` |
| `GET /action-types/{id}/stats` | `days=30` | `{total,ok,failed,unknown,pending,confirm,rate,avgMs}` | — | `runStats()`；照前端公式，已对账都算成功（实时统计时 `not_executed` 也会算进去）【推断：是否把 `not_executed` 排除交用户】 |
| `GET /action-types/{id}/stats/daily` | `days=14` | `items[{date,ok,failed,unknown}]` | — | `runDaily()` |
| `GET /action-types/{id}/versions` | — | `items[{ver,release,status,by,at,change}]` | 不分页 | `versionsOf()`、`ACTION_VERSION_NOTES` |
| `POST /action-types/{id}:dry-run` | query `view`、`cr`（草稿视图按草稿内容校验，`checked` = 草稿里未删除的条件数）、`ver`；body `{params?}` | **一律 200** `{passed, checked, failed[{name,fail}]}`（条件不过也是 200，`passed=false`，前端只显示结果） | — | `condsOf()`；`ActionTypes.vue` `dryRun()` |
| `GET /api/data/v1/ext-systems?writeback=1`（外部系统接口，前缀不同） | — | `items[{id,name,idemKey,timeoutMs,maxRetry,retry,idempotent,status,env}]` | 不分页 | `extsys.ts` `EXT_SYSTEMS`；过滤同 `writebackSystems()`（生产环境且未停用）。`ext_system` 归本模块 `integration`，外部系统页也调这组接口（见下方“外部系统”） |
| 草稿写入 | `PUT /change-requests/{cr}/items`，`target{kind:'action',id}`，`section ∈ params\|conds\|edits\|effects\|config` | 静态接口 | — | `useDraftEdits.ts`；前端已接（palantir-cr，开关 `VITE_PALANTIR_CR_WRITE`，默认关）：先 `GET …/items?kind&targetId&section` 读回再整段 PUT |

`used-by` 关系成员及顺序取当前定义或所选发布版 `actionTypes.<id>.usedBy`，历史规则名称、触发和使用方式取同版 `rules` 段；缺失软引用保留并标明定义不可用。当前定义优先使用 `mode_text`，否则按规则 `autoStage` 推导。`alerts` 为当前所有告警记录数，`openAlerts` 仅含“待处理”（不含“处理中”）；两项实时计数不受 `ver` 影响。

动态的接口（只引用，动态唯一的执行记录接口）：`GET /action-runs`（按动作类型过滤的参数是 **`actionType`**（动作类型 id；动态同时收 `action` = 中文动作名）、`status` 可多选、`page`、`size=14`；返回统一响应类型 **`ActionRun`**，字段以 dynamic-api.md 为准，其中含动作类型页要的 `typeId,at,initiator,confirmer,approver,source,objKind,objId,objLabel,summary,status,ms,code,retries,idemKey`）、`GET /action-runs/{id}`（返回 `ActionRun` + `action_run_log` 时间线，执行抽屉用）。“最近异常”= `GET /action-runs?actionType=&status=失败,结果未知&size=4`。

**外部系统（前缀 `/api/data/v1`；代码在 `integration` 模块，归我；PDE 的“外部系统”页和动作类型页共用）**

| 方法 路径 | 参数 / 请求体 | 返回（字段名取自 `extsys.ts`） | 分页 | 说明 |
|---|---|---|---|---|
| `GET /ext-systems` | `writeback=1`（只返回生产环境且未停用的，同 `writebackSystems()`）、`kw` | `items[{id,name,vendor,category,endpoint,auth,authDetail,idempotent,idemKey,timeoutMs,maxRetry,retry,rateLimit,owner,status,env,usage}]` | 不分页 | `EXT_SYSTEMS`；密钥不返回明文 |
| `GET /ext-systems/{id}` | — | 同上，加关联的动作类型 | — | — |
| `POST /ext-systems`、`PUT /ext-systems/{id}`（编辑也接受 `POST /ext-systems/{id}`，见 §11） | 同上字段 | 保存后的对象 | — | 新建、编辑外部系统；直接生效，不走变更单（和前端一致） |
| `POST /ext-systems/{id}:test` | `{op?}`（可不带请求体） | 当前503 `EXT_SYSTEM_TEST_UNAVAILABLE` | — | 没有配置真实协议/凭据执行器，所有环境明确说明未发送请求，不写虚假调用记录；停用仍409 `EXT_SYSTEM_DISABLED`，不存在404。前端入口置灰并说明未配置，不模拟鉴权通过或HTTP成功。 |
| `GET /ext-systems/{id}/calls` | `result`（全部 / 成功 / 失败 / 结果未知）、`page`、`size=10`（前端固定每页 10 条） | `{items[...ExtCall, runId], total, page, size, counts}`；`counts` = `{全部,成功,失败,结果未知}`（单选按钮上的数字，不受 `result` 影响）【推断】 | 页码 | `writeback_call` 的行（含测试调用）。顺序：测试调用在最前（新的在上），其余按 `at` 倒序、同时间按 `code` 倒序（同 `extCalls()`）【推断】 |
| `GET /writeback-calls` | `sysId`（可不传）、`result`、`page`、`size=10` | 同上 | 页码 | 跨系统查调用记录（同一个查询） |
| `GET /writeback-calls/{id}` | — | 一条 `ExtCall`（无执行记录时 `runId=null`） | — | 调用详情抽屉 `?call=` |
| `POST /writeback-calls/{id}:mark-checked` | — | 调用记录 `result=成功`，`msg=人工核对：对方已处理（{姓名} {HH:mm} 标记）` | — | “人工核对 · 确认对方已处理”，要 `external-systems · manage`。不是「结果未知」或带执行记录的 → 409 `CALL_NOT_CHECKABLE`。前端只对不带 runId 的调用显示这个按钮（`ExternalSystems.vue` L319–323），带 runId 的只跳到动作中心。只改这条调用记录的结果，不碰执行记录 |

### 3.2 事件类型

2026-10-26 内部接收已实现：`POST /internal/v1/events` 仅认证持久接收，不执行投递。必须配置 `RENGU_INTERNAL_EVENT_TOKEN` 并使用 `Authorization: Bearer`；缺配置503、错误401，不使用X-Demo默认身份，history/preview统一403。eventType按真实当前code或api查已发布且未下线，objectType匹配绑定对象，payload校验实际必填/基础类型。HTTP JSON信封最大64 KiB、深度16、总值2048、容器512，NUL/非法Unicode/非有限数/额外信封字段422；大小超限413。

生产eventId按事务锁幂等：同完整信封返回原结果，内容不同409 EVENT_ID_CONFLICT。业务dedup按type+dedupKey咨询锁及持久event_dedup固定窗口/永久状态；命中保留新ID的真实去重记录。缺省dedupKey仅空规则/事件ID规则可用eventId，其他中文说明须生产方显式传键；不可执行窗口409。未命中仅为“待投递”，subscribers=0、latencyMs=null，不声称投递或回执成功。EventType FOR SHARE锁内读取定义和实际发布头，入站保存接收时ontologyVersion；旧seed版本和来源未知时null。事件读接口增加source/producerType/producerObjectType/traceId/ingestedAt/ontologyVersion，stats增加pending，p95无真实时延为null，pending不代表成功。真模式抽屉只展示实际记录，mock模式不变；完整边界见`backend/docs/backend/handoff/event-ingress.md`。

| 方法 路径 | 参数 / 请求体 | 返回 | 分页 | 来源 mock |
|---|---|---|---|---|
| `GET /event-types` | `source`、`kw`、`page`、`size=22`、`ver` | `items[{id,name,api,source,status,version,object}]`；`sources[]` | 页码 | `EVENT_TYPES`、`EVENT_SOURCES` |
| `GET /event-types/{id}` | `ver`、`view`、`cr` | `source,sourceRef,ruleId?,object,objectApi,objKind,owner,desc,dedupKey,dedupWindow,delivery,ordering,retention,weekly,specific[]`、`draft{…}` | — | `EVENT_TYPES` |
| `GET /event-types/{id}/payload-fields` | `page`、`size=12`、`ver`、`view`、`cr` | `items[{name,api,type,required,desc,sample,envelope,mark?}]`；`envelopeCount` | 页码 | `payloadOf()`（`specific` + `ENVELOPE`） |
| `GET /event-types/{id}/subscribers` | `ver`、`view`、`cr` | `items[{kind,name,ruleId?,filter,then,status,weekly,mark?}]` | 不分页 | `subscribersOf()` |
| `GET /event-types/{id}/stats` | — | `{weekly,total,dedup,failed,partial,p95,asOf}`；`daily[{date,value}]`（14 天） | — | `eventDaily()`；健康指标同 `EventTypes.vue` 的 `health`（按 `eventsOf()` 算） |
| `GET /event-types/{id}/events` | `range=24h\|7d`、`outcome`、`page`、`size=14` | `{items[{id,typeId,at,objKind,objId,objLabel,summary,outcome,subscribers,dedupKey,latencyMs}], total, page, size}`（前端只显示 ‹ 1/N ›，不显示总数） | 页码，**无游标** | `EVENTS` |
| `GET /events/{id}` | — | 一条 `EventRecord`（同近期事件的一行，= mock `eventById()`）；时间线、关联由前端 `actionEventResolvers.ts` `event()` 按 `outcome` 和事件类型拼（palantir-r3 已实现，见 §12.2） | — | `eventById()`、`actionEventResolvers.ts` `event()` |
| `GET /event-deliveries` | `eventId`（事件 code，必填），或 `eventType`（事件类型 code）+ `subscription`（订阅方 name）加 `page`、`size`【推断：前端没有按订阅方查投递的入口】 | `items[{subscription,status,attempts,lastError,deliveredAt}]`；按订阅方查时为分页结构 | 按事件查不分页 | mock 里没有逐个订阅方的记录，按 `outcome` 和 `subscribers` 推出来【推断】 |
| `POST /event-types/{id}:simulate` | query `view`、`cr`（草稿视图按草稿里的载荷字段和订阅方）、`ver`；body `{payload?}`（默认取各字段的 `sample`） | `{testEventId, deliveredTo}`（`deliveredTo` 不含停用的订阅方） | — | `EventTypes.vue` `simulate()`；`subscribersOf()` |
| `GET /event-types/{id}/versions` | — | 同动作类型 | 不分页 | `versionsOf()`、`EVENT_VERSION_NOTES` |
| 草稿写入 | `PUT /change-requests/{cr}/items`，`target{kind:'event',id}`，`section ∈ payload\|subs` | 静态接口 | — | `useDraftEdits.ts`；同 §3.1（palantir-cr） |
| 内部：接收事件 | `POST /internal/v1/events` `{eventId, eventType, occurredAt, source, objectType, objectId, payload, dedupKey?, traceId?}`（`eventId` 由生产方生成） | `{eventId, deduped}` | — | 信封字段 `ENVELOPE` |

### 3.3 只读与权限

- **第一期单租户**：不设 `tenant_id`，所有表和接口都不按租户隔离。
- 带 `?ver=`（历史版本）的写请求返回 403 `READONLY`（message 用前端文案“正在查看历史版本 x，只读”）。试运行、模拟触发在历史版本下前端没有置灰，后台**不拦截**，按当前查看的版本执行。
- **以此用户预览**时，所有确认卡被锁住，试运行和模拟触发也点不下去；后台对带 `?previewAs=` 的 `:dry-run`、`:simulate` 和所有写接口返回 403 `READONLY`（message“正在以 xx 的权限预览，只读”）。
- 试运行、模拟触发对能进入页面的用户（页面权限 `view` 及以上）都开放，只在类型为草稿时置灰（`TYPE_IS_DRAFT`）。“只有执行人角色才能试运行”“只有负责人才能模拟触发”是【推断】，见 §8.2 U6。
- 草稿写入的权限按静态的 CR 规则（静态的接口，不在下表）。
- 每个接口的权限见 §3.6。

### 3.4 错误码（在静态 §4 的基础上增加）

| code | HTTP | 什么时候 | message 示例（取自前端文案） |
|---|---|---|---|
| `SUBMISSION_CRITERIA_FAILED` | 422 | **只用在 HTTP 入口**（动态的 `:submit`/`:confirm` 等）有条件不满足；试运行不用它（一律 200）。内部 `submit()` 不是 HTTP：条件不过返回 `result=失败`、`code=提交条件未通过：…`，执行记录照 mock 留一条「失败」 | `details.failed[{name,fail}]`，如“只有会员运营组可以同步会员标签” |
| `DUPLICATE_KEY` | 409 | 草稿新增时键重复，键与前端一致：参数 `api`、条件 `name`、本体编辑 `op`+`obj`、副作用 `target`、订阅方 `name`、载荷字段 `api`（只在新增时校验，编辑不校验） | “已存在同名项”；载荷字段为“API 名与已有字段（含信封字段）重复” |
| `ENVELOPE_READONLY` | 409 | 试图改信封字段 | “信封字段所有事件类型共有，不可修改” |
| `WRITEBACK_SYSTEM_INVALID` | 422 | 写回系统不是生产环境，或者已停用（和前端下拉的过滤规则一样） | — |
| `TYPE_IS_DRAFT` | 409 | 对草稿状态的类型做试运行或模拟触发 | — |
| `READONLY` | 403 | 带 `?ver=` 的写请求；带 `?previewAs=` 时调用试运行、模拟触发或写接口（骨架统一码；`details.mode` = `history` / `preview`） | “正在查看历史版本 x，只读” / “正在以 xx 的权限预览，只读” |
| `VALIDATION_FAILED` | 422 | 请求字段校验不过（骨架统一码） | `details.fields[{field, message}]` |
| `EXT_SYSTEM_DISABLED` | 409 | 对已停用的外部系统做测试调用 | “该系统已停用，先在‘编辑’里改为正常再测试” |
| `EXT_TIMEOUT` | — | 不是 HTTP 错误；submit 返回 `result=结果未知`，`code` 为“超时 N 秒未响应 · 未重试” | — |

`INCOMPATIBLE_PAYLOAD_CHANGE` 前端没有对应的拦截，第一期不做，列在 §8.2。`IDEMPOTENCY_CONFLICT`（409）已进公共错误码，由动态在 `action_run` 入口返回（dynamic-api `08dd366`），本模块不另外返回。

### 3.5 库内值 ↔ 接口值

库里存英文代码，接口返回前端 mock 的值；转换写在 Pydantic schema 里（读时转中文，写时转回）。

| 字段 | 库内值 → 接口值 |
|---|---|
| 动作/事件类型 `status`、版本 `status` | `published`→已发布，`changing`→变更中，`draft`→草稿；版本的「生效中/已替代」不落库，按 `action_type.version`/`event_type.version` 推出（版本表只追加，见 §9） |
| 执行状态 `action_run.status`（动态的表） | 库内码以 dynamic-api.md 1.3 为准，本文不重复（【推断】已驳回只有用户确认 U1 才加） |
| `reconcile_result` | `succeeded`→外部已成功，`not_executed`→外部未执行 |
| 执行来源 `source`（动态的表） | 库内码以 dynamic-api.md 1.3 为准；接口按 `source_label` 原文返回 |
| 提交条件 `kind` | `current_user`→当前用户，`parameter`→参数，`context_object`→上下文对象 |
| 本体编辑 `op` / 副作用 `kind` | `modify`→修改，`create`→新建，`link`→关联；`notify`→通知，`event`→事件，`webhook`→Webhook |
| 事件来源 `source` | `sync`→数据同步，`rule`→规则，`action`→动作，`external`→外部系统 |
| 事件结果 `outcome` | `delivered`→已投递，`alert_created`→已生成告警，`deduped`→已去重，`partial`→部分投递，`failed`→投递失败（库里另有 `pending` 表示还没投完，接口不出现） |
| 订阅方 `kind` / `status` | `rule`→规则，`automation`→自动化，`notify`→通知，`external`→外部订阅；`enabled`→启用，`disabled`→停用，`error`→异常 |
| 投递 `event_delivery.status` | `pending`→待投递，`sending`→投递中，`delivered`→已投递，`retrying`→待重试，`dead`→死信 |
| 外部系统 `status` | `ok`→正常，`degraded`→降级，`disabled`→停用 |
| `writeback_call.result` | `succeeded`→成功，`failed`→失败，`unknown`→结果未知 |
| `reconcile_task.state`（不出接口） | `querying` 查询中，`resolved` 已有结论，`manual` 转人工 |
| 字段名 | 库 `biz_key_template` ↔ 接口 `idemKey`（动作类型）；库 `idem_key` ↔ 接口 `idemKey`（执行记录）；其余 snake_case ↔ camelCase 一一对应 |

### 3.5.1 时间字段（PDE 定：库内一律 `timestamptz`，接口按每个字段的 mock 格式返回北京时间、不带时区）

下表是 2026-10-25 在 master `2e5b002` 实跑 `export-mock` 后逐条核过的格式（每个字段所有行格式一致）。默认 `YYYY-MM-DD HH:mm`，只有 mock 带秒的才带秒；查询参数里的时间也按对应字段的格式收。

| 接口字段 | 库内列 | 格式 | 带秒 | mock 出处 |
|---|---|---|---|---|
| 执行记录 `at`（动作类型页近期执行，表归动态） | `action_run.submitted_at`（见 dynamic-api.md 1.3） | `YYYY-MM-DD HH:mm:ss` | 是 | `runsOf()` |
| 执行抽屉时间线 `timeline[].time` | `action_run_log.at`（动态） | 首条 `YYYY-MM-DD HH:mm:ss`，其后 `HH:mm:ss`（与 resolver 一致） | 是 | `ACTION_EVENT_RESOLVERS.action` |
| 执行日统计 `date` | `action_run_daily_stat.date`（`date` 类型） | `MM-DD` | — | `runDaily()` |
| 动作/事件类型版本 `at` | `action_type_version.at`、`event_type_version.at` | `YYYY-MM-DD HH:mm` | 否 | `versionsOf()` |
| 事件 `at` | `event.ingested_at`（`occurred_at` 同格式） | `YYYY-MM-DD HH:mm:ss` | 是 | `eventsOf()` |
| 事件抽屉时间线 `timeline[].time` | `event_delivery.delivered_at` 等 | 首条 `YYYY-MM-DD HH:mm:ss`，其后 `HH:mm:ss`（与 resolver 一致） | 是 | `ACTION_EVENT_RESOLVERS.event` |
| 事件日统计 `date` | `event_daily_stat.date`（`date`） | `MM-DD` | — | `eventDaily()` |
| 外部调用记录 `time` | `writeback_call.at` | `YYYY-MM-DD HH:mm:ss` | 是 | `system/derived/extCalls.json` |
| 草稿横幅 `draft.updatedAt` | 静态的 `change_request` | 按静态文档 | — | 静态 |

载荷字段的 `sample`（如 `2026-10-25 15:40:58`、`2026-08-19`）是示例文本，原样存 `text`，不是时间列。

### 3.6 每个接口的权限

- **pageKey + 级别**：pageKey 取 `src/nav.ts` 的页面 key，级别取 `src/mock/roles.ts` 的 `FuncLevel`（`view | manage`）。读接口要 `view`，改数据的接口要 `manage`。
- **API 密钥 scope**：取 `src/mock/apikeys.ts` 的 `KEY_SCOPES`。“不开放”表示只能用登录会话调用，API 密钥调不了（前端 `API_PATHS` 里没有这些路径）。
- **`?ver=`**：“是”表示带 `ver` 也能调；写接口为“否”，返回 403 `READONLY`。
- **预览**：“以此用户预览”时能不能调；“否”返回 403 `READONLY`。读接口按被预览用户的权限返回。
- **行级过滤**：是否按角色的行级范围（`RowScope`，如“订单 大区 = 华东”）过滤返回的行。前端这两页和外部系统页都**不按行级范围过滤**，所以第一期一律“否”；要加的话见 §8.2 U12。

| 接口 | pageKey · 级别 | API 密钥 scope | `?ver=` | 预览 | 行级过滤 |
|---|---|---|---|---|---|
| `GET /action-types` | action-types · view | 元数据读取 | 是 | 是 | 否 |
| `GET /action-types/{id}` | action-types · view | 元数据读取 | 是 | 是 | 否 |
| `GET /action-types/{id}/params` | action-types · view | 元数据读取 | 是 | 是 | 否 |
| `GET /action-types/{id}/submission-criteria` | action-types · view | 元数据读取 | 是 | 是 | 否 |
| `GET /action-types/{id}/writeback` | action-types · view | 元数据读取 | 是 | 是 | 否 |
| `GET /action-types/{id}/used-by` | action-types · view | 元数据读取 | 是 | 是 | 否 |
| `GET /action-types/{id}/stats`、`/stats/daily` | action-types · view | 不开放 | 是（运行数据不分版本） | 是 | 否 |
| `GET /action-types/{id}/versions` | action-types · view | 元数据读取 | 是 | 是 | 否 |
| **`POST /action-types/{id}:dry-run`** | **action-types · view**（前端不按级别限制；要 manage 或执行人角色是【推断】U6） | **不开放** | **是**（前端不置灰，按当前查看的版本） | **否**（`READONLY`） | **否** |
| `GET /event-types` | event-types · view | 元数据读取 | 是 | 是 | 否 |
| `GET /event-types/{id}` | event-types · view | 元数据读取 | 是 | 是 | 否 |
| `GET /event-types/{id}/payload-fields` | event-types · view | 元数据读取 | 是 | 是 | 否 |
| `GET /event-types/{id}/subscribers` | event-types · view | 元数据读取 | 是 | 是 | 否 |
| `GET /event-types/{id}/stats` | event-types · view | 不开放 | 是（运行数据不分版本） | 是 | 否 |
| `GET /event-types/{id}/events` | event-types · view | 不开放 | 是 | 是 | 否 |
| `GET /events/{id}` | event-types · view | 不开放 | 是 | 是 | 否 |
| `GET /event-deliveries` | event-types · view | 不开放 | 是 | 是 | 否 |
| **`POST /event-types/{id}:simulate`** | **event-types · view**（前端不按级别限制；只给负责人和管理员是【推断】U6） | **不开放** | **是**（前端不置灰，按当前查看的版本） | **否**（`READONLY`） | **否** |
| `GET /event-types/{id}/versions` | event-types · view | 元数据读取 | 是 | 是 | 否 |
| `GET /ext-systems`（含 `?writeback=1`） | external-systems · view；动作类型页的写回下拉按 action-types · view 放行 | 不开放 | 是（不受版本影响） | 是 | 否 |
| `GET /ext-systems/{id}` | external-systems · view | 不开放 | 是 | 是 | 否 |
| `GET /ext-systems/{id}/calls` | external-systems · view | 不开放 | 是 | 是 | 否 |
| `POST /ext-systems`、`PUT /ext-systems/{id}` | external-systems · manage | 不开放 | 否 | 否 | 否 |
| `POST /ext-systems/{id}:test` | external-systems · view（前端只按只读置灰；要 manage 是【推断】U13） | 不开放 | 否（前端在只读时置灰） | 否 | 否 |
| `POST /writeback-calls/{id}:mark-checked` | external-systems · manage | 不开放 | 否 | 否 | 否 |
| `POST /internal/v1/events` | —（服务间调用，内部凭证） | 外部系统的回调从 `Webhook 回调` scope 的 `POST /api/data/v1/webhooks/ext/callback` 进来，转成 `emit` | — | — | 否 |

动态的接口（`/action-runs…`）、静态的接口（变更单、草稿写入）的权限由各自文档定义，不在此表。

---

## 4. 业务链路

### 4.1 动作提交（本模块的 `submit`）

0. **暂存（动态）**：动态在 `action-runs:stage` 时生成外部幂等键 `act-…`，并按业务键模板算出 `biz_key`（与 `idem_key` 分开存，不传外部系统）；同一业务键已有在途记录时拒绝暂存是动态的【推断 P4】，前端没有。这一步不调本模块的写接口。
1. **读版本**：按执行记录锁定的动作类型版本执行。新版本发布后，进行中的执行仍按提交时的版本完成（版本历史页签的 ⓘ）。
2. **参数校验**：类型、必填，以及“校验”一列里的规则（如 `1 ≤ 天数 ≤ 30`、`≤ 200 字`）。对象引用参数检查对象是否存在。
3. **提交条件**：三种模板——当前用户、参数、上下文对象。**全部满足**才能提交，任一不满足就返回 `SUBMISSION_CRITERIA_FAILED`（422）和它的“不满足时提示”。规则命中生成的动作，在确认人确认时才求值（与 resolver 时间线的顺序一致：确认提交 → 提交条件）。
4. **审批**：审批由动态负责。审批条件按 mock 的原文存储（如“单次 > 500 张需审批”），做不做成可求值的表达式见 §8.2 U8。
5. **写回外部系统（同步，决定动作结果）**：适配器带上外部系统的幂等请求头（如 `X-Request-Id`、`requestNo`），值就是执行记录的外部幂等键。每次调用写一行 `writeback_call`。
   - 返回 2xx：成功，记下 `extRef`（外部回执号）。
   - 明确失败（4xx/5xx、429，或业务失败码如 `NC-2071`）：直接返回失败，**不自动重试**（写回提交只发一次、带签名，见 §14；PDE 定以代码为准【仓库】）。
   - 超时或无响应：**结果未知，不重试**，因为对方可能已经处理，重试可能造成重复写回。
6. **提交本体编辑**：写回成功后才提交（修改、新建、关联），保证本体和外部系统一致。没有外部系统的动作直接提交，失败就报错，不重试。
7. **副作用（异步）**：通知、事件、Webhook。成功后发 `ActionExecuted`；结果未知时发 `ActionOutcomeUnknown`。副作用失败不影响动作结果。
8. **定时对账**：procrastinate 的 cron 每 15 分钟对 `reconcile_task.next_at` 到期的任务调一次 `query_by_key`，每轮最多 3 次（“查无”也计次）。
   - 查到“查到·成功” → `on_reconciled(succeeded, 系统)` → 动态把状态改成**已对账**（**不是成功**），再由动态提交本体编辑、发 `ActionExecuted`。
   - 查到“查到·未执行” → `on_reconciled(not_executed, 系统)` → **已对账**，不提交本体编辑，不能重新提交（“如仍需执行请重新发起”）。
   - 3 次后仍无法确认 → 转人工（`state=manual`，`next_at=null`）。人工对账由对账人直接选结论（前端弹窗只有结论 + 说明；先调 `query_by_key` 查一次是【推断】，见动态 `[推断 P7]`）：外部已成功（同“查到·成功”，动态提交本体编辑、发 `ActionExecuted`）/ 外部未执行 / **仍无法确认**（保持结果未知，`round + 1`，`tries` 归零，再自动查 3 次）。

### 4.2 动作类型的版本与变更

- 所有改动（参数、条件、本体编辑、副作用、写回配置）都写进变更单草稿的 `cr_item`，`target_kind=action`，`section` 和前端一致。审核通过后**随本体整版发布生效**，没有单独发布动作类型这回事。
- 发布写回已实现 action 的 params / conds / edits / effects / config 和 event 的 payload / subs 七段。行 key 与字段协议见 `backend/docs/backend/handoff/palantir.md §6`；config 固定整体 key=`cfg`。update 以旧 key 定位，支持身份改名并检查唯一；重复 seed key 不允许歧义修改，载荷 api 不得撞全局信封。非法定义发布返回 409 WRITEBACK_FAILED，全部写回与 CR 状态同事务回滚；草稿保存的 NUL 文本先返回 422。
- 每次整版发布，每个被修改类型只追加一个完整定义版本，版本为当前及历史最大 vN + 1；不修改旧历史，回滚后再发布继续递增。文本未编辑则保留内部 AST，表达式文本改变时清掉旧 AST；订阅 weekly 和规则 generates 不由草稿覆盖。
- 后台 isNew 支持调用方 code / name 的最低定义骨架（api=code；事件默认外部系统来源、30 天保留；动作默认内部写回）。产品的新建表单尚未设计，不据此添加 UI；仍在线 code 冲突拒绝，回滚下线类型经明确编辑可重新发布，add 不覆盖已有子行。
- **回滚**：两页的回滚链接现在都置灰（“回滚暂未开放”），第一期不做。开放以后按前端/静态的口径，**生成一个新版本**（调基础层的 `create_version`，内容等于目标版本），不是把当前指针移回去。
- **整版回滚时恢复本模块定义表**（发布管理页的整版回滚前端已开放，`Releases.vue:183-189`，跟上一条单个类型的回滚不同）：按骨架契约 `app/contracts/versioning.py` 第 4 条，`actionTypes`、`eventTypes` 两个 builder 都实现可选钩子 `restore_section(session, content, *, release_id)`（`release_id` = 这次回滚产生的那一版的 `ontology_release.id`，见 §10.4），由版本层在 `create_version` 回滚分支里同一事务内调用，`content` = 被恢复那一版（`rollback_of` 的上一版）快照里这一节。做法与静态 §2.2-6、动态 §2.8 一致：按前端 id upsert，把 `action_type`/`event_type` 及其参数、条件、编辑、副作用、载荷、订阅方等定义行改回快照里的值，并把当前版本指针指回快照里的 `version`；`action_type_version`/`event_type_version` 只追加，不动。快照里没有的类型不删行，也不加新的 `status` 值（前端类型状态只有已发布/变更中/草稿），而是把该行 `retired_release_id` 写成这次回滚产生的那一版的 `ontology_release.id`：当前视图的列表和详情都不返回它（详情按不存在处理），`?ver=V` 历史视图在「V 那一版的 id ≥ `retired_release_id`」时不出现（版本号是 `v2026.10.N` 字符串，不能按字符串比；按 id 比和骨架取「最新版」的口径一致）；以后再发布到这个类型时把 `retired_release_id` 清空 [推断]。`content=None`（那一版快照里根本没有这一节）时什么都不做 [推断]。
- **发布快照里的本模块分段**（PDE 定：每个模块自己组装自己的分段交给基础层 `create_version`；**发布在发布事务里同步展开**，展开失败整次发布回滚）。快照分段 = 静态的各段 + `actionTypes`、`eventTypes`（本模块）+ `rules`、`lifecycles`（动态）。基础层 `create_version` 按分段登记组装函数：**本模块在启动时登记 `actionTypes`、`eventTypes` 两个组装函数**（`action/` 登记 `actionTypes`，`event/` 登记 `eventTypes`），发布时由 `create_version` 在同一事务里依次调用。两段的键用**前端 id**（`ACTION_TYPES[].id`、`EVENT_TYPES[].id`），字段名和取值与前端 mock 一致（中文枚举，库内转换见 §3.5）：

```json
{
  "actionTypes": {
    "request_replenish": {
      "id": "request_replenish", "name": "…", "api": "…", "group": "…", "status": "已发布", "version": "v3",
      "target": "…", "targetApi": "…", "targetKind": "store", "owner": "…", "desc": "…（含 {sys} 模板原文）",
      "runners": "…", "approval": "…",
      "sysId": "EXT-ERP", "endpoint": "POST /…", "idemKey": "requestReplenishment:{门店ID}:{SKU}:{日期}",
      "params":  [{ "name": "…", "api": "…", "type": "…", "required": true, "def": "…", "rule": "…" }],
      "conds":   [{ "kind": "当前用户|参数|上下文对象", "name": "…", "expr": "…", "fail": "…" }],
      "writeback": {
        "edits":   [{ "op": "修改|新建|关联", "obj": "…", "field": "…" }],
        "effects": [{ "kind": "通知|事件|Webhook", "target": "…", "when": "…", "note": "…" }]
      }
    }
  },
  "eventTypes": {
    "order_status_changed": {
      "id": "order_status_changed", "name": "…", "api": "…", "source": "数据同步", "sourceRef": "…", "ruleId": null,
      "object": "…", "objectApi": "…", "objKind": "order", "status": "已发布", "version": "v3", "owner": "…", "desc": "…",
      "dedupKey": "…", "dedupWindow": "24 小时", "retention": "180 天", "delivery": "至少一次", "ordering": "…",
      "payload":     [{ "name": "…", "api": "…", "type": "…", "required": true, "desc": "…", "sample": "…" }],
      "subscribers": [{ "kind": "规则|自动化|通知|外部订阅", "name": "…", "ruleId": "R-007", "filter": "…", "then": "…", "status": "启用|停用|异常" }]
    }
  }
}
```

  - `idemKey` 是**业务键模板**（库列 `biz_key_template`），不是外部幂等键。`payload` 只放事件自身字段（`specific`），10 个信封字段全局共用，不进快照。
  - `system/timeout/retry/idempotent` 不进快照（从 `ext_system` 关联，外部系统改了不需要发版）；统计类字段（`weekly`、订阅方 `weekly`）也不进。`usedBy`（规则 ID 列表）是定义关系，存 `action_type_rule`，**进快照**（`actionTypes.<id>.usedBy`）；“处理告警时手动执行”等使用方式从 `RULE_ACTION` 推不出来，按 mock 原样存在这张表。
  - **运行数据不进快照**：执行记录（`action_run`）、事件（`event`）、投递（`event_delivery`）、写回调用（`writeback_call`）、对账任务都不在快照里。
  - `?ver=` 读历史版本时，本模块的读接口从这段快照取定义；当前版本读展开表（§9）。
- 本模块给静态的校验链路提供动作类型自己的校验项（并入静态的 V 系列，见静态待定问题第 12 条）【建议】：
  - API 名唯一；
  - 引用的外部系统可用；
  - 业务键模板里的占位符必须都能从参数中取到；
  - 对外部系统、本体都没有写操作时给出警告。
  - 以上都是【推断】，前端目前没有这些校验，第一期默认**只警告**，见 §8.2。

### 4.3 事件：定义、产生、投递

1. **定义**：载荷 = 事件自身字段 + 10 个信封字段（`eventId, eventType, occurredAt, ingestedAt, source, objectType, objectId, ontologyVersion, dedupKey, traceId`）。定义还包括去重键、去重窗口、投递保证（mock 里都是“至少一次”）、顺序（如“同一订单内有序”）、保留期。
2. **兼容规则**：载荷建议只新增**可选**字段。删除字段或改类型时，前端只提示“需要所有订阅方确认”，**不拦截**；后台第一期也只警告（是否拦截见 §8.2 U4）。
3. **产生**：有 4 种来源——数据同步（CDC 或定时增量）、规则命中、动作（写回成功、对账为外部已成功，或结果未知）、外部系统（Webhook 推送或回调）。都调用 `emit`，**`eventId` 由生产方生成**并放在请求里，这样生产方重发时不会产生两条事件。
4. **去重（消费方）**：先按 `eventId` 去重（重发直接返回原结果）；再按去重键模板算 `dedupKey`，在去重窗口内命中就丢弃，事件结果记为“已去重”，订阅方数为 0。窗口可以是“永久”。去重用 `event_dedup` 表，不引入 Redis。
5. **投递（至少一次）**：`event` 表兼作 outbox——事件和它的投递记录在同一个事务里写入，procrastinate 任务从未投完的行（部分索引）取出投递。按订阅方的过滤条件匹配，每个非停用（启用或异常）的订阅方一条 `event_delivery`（`UNIQUE(event_id, subscription_id)`）。失败后按退避重新入队，重试 3 次后进入死信。保证是**至少一次**，订阅方要按 `eventId` 做幂等。
   - **部分投递只重投失败的订阅方**：已投递的那条不会再发。
   - 整条事件的结果：全部成功为“已投递”（规则来源的为“已生成告警”）；部分成功、其余还在重试为“部分投递”；全部失败为“投递失败”。
6. **模拟触发**：按当前视图（草稿视图用草稿里的载荷字段和订阅方）只投递到非停用订阅方（`status !== '停用'`，异常也算）的测试通道，不产生告警，不执行动作，不写下游；前端不写数据，后台也不计入近期事件和统计（U9）。
7. **保留期**：第一期 `event` 表**不分区**，procrastinate 每天按事件类型的保留期删除过期的事件和投递记录。

### 4.4 我负责的 procrastinate 任务

| 任务 | 触发 | 说明 |
|---|---|---|
| 定时对账 | cron 每 15 分钟 | 扫 `reconcile_task.next_at` 到期且 `state=querying` 的任务，调 `query_by_key`，每轮最多 3 次；按 `actionRunId` 加锁 |
| 人工“仍无法确认”后的追加查询 | 动态通知后入队 | `round + 1`、`tries=0`，再自动查 3 次（每 15 分钟一次），仍无法确认再转人工 |
| outbox 分发 | `emit` 写入事件后入队，另有 cron 兜底扫 outbox 部分索引 | 从 `event` 表未投完的行生成 `event_delivery` 并投递 |
| 事件投递与失败重投 | 每条投递记录一个任务 | 失败按退避重新入队，3 次后死信；部分投递**只重投失败的订阅方** |
| 过期事件清理 | cron 每天 | 按事件类型保留期删除 `event`、`event_delivery`（不分区） |

---

## 5. 状态机

接口里的状态值就是前端 mock 的中文值；库内英文代码见 §3.5。

### 5.1 动作执行（状态归动态，这里列出来是为了对齐接口）

| 当前状态 | 触发 | 下一状态 | 由谁做 |
|---|---|---|---|
| （无） | 规则命中，暂存（生成外部幂等键，按业务键防重复暂存【推断 P4】） | 待确认 | 动态 |
| （无） | 人手动发起 | 审批中 / 执行中 | 动态 |
| 待确认 | 确认人确认提交 | 审批中（需要审批）/ 执行中 | 动态 |
| 待确认 | 确认人撤回（必须填原因） | 已撤回（终态，不写外部系统） | 动态 |
| 审批中 | 审批通过 | 执行中 | 动态 |
| 审批中 | 审批人驳回 | 【推断，默认不实现】前端没有“已驳回”，见 §8.2 U1 | 动态 |
| 执行中 | `submit` 返回成功 | 成功（终态） | 动态（依据本模块的返回） |
| 执行中 | `submit` 返回失败（提交条件未通过，或外部明确失败；写回提交不自动重试） | 失败 | 动态 |
| 执行中 | `submit` 返回结果未知（超时，未重试） | 结果未知 | 动态 |
| 结果未知 | 定时对账查到“查到·成功” → `on_reconciled(succeeded)` | **已对账**（终态，不是成功）；动态提交本体编辑、发 `ActionExecuted` | 本模块回调，动态改状态 |
| 结果未知 | 定时对账查到“查到·未执行” → `on_reconciled(not_executed)` | **已对账**（终态），`reconcileResult=not_executed`；**不能重新提交**，提示“如仍需执行请重新发起” | 本模块回调，动态改状态 |
| 结果未知 | 人工对账选“外部已成功” | 已对账（`succeeded`）；动态提交本体编辑、发 `ActionExecuted` | 动态 |
| 结果未知 | 人工对账选“外部未执行” | 已对账（`not_executed`），不能重新提交 | 动态 |
| 结果未知 | 人工对账选“仍无法确认” | 结果未知（不变）；本模块再开一轮 3 次自动查询 | 动态，本模块调度 |
| 失败 | 重新提交（只有失败可以，和前端一致） | 原记录不变；新建一条执行记录，新的外部幂等键 | 动态 |

### 5.2 外部写回调用与对账任务（本模块内部，`writeback_call` / `reconcile_task`）

| 状态 | 触发 | 下一状态 |
|---|---|---|
| 调用中 | 2xx | 成功 |
| 调用中 | 明确失败（HTTP 4xx/5xx、429 或业务失败码） | 失败（只发一次，不重试【仓库】） |
| 调用中 | 超时或无响应 | 结果未知，**不重试** → 新建 `reconcile_task(state=querying, round=1, tries=0, next_at=+15 分钟)` |
| querying | `query_by_key` 返回查到·成功 | `resolved`，回调 `on_reconciled(succeeded, 系统)` → 已对账 |
| querying | 返回查到·未执行 | `resolved`，回调 `on_reconciled(not_executed, 系统)` → 已对账，不能重新提交 |
| querying | 返回查无，`tries + 1 < 3` | 仍 `querying`，`tries + 1`，`next_at` 加 15 分钟（“查无”也计次） |
| querying | 第 3 次仍查无 | `manual`（`manual=true`，`next_at=null`），等动态人工对账 |
| （建任务时） | 对方不支持幂等查询（`idempotent=false`） | 直接 `manual`，不调 `query_by_key` |
| manual | 人工选“仍无法确认” | `querying`，`round + 1`，`tries=0`，`next_at=+15 分钟`（再查 3 次）；**`idempotent=false` 时仍停在 `manual`，不入队**【推断：前端“继续自动查询”文案不区分；目前没有动作写回短信或企业微信，界面上碰不到】 |
| manual | 人工选“外部已成功 / 外部未执行” | `resolved`，记 `reconciler=人名` |

### 5.3 事件（整条）

| 状态 | 触发 | 下一状态 |
|---|---|---|
| 已接收 | `eventId` 重复，或去重键在窗口内命中 | 已去重（终态） |
| 已接收 | 去重通过 | 投递中（库内 `pending`） |
| 投递中 | 所有投递记录都成功 | 已投递 / 已生成告警（规则来源） |
| 投递中 | 部分成功，其余在重试 | 部分投递（只重投失败的订阅方） |
| 部分投递 | 剩下的重试也成功 | 已投递【推断：mock 只有静态快照，没有演示状态回写】 |
| 投递中 / 部分投递 | 全部进入死信 | 投递失败 |

### 5.4 投递记录（每个订阅方一条，`UNIQUE(event_id, subscription_id)`）

| 状态 | 触发 | 下一状态 |
|---|---|---|
| 待投递 | 订阅方已停用 | 不生成记录 |
| 待投递 | 发送 | 投递中 |
| 投递中 | 订阅方确认 | 已投递 |
| 投递中 | 失败或超时，次数 < 3 | 待重试 |
| 待重试 | 到了重试时间 | 投递中 |
| 投递中 | 第 3 次仍失败 | 死信 |

---

## 6. Palantir 对照

| 主题 | Palantir 的做法 | 我们的做法 | 出处 / 标注 |
|---|---|---|---|
| 动作类型是什么 | 一次动作是一个事务，改一个或多个对象的属性和关系；动作类型定义一组改动，也包括提交时的副作用 | 一致：动作类型 = 参数 + 提交条件 + 写回 + 本体编辑 + 副作用 | 【官方】https://www.palantir.com/docs/foundry/action-types/overview/ |
| 参数 | 参数是动作类型的输入，有类型，可以设置是否在表单里显示、能不能改；也能接收宿主应用传入的值 | 一致；“对象引用”参数用对象选择器填写；“关联告警”从告警处理发起时自动带入 | 【官方】https://www.palantir.com/docs/foundry/action-types/parameter-overview/ |
| 规则 | Ontology 规则包括新建、修改、删除对象和关系；Function 规则不能和其他规则混用；另有通知规则和 Webhook 规则。通知在所有改动应用之后发出 | “本体编辑”只有修改、新建、关联三种，**没有删除**；通知放在副作用里，写回成功后发 | 【官方】https://www.palantir.com/docs/foundry/action-types/rules/ ；取舍为【笔者归纳】 |
| 提交条件 | 三种模板：Current user、Parameter、Execution context（是否在 Scenario 里执行）。**全部满足**才能提交；每个根级条件有自己的失败提示；可以在 Ontology Manager 里试运行 | 模板是 当前用户 / 参数 / 上下文对象。我们的“上下文对象”是**检查对象状态**（如“告警.状态 = 处理中”），Palantir 是用 Parameter 模板检查对象参数的属性；它的 Execution context 指 Scenario。名字相近但含义不同，别混用。“试运行”对应它的 test run | 【官方】https://www.palantir.com/docs/foundry/action-types/submission-criteria/ ；对应关系为【笔者归纳】 |
| 写回与副作用 | Webhook 分两种：**writeback**（在其他规则之前执行，失败则什么都不改，失败会显示给用户；只能配置一个）；**side effect**（在改动之后执行，可以有多个，顺序不保证，用户可能先看到成功）。官方说明：外部请求成功、但 Ontology 改动失败的情况仍可能发生 | 完全照这个分层：写回同步、只有一个、决定结果；本体编辑在写回成功后提交；副作用异步、失败不影响结果 | 【官方】https://www.palantir.com/docs/foundry/action-types/webhooks/ |
| 幂等键、超时 → 结果未知 → 对账 | 我读的上面这几页没有讲幂等键、超时和对账 | 我们加的：每次执行一个幂等键；写回提交只发一次、不重发；超时记结果未知，写操作永不重试；每 15 分钟按键查询，最多 3 次，之后人工对账 | 【笔者归纳】 |
| 执行记录 | Action log 只记录**成功**的提交（失败看 Action metrics）；必记字段包括 Action RID、动作类型版本（每次更新动作类型都会自动加 1）、提交时间、提交人、被编辑的对象，以及调用过的 webhook 和发过的通知 | 我们的执行记录**成功和失败都记**，状态更多（待确认、结果未知、已对账……），相当于 action log 加 action metrics 的合体。版本号随**本体整版发布**变，不是每改一次就加 1 | 【官方】https://www.palantir.com/docs/foundry/action-types/action-log/ ；差异为【笔者归纳】 |
| 函数支持的动作 | 用 `@OntologyEditFunction()` 写的函数作为唯一一条规则；函数版本要手动升级，也可以开自动升级（只升兼容版本，`0.y.z` 不能自动升级） | 第一期不做函数支持的动作；以后如果要做，动作类型应**锁定函数版本**，随整版发布一起升级 | 【官方】https://www.palantir.com/docs/foundry/action-types/function-actions-getting-started/ ；取舍为【笔者归纳】 |
| 事件 / 自动化 | Automate 取代 Object Monitoring。条件有时间、对象数据、数据集三类；效果有提交动作、AIP Logic、函数、通知。官方建议：后果重大的改动先**暂存，等人审核** | 规则命中 → 生成“待确认”动作 → 确认人确认后提交，和官方的这条建议一致 | 【官方】https://www.palantir.com/docs/foundry/automate/overview/ |
| 触发条件 | 对象集条件：对象加入集合、移出集合、在集合内被修改、对全部对象定时运行、跨过阈值 | 我们多了一层显式的“**事件类型**”（有载荷 schema、去重键、订阅方、投递记录）。我读的官方页面里没有看到对应的独立事件注册表，Automate 是直接按对象集变化触发的 | 【官方】https://www.palantir.com/docs/foundry/automate/condition-objects/ ；“事件类型”为【笔者归纳】 |
| 重试与执行保证 | 效果是 **at-least-once**（至少一次），同一个触发可能执行多次，官方建议做成幂等；可以按单个效果自动重试、按整个事件重试，也可以配置 fallback 效果 | 投递保证“至少一次”加去重键，动作写回用幂等键，思路一致 | 【官方】https://www.palantir.com/docs/foundry/automate/effects/ 、https://www.palantir.com/docs/foundry/automate/effect-settings/ |

> 本节没有引用社区资料。所有【官方】条目都是 2026-10-25 当天打开核对过的页面。

---

## 7. 模拟数据入库

用户的要求：后台按前端界面逻辑逐项实现，**现在 mock 里的数据全部要进数据库**，上线第一天页面和现在看到的一模一样。

### 7.1 seed 方案（已定（PDE 汇总团队决定，2026-10-25））

- **导出**：统一用 PDE 的 `npm run export-mock`（`scripts/export-mock.mjs` + `scripts/export-mock.derived.mjs`，PDE 的分支提交 `e03a575` 内容相同、master 上是：`9440e9c` 原样导出、`2e5b002` `OUT_DIR` 必填 + derived 调用清单；Vite `ssrLoadModule` 加载，`TZ=Asia/Shanghai`），把 `src/mock/*` 的每个导出**原样**写成 `${OUT_DIR}/<导出组>/<export>.json`（seed 环境即 `backend/seeds/json/<导出组>/`，导出组 = 脚本 MODULES 的键：action / dynamic / system / static / common / qa），另生成 `manifest.json`。**`OUT_DIR` 必须显式给出**（没有默认值，不给直接报错退出），seed 用 `OUT_DIR=../backend/seeds/json npm run export-mock`。各模块的 Python seed 只从这里读，不再自己写导出步骤。
  - 本模块读的目录（真实路径）：
    - `backend/seeds/json/action/`：`actionEvent.ts` 的导出。脚本的 MODULES 里只有 `action: ['actionEvent']`，**没有 `event/` 目录**，事件类型的数据也在这里。
    - `backend/seeds/json/action/derived/<函数名>.json`：函数调用结果（§7.1.1），例如 `runsOf.json`。
    - `backend/seeds/json/dynamic/`：`ACTIONS`、`ALERTS`、`RULES`、`RULE_CONFIRMER`、`AUTO_QUERY_TEXT`（只读，归动态）。
    - `backend/seeds/json/system/`：`EXT_SYSTEMS`（`extsys.ts`）。
  - 时间：字符串时间（如 `2026-10-25 14:28:03`）原样保留，都是北京时间；`Date` 写成带 `+08:00` 的 ISO 字符串。
  - **seed 的“现在”固定为 2026-10-25 15:42（北京时间）**，取自骨架的设定（mock `NOW = 2026-10-25T15:42:18+08:00`）。近 24 小时 / 7 天 / 30 天的窗口都按这个时间算，seed 环境的页面才和 mock 一样。
  - **顶层导出是函数的会被跳过**，函数结果由 derived 调用清单在同一进程、常量导出之后逐个调用得到（§7.1.1）。
  - **`RUNS`、`EVENTS` 没有加 `export`**（`actionEvent.ts` 不改），所以**没有** `RUNS.json`、`EVENTS.json`。执行记录 = `action/derived/runsOf.json` 17 次调用结果的并集（1,849 条），事件 = `action/derived/eventsOf.json` 15 次调用结果的并集（1,243 条），按 id 去重后正好还原 `RUNS`、`EVENTS`。下文的 `RUNS`、`EVENTS` 都指这两个并集。
  - 数量参考（本机数过）：17 个动作类型、101 个参数、59 条提交条件、36 条本体编辑、47 条副作用、1,849 条执行记录（其中 54 条结果未知）、15 个事件类型、37 个订阅方、1,243 条事件、10 个外部系统、5 条规则、154 条告警、31 + 27 条版本记录；动作中心 `actions.ts` 141 条执行记录（其中 20 条结果未知）。

#### 7.1.0 2026-10-25 实跑结果（临时 worktree 检出 master `2e5b002`，`OUT_DIR=/tmp/exp-pal-out npm run export-mock`，核完 worktree 和输出都已删除）

- 输出：`action` 8 个导出 56 行；派生 42 个函数、7,041 次调用、29,913 行（推断参数 11 个）；跳过函数 120 个，失败 0 个。不给 `OUT_DIR` 直接退出。
- `action/derived/` 实际文件（15 个）：`paramsOf.json`、`condsOf.json`、`writebackOf.json`、`runsOf.json`、`runStats.json`、`runDaily.json`、`versionsOf.action.json`、`writebackSystems.json`、`payloadOf.json`、`subscribersOf.json`、`eventsOf.json`、`eventDaily.json`、`versionsOf.event.json`、`ACTION_EVENT_RESOLVERS.action.json`、`ACTION_EVENT_RESOLVERS.event.json`。每个文件内容都是 `[{args, result}]`。
- 核数：`runsOf` 17 次调用、1,849 行，按 `code`（`ACT-…`）去重 1,849 条（结果未知 54 条）；`eventsOf` 15 次、1,243 条；`ACTION_EVENT_RESOLVERS.action` 1,849 次、`.event` 1,243 次；本模块条目 manifest 里都没有 `guessed`。
- 当时 `action` 导出的 8 个：`ACTION_GROUPS`(7)、`ACTION_TYPES`(17，已带 `withSystem` 合并的 system/timeout/retry/idempotent)、`ACTION_VERSION_NOTES`(4)、`EVENT_SOURCES`(5)、`EVENT_TYPES`(15，含 `specific`)、`EVENT_VERSION_NOTES`(3)、`RULE_ACTION`(4)、`UNKNOWN_FOLLOWUP`。`RUNS`、`EVENTS` 不导出，从 derived 还原。（更早在 `d9e61c3` 上试跑过一次，结论相同，以本次为准。）
- 其他模块要用的都已导出：`dynamic/ACTIONS`(141，带 `log`)、`dynamic/RULE_CONFIRMER`、`dynamic/AUTO_QUERY_TEXT`、`dynamic/ALERTS`(154)、`dynamic/RULES`(5)、`system/EXT_SYSTEMS`(10)。
- 仍然缺的：`ENVELOPE`、`PARAMS`、`CONDS`、`SUBS` 没导出（通过 derived 的函数结果拿到，不必单独导出）；抽屉 resolver `src/components/actionEventResolvers.ts` 不在 `src/mock/` 下，master `2e5b002` 已在 derived 里加载并导出；草稿目标已在 `6428b87` 挪成 `releases.ts` 的 `DRAFT_TARGETS`（归静态，CR-0013/0014 由静态 seed）。
- 纯查找函数（`actionTypeById`、`eventTypeById`、`runById`、`eventById`、`ruleById`、`extSystemOf`、`ruleConfirmer`、`withSystem`）不用导出。

#### 7.1.1 derived 调用清单（PDE 已实现，master `2e5b002`，`scripts/export-mock.derived.mjs`）

每个函数一个文件：`${OUT_DIR}/<模块>/derived/<函数名>.json`，内容是 `[{args, result}]`，每次调用一项；参数里的对象写成引用 `{ref, key, id}`（整份常量如 `ACTION_VERSION_NOTES` 只有 `{ref}`），后台按引用去对应的导出 JSON 找那一行。每次调用的参数、行数、是否 `guessed` 记在 `manifest.json` 的 `derived`。本模块的条目都按本文 §7.1.1 调用，没有 `guessed`。2026-10-25 在 master `2e5b002` 实跑核对过（§7.1.0），文件名就是下表第一列，数量全部和“数量参考”一致。

| 输出（`json/action/derived/`） | 源文件 | 调用 | 参数 / 遍历 | 用途 / 对应表 |
|---|---|---|---|---|
| `paramsOf.json` | `src/mock/actionEvent.ts` | `paramsOf(a)` | `ACTION_TYPES` 的 17 项（参数写成引用 `{ref:"action/ACTION_TYPES",key:"id",id}`） | action_param |
| `condsOf.json` | 同上 | `condsOf(a)` | 同上 | submission_criterion |
| `writebackOf.json` | 同上 | `writebackOf(a)` → `{edits, effects}` | 同上 | ontology_edit_rule, side_effect |
| `runsOf.json` | 同上 | `runsOf(id)` | `ACTION_TYPES` 的 17 个 id | **执行记录的来源**：17 次调用的并集 = `RUNS`（1,849 条），交给动态写 `action_run` |
| `runStats.json` | 同上 | `runStats(id)` | 同上 | 对比校验 |
| `runDaily.json` | 同上 | `runDaily(id)` | 同上 | action_run_daily_stat |
| `versionsOf.action.json` | 同上 | `versionsOf(a.version, a.owner, ACTION_VERSION_NOTES)` | `ACTION_TYPES` 每一项 | action_type_version |
| `writebackSystems.json` | 同上 | `writebackSystems()` | 无参数，调一次 | 对比校验（写回下拉） |
| `payloadOf.json` | 同上 | `payloadOf(e)`（自身字段 + 10 个信封字段；`ENVELOPE` = 结果的最后 10 项） | `EVENT_TYPES` 的 15 项（引用） | event_payload_field, event_envelope_field |
| `subscribersOf.json` | 同上 | `subscribersOf(e)` | 同上 | event_subscription |
| `eventsOf.json` | 同上 | `eventsOf(id)` | `EVENT_TYPES` 的 15 个 id | **事件的来源**：15 次调用的并集 = `EVENTS`（1,243 条） |
| `eventDaily.json` | 同上 | `eventDaily(id, e.weekly)` | `EVENT_TYPES` 每一项（weekly 见下） | event_daily_stat |
| `versionsOf.event.json` | 同上 | `versionsOf(e.version, e.owner, EVENT_VERSION_NOTES)` | `EVENT_TYPES` 每一项 | event_type_version |
| `ACTION_EVENT_RESOLVERS.action.json` | `src/components/actionEventResolvers.ts` | `ACTION_EVENT_RESOLVERS.action(runId)`（函数字段已去掉） | `runsOf` 并集里每个 id | 交给动态写 `action_run_log`；外部调用部分写 `writeback_call` |
| `ACTION_EVENT_RESOLVERS.event.json` | 同上 | `ACTION_EVENT_RESOLVERS.event(eventId)` | `eventsOf` 并集里每个 id | event_delivery（时间线） |

其他模块的 derived 里和本模块有关的：`json/dynamic/derived/ACTION_EVENT_RESOLVERS.rule.json`（规则抽屉，动态的）、`json/system/derived/extCalls.json`（外部系统页的调用记录，参数照抄 `ExternalSystems.vue` 的 `opsOf`，manifest 标 `guessed`）。本模块不读 `dsTables`；`dsTables` 只认 `system/derived/dsTables.json`（`static/derived/` 下的同名文件不用）。

- 17 个动作类型 id：`issue_recall_coupon, sync_member_tags, assign_region_manager, adjust_region_budget, correct_order_status, request_replenish, adjust_store_price, start_price_approval, close_alert, urge_logistics, claim_alert, reassign_alert, reissue_shipment, approve_refund, change_member_level, change_store_status, set_safety_stock`。
- `eventDaily` 的 weekly 参数：`order_status_changed` 48210、`inventory_changed` 126480、`stock_below_safety` 386、`store_sales_drop` 9、`gross_margin_breach` 54、`shipment_overdue` 212、`member_churn_risk` 3120、`illegal_transition` 31、`action_executed` 1864、`action_outcome_unknown` 37、`coupon_redeemed` 8640、`refund_created` 1420、`store_price_changed` 2260、`waybill_signed` 31240、`alert_closed` 318。
- 所有调用**在同一个进程里、模块加载完之后**做：数据在加载时已按固定种子生成，只调函数不会改变随机序列。

#### 7.1.2 入库

- **入库脚本**：`backend/app/modules/action/seeds/seed.py`、`backend/app/modules/event/seeds/seed.py` 都读 `backend/seeds/json/action/` 和 `backend/seeds/json/action/derived/`，`integration` 的 seed 读 `backend/seeds/json/system/EXT_SYSTEMS.json`。幂等写库（`INSERT … ON CONFLICT DO NOTHING`），业务编码原样写进 `code` 列（`ACT-…`、`EV-…`、`EXT-…`），`id` 由库生成，外键在 seed 时按 `code` 查回 `id`。统一的 `seed` 命令在迁移之后执行。
- **种子顺序**（骨架 `app/seeds/registry.py` 的 `SEED_MODULES`）：integration 分两段。前一段 `integration` 排在 action 之前，只写 `ext_system`（`action_type.sys_id` 按外键查它）；后一段 `integration_runs` 排在 dynamic 之后、qa 之前，写 `writeback_call` 和 `reconcile_task`，`action_run_id` 按 `action_run.code` 查回 `id`。两张表都只由 integration 写种子，动态的种子不碰。已和本体小白定：骨架在 `SEED_MODULES` 的 dynamic 和 qa 之间加 `integration_runs` 一项，归 integration，顺序同时写进 CONVENTIONS。
- **执行记录由动态 seed**：`action_run` 只由动态写，动态的 seed 读动作中心的 `dynamic/ACTIONS.json` 和本模块的 `action/derived/runsOf.json`（所有调用结果的并集），按 `code`（`ACT-…`）合并（`RUNS` 编号从 2000 起，和 `ACTIONS` 不冲突）；执行时间线写进动态的 `action_run_log`（`ACTIONS` 自带 `log`，`RUNS` 用 `action/derived/ACTION_EVENT_RESOLVERS.action.json`）。各段只写自己的表，顺序见上一条：`ext_system` 由 `integration` 在 action 之前写；定义表、事件和投递表由 action、event 在 dynamic 之前写；`writeback_call`、`reconcile_task` 由 `integration_runs` 在 dynamic 之后写。
- **seed 保留 mock 原值**：外部幂等键即使重复（如告警生成的 `act-al-…`）也照原样入库。`action_run` 加 `is_seed` 列，`idem_key` 只对非 seed 行唯一：`CREATE UNIQUE INDEX … ON action_run(idem_key) WHERE NOT is_seed`（建议动态这样建）。
- **`writeback_call`**：执行记录侧的重试次数、耗时看动态的 `action_run`。**一行 = 一次业务调用**（同 `extCalls` 的一行），`attempt` 记这次调用一共试了几次（前端的「第 N 次」），重试不另起行。seed **只取 `system/derived/extCalls.json`**，不从 `RUNS`/`ACTIONS` 展开；带 `runId` 的行写进 `action_run_id`，执行记录这边的重试次数、耗时仍看动态的 `action_run`，两边不合并，所以不会重复。
- **对账任务**：seed 进来的结果未知记录（`ACTIONS` 20 条、`RUNS` 54 条）都建 `reconcile_task`，**`tries=3, state=manual, manual=true, round=1, next_at=null`**（mock 里没有下次查询时间，时间线已写到“自动查询 3 次仍无法确认 → 转人工”），不会再被调度。种子里对账说明的原文（如“（某人 复核）”）照原样保留，不改写。
- **适配器全部用 mock**：seed、dev、test 环境里 `integration/` 下的适配器都是 mock 实现，`query_by_key` 一律返回枚举里的「查无」（计 1 次）。seed 的结果未知记录都是 `state=manual, next_at=null`，定时对账不会扫到，页面不变；只有人工选「仍无法确认」再开一轮时才会真的去查，mock 连续 3 次「查无」后再转人工，这一轮新增的时间线是用户操作产生的，属预期。
- **统计**：`runDaily`、`eventDaily`、订阅方的 `weekly` 和明细对不上（另一个随机种子），**不处理**——照原样入库，不要求 mock 内部一致。
- **校验**：seed 之后跑对比脚本，逐个接口请求，和导出的 JSON 比较，必须完全一致（分页、`total`、排序、统计数字、中文枚举值）。

### 7.2 导出项 → 表 对照

> 文件都在 `backend/seeds/json/<导出组>/` 下：常量导出 `<导出组>/<导出名>.json`，函数结果 `<导出组>/derived/<文件名>.json`（文件名见 §7.1.1）。下表“mock”列写导出项，对应文件按这个规则找。`CR-NNNN`（变更单编号、`cr_item`）归静态，本模块只引用。

| mock（文件 · 导出） | 表（§9） | 备注 |
|---|---|---|
| `actionEvent.ts` · `ACTION_TYPES`（`DEFS` 经 `withSystem` 处理） | `action_type`、`action_type_version` | `desc` 里的 `{sys}` 存**模板原文**，读时替换；`system/timeout/retry/idempotent` 不存，从 `ext_system` 关联 |
| `ACTION_GROUPS` | `dict_item(dict='action_group')` | — |
| `RULE_ACTION`、`RULE_CONFIRMER` | 动态的 `rule.action_type_id`、`rule.confirmer` | 本模块只读 |
| `paramsOf()` | `action_param` | 包括 `GENERIC_PARAMS` 生成的行 |
| `condsOf()` | `submission_criterion` | 包括 `GENERIC_CONDS` |
| `writebackOf().edits` / `.effects` | `ontology_edit_rule` / `side_effect` | — |
| `ActionTypeDef.sysId/endpoint/idemKey` | `action_type.sys_id/endpoint/biz_key_template` | `idemKey` 是业务键模板，接口字段名不变 |
| `UNKNOWN_FOLLOWUP`、超时处理文案 | 代码常量 | 不进库 |
| `RUNS`（= `action/derived/runsOf.json` 并集；时间线 = `action/derived/ACTION_EVENT_RESOLVERS.action.json`） | `action_run`（**动态**）+ `action_run_log`（**动态**）+ `writeback_call` + `reconcile_task` | 动态按 id 和 `ACTIONS` 合并；`is_seed=true` |
| `runStats()` / `runDaily()` | 统计接口实时算；`runDaily` 写进 `action_run_daily_stat` | 和 `RUNS` 对不上，不处理，原样入库 |
| `versionsOf()` + `ACTION_VERSION_NOTES` / `EVENT_VERSION_NOTES` | `action_type_version` / `event_type_version`（关联 `ontology_release`） | 发布号 `v2026.10.3` 等要和 `releases.ts` 对上 |
| `EVENT_TYPES` | `event_type`、`event_type_version` | — |
| `EventType.specific` | `event_payload_field` | — |
| `ENVELOPE`（`payloadOf` 结果的最后 10 项） | `event_envelope_field` | 全局 10 行 |
| `EVENT_SOURCES` | `dict_item(dict='event_source')` | — |
| `subscribersOf()`（`SUBS`、`SUBS_RULE`、默认） | `event_subscription` | `weekly` 存进 `event_subscription_weekly_stat` |
| `EVENTS`（= `action/derived/eventsOf.json` 并集；时间线 = `ACTION_EVENT_RESOLVERS.event.json`） | `event`、`event_delivery` | 按 `subscribers` 生成投递记录：“部分投递”的 1 条记为待重试，“投递失败”的全部记为死信【推断，与抽屉文案一致】 |
| `eventDaily()`、`EventType.weekly` | `event_daily_stat` | 和 `EVENTS` 对不上，不处理 |
| `extsys.ts` · `EXT_SYSTEMS` | `ext_system`（`integration` 模块，归我） | PDE 的外部系统页通过我的接口读写；密钥**加密存储**，mock 值只是示例 |
| `extsys.ts` · `extCalls()`（`system/derived/extCalls.json`） | `writeback_call` | 外部系统页的调用记录；动态原来的 `ext_call` 并入此表 |
| `dynamic.ts` · `RULES`、`ALERTS`；`actions.ts` · `ACTIONS` | 动态的 `rule`、`alert`、`action_run` | 本模块只引用 |
| `releases.ts` · `DRAFT_TARGETS`（CR-0013 → `store_price_changed`，CR-0014 → `adjust_region_budget`）、`releases.ts` · `DRAFTS` | 静态的 `change_request`、`cr_item` | 静态 seed；本模块确认目标正确 |
| `stores.ts` / `products.ts` / `orders.ts` | 基础层的对象实例 | 必须先入库 |

### 7.3 页面操作 → 接口 → 表 核对清单

| # | 页面操作 | 接口 | 表 |
|---|---|---|---|
| A1 | 动作列表：搜索、分组、翻页 | `GET /action-types` | action_type, action_type_version |
| A2 | 选中动作、看概览基本信息 | `GET /action-types/{id}` | action_type, ext_system |
| A3 | 使用该动作的规则与告警 | `GET /action-types/{id}/used-by` | rule, alert（动态） |
| A4 | 近 30 天统计、14 天柱图 | `GET …/stats`、`…/stats/daily` | action_run, action_run_daily_stat |
| A5 | 最近异常、执行记录：筛选和分页 | 动态的 `GET /action-runs` | action_run（动态） |
| A6 | 执行抽屉和时间线（版本显示**当前类型版本**） | 动态的 `GET /action-runs/{id}` | action_run, action_run_log（动态）, writeback_call |
| A7 | 参数页签和分页 | `GET …/params` | action_param |
| A8 | 提交条件页签 | `GET …/submission-criteria` | submission_criterion |
| A9 | 写回与副作用、执行顺序 | `GET …/writeback` | action_type, ontology_edit_rule, side_effect, ext_system |
| A10 | 编辑写回时的外部系统下拉 | `GET /api/data/v1/ext-systems?writeback=1` | ext_system |
| A11 | 试运行（含草稿视图） | `POST …:dry-run?view=&cr=` | submission_criterion, cr_item（只读） |
| A12 | 版本历史 | `GET …/versions` | action_type_version, ontology_release |
| A13 | 新建动作类型、发起变更（含**变更说明**：StartChangeModal 的“变更说明”文本框）、加入已有变更单 | `POST /change-requests`（静态契约字段名是 `reason`，共用弹窗还没发出来，暂不传）、`…/targets` | change_request, cr_item（静态） |
| A14 | 草稿里新增、编辑、删除、恢复，保存草稿 | `PUT /change-requests/{cr}/items` | cr_item |
| A15 | **撤销修改**（`de.revert()`，丢弃本地未保存的改动） | 不调接口；之后重新读 `view=draft&cr=` | — |
| A16 | **草稿视图切换**（页头“已发布 / 草稿 CR-xxxx”分段切换） | 读接口改带 `view=published` 或 `view=draft&cr=` | cr_item |
| A17 | 提交审核、放弃草稿 | `:submit`、`DELETE …/targets/…` | change_request, cr_item |
| A18 | `?ver=` 历史版本 | 所有读接口带 `ver` | ontology_release（基础层） |
| E1 | 事件列表：搜索、来源、翻页 | `GET /event-types` | event_type, event_type_version |
| E2 | 概览基本信息、载荷摘要 | `GET /event-types/{id}`、`…/payload-fields` | event_type, event_payload_field, event_envelope_field |
| E3 | 概览统计、14 天柱图 | `GET …/stats` | event, event_daily_stat |
| E4 | 载荷字段页签和分页 | `GET …/payload-fields` | event_payload_field, event_envelope_field |
| E5 | 订阅方页签 | `GET …/subscribers` | event_subscription |
| E6 | 近期事件：范围、结果筛选、翻页 | `GET …/events` | event |
| E7 | 事件抽屉和投递时间线 | `GET /events/{id}`、`GET /event-deliveries` | event, event_delivery |
| E8 | 模拟触发（含草稿视图） | `POST …:simulate?view=&cr=` | event_subscription（写测试通道，不写 event） |
| E9 | 版本历史 | `GET …/versions` | event_type_version |
| E10–E15 | 新建、发起变更（含变更说明）、草稿编辑、撤销修改、草稿视图切换、提交和放弃、`?ver=` | 同 A13–A18，`target.kind=event` | change_request, cr_item |

对比、回滚是“暂未开放”，第一期不做接口；回滚开放后按 §4.2 生成新版本（`create_version`）。

---

## 8. 待定问题

1. **审批驳回** → 【推断】，默认不实现（前端没有“已驳回”），见 §8.2 U1。
2. ~~对账确认外部没处理~~ → **已定，以前端为准**：已对账，`reconcileResult=not_executed`，不能重新提交，提示“如仍需执行请重新发起”。
3. ~~事件流分页~~ → **已定**：`page`、`size`，响应 `{items,total,page,size}`，不用游标。
4. ~~统计和明细对不上~~ → **已定，不处理**：不要求 mock 内部一致，照原样入库（2026-10-25 团队决定）。
5. ~~执行记录由谁 seed~~ → **已定**：`ACTIONS` + `RUNS` 都由动态 seed 进 `action_run`，时间线进动态的 `action_run_log`。
6. ~~重试规则~~ → **已改定（PDE，2026-10-27）**：写回提交只发一次、带签名、不重发；明确失败即失败，超时结果未知转对账。原“以前端为准、同键重试到 `maxRetry`”作废，U2 随之不再适用。
7. **审批条件**现在是一段文字（如“单次 ≤ 500 张免审”）。要做成可求值的表达式的话，放在动作类型定义（本模块）还是动态的审批配置？（U8）
8. **试运行、模拟触发在 `?ver=` 下按哪个版本执行**：前端不置灰，后台不拦截，默认按当前查看的版本执行【推断】。
9. **部分投递**剩下的重试成功后，是改成“已投递”还是保留“部分投递”留痕？
10. 【推断】是否提供**死信重投**接口（前端没有入口，第一期不做）。
11. `reconcile` 是独立模块，还是并入 `action` 模块。
12. 第一期是否做**函数支持的动作**（建议不做）。

### 8.2 待用户确认：前端没有、后台可能要加的规则（全部【推断】，默认不做或只警告）

| # | 规则 | 前端现状 | 第一期默认 |
|---|---|---|---|
| U1 | 审批驳回后进入新终态“已驳回”（库内 `rejected`），关联告警退回“处理中”，要再做就新发起（动态的方案） | 没有这个状态 | 不实现 |
| U2 | 业务失败码（如 `NC-2071`）不重试，只对网络错误、429、5xx，在超时预算内同键重试 | 明确失败一律按 `maxRetry` 重试 | 按前端 |
| U3 | 同一幂等键带不同参数时拒绝（`IDEMPOTENCY_CONFLICT`） | 没有这个场景 | 本模块不做；动态在 `action_run` 入口已实现（dynamic-api `08dd366`） |
| U4 | 载荷不兼容变更（删字段、改类型）必须所有订阅方确认才能发布（`INCOMPATIBLE_PAYLOAD_CHANGE`） | 只提示 | 只警告 |
| U5 | 动作类型发布校验：业务键模板的占位符都能从参数取到；对外部和本体都没有写操作时警告 | 没有 | 只警告 |
| U6 | 试运行只给执行人角色、模拟触发只给负责人和管理员 | 不限制 | 不限制 |
| U7 | 历史版本 `?ver=` 下禁止试运行和模拟触发 | 不禁止 | 不禁止 |
| U8 | 审批条件做成可求值的阈值表达式 | 只是文字 | 原文存储 |
| U9 | 模拟触发不计入近期事件和统计 | 前端不写数据 | 不计入（不改变页面） |
| U10 | 执行抽屉显示**执行时锁定的版本**，而不是当前类型版本 | 显示当前类型版本（resolver 用 `a.version`） | 按前端，显示当前版本 |
| U11 | “仍无法确认”再开的自动查询轮数上限（如最多 3 轮后不再允许） | 没有上限，文案只说“继续自动查询” | 每次 3 次，不设轮数上限 |
| U12 | 按角色行级范围（`RowScope`）过滤执行统计、近期事件、投递记录、外部调用记录 | 不过滤 | 不过滤 |
| U13 | 外部系统“测试调用”要求 external-systems · manage | 只按只读置灰 | 按前端（view） |

---

## 9. 表结构建议（【建议】，给 本体小白 对齐用）

### 9.0 表规范（PDE 已定）

- 表名 snake_case、单数，**不加模块前缀**。
- **主键全员统一（PDE 已定，2026-10-25）**：每张表都用代理主键 `id bigint`（identity），业务编码（`request_replenish`、`EXT-ERP`、`EV-…`、`ACT-…`）放在 `code` 列并加唯一约束；**外键一律指向 `id`**；接口对外只用业务编码（字段名仍叫 `id`）。mock 里的复合键都改成唯一约束（下表“唯一”列）。
- **可变表**都带 `created_at, updated_at, created_by, is_seed, lock_version`；**只追加表**只带 `created_at, created_by, is_seed`，**没有 `lock_version`、没有软删除**（下表不重复列出这些列）。
- **软删除只用在前端有删除 / 恢复的地方**：本模块页面上的“删除 / 恢复”（参数、条件、本体编辑、副作用、载荷字段、订阅方）都是变更单草稿里的标记，落在静态的 `cr_item`；外部系统只有“停用”（`status=disabled`），没有删除。所以**本模块的表都不软删**。
- 时间列一律 `timestamptz`，接口格式见 §3.5.1。
- 种子在 `backend/seeds/json/<导出组>/`，导出项 → 表见 §7.2。`dsTables` 只取 `system` 组；`CR-NNNN` 编号和变更单表归静态。

| 表 | 类型 | 主键 | 唯一 |
|---|---|---|---|
| `action_type` / `event_type` | 可变（当前版本展开） | `id` bigint | `code`（前端 id，如 `request_replenish`）；`api` |
| `action_param` | 可变（随发布重写） | `id` bigint | `(action_type_id, api)` |
| `submission_criterion` | 可变（随发布重写） | `id` bigint | `(action_type_id, name)` |
| `ontology_edit_rule` | 可变（随发布重写） | `id` bigint | `(action_type_id, op, obj)` |
| `action_type_rule` | 可变（随发布重写） | `id` bigint | `(action_type_id, rule_id)` |
| `side_effect` | 可变（随发布重写） | `id` bigint | `(action_type_id, target)` |
| `action_type_version` / `event_type_version` | 只追加 | `id` bigint | `(action_type_id, ver)` / `(event_type_id, ver)` |
| `event_payload_field` | 可变（随发布重写） | `id` bigint | `(event_type_id, api)` |
| `event_envelope_field` | 可变（全局 10 行，只读） | `id` bigint | `api` |
| `event_subscription` | 可变 | `id` bigint | `(event_type_id, name)` |
| `ext_system` | 可变 | `id` bigint | `code`（`EXT-ERP`…）；`name` |
| `writeback_call` | 可变（`:mark-checked` 改 `result`） | `id` bigint | `code`（`extCalls` 的 `id`） |
| `reconcile_task` | 可变 | `id` bigint | `action_run_id` |
| `event` | 可变（`outcome` 会变） | `id` bigint | `code`（生产方 `eventId`） |
| `event_delivery` | 可变 | `id` bigint | `(event_id, subscription_id)` |
| `event_dedup` | 可变（到期按 `expires_at` 物理删除） | `id` bigint | `(event_type_id, dedup_key)` |
| `action_run_daily_stat` / `event_daily_stat` / `event_subscription_weekly_stat` | 只追加（seed） | `id` bigint | `(…_id, date)` |
| `dict_item` | 可变 | `id` bigint | `(dict, value)` |

### 9.1 字段

> `ext_system`、`writeback_call` 都在 `integration` 模块，归 Palantir（PDE 已定，2026-10-25），接口前缀 `/api/data/v1`；动态原来的 `ext_call` 已并入 `writeback_call`。
> **外键**：子表的 `action_type_id`、`event_type_id`、`event_id`、`subscription_id`、`action_run_id`、`sys_id`、`rule_id` 一律指向对应表的 `id`（bigint）；seed 时按业务编码（`code`、`R-…`、`ACT-…`）查回 `id`。
> 定义表按静态的约定存：已发布内容来自基础层的版本快照，草稿是 `cr_item` 增量。下面是“当前版本展开表”和运行数据表。枚举列存英文代码（§3.5）。

| 表 | 关键字段 | 说明 |
|---|---|---|
| `action_type` | id(pk bigint), code(unique, 如 `request_replenish`), name, api(unique), grp, status, version, target, target_api, target_kind, retired_release_id(fk ontology_release.id, 可空, 整版回滚时用), owner, desc_tpl, runners, approval, sys_id(fk ext_system.id, 可空), endpoint, biz_key_template, release_id(fk ontology_release.id) | 当前版本 |
| `action_type_rule` | id(pk), action_type_id, rule_id(fk 动态 rule.id；seed 按 `R-…` 查回), seq, mode_text | 使用该动作的规则（`ACTION_TYPES[].usedBy`）；`(action_type_id, rule_id)` 唯一；进快照 |
| `action_type_version` | id(pk), action_type_id, ver, release_id(fk ontology_release.id), change_note, by, at, definition jsonb | 每次整版发布写一行；**只追加，不存状态**：`ver` 等于 `action_type.version` 的那行是生效中，其余是已替代（接口照前端返回「生效中/已替代」） |
| `action_param` | id(pk), action_type_id, seq, name, api, type, required, default_text, rule_text, rule_expr jsonb(可空) | (action_type_id, api) 唯一 |
| `submission_criterion` | id(pk), action_type_id, seq, kind(current_user/parameter/context_object), name, expr_text, expr_ast jsonb, fail_msg | (action_type_id, name) 唯一 |
| `ontology_edit_rule` | id(pk), action_type_id, seq, op(modify/create/link), obj, field | — |
| `side_effect` | id(pk), action_type_id, seq, kind(notify/event/webhook), target, when_text, note | (action_type_id, target) 唯一 |
| `ext_system` | id(pk bigint), code(unique, `EXT-ERP`…), name, vendor, category, endpoint, auth_type, auth_detail（接口 `authDetail`，脱敏原文）, secret_ref(加密), idempotent, idem_header, timeout_ms, max_retry, retry_text（接口 `retry` 原文）, backoff jsonb, rate_limit, owner, status(ok/degraded/disabled), env, usage | `integration` 模块，归我；PDE 的外部系统页通过我的接口读写 |
| `writeback_call` | id(pk bigint), code(unique, `extCalls` 的 `id`), action_run_id(fk action_run.id, 可空), sys_id(fk ext_system.id), idem_key, kind(write/query/test), op_name（接口 `action`，如“补货申请”）, target_label（接口 `target`）, actor（接口 `actor`，发起人原文）, attempt, method, path, http_status, biz_code, elapsed_ms, result(succeeded/failed/unknown), msg, at | `integration` 模块，归我；动态原来的 `ext_call` 已并入；一行 = 一次业务调用（重试计入 `attempt`，不另起行），包括写回、对账查询、外部系统页的测试调用（kind 加 `test`）；通知类调用没有执行记录，`action_run_id` 为空 |
| `reconcile_task` | id(pk), action_run_id(fk action_run.id, unique), idem_key, sys_id(fk ext_system.id), state(querying/resolved/manual), round, tries, next_at, manual(bool，是否已转人工), last_ext_status, resolved_at, reconciler(系统/人名), reconcile_result(succeeded/not_executed) | 归我；**查询次数和下次时间只在这里**。cron 每 15 分钟扫 `next_at`；seed 的结果未知行 `tries=3, state=manual, manual=true, next_at=null` |
| `action_run` *(动态)* | 见 dynamic-api.md 1.2 | 只由动态写，字段、约束以动态文档为准（含 `biz_key`、`reconcile_result`、`UNIQUE(idem_key) WHERE NOT is_seed`）；本模块只读 |
| `action_run_log` *(动态)* | 见 dynamic-api.md 1.2 | 只追加，归动态 |
| `action_run_daily_stat` | id(pk), action_type_id, date, ok, failed, unknown | seed 用 |
| `event_type` | id(pk bigint), code(unique, 如 `order_status_changed`), name, api(unique), source, source_ref, rule_id(fk 动态 rule.id，可空), object, object_api, obj_kind, status, version, owner, desc, dedup_key_rule, dedup_window, delivery, ordering, retention_days, retired_release_id(fk ontology_release.id, 可空, 同 action_type), release_id(fk ontology_release.id) | — |
| `event_type_version` | 同 action_type_version | — |
| `event_payload_field` | id(pk), event_type_id, seq, name, api, type, required, desc, sample | (event_type_id, api) 唯一，不能和信封字段重名 |
| `event_envelope_field` | id(pk), api(unique), name, type, required, desc, sample | 全局 10 行，只读 |
| `event_subscription` | id(pk bigint), event_type_id, kind, name, rule_id(fk 动态 rule.id，可空), filter_text, filter_expr jsonb, then_text, target(topic/url/automation_id), status(enabled/disabled/error) | — |
| `event` | id(pk bigint), code(unique，即生产方生成的 `eventId`；seed 为 `EV-…`), event_type_id, occurred_at, ingested_at, source, object_type, object_id, obj_label, ontology_version, dedup_key, trace_id, payload jsonb, summary, outcome(pending/delivered/alert_created/deduped/partial/failed), subscriber_count, latency_ms, is_test | **第一期不分区**，按保留期定时删除；**兼作 outbox**：部分索引 `(ingested_at) WHERE outcome IN ('pending','partial')`；索引 (event_type_id, ingested_at desc, id) 支持页码分页和 count |
| `event_dedup` | id(pk), event_type_id, dedup_key, expires_at | 去重窗口，**不用 Redis** |
| `event_delivery` | id(pk), event_id, subscription_id, status(pending/sending/delivered/retrying/dead), attempts, next_at, last_error, delivered_at | `UNIQUE(event_id, subscription_id)`；不分区；重投由 procrastinate 执行，只重投失败的订阅方 |
| `event_daily_stat` / `event_subscription_weekly_stat` | id(pk), event_type_id/subscription_id, date, value | seed 用 |
| `dict_item` | id(pk), dict, value, seq | 动作分组、事件来源等字典 |

## 10. 后台实现里的【推断】（第 1 批 `292ee28` + review 修订 `ee39f8b`，基于 master `c705c25`）

前端没写死、实现时由我补的口径，全部【推断】，用户或 PDE 改口径时按这里逐条改。

### 10.1 第 1 批（11 条）

1. **`?ver=` 回退**：快照里没有 `actionTypes`/`eventTypes` 这一节（或该版本不存在这一节）时，退回读当前定义表——前端历史视图显示的就是当前 mock。【推断】
2. **`seed_section` 用当前 mock**：mock 只有当前定义，seed 出来的历史各版快照都用当前定义组装。【推断】
3. **分组来自数据**：`groups` = `['全部']` + 定义表里按首次出现顺序的分组；前端 `ACTION_GROUPS` 是常量，mock 里与此一致。【推断】
4. **`weekly` 读当前行**：事件类型、订阅方的「近 7 天」计数暂作为列存在定义表上（统计表不在本批），seed 来自 mock，以后由统计任务刷新；不进快照。【推断】
5. **`generates` 标记**：`action_type_rule.generates`（规则命中生成本动作，来自 `RULE_ACTION`）暂存在动作类型的规则行上，动态合并后改读规则。【推断】
6. **`retention_days`**：事件 `retention` 原文「N 天」入库存整数天，接口按原文格式输出；不是「N 天」格式 seed 直接报错。【推断】
7. **描述列命名**：接口字段 `desc` 库里统一叫 `description`（避开 SQL 关键字）。【推断】
8. **类型行不存 `release_id`**：定义表只存当前定义，属于哪一版由发布快照和 `*_version.release_ver` 表达。【推断】
9. **版本行 `definition` 为空**：seed 的 `action_type_version`/`event_type_version` 没有逐版定义，`definition` 留 `null`。【推断】
10. **外部系统接口**：
    - 权限：`external-systems · view` 或（`writeback=1` 时）`action-types · view` 二者满足其一；`require_func` 现在是桩，落地后改成「其一」。
    - `kw` 匹配字段：名称 / 编号 / 厂商。
    - 详情带 `actionTypes`（引用它的动作类型）。【推断】
11. **流程时间线 `time`**：mock 没有时间，`time` 按步骤序号输出（`"1"`、`"2"`…）。【推断】

### 10.2 review 修订新增

- **ext-systems `kw`**：前端外部系统页**没有搜索框**，`kw` 是后台补的可选过滤（名称 / 编号 / 厂商包含），不传 = 全部。【推断】
- **`/action-types/{id}/versions`、`/event-types/{id}/versions` 按视图可见性返回 404**：当前视图下已下线（`retired_release_id` 非空）的类型 404；`?ver=V` 下 V 里看不到的类型也 404。【推断】
- **分页**：用骨架的 `PageParams`，先 `clamp(total)` 再切片（参数列表用 `Page.of`），口径见 §3 的「分页」。
- **`restore_section` 的口径**：见 §10.4。

### 10.3 软引用（不建外键）

| 列 | 指向 | 为什么不建外键 |
|---|---|---|
| `action_type_rule.rule_code` | 规则 `R-…`（动态） | 规则表归动态，未合并 |
| `event_type.rule_code` | 规则 `R-…` | 同上 |
| `event_subscription.rule_code` | 规则 `R-…` | 同上 |
| `action_type.target_kind`、`event_type.obj_kind` | 静态的对象类型（前端 id） | 跨模块，静态表结构未定稿 |
| `action_type_version.release_ver` / `event_type_version.release_ver` | `ontology_release.ver` | mock 版本记录里有发布表没有的版本号（如 `v2026.09.2`、`v2026.08.1`），只能按字符串存 |

`action_type.retired_release_id`、`event_type.retired_release_id` → `ontology_release.id` 是**真外键**（迁移 `event_0002`，`ON DELETE` 默认 NO ACTION，CONVENTIONS §6；`event_0001` 时是软引用）。列可空，NULL = 在用。

### 10.4 `restore_section(session, content, *, release_id)` 的行为

`actionTypes`、`eventTypes` 两个 builder 在 `app/modules/action/sections.py`、`app/modules/event/sections.py` 里，各自导出幂等的 `register()`（重复登记同一实例不报错）。由骨架 `load_sections()`（`create_app()`、seed CLI 各调一次）发现并调用；**import 时不登记**。

1. **覆盖定义**：按前端 id（`code`）upsert `content` 里的每个类型，把类型行及子表改回快照里的值：
   - 动作：参数、条件、编辑、副作用、规则
   - 事件：载荷字段、订阅方
   - 外部系统 `sysId` 解析成 `ext_system.id`
2. **缺席即下线**：当前在用、但 `content` 里没有的类型，`retired_release_id = release_id`。不删行，也不加新 `status`。
3. **重新出现即恢复**：`content` 里有的类型清空 `retired_release_id`。所以恢复一个包含该类型的版本时，它会重新出现。
4. **`release_id` 必填**（keyword-only `int`）：骨架 `restore_sections` 先写新回滚版本行，再把它的 `ontology_release.id` 传进来（所有钩子在同一个 SAVEPOINT 里，任一钩子出错整体撤销）。不再有 `max(id) + 1` 兜底。
5. **子行 `is_seed` 跟随父行**：mock 自带重复 api 的参数只在 seed 行上放行。【推断】
6. **快照里没有的运行数据保留旧值**：
   - 规则行的 `generates`
   - 订阅方的 `weekly`，按 `(event_type_id, name)` 对应
   - 全局信封字段：不进快照，不动

   【推断】
7. **版本表只追加**：回滚不写 `*_version` 行。【推断】
8. **`content=None`**：那一版快照里根本没有这一节时，什么都不做。【推断】

## 11. 第 2 批：四个页面切真接口（基于 master `b688d5d`）

前端开关键 `action`（→ `/api/ontology/v1`，动作类型 / 事件类型页）和 `integration`（→ `/api/data/v1`，外部系统页）。
前端接口文件：`src/api/action.ts`、`src/api/integration.ts`。规则：前端为准，后台往 mock 形状靠。

### 11.1 为了对齐前端改的后台

| 改动 | 原因 |
|---|---|
| `GET /event-types/{id}` 加 `specific[{name,api,type,required,desc,sample}]` | 页面草稿编辑（载荷字段 section）和“信封字段”区分都直接读 `EVENT_TYPES[].specific`；值 = `payload-fields` 里 `envelope=false` 的行 |
| `GET /ext-systems` 每行加 `actionCount` | 页面“关联动作”列；= 指向该系统、未下线的动作类型数（含草稿）。【推断】草稿也算，和 mock 按 `ACTION_TYPES` 数一致 |
| `POST /ext-systems` 新建 | 编号 `EXT-N` + 两位序号，取现有 `EXT-N*` 最大值 + 1（`EXT-N01`…）。取号前在本事务里拿事务级咨询锁 `integration.ext_system.code`（`get_adapter(session).xact_lock`，提交 / 回滚自动释放），两个并发新建不会拿到同一个编号（palantir-r3，`tests/test_integration_code_lock.py`）。【推断】mock 里新建只在内存，编号规则后台定 |
| 编辑同时接受 `PUT` 和 `POST /ext-systems/{id}` | 前端 `apiClient` 只有 get / post（`http.ts` 共用、不归我改），所以编辑走 POST。【推断】PUT 保留给文档约定 |
| 路由顺序：`:test` 先于 `{id}` 编辑登记 | 否则 `POST /ext-systems/EXT-X:test` 会被编辑路由吞掉 |
| 校验文案 = 页面文案 | `请填写系统名称`、`请填写供应商`、`接口地址需以 https:// 开头`、`支持幂等时需填写幂等键`（整体校验，`field` 为 `-`），`timeoutMs` 1000–60000、`maxRetry` 0–5；422 `VALIDATION_FAILED` + `details.fields` |
| 重名 | 名称与其他系统重复 → 409 `DUPLICATE_KEY`“系统名称已存在”（改成自己原来的名字不算） |
| 不支持幂等时 | 后台把 `idemKey` 存成 `-`、`maxRetry` 存成 0，`retry` 文案由后台按 `idempotent / maxRetry` 生成（同页面 `retryText()`） |
| `usage` 不传 = 不改 | 编辑表单不含用途说明 |
| `:test` 不再模拟执行 | 当前没有真实连通性执行器，正常/降级统一503 `EXT_SYSTEM_TEST_UNAVAILABLE`（`details.reason=executor_not_configured`）；未发送HTTP请求，也不创建 `writeback_call`。既有开发库调用记录保留，读列表仍来自DB；停用409/不存在404保留。页面测试按钮说明未配置，不生成临时演示调用。 |
| 调用详情报文与自动查询 | 当前 `writeback_call` 没有原始请求/响应体列，页面明确显示“未保存”，不再依据 HTTP 200 合成 `{code:0,msg:success}` 或拼造请求体。未知结果说明人工核对，不承诺尚未配置的每15分钟自动查询执行器；不新增外部HTTP调用、凭据或自动worker。 |
| `writeback_call` 列 | 按前端 `ExtCall` 建：`code`=id、`sys_id`、`at`=time、`op_name`=action、`target_label`=target、`method`、`path`、`idem_key`、`elapsed_ms`=ms、`http_status`=http（超时为空）、`result`、`attempt`、`actor`、`msg`、`action_run_id`=runId。§9.1 设计里的 `biz_code` 前端没有，**没建**。`kind`【推断】：种子里 GET → query，其余 → write；测试调用 → test |
| 种子 | `integration_runs` 阶段（registry 已登记，dynamic 之后）读 `system/derived/extCalls.json`（PDE `export-mock` 原样输出，756 行，20 行带 runId）；**不用改 `seeds/loader.py`** |
| 迁移 | `integration_0002`，`down_revision = dynamic_0002`。合并顺序 static_0002（接 event_0002）→ dynamic_0002（接 static_0002）→ integration_0002；改接是本分支最后一个单独提交，单独合时回退它即回到 event_0002 |
| 排序 / `counts` | 见 §3.1；dev 时钟固定在 15:42，比种子里最晚的调用（16:29）还早，所以测试调用不能靠时间排最前，按 `kind=test` 排【推断】 |
| `:test` 要 manage | 同 §3.6 U13【推断】；功能权限校验本身是 PDE 的扩展点（`require_func` 目前放行） |

### 11.2 前端怎么接（不改页面样式）

- **动作类型 / 事件类型**：左侧列表（分组 / 来源、关键字、分页 22）直接调列表接口；详情和各页签（参数、提交条件、写回、版本历史；载荷字段、订阅方、版本历史）进页面时一次取回所有类型做成快照（17 + 15 个类型，约 180 个 GET），`?ver=` 变了重取。原因：页面和草稿编辑（`useDraftEdits`）都是同步读 `paramsOf()` 这类函数，快照方式页面改动最小。快照没到前只显示加载中。【推断】数量大了再改成按选中项取。
- **外部系统**：列表、详情（关联动作类型）、新建、编辑、测试调用、最近调用（结果筛选、分页、`?call=` 详情抽屉）、人工核对走真接口；测试调用和人工核对后重新拉当前页。
- **动作类型执行记录、最近异常、概览统计**：开关 `dynamic`，`GET /action-runs?actionType=&scope=runs&size=200` 取该类型全量，统计按 mock `runStats()` 口径在前端算；取不到用 mock。
- **动作类型写回 tab 的外部系统下拉**：`GET /ext-systems?writeback=1`（每次进动作类型页重取，取到之前用 mock `writebackSystems()`）。

### 11.3 还没接通（仍读 mock）

完整的未完成清单、代码位置和接手步骤见 `backend/docs/backend/handoff/palantir.md`。

| 页面操作 | 原因 |
|---|---|
| 动作类型的执行抽屉 | 抽屉接动态 `GET /action-runs/{id}`（抽屉是公共组件，见 §12.5） |
| ~~近 14 天趋势图、事件类型“近期事件”和趋势~~ | 第 3 批已接（§12） |
| 两页的草稿保存 / 提交 | 走变更单（versioning，不归我） |
| 删除外部系统 | 页面没有删除操作 |

## 12. 第 3 批：事件运行数据 + 动作趋势（分支 `palantir-r2`，基于 master `bb47a5c`）

### 12.1 新表 / 迁移 / 种子

| 表 | 模块 / 迁移 | 种子（export-mock 原样输出，不手改） | 行数 |
|---|---|---|---|
| `event`（code = EV-…，event_type_id、occurred_at、obj_kind、object_id、obj_label、summary、outcome、subscriber_count、dedup_key、latency_ms、payload、is_test；索引 `(event_type_id, occurred_at, id)`） | event · `event_0003`（接 `admin_0002`） | `action/derived/eventsOf.json`（15 次调用的并集） | 1,243 |
| `event_daily_stat`（event_type_id、stat_date、value；唯一 `(event_type_id, stat_date)`） | event · `event_0003` | `action/derived/eventDaily.json` | 15 × 14 |
| `action_run_daily_stat`（action_type_id、stat_date、ok、failed、unknown；唯一 `(action_type_id, stat_date)`） | action · `action_0002`（接 `event_0003`） | `action/derived/runDaily.json` | 17 × 14 |

`event.outcome` 枚举 `EventOutcome`：delivered=已投递、alert_created=已生成告警、deduped=已去重、partial=部分投递、failed=投递失败（只有前端这 5 个）。

### 12.2 接口（挂 `/api/ontology/v1`，功能权限同所属类型的 view）

| 接口 | 返回 | 说明 |
|---|---|---|
| `GET /event-types/{id}/events?range=24h\|7d&outcome=&page=&size=14` | `{items: EventRecord[], total, page, size}` | 按 `at` 倒序（同 mock）；`outcome` 中文、`全部`/不传不过滤；非法 range / outcome 422；类型不存在 404；页码越界收拢到最后一页 |
| `GET /event-types/{id}/stats` | `{weekly, total, dedup, failed, partial, p95, asOf, daily: [{date: "MM-DD", value}] × 14}` | 健康口径同 `EventTypes.vue` 的 `health`（该类型全部事件；p95 = 升序第 floor(n×0.95) 个，没有事件 0）；`weekly` = event_type.weekly；`asOf` = clock 当前（分钟） |
| `GET /events/{id}`（palantir-r3） | 一条 `EventRecord` | 事件抽屉用：字段和 `eventById(id)` 完全一样（不加投递明细，mock 没有）；按事件编号（`EV-…`）找，`is_test` 的不算；不存在 404 `NOT_FOUND`（`message`「事件不存在」，`details.id`）；功能权限 event-types · view |
| `GET /action-types/{id}/stats/daily?days=14` | `{items: [{date: "MM-DD", ok, failed, unknown}]}` | days 1–90，截至今天，缺的天补 0 |

`EventRecord` 字段 = mock：`id, typeId, at("YYYY-MM-DD HH:mm:ss"), objKind?, objId, objLabel, summary, outcome, subscribers, dedupKey, latencyMs`（objKind 为空返回 null，前端 `clean()` 收掉）。

### 12.3 【推断】

1. **`range=7d` 不设时间下限**：mock「近 7 天」不按时间过滤（`eventsOf()` 全部样本，低频类型有 7 天前的，最早 2026-08-26），前端为准；`range=24h` = `clock.now() − 24h` 起（和前端 `CUTOFF_24H = 10-24 15:42:18` 之间 18 秒内没有事件，结果一致）。真实上线后 7d 是否改成真 7 天，需 PDE / 用户定。
2. **趋势和明细对不上**：`eventDaily` / `runDaily` 是 mock 另一套随机数，按 §7.1.2 原样入库成统计表，不从 `event` / `action_run` 汇总；以后由统计任务刷新。
3. **"MM-DD" → 日期**：年份取 `clock.today()` 的年，月日在今天之后算去年。
4. **运行数据不分版本**：`?ver=` 不影响这三个接口，按当前类型表找类型；已下线类型照样能查。
5. `is_test` 默认 false，列表 / 统计只算 false（模拟触发的测试事件以后写 true）。设计稿的 `pending`（outbox 待投递）前端没有，枚举不加。
6. 前端：`EventTypes.vue` 近期事件页签按 range / 结果 / 页码走后台分页；概览「最近事件」= `…/events?range=7d&size=5`；健康和趋势 = `…/stats`；「截至」改用 `asOf`。typeId 对不上（切换中、草稿新类型 404）或后台没起来时读 mock。

### 12.4 外部系统编辑改 PUT

`src/api/integration.ts` 的 `updateExtSystem` 改用 `api.put`（`apiClient` 已有 put）。后台 `POST /ext-systems/{id}` 保留为同义别名。

### 12.5 仍未接

| 内容 | 原因 |
|---|---|
| 动作执行抽屉、事件抽屉（`drawer.open('action' \| 'event', id)`） | 抽屉解析在公共文件 `src/components/actionEventResolvers.ts`（不归我）。动态已有 `GET /action-runs/{id}`（动作抽屉 PDE 已接，`c5ec3b3`）；`GET /events/{id}` palantir-r3 已实现，等 PDE 把 `event(id)` 的 `eventById` 换成它 |
| `event_delivery` / `event_dedup`、`POST /internal/v1/events` | 没建（投递流水、去重窗口、内部接收） |
| 两页草稿保存 / 提交（变更单） | 静态还没出 create / edit 变更单接口，保持 mock |

---

*审阅：请 动态本体负责人 核对 §1、§5.1 和 §9 的 `action_run`，静态本体负责人 核对 §4.2 和 §7.2 里的 CR 部分，本体小白 核对 §9。*

## 对象类型“行为 / 动作”真实读取（2026-10-26）

`ObjBehaviorActions.vue` 真模式读取 `/object-types/{id}/behavior/stats` 的轻量动作定义和近30日实际运行聚合；参数只在打开抽屉时请求。动作中心按 `/action-runs?scope=center&objKind={objectId}&page=&size=10` 逐页读取，摘要计数由后台在完整匹配范围聚合。生命周期迁移关联来自现有 `/object-types/{id}/lifecycle`，只有该接口404视为未配置，其他错误直接显示。

所有读取在同一局部结果中更新；身份、预览、版本或对象切换立即清空旧数据，迟到响应丢弃，失败不回演示值。没有动作类型但有历史动作中心记录时仍展示真实记录。本页只读，不提交动作，也不使用固定的对象→动作名映射、运行统计、参数或回退限制。

动作/事件定义列表和参数、载荷、近期运行均默认10条，通过SQL计数和LIMIT/OFFSET分页。轻量目录不预加载全部类型的子表，选中类型后才读取详情；编辑前仍读取该目标完整基线，防止分页保存丢行。历史子段缺失返回空，不借当前定义补齐。

`GET /action-types/{id}/stats?days=30` 从实际执行记录聚合最近30日（允许1—90日），包含下边界并排除未来记录；沿执行历史范围排除动作中心记录。返回 `{total,ok,failed,unknown,pending,confirm,rate,avgMs}`，需要动作类型及动作中心查看权限。该汇总不从当前表格页或旧演示日统计推算。

## 13. 变更单草稿：前端接法补充（分支 `palantir-r3`，基于 master `b475283`）

只改前端和外部系统编号，后台接口没变；变更单接口契约见 `handoff/static.md`。以下都只在 `VITE_PALANTIR_CR_WRITE=1` 且 `action` 走真接口时生效，开关默认关，关着时行为同以前。

| 场景 | 前端做法 |
|---|---|
| 进页面 / 刷新 | `GET /change-requests/draft-targets` + `GET /change-requests?status=草稿`：库里有草稿登记的本页对象，用 `useDraftView().start({join, target})` 重新登记；mock 登记里库中已没有的，用 `forgetTarget` 去掉 |
| 选中有草稿的对象 | 快照就绪后 `GET /change-requests/{id}/items?kind=&targetId=`，按行叠回草稿视图（`delete` 标删除、`update` 替换、`add` 追加）；每个 变更单+对象 只读一次，之后以本页为准 |
| 改动数 | 横幅和提交确认里的“变更单改动”取库里的 `changes`（发起 / 加入时的 CR、`PUT items` 返回、放弃后重读 `GET /change-requests/{id}`），取不到时按前端计 |
| 放弃草稿 | 先弹确认卡；确认后 `DELETE /{id}/targets/{kind}/{targetId}`，成功后调 `useDraftView().cancel(id)` 撤掉登记并切回已发布版 |
| 提交审核 | `POST /{id}:submit`；没有审核人返回 409 `CR_NO_REVIEWER`，页面弹 `提交审核失败：还没有指定审核人（CR_NO_REVIEWER）`，不跳转 |


## 14. Webhook 出站：签名与自动重试（分支 `palantir-t1`，2026-10-27）

> 设计与取舍见 `backend/docs/backend/palantir-tier1-design.md`。头名与签名串格式为本仓库自定【推断】，接入方需按此实现验签。

### 14.1 适用范围

| 出站 | 签名 | 自动重试 |
|---|---|---|
| 动作副作用 通知/Webhook（`action.effect` outbox） | 是 | 是 |
| 事件 HTTP 投递（`event_delivery`） | 是 | 是 |
| 动作写回提交（外部执行器，`writeback_call`） | 是 | **否**（只发一次；明确失败即失败，结果未知转对账，不重发业务写入【仓库】；§1.1/§2/§4/§5 已按此改正） |
| 适配器 GET 查询/探测 | 否 | 否 |

### 14.2 请求头【推断】

| 头 | 值 | 说明 |
|---|---|---|
| `Idempotency-Key` | 副作用 = outbox `event_id`；投递 = `event-<sha256>`；提交 = `action_run.idem_key` | 同一条业务的所有尝试**不变**，接收方按它去重 |
| `X-Signature-Timestamp` | Unix 秒 | 每次尝试重新生成；仅配置了签名密钥时出现 |
| `X-Signature-256` | `sha256=` + hex(HMAC-SHA256(密钥, `"<timestamp>."` + 原始请求体字节)) | 仅配置了签名密钥时出现 |
| `Content-Type` | `application/json` | 请求体为紧凑 JSON（UTF-8，不转义中文） |
| `X-Ontology-Id`、`Authorization: Bearer …` | 同前 | 不变 |

接收方：取原始字节验签（常量时间比较），并拒绝与本地时间相差超过 5 分钟的时间戳【推断】；再按 `Idempotency-Key` 去重。

### 14.3 密钥配置

- 适配器（`RENGU_INTEGRATION_ADAPTERS` 每个系统的配置）：`"signing_secret": "…"`。
- 事件路由（`RENGU_EVENT_DELIVERY_ROUTES` 每条路由）：`"signing_secret"`、`"test_signing_secret"`。
- 值为明文或 `fernet$…`（`app.modules.data.secrets.encrypt_password` 加密，`RENGU_DS_SECRET_KEY`）。至少 16 字节。
- 未配置 = 不签名（与旧行为一致）；配置了但无效 = 不发送，错误码 `SIGNING_SECRET_INVALID` / `SIGNING_SECRET_UNDECRYPTABLE`。

### 14.4 重试与结果【推断】

- 可重试：超时、网络错误、HTTP 429、5xx。其它非 2xx 立即失败、不重试。
- 退避：`30s × 2^(n-1)`，上限 15 分钟；`Retry-After`（秒）取较大值。由 worker 定时扫描执行，不在请求内等待。
- 次数（不含首发）：副作用 = 外部系统 `maxRetry`（0–5，0 不重试）；事件路由 = 路由 `max_retries`（默认 3，上限 5）。
- 用尽：429/5xx → 失败（副作用 `EXTERNAL_HTTP_<code>`；投递 `HTTP_SUBSCRIBER_REJECTED`）；超时/网络错误 → 结果未知（投递 `HTTP_RESULT_UNKNOWN_REQUIRES_REVIEW`）。
- 重试排队中：副作用 `payload.state = "retrying"`、`last_error = EFFECT_RETRY_SCHEDULED`；投递 `state = 待处理(pending)`、`error = HTTP_RETRY_SCHEDULED`、`nextAt` 为下次时间（会出现在投递异常收件箱的“待处理”里）。
- 记录：副作用逐次写入 outbox `payload.attempts[]`（`attempt, at, httpStatus, error, retryable, signed, elapsedMs`）；投递逐次写 `event_delivery_record`（`attempt_started` / `attempt_finished`）。无新表、无新迁移。

## 15. 动作执行策略 / 机器接口 / 入站服务账号（分支 `palantir-t2`，基于 master `bc32ce6`，2026-10-27）

无迁移：策略存在动作类型版本定义（JSON）里，额度从时间线日志计数【仓库】。全部由动作类型 / 事件类型配置和 API 密钥配置驱动，代码不写死业务对象或本体编号。

### 15.1 执行策略字段（变更单 `policy` 段）

变更单写回新增 `section = "policy"`，`itemKey` 固定 `"policy"`，`value` 为整个策略对象；发布后写入新动作类型版本 `definition.policy`，快照同步【仓库】：

```json
{"mode": "auto", "callers": ["人", "服务账号"], "overflow": "stage",
 "limits": {"dailyCount": 50, "perCallerDailyCount": 20,
            "amount": {"param": "qty", "max": 500}, "objects": ["S001", "S002"]}}
```

| 字段 | 说明 |
|---|---|
| `mode` | `suggest` 只给建议（暂存返回 409 `ACTION_POLICY_SUGGEST_ONLY`，请用试运行）/ `confirm` 人工确认（默认，= 现有行为）/ `auto` 在上限内自动执行 |
| `callers` | 允许的调用方：`人`、`规则`、`AI`、`服务账号`；省略 = 不限制（默认）。不在内 → 403 `CALLER_NOT_ALLOWED`，不建记录 |
| `limits` | 只有 `auto` 能设，且 `auto` 必须有 `dailyCount`。`perCallerDailyCount` 每个调用方身份每日上限；`amount` 指定数值参数 API 与单次上限；`objects` 允许的对象 ID 列表 |
| `overflow` | 固定 `stage`（超限转人工确认），写回时自动补上 |

调用方映射【推断】：来源 手动 / 推演 / 告警处理 → 人；问数 → AI；规则 / 告警 → 规则；API 密钥的主体是服务账号时一律算 `服务账号`。

发布校验（409 `WRITEBACK_FAILED`，整单回滚）：格式不合法；`auto` 但动作不是 `免审`（`auto` = 系统代为确认 + 免审）。

### 15.2 分流

`POST /action-runs:stage`（人工）和 `POST /machine/actions/{typeId}:execute` 在新建记录后按策略分流【仓库】：

- `confirm` / 默认：与原来完全一样，返回 `待确认`。
- `auto`：依次检查 免审 → 对象范围 → 数值上限 → 当日总额度 → 调用方额度（自然日按 `display_tz`，同一动作类型加事务级锁）。全部通过 → 系统代为确认、免审提交，时间线记“按执行策略自动执行”（说明里有调用方和额度 n/N）；任一不满足 → 保持 `待确认`，时间线记“未自动执行，转人工确认：原因”，之后照常人工 `:confirm`。
- 规则、告警、问数、推演产生的记录目前一律暂存，不自动执行（缺口）。
- 服务账号调用：~~即使在额度内也转人工确认~~（t2 行为，t5 已改，见 §17）；服务账号发起的记录不指定确认人，转人工时任一有动作中心管理权限的人可确认【推断】。

### 15.3 机器接口（挂 `/api/ontology/v1`）

鉴权：`Authorization: Bearer rg_…`（本体小白的 API 密钥 / 服务账号，scope 校验与动作类型白名单由 `require_api_scope` 统一处理），人工会话同样可调。

| 接口 | scope | 说明 |
|---|---|---|
| `POST /action-types/{typeId}:dry-run` | `actions:dry-run` | 试运行（已有接口，不另开 `/machine` 别名） |
| `POST /machine/actions/{typeId}:execute` | `actions:execute` + 动作类型白名单 | 请求头 **必须** `Idempotency-Key`（`[A-Za-z0-9_-]{1,80}`）；body `{"objKind"?, "objId", "params"?: "JSON 字符串"}`；首次 201，同键同内容 200 返回原记录，同键不同内容 409 |
| `GET /machine/action-runs/{id}` | `actions:execute` | 查执行结果 |

响应：

```json
{"ok": true, "status": "待确认|执行中|成功|失败|结果未知", "runId": "AR-…",
 "policyMode": "confirm|auto|null", "overflowReason": "今日自动执行已达上限 2", "typeVersion": "v3"}
```

`policyMode`、`overflowReason` 只在本次执行请求里有值，查询接口为 `null`。错误沿用 `AppError`。服务账号密钥要读对象（参数校验 / 行范围）需同时带 `objects:read`【仓库，按密钥规则】。

### 15.4 入站事件：服务账号密钥

`POST /internal/v1/events` 同时接受【仓库】：

- `Bearer rg_…` API 密钥：须有 scope `events:ingest`，否则 403；密钥设了事件类型白名单时，事件类型编码或 API 名须在其中，否则 403 `NO_PERMISSION`；`X-Ontology-Id` 按密钥工作空间规则校验。事件记录 `created_by = "api:{服务账号编号或密钥前缀}"`，审计调用方由密钥鉴权写入。
- 原静态令牌：过渡期保留，行为不变（`created_by = "internal:event"`）。

### 15.5 未做

评测门槛（`evalPassedAt`）；`objects` 用行范围表达式；规则 / 问数等来源自动执行；服务账号代执行身份；`suggest` 在试运行 / 选项接口里的展示；前端策略编辑器；`/machine/objects:query`、函数调用别名、AI 工具导出；机器响应里的 `checks` / `edits`。

> 2026-10-27（UTC+8）补：§15.5 中 `/machine/objects:query`、函数调用别名、AI 工具导出、`checks` / `edits` 已在 palantir-t3 完成，见 §16。

## 16. 机器接口第 3 期：对象查询 / 函数调用 / AI 工具清单（分支 `palantir-t3`，基于 master `c55f370`，2026-10-27）

无迁移。全部挂 `/api/ontology/v1`，鉴权同 §15.3（API 密钥或人工会话）；工具清单由已发布元数据生成，代码不写死业务对象或本体编号【仓库】。

### 16.1 接口

| 接口 | scope | 说明 |
|---|---|---|
| `POST /machine/objects:query` | `objects:read` | body = 问数对象检索参数 + `objectType`（对象类型 API 名）：`{"objectType", "q"?, "filters"?, "sort"?, "page"?, "size"?, "withFacets"? = false}`。复用 `/api/qa/v1/object-types/{api}/search` 的检索和**同一套行级范围**（用户密钥按本人、服务账号按绑定角色），另需页面权限 对象读取 + `object-search` 查看。返回 `{objectType, items, total, page, size, titleProp, bound, boundProps, facets}` |
| `POST /machine/functions/{id}:invoke` | `functions:invoke` | 函数调用别名，复用函数运行时；只允许 **已发布** 函数（草稿/停用 409 `FUNCTION_NOT_PUBLISHED`，不存在 404）。body `{"input": {...}}`，返回 `{ok, functionId, version, output, ms, at}` |
| `GET /machine/tools?format=mcp\|openai` | `meta:read` | 只读工具清单，见 16.2 |

### 16.2 工具清单

`/machine/tools` 是 `GET /meta/capabilities`（静态，`static/capabilities.py`）的薄转换层：动作类型、函数取自能力目录（已按页面权限过滤），再叠加密钥的 scope 与白名单【仓库】。

| 工具 | 名字 | 出现条件 | 参数 schema |
|---|---|---|---|
| 动作类型 | `action_{api}` | 目录有 `actions` 段 + 动作中心管理权限 + 密钥有 `actions:execute` 且在动作类型白名单内（目录的 `machine.keyAllowed`）；执行策略 `suggest` 不导出；`callers` 不含本调用方（人 / 服务账号）不导出 | `{objKind: enum[目标对象], objId, params: {参数 API: 类型}}`，必填参数进 `required` |
| 事件类型 | `event_{api}` | 目录 `events` 节（需事件类型页面权限）里 `ingest.accepting = true`（已发布且来源为 外部系统）；密钥看 `ingest.keyAllowed`（`events:ingest` + 事件类型白名单）（t5 起，见 §17） | 入站事件信封：`eventId, eventType, occurredAt, source, objectType, objectId, payload{字段}`，`dedupKey` / `traceId` 可选 |
| 函数 | `fn_{name}` | 目录有 `functions` 段（已发布）+ 密钥有 `functions:invoke` | `{input: {入参: 类型}}` |

返回：

```json
{"format": "mcp",
 "tools": [{"name": "action_OpsLocal", "description": "…；执行策略：人工确认", "inputSchema": {"type": "object", "…": "…"}}],
 "bindings": {"action_OpsLocal": {"method": "POST", "path": "/api/ontology/v1/machine/actions/ops_local:execute",
                                   "scope": "actions:execute", "idempotencyHeader": "Idempotency-Key",
                                   "policyMode": "confirm", "version": "v1"}}}
```

`format=openai` 时 `tools[i] = {"type": "function", "function": {name, description, parameters}}`。名字只含 `[A-Za-z0-9_-]`、最长 64，重名加序号。类型映射：字符串/日期等 → `string`（日期带 `format`），整数 → `integer`，数值/金额 → `number`，布尔 → `boolean`，列表 → `array`；对象引用 → 对象 ID 字符串【推断】。

### 16.3 执行响应补字段

`POST /machine/actions/{typeId}:execute` 与 `GET /machine/action-runs/{id}` 新增【仓库】：

| 字段 | 含义 |
|---|---|
| `checks` | `{"passed": true, "checked": n}`：暂存时提交条件全部通过（不通过直接 422 `SUBMISSION_CRITERIA_FAILED`，不建记录）；n = 当前版本条件数 |
| `edits` | 当前版本声明的编辑 `[{op, obj, field}]`（声明，不是实际写入值） |
| `retryable` | 该记录能否重新提交（同详情 `can.resubmit`） |

动作类型版本已变（记录版本 ≠ 当前版本）时 `checks` / `edits` 为 `null`。另外 `params` 现在可直接传 JSON 对象（仍兼容 JSON 字符串）。

### 16.4 动作意图评测集

`backend/tests/fixtures/palantir_action_intents.yaml`：20 条通用用例（调用 8、追问 4、拒绝 5、超限转确认 3），只用测试夹具名字（store / ops_local / ops_cross / OpsSignal / calculate）；`tests/test_palantir_machine_tools.py` 校验每条期望参数能通过导出的 `inputSchema`、拒绝用例的工具确实不导出、确认用例真实执行后为 `待确认`。格式说明见文件头。

### 16.5 未做

能力目录没有事件类型段（工具清单自己读事件类型）；动作参数在目录里没有枚举选项 / 规则文本（枚举参数导出为普通字符串）；目录不能按段只算动作/函数；函数无按密钥白名单；`suggest` 动作不导出（无试运行工具）；清单里没有对象查询工具；只读角色用户的密钥仍不能调 `/machine` 的 POST。

### 16.6 补丁（分支 `palantir-t4`，基于 master `492d746`，2026-10-27）

- 入站 `POST /internal/v1/events`（静态令牌与 API 密钥一样）**只接受来源为 外部系统 的事件类型**；数据同步 / 动作 / 规则来源返回 403 `EVENT_SOURCE_NOT_EXTERNAL`，不写任何记录。平台内部发射（动作副作用、动作事件）不走 HTTP，不受影响【仓库】。工具清单的事件工具相应只导出外部来源。
- 提交校验：`枚举列表` 参数的每一项都必须在规则声明的枚举集合内，否则 422 `SUBMISSION_CRITERIA_FAILED`（`details.failed[].fail` = “参数值不在已声明枚举集合：…”，与单值枚举一致；按 `core/errors.py` 约定不另设错误码）【仓库】。

## 17. 服务账号自动执行 / 工具清单改用能力目录（分支 `palantir-t5`，基于 master `343ba5f`，2026-10-27）

### 17.1 服务账号自动执行【仓库】

`POST /machine/actions/{typeId}:execute` 用服务账号密钥调用时，分流规则与人一致：

| 条件 | 结果 |
|---|---|
| 策略 `auto` + `callers` 含 `服务账号`（或不限）+ 在 `limits` 内 + 免审 | 系统代为确认并真实执行，响应 `status` 为执行结果（如 `成功`），`overflowReason = null` |
| 超出上限 / 需审批 / 不在允许对象等 | 照旧转暂存（`待确认`），`overflowReason` 说明原因 |
| `callers` 不含 `服务账号` | 403 `CALLER_NOT_ALLOWED`，不建记录 |

幂等不变：同一密钥主体 + `Idempotency-Key` 重放返回 200 和同一 `runId`。

### 17.2 执行主体

服务账号模型没有绑定的真人（runAs）字段，所以执行主体是系统主体 **`api:{服务账号编号}`**（如 `api:SA-0001`）：

- 写入执行记录 `created_by`、外部提交调用记录 `actor`、动作副作用 outbox 的 `actor`；自动执行的时间线备注带“执行主体 api:…”。
- 确认人 `confirmer` 为空（不冒充真人）；用户调用时仍是本人编号。
- 后台步骤（排队的外部提交、副作用复核、动作事件 / 编辑授权）按主体重建身份：`api:` 开头 → 服务账号须未吊销、角色须绑定当前本体空间，权限 = 角色的功能权限与行范围；否则 401，该次执行失败，不会改用其他人身份【推断】。

### 17.3 工具清单数据来源

`/machine/tools` 三类工具全部来自 `GET /meta/capabilities`：动作参数 / 函数入参的取值用目录的 `enum`（`枚举列表` → `items.enum`）；事件工具来自目录 `events` 节，`binding` 的 `method` / `path` / `scope` 取自该节 `ingest`。工具侧不再读定义表、不再解析参数规则。

### 17.4 未做

真人 runAs（需服务账号模型加字段）；后台身份不叠加密钥 scope 收窄（入口已校验）【推断】；其余同 §16.5（事件段与参数枚举两条已解决）。
