# 动作类型与事件类型：页面操作、接口、业务链路与表结构（v0.1 草案）

> 作者：Palantir专家　　日期：2026-10-25
> 依据：master `f54db65` 的前端代码与 mock，只读，没有改源码。涉及的文件：
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
| **动态（Dynamic）** | 动作执行的**状态流转**和对应接口：暂存（规则命中后生成待确认动作）、确认、审批、撤回、重新提交、人工对账。`action_run` 表由动态负责。**幂等键在暂存时由动态生成**；重新提交会生成**新的执行记录和新的幂等键**，原记录保留。 |
| **Palantir（本模块）** | 动作类型、事件类型的定义（读接口 + 草稿校验）；外部系统适配器；幂等键**规则**（动作类型上的模板，例如 `requestReplenishment:{门店ID}:{SKU}:{日期}`）和按键调用、查询；超时判定；定时对账任务；事件的接收、去重、投递和投递记录。 |
| **静态（Static）** | 变更单（CR）、草稿增量 `cr_item`、校验、整版发布。动作类型和事件类型的**所有定义改动**都走这条链路，不另做一套。 |
| **本体小白（基础层）** | 本体版本的存储（`ontology_release` 快照）和 `?ver=` 历史版本的读取。本模块的读接口只把 `release` 参数透传给基础层，自己不存历史快照。 |

### 1.1 本模块提供给动态的内部接口（进程内调用或 `/internal/v1`）

| 接口 | 入参 | 同步返回 | 说明 |
|---|---|---|---|
| `submit(action_run_id, idempotency_key)` | 执行记录号和幂等键；本模块再按 `action_run_id` 读执行时锁定的动作类型版本和参数快照 | `result: 成功 \| 失败 \| 结果未知`，`ext_ref`（外部回执号，可空），`code`（返回码或失败原因），`retries`，`elapsed_ms`，`calls[]`（每次调用：attempt、http、ms） | 先校验提交条件，再调用外部系统，成功后提交本体编辑，最后异步执行副作用。外部**明确返回失败**时，如果对方支持幂等，就按同一个幂等键自动重试（次数和间隔取外部系统配置）。**超时或无响应**时记为结果未知，**不重试**。本体内部动作（不写外部系统）不存在结果未知。 |
| `query_by_key(idempotency_key)` | 幂等键 | `ext_status: 已处理 \| 未处理 \| 查无 \| 不支持查询`，`ext_ref`，`raw` | 定时对账和动态的**人工对账**都走这个接口，不直接连外部系统。对方不支持幂等（如短信、企业微信）时返回 `不支持查询`。 |
| 回调 `on_reconciled(action_run_id, result)` | 由本模块调用动态 | — | 定时对账有了结论时回调动态，由**动态**改状态（结果未知 → 已对账，或 → 失败，见 §4 待定问题）。本模块不直接写 `action_run.status`。 |
| `dry_run(action_type_id, params, release?)` | 动作类型和参数 | `passed`，`failed[{name, fail}]` | 对应“试运行”。只校验提交条件并模拟调用，不写外部系统，不改本体。 |
| `emit(event)` | 事件信封和载荷 | `event_id`，`deduped: bool` | 动作成功后产生 `ActionExecuted`，结果未知时产生 `ActionOutcomeUnknown`；规则和数据同步也走这个接口。 |

- 定时对账：**每 15 分钟**按幂等键查询一次，最多 3 次。3 次后仍无法确认的，留给动态做人工对账（mock：`actions.ts` 里的 `AUTO_QUERY_TEXT`、`extsys.ts` 里调用记录的提示文字）。
- 结果未知超过 30 分钟，由“结果未知提醒”这个订阅方通过企业微信通知发起人和确认人（mock：`actionEvent.ts` 的 `subscribersOf('action_outcome_unknown')`）。

### 1.2 代码位置（与 PDE 约定）

```
backend/app/modules/
  action/            动作类型定义读接口、提交条件求值、试运行、submit / query_by_key
    seeds/           从前端 mock 读数据写库（见 §7）
  event/             事件类型定义、事件接收与去重、投递、投递记录、模拟触发
    seeds/
  integration/       外部系统适配器（每个 EXT-* 一个 adapter）：超时、重试、幂等请求头
  reconcile/         定时对账任务（可以并入 action，见 §8 待定问题）
```

FastAPI，先用 PostgreSQL，数据库访问留适配层（与 智能问数项目调研员 的建议一致）。迁移跑完后执行统一的 `seed` 命令。

---

## 2. 页面操作流程

### 2.1 两页共有的规则

| 规则 | 前端实现 | 后台对应 |
|---|---|---|
| 左侧列表 | 搜索名称和分组（动作）/来源（事件），每页 22 条，‹ 1/N › 简洁分页；改筛选条件后回到第 1 页 | 页码分页，返回 `total` |
| 选中项写在 URL | `?action=` / `?event=`；切换选中项会清掉抽屉、分页和筛选参数 | — |
| 页签写在 URL | `?tab=`，切换页签会清掉分页参数 | — |
| 已发布版本只读 | 编辑类按钮置灰，悬停提示“已发布版本只读，请先发起变更” | 写接口一律走 CR，直接改返回 `NOT_IN_DRAFT` |
| 发起变更 | 弹窗二选一：**新建变更单**（标题必填，最多 40 字，说明可选），或**加入已有变更单**（下拉选未发布的单）。点“进入草稿”后切到 `?view=draft` | `POST /change-requests` 或 `POST /change-requests/{id}/targets`（静态 §3.1） |
| 有草稿时 | 页头出现“已发布 / 草稿 CR-xxxx”切换。默认看已发布版，因为问数用的是这一版；主按钮变成“继续编辑草稿” | 读接口带 `view=draft&cr=` |
| 草稿横幅 | 显示变更单号、标题、改动数、最后编辑人和时间、“问数仍使用已发布版本”；提供“提交审核”和“放弃草稿” | 改动数由后台返回 |
| 草稿内编辑 | 每张表的行带改动标记（新增/修改/删除）；删除的行加删除线，可以“恢复”。改动先存在本地，点“保存草稿”才写入 | `PUT /change-requests/{id}/items`，`section` 与前端 `de.section(name)` 一一对应（见 §3.4） |
| 未保存保护 | 有未保存修改时，切换左侧选中项、切回已发布版、离开页面都会弹“有未保存的修改”；关闭浏览器标签页时用浏览器自带提示 | 后台以已保存内容为准，带 `lock_version` |
| 提交审核 | 确认卡显示本对象、本对象改动数、变更单改动数、审核人（mock 写死“本体管理员 陈立军”）。有未保存修改时，卡片上的确定按钮锁住并显示原因。提交后草稿锁定，跳到 `/build/ontology/releases?cr=` | `POST /change-requests/{id}:submit`；锁定后写接口返回 `CR_LOCKED` |
| 放弃草稿 | 红色确认卡：丢弃本对象在草稿里的全部改动（含已保存的） | `DELETE /change-requests/{id}/targets/{kind}/{targetId}` |
| 历史版本 `?ver=` | 整页只读：“新建动作/事件类型”“发起变更”置灰，悬停显示“正在查看历史版本 x，只读”；不显示草稿切换；回滚链接的悬停也显示这个原因 | 读接口带 `release`，写接口返回 `READONLY_HISTORY` |
| 以此用户预览 | 同样只读；所有确认卡自动锁住，显示“正在以 xx 的权限预览，只读” | `READONLY_PREVIEW` |
| 版本历史 | 表格：版本、本体发布、状态（生效中/已替代）、变更说明、发布人、发布时间。**对比**和**回滚**都置灰，悬停“暂未开放” | `GET …/versions`；对比、回滚放第二期 |
| 抽屉 | 点编号在当前页打开抽屉（`?drawer=kind:id`，最多两层），不跳转 | `GET /action-runs/{id}`、`GET /events/{id}`、规则抽屉（动态的接口） |

> 注意：“试运行”和“模拟触发”这两个按钮**只在状态为草稿时置灰**，在 `?ver=` 下仍然可以点（前端没有用 readonly 控制）。后台需要决定这时按哪个版本执行，见 §8。

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
| 执行抽屉 | 标题“动作名 · 对象”；字段：动作类型、版本、发起人、确认人、来源、审批人、目标对象、参数摘要、外部系统、幂等键、返回、自动重试、时间、关联告警；过程时间线（见 §3.1）；关联：目标对象、发起规则 | — |

“执行顺序”时间线的生成规则（`ActionTypes.vue` 里的 `flow`），后台按这个顺序执行：
1. 规则生成 ⇒ 待确认（只在被规则绑定时出现；确认前可以撤回）
2. 校验提交条件（任一不满足即拒绝）
3. 审批（审批不是“免审”时）
4. 调用外部系统（同步；超时值和幂等键取外部系统配置）
5. 外部明确返回失败 ⇒ 自动重试
6. 超时或无响应 ⇒ 结果未知·待对账，不重试
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

> **事件流分页的差异**：`paginate.ts` 已经提供游标分页 `paginateCursor`，`AppPagination` 也有 `cursor` 变体，但近期事件页现在用的是页码分页，只是用 CSS 把总数隐藏了。‹ 1/N › 里的 N 本身需要总数。后台按**游标**设计（§3.3），同时给一个 `has_more`。前端要么改用 `useCursorQuery`，要么后台额外返回估算页数，需要 PDE 定（§8）。

---

## 3. 接口清单

通用约定沿用静态 §4：页码分页 `{items, total, page, size}`；读接口接受 `release`（即 `?ver=`）、`view=published|draft`、`cr`；错误体 `{code, message, details}`，`message` 可以直接用作悬停文字或确认卡上的阻断原因。前缀统一为 `/api/v1`。
“来源 mock”一列是 seed 数据和接口返回形状的出处（`src/mock/` 下）。

### 3.1 动作类型

| 方法 路径 | 参数 / 请求体 | 返回（取自前端实际显示的字段） | 分页 | 来源 mock |
|---|---|---|---|---|
| `GET /action-types` | `group`、`kw`（匹配名称）、`page`、`size=22`、`release` | `items[{id,name,api,group,status(已发布/变更中/草稿),version,target,target_api}]`、`total`；分组字典 `groups[]` | 页码 | `actionEvent.ts` `ACTION_TYPES`、`ACTION_GROUPS` |
| `GET /action-types/{id}` | `release`、`view`、`cr` | 上面的字段，加 `owner,desc,runners,approval,sys_id,system,endpoint,idem_key_rule,timeout,retry,idempotent,used_by[]`；草稿视图另带 `draft{cr_id,title,changes,owner,updated_at,locked}`、写回配置的 `mark/base` | — | `ACTION_TYPES`（`withSystem` 从 `extsys.ts` 合并系统字段）、`useDraftView.ts` `TARGETS`、`releases.ts` `DRAFTS` |
| `GET /action-types/{id}/params` | `page`、`size=12`、`release`、`view`、`cr` | `items[{name,api,type,required,default,rule,mark?}]`、`total` | 页码 | `paramsOf()`（`PARAMS`，没有专门配置的用 `GENERIC_PARAMS`） |
| `GET /action-types/{id}/submission-criteria` | 同上 | `items[{kind,name,expr,fail,mark?}]` | 不分页 | `condsOf()`（`CONDS` / `GENERIC_CONDS`） |
| `GET /action-types/{id}/writeback` | 同上 | `config{sys_id,endpoint,idem_key_rule,timeout,retry,ext_idem_header,mark,base}`、`edits[{op,obj,field,mark?}]`、`effects[{kind,target,when,note,mark?}]`、`flow[{text,desc,status}]` | — | `writebackOf()`、`extsys.ts` `EXT_SYSTEMS` |
| `GET /action-types/{id}/used-by` | `release` | `items[{rule_id,rule_name,trigger,mode,alerts,open_alerts}]` | 不分页 | `usedBy`、`dynamic.ts` `RULES`/`ALERTS`、`RULE_ACTION`、`RULE_CONFIRMER` |
| `GET /action-types/{id}/stats` | `days=30` | `total,ok,failed,unknown,pending,confirm,rate,avg_ms,as_of` | — | `runStats()` |
| `GET /action-types/{id}/stats/daily` | `days=14` | `items[{date,ok,unknown,failed}]` | — | `runDaily()` |
| `GET /action-types/{id}/versions` | — | `items[{ver,release,status,change,by,at}]` | 不分页 | `versionsOf()`、`ACTION_VERSION_NOTES` |
| `POST /action-types/{id}:dry-run` | `{params?, release?}` | `{passed, checked, failed[{name,fail}]}` | — | `condsOf()` |
| `GET /action-runs` | `action_type`、`status`（可多选）、`page`、`size=14` | `items[{id,at,obj_kind,obj_id,obj_label,summary,initiator,rule_id?,confirmer,source,status,ms,code}]`、`total` | 页码 | `runsOf()`（`RUNS`）。表归动态，本模块只读或由动态提供 |
| `GET /action-runs/{id}` | — | 抽屉字段（§2.2）+ `timeline[{time,text,desc,status}]` + `related[]` | — | `runById()`、`actionEventResolvers.ts` `action()` |
| `GET /ext-systems?writeback=1` | — | `items[{id,name,idem_key_header,timeout_ms,max_retry,retry,idempotent,status,env}]` | 不分页 | `extsys.ts` `EXT_SYSTEMS`；过滤规则同 `actionEvent.ts` `writebackSystems()`（生产环境且未停用） |
| 草稿写入 | `PUT /change-requests/{cr}/items`，`target{kind:'action',id}`，`section ∈ params\|conds\|edits\|effects\|config` | 静态接口 | — | `useDraftEdits.ts` |

“最近异常”直接用 `GET /action-runs?action_type=&status=失败,结果未知&size=4`。执行时间线由后台按 `actionEventResolvers.ts` 的规则生成，或者存成 `action_run_step` 后原样返回（§6）。

### 3.2 事件类型

| 方法 路径 | 参数 / 请求体 | 返回 | 分页 | 来源 mock |
|---|---|---|---|---|
| `GET /event-types` | `source`、`kw`、`page`、`size=22`、`release` | `items[{id,name,api,source,status,version,object}]`、`total`；`sources[]` | 页码 | `EVENT_TYPES`、`EVENT_SOURCES` |
| `GET /event-types/{id}` | `release`、`view`、`cr` | `source,source_ref,rule_id?,object,object_api,owner,desc,dedup_key,dedup_window,delivery,ordering,retention,weekly`、`draft{…}` | — | `EVENT_TYPES` |
| `GET /event-types/{id}/payload-fields` | `page`、`size=12`、`view`、`cr` | `items[{name,api,type,required,desc,sample,envelope:bool,mark?}]`、`total`；`envelope_count` | 页码 | `EventType.specific`、`ENVELOPE`、`payloadOf()` |
| `GET /event-types/{id}/subscribers` | `view`、`cr` | `items[{kind,name,rule_id?,filter,then,status,weekly,mark?}]` | 不分页 | `subscribersOf()`（`SUBS` / `SUBS_RULE` / 默认） |
| `GET /event-types/{id}/stats` | — | `weekly,p95_ms,dedup,failed,partial,as_of`；`daily[{date,value}]`（14 天） | — | `eventDaily()`；健康指标按 `eventsOf()` 计算 |
| `GET /event-types/{id}/events` | `range=24h\|7d`、`outcome`、`cursor`、`size=14` | `items[{id,at,obj_kind,obj_id,obj_label,summary,subscribers,latency_ms,outcome}]`、`next_cursor`、`has_more`；**不返回 total** | 游标 | `eventsOf()`（`EVENTS`） |
| `GET /events/{id}` | — | 抽屉字段 + `timeline[]` + `related[]` | — | `eventById()`、`actionEventResolvers.ts` `event()` |
| `GET /event-deliveries` | `event_id`（必填）或 `subscription_id`+`cursor` | `items[{subscription,status,attempts,last_error,delivered_at}]` | 游标 | mock 里没有逐个订阅方的记录，按 `outcome` 和 `subscribers` 推出来【推断】 |
| `POST /event-types/{id}:simulate` | `{payload?}`（默认取各字段的示例值） | `{test_event_id, delivered_to}` | — | 页面逻辑 `EventTypes.vue` `simulate()`（只算启用中的订阅方）；`subscribersOf()` |
| `GET /event-types/{id}/versions` | — | 同动作类型 | 不分页 | `versionsOf()`、`EVENT_VERSION_NOTES` |
| 草稿写入 | `PUT /change-requests/{cr}/items`，`target{kind:'event',id}`，`section ∈ payload\|subs` | 静态接口 | — | `useDraftEdits.ts` |
| 内部：接收事件 | `POST /internal/v1/events` `{event_type, occurred_at, source, object_type, object_id, payload, trace_id?}` | `{event_id, deduped}` | — | 信封字段 `ENVELOPE` |

### 3.3 只读与权限

- 带 `release`（历史版本）的请求只能读；写接口返回 `READONLY_HISTORY`。
- 试运行需要当前用户满足该动作的“执行人角色”条件（mock 写在 `runners` 里）；模拟触发只允许事件负责人和本体管理员【推断】。
- 草稿写入的权限按静态的 CR 规则。

### 3.4 错误码（在静态 §4 的基础上增加）

| code | HTTP | 什么时候 | message 示例（取自前端文案） |
|---|---|---|---|
| `SUBMISSION_CRITERIA_FAILED` | 422 | 试运行或 submit 时有条件不满足 | `details.failed[{name,fail}]`，如“只有会员运营组可以同步会员标签” |
| `DUPLICATE_KEY` | 409 | 草稿新增参数、条件、字段、订阅方时键重复 | “已存在同名项”；载荷字段为“API 名与已有字段（含信封字段）重复” |
| `ENVELOPE_READONLY` | 409 | 试图改信封字段 | “信封字段所有事件类型共有，不可修改” |
| `INCOMPATIBLE_PAYLOAD_CHANGE` | 422 | 草稿里删除载荷字段或改字段类型、却没有拿到全部订阅方的确认 | “删除或改类型属于不兼容变更，需要所有订阅方确认” |
| `WRITEBACK_SYSTEM_INVALID` | 422 | 写回系统不是生产环境，或者已停用 | — |
| `TYPE_IS_DRAFT` | 409 | 对草稿状态的类型做试运行或模拟触发 | — |
| `IDEMPOTENCY_CONFLICT` | 409 | 同一个幂等键带着不同参数再次提交 | 【推断】 |
| `EXT_TIMEOUT` | — | 不是 HTTP 错误；submit 返回 `result=结果未知`，`code` 为“超时 N 秒未响应 · 未重试” | — |

---

## 4. 业务链路

### 4.1 动作提交（本模块的 `submit`）

1. **读版本**：按执行记录锁定的动作类型版本执行。新版本发布后，进行中的执行仍按提交时的版本完成（版本历史页签的 ⓘ）。
2. **参数校验**：类型、必填，以及“校验”一列里的规则（如 `1 ≤ 天数 ≤ 30`、`≤ 200 字`）。对象引用参数检查对象是否存在。
3. **提交条件**：三种模板——当前用户、参数、上下文对象。**全部满足**才能提交，任一不满足就返回它的“不满足时提示”。规则命中生成的动作，在确认人确认时才求值【推断：mock 里条件失败只出现在确认之后】。
4. **审批**：审批由动态负责。本模块只提供“这次是否需要审批”的判断，例如“单次 > 500 张需审批”【推断：mock 只有一段文字说明，需要做成可求值的阈值表达式】。
5. **写回外部系统（同步，决定动作结果）**：适配器带上外部系统的幂等请求头（如 `X-Request-Id`、`requestNo`），值就是执行记录的幂等键。
   - 返回 2xx：成功，记下 `ext_ref`（外部回执号）。
   - 明确失败（4xx/5xx，或对方返回业务失败码）：对方支持幂等时，按同一个键重试，次数和退避取外部系统配置（如“最多 3 次，1s/2s/4s”）；对方不支持幂等时不重试，直接失败。
   - 超时或无响应：**结果未知，不重试**，因为对方可能已经处理，重试可能造成重复写回。
6. **提交本体编辑**：写回成功后才提交（修改、新建、关联），保证本体和外部系统一致。没有外部系统的动作直接提交，失败就报错，不重试。
7. **副作用（异步）**：通知、事件、Webhook。成功后产生 `ActionExecuted`；结果未知时产生 `ActionOutcomeUnknown`。副作用失败不影响动作结果。
8. **定时对账**：结果未知的执行每 15 分钟调一次 `query_by_key`，最多 3 次。查到“已处理”就回调 `on_reconciled(成功/已对账)`，补发 `ActionExecuted` 并提交本体编辑【推断：resolver 的时间线里，已对账之后才“本体编辑已提交”】；3 次后仍无法确认，留给动态做人工对账。

### 4.2 动作类型的版本与变更

- 所有改动（参数、条件、本体编辑、副作用、写回配置）都写进变更单草稿的 `cr_item`，`target_kind=action`，`section` 和前端一致。审核通过后**随本体整版发布生效**，没有单独发布动作类型这回事。
- 本模块给静态的校验链路提供动作类型自己的校验项（并入静态的 V 系列，见静态待定问题第 12 条）【建议】：
  - API 名唯一；
  - 引用的外部系统可用；
  - 幂等键模板里的占位符必须都能从参数中取到；
  - 对外部系统、本体都没有写操作时给出警告。

### 4.3 事件：定义、产生、投递

1. **定义**：载荷 = 事件自身字段 + 10 个信封字段（`eventId, eventType, occurredAt, ingestedAt, source, objectType, objectId, ontologyVersion, dedupKey, traceId`）。定义还包括去重键、去重窗口、投递保证（mock 里都是“至少一次”）、顺序（如“同一订单内有序”）、保留期。
2. **兼容规则**：载荷只能新增**可选**字段。删除字段或改类型属于不兼容变更，需要所有订阅方确认，再随整版发布生效。
3. **产生**：有 4 种来源——数据同步（CDC 或定时增量）、规则命中、动作（写回成功或结果未知）、外部系统（Webhook 推送或回调）。都调用 `emit`。
4. **去重**：按去重键模板计算 `dedup_key`，在去重窗口内命中就丢弃，事件结果记为“已去重”，订阅方数为 0。窗口可以是“永久”。
5. **投递**：按订阅方的过滤条件匹配，每个启用的订阅方生成一条投递记录。失败重试 3 次后进入死信队列。
   - 整条事件的结果：全部成功为“已投递”（规则来源的为“已生成告警”）；部分成功、其余还在重试为“部分投递”；全部失败为“投递失败”。
6. **模拟触发**：只投递到订阅方的测试通道，不产生告警，不执行动作，不写下游，也不计入近期事件和统计【推断】。
7. **保留期**：超过保留期的事件和投递记录按分区删除。

---

## 5. 状态机

### 5.1 动作执行（状态归动态，这里列出来是为了对齐接口）

| 当前状态 | 触发 | 下一状态 | 由谁做 |
|---|---|---|---|
| （无） | 规则命中，暂存 | 待确认 | 动态（同时生成幂等键） |
| （无） | 人手动发起 | 审批中 / 执行中 | 动态 |
| 待确认 | 确认人确认提交 | 审批中（需要审批）/ 执行中 | 动态 |
| 待确认 | 确认人撤回（必须填原因） | 已撤回（终态，不写外部系统） | 动态 |
| 审批中 | 审批通过 | 执行中 | 动态 |
| 审批中 | 驳回 | 未定义，见 §8 | 动态 |
| 执行中 | `submit` 返回成功 | 成功（终态） | 动态（依据本模块的返回） |
| 执行中 | `submit` 返回失败（提交条件未通过，或外部明确失败且重试后仍失败） | 失败 | 动态 |
| 执行中 | `submit` 返回结果未知 | 结果未知 | 动态 |
| 结果未知 | 定时对账查到已处理 → `on_reconciled`；或人工对账 | 已对账（终态） | 本模块回调，动态改状态 |
| 结果未知 | 对账确认对方未处理 | 未定义，见 §8 | — |
| 失败 | 重新提交 | 原记录不变；新建一条执行记录，用新的幂等键 | 动态 |

### 5.2 外部写回调用（本模块内部）

| 状态 | 触发 | 下一状态 |
|---|---|---|
| 调用中 | 2xx | 成功 |
| 调用中 | 明确失败，且对方支持幂等，且还有重试次数 | 退避后重试（attempt + 1） |
| 调用中 | 明确失败，没有重试次数了，或对方不支持幂等 | 失败 |
| 调用中 | 超时或无响应 | 结果未知 → 进入对账任务 |
| 对账中 | `query_by_key` 返回已处理 | 已确认（回调 `on_reconciled`） |
| 对账中 | 返回未处理 | 已确认未处理（回调，结果待定，见 §8） |
| 对账中 | 返回查无，或第 3 次仍无法确认 | 转人工 |
| 对账中 | 对方不支持查询 | 直接转人工 |

### 5.3 事件（整条）

| 状态 | 触发 | 下一状态 |
|---|---|---|
| 已接收 | 去重命中 | 已去重（终态） |
| 已接收 | 去重通过 | 投递中 |
| 投递中 | 所有投递记录都成功 | 已投递 / 已生成告警（规则来源） |
| 投递中 | 部分成功，其余在重试 | 部分投递 |
| 部分投递 | 剩下的重试也成功 | 已投递【推断：mock 只有静态快照，没有演示状态回写】 |
| 投递中 / 部分投递 | 全部进入死信 | 投递失败 |

### 5.4 投递记录（每个订阅方一条）

| 状态 | 触发 | 下一状态 |
|---|---|---|
| 待投递 | 订阅方已停用 | 跳过（不生成记录，或标记跳过） |
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
| 幂等键、超时 → 结果未知 → 对账 | 我读的上面这几页没有讲幂等键、超时和对账 | 我们加的：每次执行一个幂等键；明确失败才重试；超时记结果未知，不重试；每 15 分钟按键查询，最多 3 次，之后人工对账 | 【笔者归纳】 |
| 执行记录 | Action log 只记录**成功**的提交（失败看 Action metrics）；必记字段包括 Action RID、动作类型版本（每次更新动作类型都会自动加 1）、提交时间、提交人、被编辑的对象，以及调用过的 webhook 和发过的通知 | 我们的执行记录**成功和失败都记**，状态更多（待确认、结果未知、已对账……），相当于 action log 加 action metrics 的合体。版本号随**本体整版发布**变，不是每改一次就加 1 | 【官方】https://www.palantir.com/docs/foundry/action-types/action-log/ ；差异为【笔者归纳】 |
| 函数支持的动作 | 用 `@OntologyEditFunction()` 写的函数作为唯一一条规则；函数版本要手动升级，也可以开自动升级（只升兼容版本，`0.y.z` 不能自动升级） | 第一期不做函数支持的动作；以后如果要做，动作类型应**锁定函数版本**，随整版发布一起升级 | 【官方】https://www.palantir.com/docs/foundry/action-types/function-actions-getting-started/ ；取舍为【笔者归纳】 |
| 事件 / 自动化 | Automate 取代 Object Monitoring。条件有时间、对象数据、数据集三类；效果有提交动作、AIP Logic、函数、通知。官方建议：后果重大的改动先**暂存，等人审核** | 规则命中 → 生成“待确认”动作 → 确认人确认后提交，和官方的这条建议一致 | 【官方】https://www.palantir.com/docs/foundry/automate/overview/ |
| 触发条件 | 对象集条件：对象加入集合、移出集合、在集合内被修改、对全部对象定时运行、跨过阈值 | 我们多了一层显式的“**事件类型**”（有载荷 schema、去重键、订阅方、投递记录）。我读的官方页面里没有看到对应的独立事件注册表，Automate 是直接按对象集变化触发的 | 【官方】https://www.palantir.com/docs/foundry/automate/condition-objects/ ；“事件类型”为【笔者归纳】 |
| 重试与执行保证 | 效果是 **at-least-once**（至少一次），同一个触发可能执行多次，官方建议做成幂等；可以按单个效果自动重试、按整个事件重试，也可以配置 fallback 效果 | 投递保证“至少一次”加去重键，动作写回用幂等键，思路一致 | 【官方】https://www.palantir.com/docs/foundry/automate/effects/ 、https://www.palantir.com/docs/foundry/automate/effect-settings/ |

> 本节没有引用社区资料。所有【官方】条目都是 2026-10-25 当天打开核对过的页面。

---

## 7. 模拟数据入库

用户的要求：后台按前端界面逻辑逐项实现，**现在 mock 里的数据全部要进数据库**，上线第一天页面和现在看到的一模一样。

### 7.1 seed 方案

- 位置：`backend/app/modules/action/seeds/`、`backend/app/modules/event/seeds/`，由统一的 `seed` 命令在迁移之后执行。
- **原样读取 mock，不改 mock**。很多数据是 mock 模块加载时用固定种子随机生成的（`RUNS`、`EVENTS`、`runDaily`、`eventDaily`、没有专门配置的参数和条件），在 Python 里重写一遍很难保证完全一致。所以分两步：
  1. `export_mock.ts`：import `frontend/src/mock/actionEvent.ts`、`extsys.ts`、`dynamic.ts`，调用里面导出的函数（`paramsOf`、`condsOf`、`writebackOf`、`runsOf`、`subscribersOf`、`eventsOf`、`versionsOf`、`runStats`、`runDaily`、`eventDaily`），把结果写成 JSON。用 esbuild 打包后在 node 里运行。**已在本机验证可行**：17 个动作类型、101 个参数、59 条提交条件、36 条本体编辑、47 条副作用、1,849 条执行记录、15 个事件类型、37 个订阅方、1,243 条事件、10 个外部系统、5 条规则、154 条告警、31 + 27 条版本记录。
  2. `seed.py`：读 JSON，幂等写库（`INSERT … ON CONFLICT DO NOTHING`），ID 保持原样（`ACT-…`、`EV-…`、`CR-0013/0014`）。
- 执行时间线（`actionEventResolvers.ts`）是按状态**算出来**的。seed 时调用 resolver 的生成逻辑，写进 `action_run_step` / `event_delivery`。上线后由真实执行过程写入。
- 校验：seed 之后跑一个对比脚本，逐个接口请求，结果和 mock 函数的输出比较，必须完全一致（分页、排序、统计数字）。

### 7.2 mock → 表 对照

| mock（文件 · 导出） | 表（§9） | 备注 |
|---|---|---|
| `actionEvent.ts` · `ACTION_TYPES`（`DEFS` 经 `withSystem` 处理） | `action_type`、`action_type_version` | `desc` 里的 `{sys}` 存**模板原文**，系统名在读的时候替换；`system/timeout/retry/idempotent` 不存，从 `ext_system` 关联 |
| `ACTION_GROUPS` | `dict_item(dict='action_group')` | — |
| `RULE_ACTION`、`RULE_CONFIRMER` | `rule.action_type_id`、`rule.confirmer`（动态的规则表） | 规则表归动态；本模块只读 |
| `paramsOf()` | `action_param` | 包括 `GENERIC_PARAMS` 生成出来的那些行 |
| `condsOf()` | `submission_criterion` | 包括 `GENERIC_CONDS` |
| `writebackOf().edits` / `.effects` | `ontology_edit_rule` / `side_effect` | — |
| `ActionTypeDef.sysId/endpoint/idemKey` | `action_type.sys_id/endpoint/idem_key_rule` | — |
| `UNKNOWN_FOLLOWUP`、超时处理文案 | 常量（写在代码里） | 不进库 |
| `runsOf()`（`RUNS`） | `action_run`（**归动态**）+ `writeback_call` + `action_run_step` | 编号是 2000 段，和动作中心 `actions.ts` 的 0413–0532 不冲突；两份数据合并进同一张表，由谁写见 §8 |
| `runStats()` / `runDaily()` | 统计接口实时算；`runDaily` 先写进 `action_run_daily_stat` | **`runDaily` 和 `RUNS` 对不上**：它是用另一个随机种子生成的，所以照原样入库，保证页面不变，见 §8 |
| `versionsOf()` + `ACTION_VERSION_NOTES` / `EVENT_VERSION_NOTES` | `action_type_version` / `event_type_version`（关联基础层的 `ontology_release`） | 发布号 `v2026.10.3` 等必须和 `releases.ts` 对上 |
| `EVENT_TYPES` | `event_type`、`event_type_version` | — |
| `EventType.specific` | `event_payload_field(envelope=false)` | — |
| `ENVELOPE` | `event_envelope_field` | 全局 10 行 |
| `EVENT_SOURCES` | `dict_item(dict='event_source')` | — |
| `subscribersOf()`（`SUBS`、`SUBS_RULE`、默认） | `event_subscription` | `weekly` 先作为统计快照存进 `event_subscription_weekly_stat` |
| `eventsOf()`（`EVENTS`） | `event`、`event_delivery` | 每条事件按 `subscribers` 生成投递记录：“部分投递”的其中 1 条记为待重试，“投递失败”的全部记为死信【推断，与抽屉文案一致】 |
| `eventDaily()`、`EventType.weekly` | `event_daily_stat` | 和 `EVENTS` 对不上，原因同 `runDaily` |
| `extsys.ts` · `EXT_SYSTEMS` | `ext_system` | 本模块负责（适配器配置）。鉴权信息里的密钥必须**加密存储**，mock 里的值只是示例 |
| `extsys.ts` · `extCalls()` | `writeback_call` | 属于外部系统页面，不在这两页的范围内；建议和 `RUNS` 一起入库 |
| `dynamic.ts` · `RULES`、`ALERTS` | 动态的 `rule`、`alert` | 本模块只引用 |
| `useDraftView.ts` · `TARGETS`（CR-0013 → `store_price_changed`，CR-0014 → `adjust_region_budget`）、`releases.ts` · `DRAFTS` | 静态的 `change_request`、`cr_item` | 由静态的 seed 写入；本模块确认这两张单的目标正确 |
| `stores.ts` / `products.ts` / `orders.ts` | 基础层的对象实例 | 执行记录和事件引用这些对象的 ID，必须先入库 |

### 7.3 页面操作 → 接口 → 表 核对清单

| # | 页面操作 | 接口 | 表 |
|---|---|---|---|
| A1 | 动作列表：搜索、分组、翻页 | `GET /action-types` | action_type, action_type_version |
| A2 | 选中动作、看概览基本信息 | `GET /action-types/{id}` | action_type, ext_system |
| A3 | 使用该动作的规则与告警 | `GET /action-types/{id}/used-by` | rule, alert（动态） |
| A4 | 近 30 天统计、14 天柱图 | `GET …/stats`、`…/stats/daily` | action_run, action_run_daily_stat |
| A5 | 最近异常、执行记录：筛选和分页 | `GET /action-runs` | action_run |
| A6 | 执行抽屉和时间线 | `GET /action-runs/{id}` | action_run, action_run_step, writeback_call |
| A7 | 参数页签和分页 | `GET …/params` | action_param |
| A8 | 提交条件页签 | `GET …/submission-criteria` | submission_criterion |
| A9 | 写回与副作用、执行顺序 | `GET …/writeback` | action_type, ontology_edit_rule, side_effect, ext_system |
| A10 | 编辑写回时的外部系统下拉 | `GET /ext-systems?writeback=1` | ext_system |
| A11 | 试运行 | `POST …:dry-run` | submission_criterion（只读） |
| A12 | 版本历史 | `GET …/versions` | action_type_version, ontology_release |
| A13 | 新建动作类型、发起变更、加入已有变更单 | `POST /change-requests`、`…/targets` | change_request, cr_item（静态） |
| A14 | 草稿里新增、编辑、删除、恢复，保存草稿 | `PUT /change-requests/{cr}/items` | cr_item |
| A15 | 提交审核、放弃草稿 | `:submit`、`DELETE …/targets/…` | change_request, cr_item |
| A16 | `?ver=` 历史版本 | 所有读接口带 `release` | ontology_release（基础层） |
| E1 | 事件列表：搜索、来源、翻页 | `GET /event-types` | event_type, event_type_version |
| E2 | 概览基本信息、载荷摘要 | `GET /event-types/{id}`、`…/payload-fields` | event_type, event_payload_field, event_envelope_field |
| E3 | 概览统计、14 天柱图 | `GET …/stats` | event, event_daily_stat |
| E4 | 载荷字段页签和分页 | `GET …/payload-fields` | event_payload_field, event_envelope_field |
| E5 | 订阅方页签 | `GET …/subscribers` | event_subscription |
| E6 | 近期事件：范围、结果筛选、翻页 | `GET …/events` | event |
| E7 | 事件抽屉和投递时间线 | `GET /events/{id}`、`GET /event-deliveries` | event, event_delivery |
| E8 | 模拟触发 | `POST …:simulate` | event_subscription（写测试通道，不写 event） |
| E9 | 版本历史 | `GET …/versions` | event_type_version |
| E10–E13 | 新建、发起变更、草稿编辑、提交和放弃、`?ver=` | 同 A13–A16，`target.kind=event` | change_request, cr_item |

对比、回滚是“暂未开放”，第一期不做接口。

---

## 8. 待定问题

1. **审批驳回**之后的状态：前端 8 个状态里没有“已驳回”。是回到待确认、记为失败，还是新增一个状态？（动态定）
2. **对账确认对方没处理**之后：mock 里“已对账”的意思只是“外部已处理”。没处理时是记失败、允许重新提交，还是新增“已对账·未处理”？`on_reconciled` 的 `result` 取值要跟着这个定。
3. **事件流分页**：后台用游标、不返回总数，可是前端现在显示 ‹ 1/N ›。是前端改用 `useCursorQuery` / `cursor` 变体，还是后台返回估算页数？（PDE 定）
4. **`runDaily`、`eventDaily`、订阅方的 `weekly` 和明细数据对不上**：现在照原样入库保证页面不变。上线后改成按明细实时统计，数字会变，需要用户确认可以接受。
5. **执行记录由谁 seed**：`actionEvent.ts` 的 `RUNS`（1,849 条）和动作中心 `actions.ts` 的记录要进同一张 `action_run` 表。是动态的 seed 统一读两份，还是本模块提供 JSON 给动态？
6. **审批条件**现在是一段文字（如“单次 ≤ 500 张免审”），需要做成可以求值的表达式。放在动作类型定义里（本模块），还是放在动态的审批配置里？
7. **试运行、模拟触发在 `?ver=` 下按哪个版本执行**：前端在历史版本下没有把这两个按钮置灰。
8. **“部分投递”的事件**在剩下的重试也成功后，是改成“已投递”，还是保持“部分投递”留痕？
9. 是否提供**死信重投**接口（前端目前没有入口）。
10. `reconcile` 是独立模块，还是放进 `action` 模块。
11. 第一期是否要做**函数支持的动作**（我的建议是不做）。

---

## 9. 表结构建议（【建议】，给 本体小白 对齐用）

> 定义表都按静态的约定存：已发布内容来自基础层的版本快照，草稿是 `cr_item` 增量。下面列的是“当前版本展开表”和运行数据表。

| 表 | 关键字段 | 说明 |
|---|---|---|
| `action_type` | id(pk, 如 `request_replenish`), name, api(unique), grp, status, version, target, target_api, target_kind, owner, desc_tpl, runners, approval, sys_id(fk ext_system, 可空), endpoint, idem_key_rule, release_id | 当前版本 |
| `action_type_version` | action_type_id, ver, release_id(fk ontology_release), status(生效中/已替代), change_note, by, at, definition jsonb | 每次整版发布写一行 |
| `action_param` | action_type_id, seq, name, api, type, required, default_text, rule_text, rule_expr jsonb(可空) | (action_type_id, api) 唯一 |
| `submission_criterion` | action_type_id, seq, kind(当前用户/参数/上下文对象), name, expr_text, expr_ast jsonb, fail_msg | (action_type_id, name) 唯一 |
| `ontology_edit_rule` | action_type_id, seq, op(修改/新建/关联), obj, field | — |
| `side_effect` | action_type_id, seq, kind(通知/事件/Webhook), target, when_text, note | (action_type_id, target) 唯一 |
| `ext_system` | id(`EXT-ERP`…), name, vendor, category, endpoint, auth_type, secret_ref(加密), idempotent, idem_header, timeout_ms, max_retry, backoff jsonb, rate_limit, owner, status, env | 适配器配置 |
| `writeback_call` | id, action_run_id, sys_id, idem_key, attempt, method, path, http_status, elapsed_ms, result(成功/失败/结果未知), msg, at | 每次 HTTP 调用一行，包括重试 |
| `reconcile_task` | action_run_id(pk), idem_key, sys_id, tries, next_at, last_status, state(查询中/已确认/转人工), resolved_at, resolution | 定时任务每 15 分钟扫 next_at |
| `action_run` *(动态)* | id, action_type_id, action_type_version, at, initiator, confirmer, approver, source, rule_id, alert_id, obj_kind, obj_id, obj_label, params jsonb, summary, status, ms, code, retries, idem_key(unique), ext_ref, reconcile_note, retry_of | 本模块只读 |
| `action_run_step` | action_run_id, seq, time, text, desc, status | 抽屉时间线 |
| `action_run_daily_stat` | action_type_id, date, ok, failed, unknown | seed 用，见 §8 第 4 条 |
| `event_type` | id, name, api(unique), source, source_ref, rule_id, object, object_api, obj_kind, status, version, owner, desc, dedup_key_rule, dedup_window, delivery, ordering, retention_days, release_id | — |
| `event_type_version` | 同 action_type_version | — |
| `event_payload_field` | event_type_id, seq, name, api, type, required, desc, sample | (event_type_id, api) 唯一，而且不能和信封字段重名 |
| `event_envelope_field` | api(pk), name, type, required, desc, sample | 全局 10 行，只读 |
| `event_subscription` | id, event_type_id, kind, name, rule_id, filter_text, filter_expr jsonb, then_text, target(topic/url/automation_id), status(启用/停用/异常) | — |
| `event` | id(`EV-…`), event_type_id, occurred_at, ingested_at, source, object_type, object_id, obj_label, ontology_version, dedup_key, trace_id, payload jsonb, summary, outcome, subscriber_count, latency_ms, is_test | **按天分区**，按保留期删除；索引 (event_type_id, ingested_at desc, id) 支持游标分页 |
| `event_dedup` | event_type_id, dedup_key, expires_at | 也可以改用 Redis |
| `event_delivery` | id, event_id, subscription_id, status(待投递/投递中/已投递/待重试/死信), attempts, next_at, last_error, delivered_at | 按天分区 |
| `event_daily_stat` / `event_subscription_weekly_stat` | event_type_id/subscription_id, date, value | seed 用 |
| `dict_item` | dict, value, seq | 动作分组、事件来源等字典 |

---

*审阅：请 动态本体负责人 核对 §1 和 §5.1，静态本体负责人 核对 §4.2 和 §7.2 里的 CR 部分，本体小白 核对 §9。*
