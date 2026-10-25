# 智能问数：后台接口、操作流程与种子数据（v0.3 草案）

> 依据：`/workspace/ui-vue` master（`b27758b`）的前端代码与 mock。v0.3 在 v0.2 基础上按 PDE 两轮条件通过意见修订（代理主键 + `code`、只读 403 `READONLY`、会话口径与回答口径分开等），并按本体小白复审 D′ 和骨架 `0c35c1d`（PDE 已过；`app/common/sql_guard.py`、`app/common/auth.py`、`app/common/outbox.py`、`app/db/session.py`、`app/core/errors.py`、`app/modules/biz/`、`app/contracts/versioning.py`、`app/seeds/registry.py`）对齐；行级范围映射按静态 `static-api.md` §1.5（master `443c009`）。v0.2 按 PDE 评审和 `api-index.md`（pde 分支 `97e86e9`）§5.4 修订：分页改 `size`；W3 去掉游标；X1/X2 改成动态模块的 `action-runs:stage` / `:confirm`；字段名和枚举值跟前端 mock 一致；第 10 节补全交互；新增第 12 节 [推断] 清单。
> 范围：问数工作台、我的页面与模板库、对象搜索、对象详情（含「带范围提问」浮层）、问数调优（4 个页签），共 **5 个路由**。
> 编号：页面 R1–R5；接口 W(工作台)/P(页面)/O(对象)/T(调优)/X(外部模块)。
> 标注：**[推断]** 表示前端代码里没有直接体现、是文档补上的规则或限制（**团队约定：后台照前端做，凡是前端没有的限制都标 [推断]，并汇总到第 12 节给用户确认**）；**[第二期]** 表示前端还没有入口或显示「暂未开放」，第一期不做；**[待用户定]** 表示前端两处不一致，需要用户拍板。
> 前缀统一用 `/api/qa/v1`（按 `/api/<模块>/v1` 的约定）。动作、变更单（`/api/ontology/v1`）、用户和权限（`/api/admin/v1`）归别的模块，只写调用关系，见第 8 节。
> 技术栈按 `/workspace/ontology-xiaobai/Python技术栈调研.md`：FastAPI、PostgreSQL + pgvector、口径编译器 + sqlglot，行级条件在编译后、执行前注入。
> **字段与枚举**：接口 JSON 的字段名和枚举值**跟前端 mock 完全一致**（camelCase；前端用中文状态值的，接口就返回中文）。数据库内部可以用 snake_case 和英文码，在 Pydantic 层转换，对照见 1.3。
> **当前时间**：种子环境由骨架提供固定的 `now()` = `2026-10-25 15:42`（Asia/Shanghai）。问数里所有相对时间（今天、近 7 天、近 30 天、上个月、环比、数据截至、高频统计周期）都按 `now()` 算，不用系统时钟。
> **总原则：后台跟前端交互一一对应。** 每个按钮都对应第 7 节里的一个接口和一个状态变化。确认卡上的数字、悬停原因、置灰原因都由后台返回，前端不再自己算。

---

## 0. 页面与接口总览

| 页 | 路由 | 前端文件 | 一句话 | 接口数 |
|---|---|---|---|---|
| R1 | `/use/qa/workbench` | `pages/qa/Workbench.vue` + `components/*` + `useQaSession.ts` | 会话式问数：流式回答、澄清、权限拦截、赞踩、固定到页面、对右栏对象发起动作 | 17（W8、W14 第二期） |
| R2 | `/use/qa/pages` | `pages/qa/MyPages.vue` + `pages/*`（PageDrawer、TemplateDrawer、UseTemplatePop、CardPreview） | 把回答卡片整理成页面：定时刷新、订阅、共享；从模板新建 | 12 |
| R3 | `/use/objects/search` | `pages/objects/ObjectSearch.vue` | 按类型、关键词、分面查对象，导出，用这批对象提问 | 4 |
| R4 | `/use/objects/detail` | `pages/objects/ObjectDetail.vue`、`parts/ObjAskModal.vue` | 对象概览、关联、时间线、动作记录、可执行动作，对它提问 | 4（动作记录直接调动态 X7） |
| R5 | `/build/ontology/qa-tuning` | `pages/ontology/QaTuning.vue` + `qa-tuning/*`（CertTab、EvalTab、FeedbackTab、FreqTab、state.ts） | 认证问答、评测题库、反馈与纠错、高频问题推荐 | 20 |
| | | | **合计** | **57**（第一期 55；另外调用其他模块的接口见第 8 节） |

---

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 分页 | `page`（从 1 开始）、`size`；响应 `{items, total, page, size}`，跟 `static-api.md`、`dynamic-api.md` 一样。前端 `usePagedQuery` 的 URL 参数是 `page`/`size`，带前缀时是 `<前缀>page`/`<前缀>size`（如认证问答 `cpage`/`csize`），`size` 等于默认值时不写进 URL。前端 simple 分页（‹ 1/N ›）也要返回 `total`。筛选或搜索变化后前端回到第 1 页。默认条数见各接口。**页码越界按前端收拢**（CONVENTIONS §3，前端 `src/mock/paginate.ts:13`）：超过最后一页返回最后一页的数据，响应 `page` 是收拢后的页码；`page` 为 0 或负数按第 1 页，不报 422；`size` 照旧校验（1–200，越界 422 `VALIDATION_FAILED`）；`total = 0` 时 `page = 1`、`items = []`。qa-impl 已实现的分页接口（W1、W13、P1、P11、T3、T9、T10 的 `fails`、T12、T13、T18）都按此收拢（骨架 `PageParams.clamp` / `Page.of`）。不分页的接口（W3）一次返回全部。 |
| 时间 | **接口按 mock 里每个字段原来的格式返回**（北京时间，逐字段见 9.3），入参用同样的格式；库里一律 `timestamptz`（日期 `date`）。相对时间按 `now()`（种子环境固定 `2026-10-25 15:42`）。 |
| 人员 | 分两类（跟静态 `object_view.owner`（`static-api.md` §1.4 L418「人名软引用，非外键」）、动态经办人 / 确认人一致；逐列见 9.2「人员列」）：**① 当前登录用户**（会话、常用、页面拥有者和共享成员、申请人、反馈提交人、查询日志）存外键 `→ sys_user(id)`，返回 `xxxId`（用户编号 `sys_user.code`，如 `U010001`）加显示名；种子按姓名映射，查不到报错。**② 定义类的负责人 / 处理人 / 审核人**（认证人、评测操作人、反馈处理人、审批人、模板提供方、订阅接收人）存人名软引用 `varchar`，**不是外键**，原样返回姓名，不查 `sys_user`。 |
| 枚举 | **接口返回前端 mock 里的值**（如认证状态 `已认证`、对象类型 `store`、口径 `ex`），不另加 `label`。数据库可以存英文码，Pydantic 层转换，对照见 1.3。 |
| 语义计划 `SemPlan` | `{metric, dims[], time, filter?, sort?}`，跟 `mock/qa-tuning.ts` 的 `SemPlan` 一致。回答依据、认证问答、评测期望、高频问题、页面卡片**共用这一个结构**；`planText` 的拼接规则搬到后台，响应带 `planText`。 |
| 指标引用 | 接口里跟 mock 一样用 `metric` + `version`（如 `PageCard{metric, version}`），响应带 `metricLabel`（`复购客户销售额（不含税） v3`）。**数据库存 `metric_id bigint → metric(id)`**（静态 `metric` 的一行就是一个 api 名 + 版本），接口返回时连表取 `metric` + `version`，不存 `metric@version` 字符串。`T_METRICS` 的键（`repeat`、`margin`…）不是指标 API 名，种子经 `drawerId` / `QA_METRIC` 映射（第 9 节）。静态 `metric` 的业务唯一键是 `(code, version int)`：mock 的中文名按 `metrics.ts` / `QA_METRIC` 的 `name → id` 换成 `metric.code`，`"v3"` 去掉 `v` 换成整数 `3`；接口返回时再拼回 `"v" + version`（对照表见 9.1）。 |
| 身份 | 第一期单租户。身份只取服务端会话（骨架 `CurrentUserDep`：会话 / API 密钥绑定的身份 / 校验后的 `?previewAs=`；**现状**：qa-impl 还不校验 `previewAs` 的「角色与权限 · 管理」权限，只按编号解析被预览人（`service.py` `effective_viewer` 的 EXTENSION POINT），待骨架鉴权接入后在那里校验 [推断]），**不读请求体或请求头里的用户 id**。开发身份用请求头 `X-Demo-As: <sys_user.code>`（跟骨架 `0c35c1d` `app/common/auth.py` 一致：值是**用户编号**（前端 userId，如 `U010001`），**不是姓名**；按编号查 `sys_user`，`CurrentUser.id` = 编号、`name` = 姓名；查不到或已停用 → 401 `UNAUTHORIZED`；不带时用 `RENGU_DEV_USER`，它也是编号，默认 `U010001`），**只在 dev/test 环境生效**，只是会话桩；**prod 一律忽略该头**（鉴权接入前 prod 直接 401 `UNAUTHORIZED`，之后走 SSO）。前端演示身份 `?as=admin` → `X-Demo-As: U010001`（王敏），`?as=viewer` → `X-Demo-As: U010006`（李娜，编号 [推断]：mock 里没有，骨架补的）（同 api-index §1.4）。**只读状态一律走查询参数，不用请求头**：历史版本 `?ver=<版本>`，预览 `?previewAs=<userId>`（跟前端 URL 一致；每人同一时间只有一个预览会话，所以只带被预览人 id，不带会话 id）。 |
| 主键与编号 | **接口只暴露业务编号**：响应里的 `id` 和路径里的 `{sid}`/`{pid}`/`{id}` 都是前端 mock 的编号（`S0001`、`P001`、`CQ-001`…，库列 `code`），库内代理主键 `id bigint` 不出现在接口里（9.2）。 |
| 字段名 | camelCase，跟 mock 的 TS 类型同名（`certifiedAt`、`lastRefresh`、`allowExport`）。下文写的结构都按 mock 类型。 |
| 错误体 | `{code, message, details, requestId}`（见 1.2）；`message` 可以直接用作悬停文字或确认卡上的阻断原因。 |
| 动作路径 | 非增删改的动作一律用冒号形式：`:submit`、`:stage`、`:confirm`、`:rollback`、`:withdraw`、`:certify`、`:cancel`、`:recalc`、`:refresh`（例如 `POST /turns/{tid}:recalc`），不用 `/submit` 这类子路径。 |
| 审计 | 提问、导出、动作、共享、权限申请，以及预览身份下的所有读写尝试，都写全局审计日志。 |

### 1.1 只读规则（两种，后台都要强制，不能只靠前端置灰）

**总规则（PDE 定）**：请求**只要带了 `ver`**（不管是不是当前版本）或 `?previewAs=` 就是只读。只读下**除 GET / HEAD / OPTIONS 以外的请求一律返回 403 `READONLY`**（`details.mode` = `history` | `preview`；两个参数同时带时按 `preview`），**只有下面「只读模式下允许的写操作白名单」里的接口例外**。下表「拒」= 403 `READONLY`。

| 状态 | 前端表现（`useReadonly` / `usePreview`） | 后台规则 |
|---|---|---|
| **历史版本** `?ver=v2026.10.1` | `readonly = viewMode==='history' \|\| preview.active`。写按钮置灰，悬停显示「正在查看历史版本 x，只读」。工作台的口径框显示「口径：按该版本默认」。已回答的消息保留各自的依据，不改写。 | 查询参数 `?ver=`。读接口按该版本读本体元数据；非 GET 请求除白名单外返回 403 `READONLY`（`details.mode=history`）。 |
| **以此用户预览** `?previewAs=U010231` | 写按钮置灰（「正在以 XXX 的权限预览，只读」）。左侧导航只显示该用户有权限的页面。对象和卡片按该用户的行级范围显示。 | 查询参数 `?previewAs=<userId>`（不用请求头），只有有「角色与权限 · 管理」权限的人能用。**数据身份 = 被预览人**（行级条件调用骨架 `auth.row_filter(被预览人, object_type)`，页面可见性按他算），**操作人 = 管理员**（写审计）。非 GET 请求除白名单外返回 403 `READONLY`（`details.mode=preview`）。 |

**按接口的 `?ver=` / 预览规则**（「依据」列是前端代码里的实际行为；前端没拦、文档要拦的都标 [推断]，汇总在第 12 节）：

| 接口 | 历史版本 `?ver=` | 预览 `?previewAs=` | 依据 |
|---|---|---|---|
| 所有读接口（W1/W3/W7/W10/W13/W17、P1/P3/P11/P12、O1/O2/O5–O7/O9、T1–T4/T9/T10/T12–T14/T18/T20） | 允许，带 `?ver=`：只影响本体元数据（O1 的分面和列、O5 的字段分组和 KPI 口径名、T 系列按该版本读认证问答和评测题）；业务数据和会话快照不随版本变 [推断]。**现状**：qa-impl 读接口不按 `?ver` 取数，只把它原样回显到 `version`——前端只把版本当标签用（`QaTuning.vue:23` `viewVer` 只用于显示和切换），按版本取数 [推断]，二期 | 允许，按被预览人的权限和可见性 | 前端读都不拦 |
| W2 新建会话 | 拒 | 拒 | 「新建」按钮 `readonly` 置灰 |
| W4 改范围 / 口径 | 拒 | 拒 | 范围、口径下拉 `readonly` 置灰 |
| W4 重命名、W5 删除会话 | 拒 [推断] | 拒 [推断] | **前端会话 ⋯ 菜单没有判断 readonly**，历史和预览下都能点 |
| W6 提问 | 拒 | 拒；**[待用户定]**，见下 | `QaComposer` 发送、追问词、范围、口径都 `readonly` 置灰 |
| W9 切换口径 | **拒** | **拒** | 澄清条单选 `readonly` 置灰 |
| W11 导出结果 | 允许 | 拒（`EXPORT_FORBIDDEN`）[推断] | **`QaStoreAnswer` 的导出没有判断 readonly 或预览** |
| W12 赞踩 | 拒 | 拒 | 赞、踩 `readonly` 置灰（复制、依据不置灰，也不调接口） |
| W15 移出常用问题 | 拒 [推断] | 拒 [推断] | **`QaListDrawer` 的「移出」气泡确认没有判断 readonly** |
| W16 申请权限 | **允许**（白名单） | **允许**（白名单） | **「申请权限」按钮和抽屉「提交申请」都没有判断 readonly 或预览**（`Workbench.vue` 的 `?apply=1` 也不拦），历史版本和预览下都能提交（`QaConversation.vue:55`、`QaApplyDrawer.vue:34`、`Workbench.vue:122,129`）；后台照前端放行，跟 api-index §8.1-9 一致。是否收紧见第 12 节 [推断] |
| P2、P4–P10 页面写操作 | 拒 | 拒 | 「新建页面」「用此模板新建」、抽屉里的写按钮都 `readonly` 置灰 |
| P7 固定到页面 | 拒 | 拒 | 「固定到页面」`readonly` 置灰 |
| O2 对象搜索（POST） | 允许（白名单） | 允许（白名单，按被预览人的行级范围） | 只读查询，用 POST 只是因为条件多 |
| O3 导出对象 | 允许（白名单） | 拒（`EXPORT_FORBIDDEN`） | 前端只在预览时置灰 |
| O4 带范围提问 | 拒（按 O4 的 askOff 规则） | **允许**（白名单；回答按被预览人的权限） | `ObjAskModal` 只读 = `readonly && !preview`；「用这批对象提问」按钮不置灰，历史版本下在浮层里拦 |
| X1/X2 发起动作 | 拒 | 拒 | 对象详情「发起」、工作台「建议动作」和右栏「发起」都 `readonly` 置灰 |
| T5–T8、T11、T15–T17、T19 调优写操作 | 拒 | 拒 | 都 `readonly` 置灰（同问法标签 `closable=!readonly`） |
| 分享会话（不调接口） | — | — | 预览时「分享」置灰 |

**只读模式下允许的写操作白名单**（只读下仍放行的非 GET 请求；除 W16 外都不落业务数据）：

| 接口 | `?ver=` | `?previewAs=` | 条件 |
|---|---|---|---|
| O2 `POST /objects/{kind}/search` | 放行 | 放行 | 纯查询 |
| O3 `POST /objects/{kind}/export` | 放行 | 不放行（`EXPORT_FORBIDDEN`） | 只生成文件，不写业务表（审计照写） |
| O4 `POST /scoped-ask` | 不放行（跟前端 askOff 一致：历史版本下浮层里拦） | 放行 | 预览下不落会话、不进 `qa_query_log` 和高频统计 |
| W16 `POST /permission-requests` | 放行 | 放行 | 照前端（申请按钮和抽屉都不判断只读），跟 api-index §8.1-9 一致；写 `qa_permission_request`。预览下申请人记谁见第 12 节 [推断] |
| W6 `POST /sessions/{sid}/turns` | 不放行 | **[待用户定]**：用户定了允许预览提问，就按「不落库」方式加进白名单 | 见下 |
| W9 `POST /turns/{tid}:recalc` | 不放行 | 不放行 | 会改轮次的口径，落库 |

审计日志不算「落库」：白名单接口在只读下照常写审计。

**预览时能不能提问 [待用户定]**：工作台在预览时不能提问（`QaComposer` 发送置灰，横幅写「预览中不能追问和发起动作」）；对象搜索和对象详情的「对它提问」在预览时可以用（`ObjAskModal`：「提问是只读操作」）。第一期后台按前端现状：W6 预览拒、O4 预览允许。**建议**：预览时允许提问，按被预览人的权限回答，**不落会话、不进高频统计和 `qa_query_log`**；用户定了之后前端再统一。

### 1.2 错误码

失败响应统一是 `{code, message, details, requestId}`（跟骨架 `CONVENTIONS.md` §2 一致）：`requestId` 取请求头 `X-Request-ID`，没有就由后台生成，并回写到响应头；成功响应直接返回资源本身，不包 `{data}`。

| code | HTTP | 前端表现 |
|---|---|---|
| `UNAUTHORIZED` / `NO_PERMISSION` | 401 / 403 | 未登录 / 没有页面权限（导航本来就不显示） |
| `READONLY` | 403 | `?ver=` / `?previewAs=` 下发了非 GET/HEAD/OPTIONS 请求且不在只读模式下允许的写操作白名单（1.1）；`details.mode` = `history` \| `preview`；前端按钮本来就置灰，悬停显示原因 |
| `OUT_OF_SCOPE` | 403 | 对象详情整页「XXX 无权查看门店「…」」；抽屉直接打开时被拦下并提示 |
| `NOT_FOUND` | 404 | 「没有找到对象 kind:id」 |
| `EMPTY_INPUT` | 409 | 必须至少一项的输入为空（骨架 `EmptyInput`），例如 P10 共享成员、T 系列批量操作没有选中项 |
| `DUPLICATE_KEY` | 409 | （`DUPLICATE_NAME` 作废，一律用这个码）违反唯一约束：「已有同名页面「…」」「已有认证问答「…」」「已有相同问法」（warning）；`details.field` 指出是哪个字段 |
| `VERSION_CONFLICT` | 409 | 改可变表时 `lockVersion` 不一致（别人先改了），提示刷新后再改 [推断：前端没有并发提示] |
| `STATE_INVALID` | 409 | 状态流转不合法（前端能点的流转后台都放行，见 7.1） |
| `CERT_NO_PLAN` | 422 | 「转为认证问答」置灰，悬停显示原因（还没有可用的语义计划 / 本体里还没有对应指标） |
| `SUBMISSION_CRITERIA_FAILED` | 422 | 动作的提交条件不满足（跟 action-event-api.md 同一个码；`ACTION_BLOCKED` 已作废，api-index `10a074e`），`details.failed[{name, fail}]`；动作「发起」置灰，悬停显示 `blocked` |
| `EXPORT_FORBIDDEN` | 403 | 共享页「允许导出」关闭，或处于预览 |
| `SQL_REJECTED` | 403 | 问数网关拒绝执行生成的 SQL（骨架 `sql_guard`：不是单条只读 SELECT；查了没登记的表；调用白名单以外的函数；带 schema 的函数调用（`pg_catalog.lower()`、`x.age()`）；类型转换目标不在白名单（只允许数值 / 文本 / 日期时间 / 布尔，拒 `regclass`、`oid`、`json`、数组、自定义类型等）；查 `public` 以外的 schema；`TABLESAMPLE`）；`details` 视情况带 `function` / `type` / `table`；问答流里按「回答失败」显示，不展示 SQL [推断：前端没有单独提示] |
| `QUERY_TIMEOUT` / `DATASOURCE_DELAY` | 504/503 | 回答失败；页面「刷新失败」，保留上次结果 |
| `LLM_UNAVAILABLE` | 503 | 回答失败，可重试 |
| `USER_NOT_FOUND` | 422 | 请求里引用的用户编号（`sys_user.code`）不存在（骨架版本层 `create_version` 的 `published_by` 已用；问数凡按编号引用人的写接口同样返回，如 P10 共享成员；软引用列（审批人、认证人等）存姓名，不校验、不返回本码）`details.userId`。注意：`X-Demo-As` 编号不存在走 401 `UNAUTHORIZED`，不是本码 |
| `VALIDATION_FAILED` | 422 | 表单校验，`details.fields[{field, message}]`（`field` 用接口的 camelCase 字段名） |
| `INTERNAL_ERROR` | 500 | 未处理异常，不带堆栈；同样带 `requestId` 和 `X-Request-ID` 头 |
| `ROLLBACK_NOT_LATEST` / `EMPTY_RELEASE` | 409 | 骨架通用码（版本层 `create_version`）；问数接口不会返回，列在这里是为了跟 api-index §1.2 主表对齐 |

### 1.3 枚举对照：数据库内部码 ↔ 接口值（接口值 = 前端 mock 的值）

接口一律返回右列的值（跟 mock 的 TS 类型一致）；左列只在数据库里用，Pydantic 层互转。

| 枚举（mock 类型） | 数据库码 → 接口值 |
|---|---|
| 认证状态 `CertStatus` | `pending` 待认证、`certified` 已认证、`recert` 待重新认证、`disabled` 已停用 |
| 评测运行状态 `RunStatus` | `queued` 待运行、`running` 运行中、`done` 已完成 |
| 评测最近结果 `EvalQ.last` | `pass` 通过、`fail` 失败 |
| 评测分组 `EvalGroup` | 接口和库都存中文：销售、退款、履约、毛利、复购、订单 |
| 评测触发方式 `EvalRun.trigger` | `daily` 每日定时、`manual` 手动运行、`pre_release` 发布前回放、`post_release` 发布后回放、`after_review` 审核通过后试跑；接口返回 mock 里的完整文字（可带后缀，前端按包含匹配筛选） |
| 失败类型 `FailKind` | `metric_not_recalled` 未召回指标、`wrong_metric` 指标选错、`dim_mismatch` 维度不匹配、`time_parse` 时间解析错误、`missing_filter` 过滤条件缺失 |
| 反馈状态 `FbStatus` | `open` 待处理、`to_synonym` 已转同义词、`to_cert` 已转认证问答、`await_release` 待发布生效、`ignored` 已忽略 |
| 反馈修正类型 `Feedback.fix.kind` | `synonym` 同义词、`cert` 认证问答 |
| 页面状态 `PageStatus` | `ok` 正常、`refresh_failed` 刷新失败、`caliber_pending` 口径待更新 |
| 页面来源 `QaPageFull.sourceKind` | `workbench` 问数工作台、`template` 模板、`manual` 手动新建 |
| 共享方式 `ShareSetting.mode` | `private` 仅自己、`members` 指定成员、`dept` 所在部门；成员权限 `SharePerm`：`view` 可查看、`edit` 可编辑 |
| 卡片样式 `CardViz` | `kpi` 指标卡、`line` 折线图、`bar` 柱状图、`table` 表格 |
| 刷新频率 `RefreshFreq` | `hourly` 每小时、`daily` 每天、`weekly` 每周、`monthly` 每月、`manual` 手动；`onFail`：`keep_last` 保留上次结果、`mark_failed_notify` 标记失败并通知 |
| 订阅 `Subscription` | `channel`：`email` 邮件、`wecom` 企业微信、`inbox` 站内信；`when`：`after_refresh` 每次刷新后、`weekly_mon_0900` 每周一 09:00、`daily_0900` 每天 09:00 |
| 动作记录结果 `ActRecord.result` | 接口值：成功、失败、待审批、待确认、结果未知、执行中。**数据来自动态的 `action_run.status`**，问数只做映射：`succeeded` 成功、`failed` 失败、`approving` 待审批、`staged` 待确认、`unknown` 结果未知、`executing` 执行中；`withdrawn` 已撤回、`rejected` 已驳回、`reconciled` 已对账在 mock 里没有，接口按动态的中文名返回 [推断] |
| 可执行动作状态（O9 `state`，界面状态，不入库） | `''`（可发起）、`待确认`、`待审批`、`执行中`，跟 `ObjectDetail.stateOf()` 一致；由该对象最近一条在途 `action_run` 推出（`staged` → 待确认、`approving` → 待审批、`executing` → 执行中，其余 → `''`）。「发起」（`ready`）只在界面上，不入库 |
| 时间线状态 | 对象详情 `TlStatus`：`done`/`warn`/`error`/`unknown`；工作台右栏 `QaTimelineLevel`：`normal`/`warn`/`error`/`unknown`（接口和库都用英文，跟 mock 一致） |
| 对象类型 `ObjKind` | `store`、`product`、`order`（右栏另有 `region`） |
| 会话时间筛选 `sessRange` | `all`、`today`、`7d`、`30d`（按 `now()` 算） |
| 会话口径（W2/W3/W4/W17，`qa_session.caliber`） | 接口值 `不含税`、`含税`（跟 `QA_CALIBER_OPTIONS` 一致，`QaComposer.vue:39` 的下拉，`Workbench.vue:51` 默认 `不含税`）；库里存 `ex`、`in` |
| 回答口径（澄清条、W9、SSE `clarify`） | `ex` 不含税、`in` 含税、`both` 两者对比（`QaAnswer.vue:90` 写死的单选；选项由 SSE `clarify.options` 下发，不走 W17） |
| 回答状态（`QA_DEMO_TURNS[].kind`） | `answer` 正常回答（含澄清）、`denied` 权限拦截、`empty` 结果为空、`system` 系统提示；`failed` 失败 mock 里没有 [推断] |


---

### 1.4 每个接口的权限策略

**现状**：qa-impl 已在路由上挂骨架 `require_func(pageKey, 'view')` 依赖（`router.py:58-61`：工作台 `qa-workbench`、页面/模板 `qa-pages`、对象类型 `object-search`、`/tuning/*` 子路由 `qa-tuning`），但骨架 `require_func` 仍是桩、不拦任何人；`manage` 级别和 API 密钥范围待权限体系接入。

列说明：**页面权限** = `pageKey`（`nav.ts` 的 key）+ 级别（`view` 查看 / `manage` 管理，对应 `mock/roles.ts` 的 `FuncLevel`）；**API 密钥范围** = 外部系统用 API 密钥调用时需要的 `KeyScope`（`mock/apikeys.ts`：问数查询、对象读取、数据导出、动作执行…；「—」表示只给登录用户，不对密钥开放 [推断]）；**`?ver=`** / **预览** 见 1.1；**行级** = 是否经骨架 `auth.row_filter(user, object_type)` 过滤。页面权限、密钥范围、行级三列前端都没有按接口体现，整列算 [推断]（前端只按页面权限控制导航可见）；**例外（PDE 已定）**：自己的会话和页面的写操作（W2–W16、P2–P10）只要求页面权限 `view`，不另设 `manage`（「拥有者 / 可编辑成员」是资源级判断，仍按下表）。预览时能否提问仍是 **[待用户定]**。X 系列的权限由动态 / 静态 / admin 各自定义（动作还要看对象权限里的 `actions`）。

| # | 页面权限 | API 密钥范围 | `?ver=` | 预览 | 行级 |
|---|---|---|---|---|---|
| W1 | qa-workbench · view | 问数查询 | 允许 | 允许（列被预览人的会话） | 否（只看自己的） |
| W2 | qa-workbench · view | 问数查询 | 拒 | 拒 | 否 |
| W3 | qa-workbench · view | 问数查询 | 允许（快照不随版本变） | 允许 | 是（分享打开时按打开人重算） |
| W4 | qa-workbench · view（只能改自己的会话） | 问数查询 | 拒（重命名 [推断]） | 拒（重命名 [推断]） | 否 |
| W5 | qa-workbench · view（只能删自己的） | 问数查询 | 拒 [推断] | 拒 [推断] | 否 |
| W6 | qa-workbench · view | 问数查询 | 拒 | 拒 **[待用户定]** | 是 |
| W7 | qa-workbench · view | 问数查询 | 允许 | 允许 | 是 |
| W8 | qa-workbench · view | 问数查询 | 拒 | 拒 | 否（[第二期]） |
| W9 | qa-workbench · view | 问数查询 | 拒 | 拒 | 是 |
| W10 | qa-workbench · view | 问数查询 | 允许 | 允许 | 是 |
| W11 | qa-workbench · view + 对象「导出」权限 | 数据导出 | 允许 | 拒 [推断] | 是 |
| W12 | qa-workbench · view | 问数查询 | 拒 | 拒 | 否 |
| W13 | qa-workbench · view | 问数查询 | 允许 | 允许 | 否 |
| W14 | qa-workbench · view | 问数查询 | 拒 | 拒 | 否（[第二期]） |
| W15 | qa-workbench · view | 问数查询 | 拒 [推断] | 拒 [推断] | 否 |
| W16 | qa-workbench · view | —（只限登录用户）[推断] | 允许（白名单） | 允许（白名单） | 否 |
| W17 | qa-workbench · view | 问数查询 | 允许 | 允许（按被预览人） | 否 |
| P1 | qa-pages · view | —（只限登录用户）[推断] | 允许 | 允许 | 否（自己的 + 共享给自己的） |
| P2 | qa-pages · view | — | 拒 | 拒 | 否 |
| P3 | qa-pages · view（拥有者或被共享人） | — | 允许 | 允许 | 是（目标：卡片按查看人重算 [推断]；**现状**：查看人不是拥有者且 `row_filter(查看人)` 不为 None 时整页卡片 `{denied:true}`，见 9.2「[推断] 待后续补齐」③） |
| P4 | qa-pages · view（拥有者或「可编辑」）[推断] | — | 拒 | 拒 | 否 |
| P5 | qa-pages · view（仅拥有者）[推断] | — | 拒 | 拒 | 否 |
| P6 | qa-pages · view（拥有者或「可编辑」）[推断] | — | 拒 | 拒 | 是 |
| P7 | qa-pages · view（拥有者或「可编辑」）[推断] | — | 拒 | 拒 | 否 |
| P8 | qa-pages · view（拥有者或「可编辑」）[推断] | — | 拒 | 拒 | 否 |
| P9 | qa-pages · view（拥有者或「可编辑」）[推断] | — | 拒 | 拒 | 否 |
| P10 | qa-pages · view（仅拥有者）[推断] | — | 拒 | 拒 | 否 |
| P11 | qa-pages · view | — | 允许 | 允许 | 否 |
| P12 | qa-pages · view | — | 允许 | 允许 | 否 |
| O1 | object-search · view | 对象读取 | 允许（分面和列按该版本） | 允许 | 否（元数据） |
| O2 | object-search · view + 对象类型「查看」 | 对象读取 | 允许（白名单 POST） | 允许（白名单 POST） | 是 |
| O3 | object-search · view + 对象类型「导出」 | 数据导出 | 允许（白名单 POST） | 拒 | 是 |
| O4 | object-search 或 object-detail · view | 问数查询 | 拒 | 允许（白名单 POST，按被预览人回答）**[待用户定]** | 是 |
| O5 | object-detail · view + 对象类型「查看」 | 对象读取 | 允许（字段分组按该版本） | 允许 | 是（不在范围 → OUT_OF_SCOPE） |
| O6 | object-detail · view | 对象读取 | 允许 | 允许 | 是（没权限的类型整组 denied） |
| O7 | object-detail · view | 对象读取 | 允许 | 允许 | 是 |
| O9 | object-detail · view | 对象读取 | 允许（只读列表） | 允许（只读列表） | 是；能否发起看对象权限里的 `actions` |
| T1 | qa-tuning · view | —（只限登录用户）[推断] | 允许 | 允许 | 否 |
| T2 | qa-tuning · view | — | 允许 | 允许 | 否 |
| T3 | qa-tuning · view | — | 允许 | 允许 | 否 |
| T4 | qa-tuning · view | — | 允许 | 允许 | 否 |
| T5 | qa-tuning · manage | — | 拒 | 拒 | 否 |
| T6 | qa-tuning · manage | — | 拒 | 拒 | 否 |
| T7 | qa-tuning · manage | — | 拒 | 拒 | 否 |
| T8 | qa-tuning · manage | — | 拒 | 拒 | 否 |
| T9 | qa-tuning · view | — | 允许 | 允许 | 否 |
| T10 | qa-tuning · view | — | 允许 | 允许 | 否 |
| T11 | qa-tuning · manage | — | 拒 | 拒 | 否（评测用评测身份，不注入行级 [推断]） |
| T12 | qa-tuning · view | — | 允许 | 允许 | 否 |
| T13 | qa-tuning · view | — | 允许 | 允许 | 否 |
| T14 | qa-tuning · view | — | 允许 | 允许 | 否 |
| T15 | qa-tuning · manage + 静态变更单「发起」 | — | 拒 | 拒 | 否 |
| T16 | qa-tuning · manage | — | 拒 | 拒 | 否 |
| T17 | qa-tuning · manage | — | 拒 | 拒 | 否 |
| T18 | qa-tuning · view | — | 允许 | 允许 | 否 |
| T19 | qa-tuning · manage | — | 拒 | 拒 | 否 |
| T20 | qa-tuning · view | — | 允许 | 允许 | 否 |

## 2. R1 问数工作台 `/use/qa/workbench`

**用途**：用对话问数。左栏是会话，中间是对话和输入框，右栏是本轮引用的对象、它的状态时间线和可执行动作。

### 2.1 接口

| # | 方法 路径 | 入参 | 出参 / 规则 | mock 来源 |
|---|---|---|---|---|
| W1 | `GET /sessions` | `q`、`range=all\|today\|7d\|30d`（按 `now()` 算）、`page`、`size=12`（URL `sessQ`/`sessRange`/`sessPage`，size 固定） | `QaSession{id,title,date}`，按时间倒序 | `mock/qa.ts` `QA_SESSION_LIST` |
| W2 | `POST /sessions` | `{scope, caliber: 不含税\|含税}` | 新会话 `title='新会话'`；第一问发出后，后台把标题设成问题的前 20 个字 | `useQaSession.newSession` |
| W3 | `GET /sessions/{sid}` | — | `{id,title,date,scope,caliber,ownerId, turns: Turn[]}`；**一次返回全部轮次，不分页、没有游标**（前端对话区没有「加载更早」）；Turn 见 3.3。左栏点会话就是切换当前会话，调这个接口 | `mock/qa.ts` `QA_DEMO_TURNS`（原来写死在 `QaConversation.vue`，已搬进 mock） |
| W4 | `PATCH /sessions/{sid}` | `{title?}` / `{scope?, caliber?: 不含税\|含税}` | 改范围或口径时，后台追加一条系统轮次「从这里开始按 大区 = X、口径 = Y 回答」，之前的答案保留各自的依据 | `Workbench.vue` `tips`、`QA_SCOPE_OPTIONS`、`QA_CALIBER_OPTIONS` |
| W5 | `DELETE /sessions/{sid}` | — | 204；已固定到页面的卡片不受影响（卡片存的是自己那份语义计划，不外键级联） | `QaSessionPanel.doDelete` |
| W6 | `POST /sessions/{sid}/turns` | `{question, refs?: ObjRef[], clientId}`（范围和口径取会话上的值） | 202 `{turnId, streamUrl}`。发送、Enter、追问词走这个接口；「改查 10 月」只把改写填进输入框，用户再发送才调用；常用问题抽屉里的「提问」**现在只关闭抽屉**，不调用（第 10 节） | `useQaSession.send` |
| W7 | `GET /turns/{tid}/stream` | `Last-Event-ID` | **SSE**，见 3.1 | `QaAnswer.vue`、`QaStoreAnswer.vue` |
| W8 | `POST /turns/{tid}:cancel` | — | **[第二期]** 中止生成（前端还没有停止按钮） | — |
| W9 | `POST /turns/{tid}:recalc` | `{caliber: ex\|in\|both}` | 澄清条里切换「不含税 / 含税 / 两者对比」：用同一份语义计划只换指标，**同步返回** `Answer`（不流式），记成这一轮的一个变体 | `QaAnswer` `caliber` 单选 |
| W10 | `GET /turns/{tid}/result` | `page`、`size=10`（URL `?tblPage=`） | `{columns, items, total, page, size, summary}`；超过 10 行才分页；`summary` 按**全部数据**算，不是只算当前页 | `QaStoreAnswer`（`QA_EC_STORES` 38 行） |
| W11 | `GET /turns/{tid}/export` | `format=xlsx\|csv` | 文件，导出全部行，写审计；预览时返回 `EXPORT_FORBIDDEN` [推断：前端导出按钮没有判断预览] | `QaStoreAnswer.onExport` |
| W12 | `PUT /turns/{tid}/vote` | `{vote: 'up'\|'down'\|''}`（跟 `QaAnswer.vote` 一致，`''` = 取消） | 赞和踩只能选一个，再点一次取消（传 `''`）。**踩**会在问数调优生成一条 `open` 反馈，取消踩时如果还没被处理就撤掉这条反馈 [推断] | `QaAnswer.onVote` |
| W13 | `GET /favorites` | `q`、`page`、`size=10` | `QaFavorite{id,question,uses,lastUsed}`；左栏贴底取前 3 条（贴底条目没有点击动作）。前端抽屉现在是一次拿全部、本地搜索和分页（每页 10），后台按分页接口实现，前端切真接口时改传参 | `QA_FAVORITE_LIST` |
| W14 | `POST /favorites` | `{question}` | **[第二期]** 前端还没有「加入常用」的入口；第一期 `uses`、`lastUsed` 由提问命中时累加 [推断] | — |
| W15 | `DELETE /favorites/{id}` | — | 「移出」 | `QaListDrawer.remove` |
| W16 | `POST /permission-requests` | `{scope, object, reason(≤200, 必填), duration, sourceTurnId}`。**`duration` 转换**：前端单选的值是显示文字 `QA_APPLY.durations` = `'30 天'\|'90 天'\|'180 天'`（默认 `'90 天'`），提交时取数字部分转成天数整数 `30\|90\|180`；后台只收整数，其他值返回 `VALIDATION_FAILED` | `{id, status:'待审批', approver}`；审批人按申请范围路由给数据负责人；这一轮改成「已申请 · 待审批」 | `QA_APPLY`、`QaApplyDrawer` |
| W17 | `GET /session-options` | — | `{scopes:[{value,label}], calibers:[{value,label}], metric:{ex:{id,name,version}, in:{id,name,version}}}`：`scopes`/`calibers` 读 `qa_session_option`（`kind` = scope / caliber，按 `sort_order`），`metric` 按 `QA_METRIC` 的映射连静态 `metric` 取；输入框的范围、口径下拉用它（澄清条的 ex/in/both 不走这里，见 1.3） | `QA_SCOPE_OPTIONS`、`QA_CALIBER_OPTIONS`、`QA_METRIC` |

右栏（当前对象摘要、状态时间线、可执行动作）用 R4 的 `O5/O7/O9`（`kind=region`）。右栏「摘要 / 指标 / 关联」页签只切换前端本地状态，内容不变，不调接口；时间线「全部」链接**没有点击处理**，不调接口 [第二期]。抽屉 `?drawer=region:REG-EC` 是公共的 DetailDrawer，内容用 O5 `view=drawer`。「我的页面 · 全部」抽屉和「固定到页面」下拉用 `P1 GET /pages?view=brief`（抽屉里每行的「打开」**没有点击处理**，不调接口）；「固定」用 `P7`；「把会话整理成页面」跳到 `/use/qa/pages?newName=标题`，用 `P2` 新建。

**分享会话**：前端只复制链接 `?session=sid`。别人打开时用 W3，**按打开人自己的权限重新执行每一轮的语义计划**，超出他范围的轮次返回 `denied`；拥有者自己看时用快照 [推断]。预览期间「分享」置灰。

**联调实现（qa-joint，W2/W4/W5/W9–W12/W15/W16 已落；X1/X2/X2b 前端已接动态）**：
- W2 返回 201 + 会话详情（同 W3，`turns: []`）；`scope` 缺省 `华东`、`caliber` 缺省 `不含税`（同 `Workbench.vue` 初值）；会话号 = `S` + 现有最大序号 + 1 [推断]。
- W4 返回会话详情（同 W3）。`title` trim 后为空就不改（同 `QaSessionPanel.saveRename`）；`scope`/`caliber` 只有值真变了才追加系统轮次（`kind=system`，`snapshot.text`），并刷新 `last_at` [推断]。
- W5、W15 返回 204；不是自己的会话 / 常用问题一律 404（不暴露存在性）[推断]。
- W16 返回 201 `{id, status:'待审批', approver}`；申请号 `AP` + 4 位序号 [推断]；审批人第一期固定取 `QA_APPLY.approver`（按范围路由待 admin 权限审批流）[推断]；`sourceTurnId` 不是有效用户自己会话里的轮次 → 404；`duration` 只收 30/90/180（其他 422）；只读白名单（`?ver=` / `?previewAs=` 都放行，`readonly_extra`）。申请人记有效用户（预览时 = 被预览人，跟轮次归属一致；第 12 节 #4 仍待定）[推断]。W3 里越权轮次若有待审批申请，带 `applied: true`（前端显示「已申请 · 待审批」，刷新后保留）[推断]；`?apply=1` 刷新复现时没有轮次号，按本会话最后一个未申请的越权轮次提交 [推断]。
- W6 生成的 SQL 过了网关但执行报错（列不存在、`statement_timeout`）→ 422 `QA_SQL_FAILED`，不落轮次，前端提示「生成的查询执行失败，请换个问法再试」并把问题放回输入框 [推断]。
- W9 只对主回答卡（`card=main`）生效，其他卡 422 `VALIDATION_FAILED`；只把口径记成这一轮的变体（`qa_turn.caliber_variant`），返回 W3 同形的 `answer`（带 `caliber`），W3 里主卡的 `answer.caliber` 也带上；含税数值仍由前端按 `TAX_RATE` 同一公式换算（同 `QaAnswer.inTax`）[推断]。不是自己会话里的轮次 → 404。
- W10 只对门店表那一轮（`card=store`）和链路表格回答（`card=table`）生效，其他 422。门店表的行取种子 `QA_EC_STORES`（38 行，同前端 mock），`summary = {count, sales, mom, orders}` 按全部行算（`mom` = 合计 / 上期合计 − 1，同 `QaStoreAnswer`）[推断]；链路表格取快照里的 `table`，`summary = {text}`。页码越界回落到最后一页（P10）。GET，`?ver` / `?previewAs` 下放行。
- W11 同 W10 的行，导出全部行；`xlsx`（缺省）用标准库写最小 OOXML（不加依赖）、`csv` 带 BOM（Excel 直接打开不乱码）；文件名取 `exportFile`（如「门店销售额」）加扩展名，`Content-Disposition: attachment; filename*=UTF-8''…`；写审计 `qa.turn.export`；预览中 403 `EXPORT_FORBIDDEN`（第 12 节 #2）[推断]。前端导出按钮不判断预览，预览时点了提示「导出失败：没有导出权限」。
- W12 返回 `{id, vote}`，W3 里这一轮带 `vote`（没投不带）。新踩 → 生成一条 `open` 反馈（`FB-` + 最大序号 + 1；问题 = 这一轮问题、回答 = `text` / `metric`（主卡用「期间 + 大区」）；修正方式缺省「认证问答」、修正内容缺省 = 问题）[推断]；从踩改成赞或取消 → 撤掉这一轮还是 `open` 的反馈（已处理的保留）[推断]；重复踩不重复生成。
- X1/X2/X2b（开关 `dynamic`）：工作台「建议动作」/ 右栏「发起」先 `POST /action-runs:stage`（`source = {type:'问数', session, sessionTitle}`，对象 = 客户群 `SEG-EC-60D`「华东大区 · 近 60 天未复购」，参数 = 确认卡上的参数文案）[推断]，确认卡里的动作类型版本、审批人、写回系统、幂等键换成 stage 返回值（影响、预算、提交条件三行动态没有返回，仍是前端文案）；「确认提交」→ `:confirm`，右栏变「待审批」并可点击跳动作中心 `/use/ops/actions?tab=records&act=编号`；「取消」→ `:withdraw {reason:'确认卡取消'}`。`?confirm=1` 刷新复现时会再 stage 一条 [推断]。
- 前端：开关 `qa` 切真接口时，左栏列表 / 搜索 / 时间筛选 / 翻页走 W1（服务端分页），切会话 W3，新建 W2，重命名 / 改范围口径 W4，删除 W5，发送 W6（同步等 202 结果，未接 SSE W7），常用问题 / 我的页面抽屉走 W13 / P1（服务端分页、`q`/`kw` 搜索），移出 W15（移出后左栏贴底的常用问题前 3 条同步重取），左栏贴底取 W13 前 3 条、P1 前 2 条；回答卡赞踩 W12、口径 W9，门店表翻页 W10（`?tblPage`）、导出 W11。PATCH / PUT / DELETE 走 `apiClient`（pde-integ 215819d）；W11 要拿文件，`src/api/qa.ts` 里暂留一个只做 GET 下载的小工具（规则同 `http.ts` request），等 `apiClient` 有下载方法后改回。链路新回答（`card=table`）用新轮次的回答气泡显示 `text`，多于 1 行 / 1 列时气泡下给结果表（前 50 行本地每页 10）[推断]。

### 2.2 一次提问的流水线

```
W6 → nlu（实体链接、时间解析、召回本体和认证问答；命中认证问答就直接用它的语义计划）
   → intent（大模型填 SemPlan，用 JSON Schema 约束，校验不通过就修复）
   → 澄清判断（多口径时按默认口径回答，同时下发 clarify）
   → 权限预判（问题里明确的范围超出用户行级范围时：直接 denied，不执行，也不透露那个范围有没有数据）
   → compiler（SemPlan → sqlglot AST → 目标库方言）
   → sql_guard（api-index `f40c81e` §1.4 网关硬规则；骨架 `app/common/sql_guard.py`）：sqlglot 解析后**检查整棵语法树**（不只看第一个关键字），只放行单条只读 SELECT：拒绝多语句、DDL/DML、CTE 里夹带的 INSERT/UPDATE/DELETE、`SELECT … INTO`、`FOR UPDATE/SHARE`；**函数按白名单放行**（聚合、窗口、日期、数学、字符串、条件），`pg_sleep` / `dblink` / `lo_*` / `pg_read_file` / `set_config` / `current_setting` / `query_to_xml` 这类一律拒绝，**带 schema 的函数调用**一律拒绝；**类型转换**（`::`、`CAST`）只允许数值 / 文本 / 日期时间 / 布尔；`TABLESAMPLE` 直接拒绝（不静默丢掉）；表名解析**按 CTE 作用域**（只有当前作用域里定义的 CTE 名才算 CTE，同名的真实表照样检查）；**带 schema 且不是 `public` 的表一律拒绝**（`pg_catalog`、`information_schema`、元数据 schema 都进不来）。被拒返回 403 `SQL_REJECTED`
   → security（骨架 `sql_guard.row_filters_for(user)`：按 `auth.row_filter(user, object_type)` + `row_filter.to_sql` 编出各受控表的谓词，再由 `inject_row_filters(sql, {table: (pred, params)}, allowed) -> (sql, params)` 把受控表换成 `(SELECT * FROM public.t WHERE 谓词) AS t`，不依赖模型自己写过滤；谓词里的表固定写成 `public.<表>`（不会被用户同名 CTE 劫持），不加过滤的表也改写成 `public.<表>`；**谓词里的值一律走绑定参数，不拼进 SQL 字符串**，多张表的参数合并后按 `$1..$n` 重新编号；`DENY_ALL` 编译成 `FALSE`；既不在受控表也不在 `allowed` 里的表一律拒绝（`SQL_REJECTED`）；预览时传被预览人。**受控表登记**（`app/modules/biz/governance.py`）：`store` 按 `store.big_region` / `store.region` 过滤；`biz_order` 经一跳关系 `order_belongs_to_store`（`biz_order.store_id → store.id`）按 `store.big_region` 过滤；`order_line` 经 `order_id → biz_order` **继承订单的谓词**（`EXISTS (SELECT 1 FROM public.biz_order AS _rf_biz_order WHERE _rf_biz_order.id = order_line.order_id AND <订单谓词>)`，实际是 order_line → biz_order → store 两跳）；`product` 骨架 `c646bd5` 起登记为受控表（`PRODUCT_GOV`，原先 `0c35c1d` 的 `register_unrestricted` 已去掉），按下面「商品」规则过滤（已实现）。**大区取值**：库里 `store.big_region` 存 `华东`（由区域「华东一区」派生 [推断]）；静态裁定维度值是「华东大区」、同义词「华东」，SQL 里写 `big_region = '华东'`。**静态 `static-api.md` §1.5（master `443c009`）另两类范围，骨架 `c646bd5` 已实现映射**（`app/modules/biz/governance.py`；`4c22942` 补了 `RowScope.values` 校验）：① 商品.品类（骨架 `c646bd5` 已实现）：范围值按 `CATEGORY_SCOPE_ALIAS` [推断]（`app/contracts/static.py`，`c646bd5` 起有）换成一级类目（乳品烘焙→乳品、休闲零食→休闲食品、日化清洁→日化家清，生鲜 / 粮油调味 / 酒水饮料同名），谓词 `split_part(product.category, '/', 1) = ANY($n)` 加在 `product` 上；**商品可见性按 `roles.ts:110`**（`static-api.md` §1.5 L424，master `443c009`；骨架 `c646bd5` 已实现，`sql_guard.effective_expr` 的 granted 过滤）：不是 `defaultDeny`、又没配商品.品类范围的角色才算商品不限；`defaultDeny` 角色没配品类范围时商品不可见——R-05 业务查看、R-06 门店店长、R-08 大区经理（华东）都能查看商品、都是 `defaultDeny`、都没配商品范围，所以按前端它们看不到商品；再按下面的多条范围合并：剩下的角色有一个不限就看全部，否则各品类范围取并集（如只有 R-04 的用户只看 S-42 三个品类）；只有 R-05/R-06/R-08 的用户 `product` 谓词为 `FALSE`，凡连 `product` 的查询（按商品、按品类的销售额等）结果为空；`order_line` **不按品类过滤，只继承订单的谓词**（静态裁定：前端范围按对象类型分，`roles.ts:62` R-04 的 S-41 在订单上、S-42 在商品上）；② 订单.门店（骨架 `c646bd5` 已实现，`ORDER_GOV` 的 `门店 → order_belongs_to_store.name`）：经 `biz_order.store_id → store.name` 一跳 `EXISTS`，谓词 `store.name = ANY($n)`，值原样按门店名比；mock 范围值（`roles.ts:26`「上海徐汇店」等简称）跟种子全称对不上，匹配 0 行，效果等于拒绝——**R-06 门店店长（`roles.ts:69`，S-61 订单.门店 = 上海徐汇店）因此看不到任何订单**，**已定（用户）：不加门店别名表，R-06 订单 0 行是预期（测试数据）**（11 节 #17、12 节 #26）；③ 会员.常去门店所属大区：**一期拒绝**（出处：`static-api.md` §1.5 范围映射表「会员.常去门店所属大区」行，master `443c009` L435：「骨架一期不建 `member` 表，这类范围网关先按拒绝处理」；该格本身标 [推断]），带这类范围的角色在会员上照 deny by default 处理。**多条范围合并**（`static-api.md` §1.5 L425，master `443c009`；前端 `effectiveAccess`，`roles.ts:110-117`；骨架 `c646bd5` 已实现，`sql_guard.effective_expr`，测试 `tests/test_sql_guard.py::test_effective_expr_follows_roles_ts`）：先按 `roles.ts:110` 只算对该对象有查看权限的角色，`defaultDeny` 但对该对象没配范围的角色不算「不限」，而是不给看（如 R-06 店长对会员）；剩下的角色里，只要有一个对该对象不限范围就看全部，否则**所有范围一律取并集（OR）**：跨角色 OR；同一角色在同一对象上配多条范围（如订单.大区 + 订单.门店）时**也取 OR** [推断]（mock 没有这种角色；前端 `roles.ts:114` 把同一角色的多条范围也拼成「；或」）。R-06 再叠加一个对会员有范围的角色时，会员只取那个角色的范围。**没有任何角色的用户默认拿不到任何行**（deny by default：谓词为 false，结果为空）。**注意**：上述合并规则只在 `effective_expr` 里实现；`auth.row_filter(user, object_type)` 仍是桩（`4c22942`/`07c4c00` 未变：只有本体管理员不限制，其他人一律 `DENY_ALL`），还没有按用户角色组装 `RoleAccess` 调 `effective_expr` **[待骨架实现]**（EXTENSION POINT，`app/common/auth.py:88`）。）
   → executor：只用骨架 `app/db/session.get_qa_readonly_engine()`，连 `RENGU_QA_READONLY_DATABASE_URL`，每个连接设 `default_transaction_read_only=on` 和 `statement_timeout`（`RENGU_QA_STATEMENT_TIMEOUT_MS`，默认 15000）；库账号本身也只读、只授权业务表、不授权元数据表（运维配置，第二道闸）+ 行数上限；**没配置就抛 `QaReadonlyNotConfigured` 拒绝执行，不回退到主库账号**；执行 = `sql_guard.run_guarded(conn, sql, user)`（校验 + 注入 + `exec_driver_sql`）
   → 0 行时返回 empty，带解释，例如「该门店 2026-10-18 开业」；能给改写建议就带上
   → 解释（结论文字、KPI、图表、归因、引用对象、追问词、建议动作）
   → 写 qa_turn（快照 + sem_plan）+ qa_query_log（给高频统计和一致率用；预览、评测不写）
```

---

### 2.3 第一期已实现的最小链路（W6，分支 qa-chain → qa-reuse）[推断]

代码：`app/modules/qa/llm.py`（LLM 客户端）、`app/modules/qa/chain.py`（上下文 / prompt / 执行 / 成形）、`router.py` W6；测试 `tests/test_qa_chain.py`（FakeLLM，不调真实接口）。

- **流水线（简化版）**：问题 → `build_context`（`store / product / biz_order / order_line` 表结构 + `object_type` 说明 + 对象间 `link_type.join_sql` + 有公式的 `metric` + `qa_synonym_link` / `dim_mapping` 同义词 + `onto_qa_path` 默认路径）→ prompt → LLM 直接写 SQL → 抽 ```sql``` 代码块 → `sql_guard.assert_single_select`（返回 sqlglot AST）→ 用 sqlglot 构造外包 `SELECT * FROM (…) AS _qa LIMIT 201`（`chain.wrap_limit`，不拼字符串）→ `run_guarded`（表白名单 + 行级权限注入）→ 只读引擎执行（`statement_timeout`、`default_transaction_read_only`）→ 成形答案轮次。[推断] SemPlan JSON → 校验器 → 编译器是二期；本期 LLM 直接写 SQL（Vanna / SQLBot 式）。
- **复用的开源件**（用户要求「有现成的就拿来用」；只抄 MIT / Apache-2.0 代码，SQLBot、WrenAI 是 GPL/AGPL，只参考思路）：

  | 环节 | 用的开源件 | 许可证 |
  |---|---|---|
  | 调 DashScope | OpenAI 官方 Python SDK `AsyncOpenAI`（`base_url` 指向 compatible-mode，`max_retries=1`），替掉手写 httpx | Apache-2.0 |
  | prompt 结构 | Vanna `get_sql_prompt`：系统消息 = 说明 + `===Tables`（DDL）+ `===Additional Context`（本体对象 / 关联 / 指标 / 同义词 / 默认路径 / 数据截至）+ `===Response Guidelines`，问题单独一条 user | MIT |
  | 表结构序列化 | SQLAlchemy 自带 DDL 编译：`CreateTable` + `COMMENT ON TABLE/COLUMN`（带 CHECK 枚举值），替掉手拼“列：a（注释）” | MIT |
  | PG 方言规则 | NVIDIA auto-ontology `text_to_sql/prompts.py` 的 `_POSTGRES_DIALECT_RULES`（别名不能进 WHERE / GROUP BY） | Apache-2.0 |
  | 抽 SQL | Vanna `extract_sql` 的围栏规则（先 ```sql 块，再任意 ``` 块，取最后一个）；[推断] 不抄它的 `SELECT … ;` 正则，那条会把 `select 1; drop …` 截短后放行，这里让多语句原样进 sql_guard 被拒 | MIT |
  | 解析 / 校验 / LIMIT | sqlglot（`app/common/sql_guard` 已在用） | MIT |

- **上下文补强（联调 EV-Q 后加）**：
  - 表结构：系统列（`is_seed / lock_version / created_at / created_by / updated_at`）不进 DDL；枚举列（库里存英文 code）的 `COMMENT ON COLUMN` 写成 `code=中文标签`（来自 `LabelEnum`）。
  - 「过滤列（按表）」：从表结构推出每张表能加的过滤列（目前只有 `is_test`：store、biz_order），没有的表明说不要加，避免模型给 product 加 `is_test / is_seed`。
  - 「维度层级与取值」：本体 `dimension / dim_level / dim_value`（Store / SalesOrder / Category / Product 上的维度），每个值带层级、同义词、上级；值和枚举标签对得上时标出库里的 code（如 `已出库=shipped`）。取值超过 40 个的维度（时间以外如门店、组织）不列。
  - 「列取值（全部）」：QA 表里低基数文本列（≤ 30 个 distinct，如 `store.city`、`biz_order.channel`）的全部取值，让 WHERE 照抄真实值（auto-ontology 附 sample values 的思路，Apache-2.0，非代码复制）。[推断] 取值查询不走行级权限，现阶段行级权限对已登录用户不裁剪；以后裁剪时这里要跟着收。
  - 「指标口径」：公式后附指标定义页里的「基础对象 / 时间字段 / 默认过滤 / 同名默认」；多个指标定义页相同只贴一次。[推断] 种子里所有指标共用同一份定义页（看着是模板照抄），有效订单数的「默认过滤 已出库」和公式「状态 ≠ 已取消」冲突，规则写了「公式里写了条件时以公式为准」，需指标负责人确认。
  - 「时间」：当前时间按业务时钟（`clock.local_now()`，测试里是 `RENGU_FIXED_NOW`）+ 今天 / 昨天 / 前天 / 本周 / 上周 / 本月 / 上月 / 今年 / 近 3、7 天的左闭右开区间，要求 SQL 只写日期字面量、不用 `NOW()`（库时间不是业务时钟）。思路同 auto-ontology `main_system_prompt_template` 的「Today's date」。
  - 规则：并列（问“最多 / 最高”没说前 N 时用 `WHERE n = (SELECT MAX(n) …)` 返回全部并列，不用 `LIMIT 1`；Spider / BIRD 金标常见写法，思路非代码 [推断]，和 auto-ontology「单数最值 → LIMIT 1」相反）；路径型列（`product.category` = 大类/中类）默认按大类 `split_part` 聚合；题目和口径都没提到的条件不要加；列对象时返回 code + name；`'--'` 等占位值计数 / 分组时排除；按日 / 月分组输出 `::date`。另抄了 auto-ontology `**Style**` 段的 DISTINCT / NULL 两条（Apache-2.0）。
- **意图判断（三选一输出，W6 内同一次 LLM 调用）**：思路参考 SQLBot / DB-GPT 的意图识别 + Vanna「上下文不够就说明」，非代码复制。规则 1 要求模型只回三种之一：```sql``` 块；```json {"clarify":{"term","question","options"}}```（说法有多种合理口径、且没有同名默认时才问，只缺时间不问）；```json {"refuse":{"code","reason"}}```（拒答优先于澄清，code ∈ `NOT_SELECT / OUT_OF_SCOPE / NO_PERMISSION / NO_DATA`，未知 code 归 `OUT_OF_SCOPE`）。`chain.parse_intent` 只认 json 围栏或整段 JSON，其余走 `extract_sql`。
  - 澄清 → `kind=answer`，`answer = {card:'clarify', dataAsOf, text:<问题>, clarify:{term, chosen:null, options:[{value:'o1', label}]}, suggest:{followUps:[label…]}}`（选项最多 6 个），不执行 SQL。
  - 拒答 → `kind=denied`，`snapshot = {lead:'', bold:<原因>, tail:'。', tag:'权限拦截'(NO_PERMISSION) | '无法回答', code}`（同 QA_DEMO_TURNS T-02 形状），不执行 SQL。`qa_query_log` 记 `denied`、0 行。
  - 两种都返回 202、照常落 `qa_turn`；HTTP 错误码不变。[推断] 前端 `QaAnswer.vue` 的澄清卡目前是写死的 mock，要接 `answer.clarify` 才能显示真实选项（前端负责人）。
- **口径裁定与拒答清单（qa 自己的数据）**：`backend/seeds/extra/qa/caliber/notes.json`（`{term, aliases, caliber, sql, source}`：销售额 = 不含税 = 行含税 ÷ 1.13、出库状态 + 出库时间；含税销售额；有效订单数 / 单量 / 几单 = 状态 ≠ 已取消、按出库时间；订单数、销量、出库及时率、优惠金额等，口径同 qa-eval §4）和 `refuse.json`（`{term, code, reason}`：毛利 / 成本 / 退款 / 会员 → NO_DATA；敏感字段、sys_* 表、关行级权限 → NO_PERMISSION；工资 / 天气等 → OUT_OF_SCOPE；写操作 → NOT_SELECT）。进 prompt 两段「口径裁定」「拒答清单」，排在最前；跟静态「指标口径」冲突时以口径裁定为准（规则 3：命中别名就保留该口径的全部条件，只有时间字段可按题目换）。不改 `static/` 种子和前端 mock。[推断] 口径定稿后应进 qa 表（要迁移），现在是文件。
- **时间上限**：所有窗口的上界都不超过当前时间（本月 / 今年 / 本周 / 近 N 天都截到「现在」）；加了近 30 天。规则 8：问「每个 / 各」门店、商品时 SELECT 和 GROUP BY 都带 code + name。
- **口径校验（sqlglot，执行前，回喂一次）**：`notes.json` 条目带 `check`（`status_eq` / `status_ne` / `time`）。`chain.matched_calibers` 按最长匹配找出问题命中的口径（“不含税”里的“含税”不算），`caliber_violations` 在 AST 里查状态条件在不在（`o.status = 'shipped'` / `<> 'cancelled'` / `NOT IN`）、用了时间列时是不是该口径的时间列（题目说了“下单 / 出库”按题目换）；不过就把问题回喂 LLM 改一次，改完不再查。[推断] 不查窗口边界，那个由下一条兜底。
- **时间上界截到现在**：`chain.cap_time_upper` 把「时间列 < / <= 晚于当前时间的字面量」改成当前时间（库里有晚于业务时钟的出库单），sql_text 存改后的 SQL。
- **值锚定与 0 行值修复（移植 auto-ontology，Apache-2.0）**：思路来自 `agents/proactive_value_check.py`、`agents/empty_result_value_repair.py`，`_string_predicates` 改写自 `db_probe/literal_check.py`；不探库做 DISTINCT、不用向量库，门店 / 商品名列表在 Python 里按最长公共子串比。(1) 提问前：问题里 ≥3 个字对上的门店 / 商品真实名称进 prompt 段「问题里提到的门店 / 商品」；(2) 执行后 0 行：`store.name / product.name` 上 `= / IN / LIKE` 的字面量在库里不存在时，把最接近的真实值回喂改一次，仍为空照常返回 `empty`。[推断] `join_path_check` / `sql_reconstruction` 本轮没移植（剩余失败里没有错 join 路径的，主要是订单行 join 后对订单金额求和的扇出，见 qa-eval 剩余失败）。
- **答案里的枚举 code 换标签**：成形前 `_label_rows` 看每一列，非空值全是 QA 表枚举 code（且这个 code 只属于一个枚举）时换成中文标签（`shipped → 已出库`、`open → 营业中`），列、行、形状不变。
- **查询超时**：只读连接 `statement_timeout` 触发（SQLSTATE 57014）→ 504 `QUERY_TIMEOUT`「查询超时，请缩小时间范围或条件后再问」，不回喂重试。
- **执行报错回喂一次**：SQL 过了 sql_guard 但执行报错（如列不存在），在 savepoint 里回滚，把数据库报错首行作为新一轮 user 消息回喂 LLM 改一次（`FIX_RETRIES=1`，常见自修正思路，非代码复制）；仍失败照旧 500 `INTERNAL_ERROR`。sql_guard 拒绝不重试，仍 403。[推断]
- **同步返回**：W6 跑完整条链路后返回 202 `{turnId, streamUrl, turn}`，`turn` 就是 W3 的轮次形状，前端可以直接追加；W7 SSE 未实现，`streamUrl` 先占位。[推断]
- **答案形状**：`kind=answer`，`answer = {card:'table', dataAsOf, basis:{caliber, perm}, text, table:{id:'result', title, columns:[{key:'c0', title:<SQL 列别名>, align?:'right'}], items:[{c0:…}], total, summary, paged}}`；0 行 → `kind=empty`（`lead/bold/tail/tag`）。最多 200 行，超过 10 行 `paged=true`。[推断] kpis / trend / insight / suggest 本期不出。
- **落库**：`qa_turn`（`code = {会话号}-T{seq:03d}` [推断]、`kind`、`question`、`refs`、`snapshot`、`sql_text`（不出接口）、`asked_at`、`elapsed_ms`），更新 `qa_session.last_at`，第一问改标题；追加 `qa_query_log`。`qa_turn_event` / `sem_plan` 本期不写。
- **LLM**：DashScope OpenAI 兼容接口（`openai` SDK，`base_url=https://dashscope.aliyuncs.com/compatible-mode/v1`），`temperature=0`。依赖：`backend/pyproject.toml` 新增 `openai>=1.50`（锁 3.37.0）。环境变量：`DASHSCOPE_API_KEY`（必填，只从环境读，不打日志）、`QA_LLM_MODEL`（默认 `qwen-flash`）、`QA_LLM_BASE_URL`、`QA_LLM_TIMEOUT_S`（默认 30）。[推断] 先不进 `app/core/config`，以后再收进 Settings。
- **错误**：没配 key → 503 `QA_LLM_UNAVAILABLE`；没配只读库 `RENGU_QA_READONLY_DATABASE_URL` → 503 `QA_EXEC_UNAVAILABLE`；LLM 调用失败 → 502 `QA_LLM_FAILED`；SQL 不是单条 SELECT、写 / DDL、用了白名单外的表 → 403 `SQL_REJECTED`（不落轮次）；会话不是自己的 → 404。
- **权限**：沿用 `require_func("qa-workbench","view")` + 行级权限（权限初版 auth-min：已登录不裁剪，未登录 401）；预览 / 历史版本由全局 readonly_guard 拒（403 READONLY）。

## 3. 流式回答（SSE）

### 3.1 事件

`GET /turns/{tid}/stream`，`Content-Type: text/event-stream`。每个事件都带 `id`（序号 `seq`），**每个事件先写 `qa_turn_event(turn_id, seq)` 再推送**；断线后用 `Last-Event-ID` 从这张表续传；回答完成后再连，就从这张表一次性回放全部事件（表结构见 9.2）。

| event | data | 前端渲染位置（`QaAnswer.vue`） |
|---|---|---|
| `status` | `{stage: understanding\|linking\|compiling\|permission\|executing\|explaining, text}` | **[第二期]** 加载中的提示（前端还没有加载态） |
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
| `denied` | `{asked:"大区 = 华南", allowed:"大区 = 华东", apply:{scope, object, approver, durations:['30 天','90 天','180 天']}}`（`apply` 跟 `QA_APPLY` 一致；提交时转天数，见 W16） | 「你问的华南大区不在你的权限范围内」+「权限拦截」标签 +「申请权限」 |
| `empty` | `{reason:"该门店 2026-10-18 开业，9 月尚无订单", rewrite?:"…改查 2026 年 10 月"}` | 「没有符合条件的记录（0 行）」+「结果为空」+「改查 10 月」（把改写填进输入框） |
| `error` | `{code, message, retryable}` | 失败提示 |
| `done` | `{turnId, kind: answer\|denied\|empty\|failed, elapsedMs}` | 结束（`kind` 跟 `QA_DEMO_TURNS[].kind` 一致；澄清是 `answer` 里带 `clarify`） |

### 3.2 结果状态与界面

| kind | 是否执行 SQL | 界面 | 可用操作 |
|---|---|---|---|
| `answer`（可带 `clarify`） | 是 | 完整回答卡 | 查看原因、建议动作、固定到页面、复制、赞踩、切换口径 |
| `denied` | 否 | 锁图标一行 | 申请权限 → 已申请 · 待审批 |
| `empty` | 是（0 行） | 收件箱图标一行 | 改查建议 |
| `failed` | 失败 | 错误 | 重试（就是再调一次 W6） [推断] |

### 3.3 Turn（W3 返回，也是落库的快照）

结构跟 `mock/qa.ts` 的 `QaDemoTurn` 一致：`{id, kind: 'system'|'answer'|'denied'|'empty', time?, question?, text?, …}`，`answer` 轮带 `metricLabel, answeredVer, dataAsOf, basis, clarify, text, kpis, trend, table, insight, suggest, vote`，`denied` 轮带 `asked, allowed, applied`，`empty` 轮带 `reason, rewrite`。快照里存的是**回答时的结果**，查看历史版本时不改写。示例数据就是 `QA_DEMO_TURNS`（第一个会话的 4 轮 + 1 条系统提示）。

---

## 4. R2 我的页面与模板库 `/use/qa/pages`

**用途**：管理固定下来的回答卡片页面（刷新、订阅、共享），以及从模板新建页面。

| # | 方法 路径 | 入参 | 出参 / 规则 | mock 来源 |
|---|---|---|---|---|
| P1 | `GET /pages` | `kw`、`src=问数工作台\|模板\|手动新建`、`share=已共享\|仅自己`、`status=正常\|口径待更新\|刷新失败`、`page`、`size=15`、`view=brief\|full`（参数名跟 URL 一致） | `QaPageFull` 列表（brief 只返回 `{id,name,cards,updatedAt,shared}`，即 `QaPage`）+ `fromWorkbenchCount`。只返回我的页面；预览时返回被预览人自己的页面加共享给他的 | `mock/qa-pages.ts` `MY_PAGES`、`OTHER_PAGES`；`mock/qa.ts` `QA_PAGE_LIST` |
| P2 | `POST /pages` | `{name(≤30), scope}`，或者 `{name, scope, template:{id, caliber, refresh}}` | 新建空白页面或从模板新建；从模板新建时 `status` 按卡片是否有 `pending` 定为 `口径待更新` 或 `正常`；`share` 默认 `{mode:'仅自己', members:[], link:false, allowExport:true}`；重名返回 `DUPLICATE_KEY` [推断：前端新建时不查重，只有重命名查重]。**`refresh` 转换**：「用此模板新建」气泡的刷新选项是显示文字，后台收 `RefreshPlan`，前端按 `MyPages.fromTemplate` 的规则转（见表下） | `createBlank`、`fromTemplate` |
| P3 | `GET /pages/{pid}` | — | `QaPageFull`：`{id,name,owner,scope,source,sourceKind,cards:PageCard[],dataAsOf,lastRefresh,updatedAt,refresh,sub,share,status,statusNote,views,calibers[{label,pending}]}`。**卡片数据按查看人的权限裁剪**，范围外的卡片返回 `{denied:true}`，显示「无权限」。**现状**（qa-impl）：查看人（被共享人，或 `?previewAs=` 的被预览人）不是拥有者、且骨架 `auth.row_filter(查看人, 'store')` 不为 None 时，**所有卡片**都返回 `{key,title,viz,metric,version,pending?,denied:true}`，不带 `value/unit/sub/series/labels/rows`；拥有者看自己的页面不变；P1 `view=full` 同样处理。`calibers` 照常返回。前端 `CardPreview.vue` 还没有 `denied` 分支 [待前端] | `QaPageFull`、`PageCard` |
| P4 | `PATCH /pages/{pid}` | `{name}` | 重名返回 `DUPLICATE_KEY`（前端也查重） | `rename` |
| P5 | `DELETE /pages/{pid}` | — | 停掉定时刷新和订阅；被共享人从此打不开；问数会话里的原始回答不受影响。确认卡（不可逆）上的字段「卡片 / 共享 / 订阅」用 P3 的返回 | `remove` |
| P6 | `POST /pages/{pid}:refresh` | — | 30 秒内同步返回 `{lastRefresh, dataAsOf, status, statusNote}`，超时返回 202 `{jobId}`（`jobId` 是字符串任务编号，如 `JOB-20261025-0001`，不暴露库内数字 id）。刷新成功后从「刷新失败」恢复成 `正常` 或 `口径待更新`；`lastRefresh` 按 `now()` | `refresh` |
| P7 | `POST /pages/{pid}/cards` | `{turnId, tableId?/chartId?, title}`（**不支持 `pid=new`**：固定到新页面时先 P2 新建，再用返回的 `id` 调 P7） | 把这一轮回答的**语义计划和 `metric@version` 冻结**成卡片：「按当前依据定时刷新，口径和范围不随会话变化」 | `QaPinModal` |
| P8 | `PUT /pages/{pid}/cards` | `{order: cardKey[]}` | 没出现在 `order` 里的卡片就是被移除的；返回 `{cards, removed[]}`。有移除时前端先弹确认卡（标题「保存卡片调整」，字段「移除 / 保存后」，按钮「移除 N 张并保存」，红色） | `PageDrawer.saveCards` |
| P9 | `PUT /pages/{pid}/schedule` | `{refresh: RefreshPlan, sub: Subscription}` | 返回 `{refresh, sub, nextRun}`（`nextRun` 按 `now()` 算）；`onFail` = `保留上次结果\|标记失败并通知`；订阅渠道：邮件、企业微信、站内信。订阅开着但没有接收人时前端给 warning、不提交 | `RefreshPlan`、`Subscription` |
| P10 | `PUT /pages/{pid}/share` | `{mode, members:[{name, dept, perm}], link, allowExport}`（`ShareMember`；后台按姓名映射用户 id） | 返回 `ShareSetting` 和 `linkUrl`。`mode='仅自己'` 时 `link` 强制 `false`（前端开关置灰）；`指定成员` 且没有成员时前端 warning、不提交。**确认卡**（`PageDrawer.saveShare`）：有新增可见人**或**新开放权限时先弹「保存共享设置」确认卡，字段「新增可见人」「链接访问」「允许导出」，按钮「共享」。新开放权限两种：链接从关到开且 mode≠仅自己 →「开启链接访问：有链接且有数据权限的人都能打开」；允许导出从关到开且 mode≠仅自己 →「允许导出：可见人能下载卡片明细」。后台不弹卡，但 `details` 里返回 `newViewers[]`、`newGrants[]` 供前端核对 [推断] | `ShareSetting`、`saveShare` |
| P11 | `GET /templates` | `kw`（URL `tkw`）、`scene`、`page`、`size=15`（URL `tpage`/`tsize`） | `QaTemplate{id,name,scene,cards,provider,uses,updatedAt,official,desc}` | `PAGE_TEMPLATES` |
| P12 | `GET /templates/{id}` | — | 带全部卡片，前端每页显示 6 张 | `TemplateDrawer` |

**P2 的刷新文字 → `RefreshPlan`**（`UseTemplatePop` 的 4 个选项，`MyPages.fromTemplate` 的转换规则；每种都加 `onFail:'保留上次结果'`）：

| 气泡里选的文字 | `refresh` |
|---|---|
| 每天 08:00（默认） | `{freq:'每天', time:'08:00'}` |
| 每小时整点 | `{freq:'每小时', time:'整点'}` |
| 每周一 08:00 | `{freq:'每周', weekday:'周一', time:'08:00'}` |
| 手动刷新 | `{freq:'手动', time:'--'}` |

反过来显示用 `freqLabel(RefreshPlan)`（后台可以在响应里带 `refreshText`）。新建空白页面没有刷新选项，`refresh` 用默认 `{freq:'每天', time:'08:00', onFail:'保留上次结果'}` [推断]。

**卡片取数**：缓存 key = `(cardKey, metric@version, 权限指纹)`，权限指纹是查看人行级谓词（`sql_guard.row_filters_for(user)` 的结果）的哈希。拥有者的定时刷新按拥有者的权限算；被共享人和订阅接收人**各按自己的权限**算 [推断：前端写的是「看到的数据按各自的数据权限裁剪」]。卡片引用的指标有未发布的 CR 时，`pending` 写 CR 说明，页面状态变成 `caliber_pending`，由静态模块的发布事件触发。

选接收人和成员用 `GET /api/admin/v1/users?kw`（第 8 节 X5）。

---

## 5. R3 对象搜索 `/use/objects/search`

**用途**：选类型、输关键词、勾分面查对象，导出结果，或者用这批对象直接提问。

**数据来源**（PDE 已定）：门店、商品、订单在 `biz` 模块（本体小白的骨架，接口前缀 `/api/biz/v1`），问数只读；行级范围由网关 `sql_guard.row_filters_for` / `inject_row_filters` 注入，问数不自己拼条件。

| # | 方法 路径 | 入参 | 出参 / 规则 | mock 来源 |
|---|---|---|---|---|
| O1 | `GET /objects/types` | — | `[{kind,label,unit,kwPlaceholder,facets:[{key,label,order?}],columns:[{key,title,num?,width?}]}]` | `mock/objects-qa.ts` `OBJ_TYPES` |
| O2 | `POST /objects/{kind}/search` | `{kw, facets:{region:["华东一区"],…}, page, size=16}`（URL 不带前缀） | `{items:[{id,title,cells:{key:value}}], total, page, size, facets:[{key,label,vals:[{v,n}]}], scopeText}`。**分面计数** = 关键词和其他分面都生效后的数量，本分面不参与。行和计数都只含行级范围内的数据 | `ObjectSearch.facetValues`；`stores.ts`、`products.ts`、`orders.ts`、`OBJ_EXTRA_STORES` |
| O3 | `POST /objects/{kind}/export` | `{kw, facets, ids?}` | CSV（UTF-8 BOM）；传了 `ids` 就导出所选，否则导出当前筛选结果；预览时返回 `EXPORT_FORBIDDEN` | `exportCsv` |
| O4 | `POST /scoped-ask` | `{scope:{kind,label,filter?:{kw,facets}, ids?, single?:{kind,id,title}}, question}` | `AskAnswer{text, basis, rows:[{id,kind,name,value,note}], rowsTitle?}`。**范围只传筛选条件**，没勾选时不要传全部 id（订单有 1286 条）。预览时也能问，见 1.1。用的是 2.2 同一条流水线，结果是同步 JSON | `answerFor`、`ObjAskModal` |

---

## 6. R4 对象详情 `/use/objects/detail?kind&id&tab`

**用途**：看一个对象的属性、关键指标、关联对象、时间线、动作记录，对它发起动作或提问。问数工作台的右栏也用这里的接口。

**数据来源**（PDE 已定）：门店、商品、订单在 `biz` 模块（本体小白的骨架，接口前缀 `/api/biz/v1`），问数只读，库里直接读 biz 的表（`store`、`product`、`biz_order`、`order_line`，骨架 `0c35c1d` `app/modules/biz/models.py`；没有会员表、没有 `store_sales`）。O1–O7 不自己拼行级条件，调用骨架 `auth.row_filter` + `row_filter.to_sql`（跟 biz 路由的 `apply_row_filter` 共用 `governance.py` 的登记）。

**跟公共抽屉合并**（PDE 已定）：`/object-views` 归问数，跟 `/objects/{kind}/{id}` 合并成一个接口 O5：`GET /api/qa/v1/objects/{kind}/{id}?view=drawer|full`。公共抽屉 `DetailDrawer`（含工作台 `?drawer=region:REG-EC`）用 `view=drawer`，返回 `{kind,kindLabel,id,code,title,status,groups}`；对象详情页用 `view=full`（默认），多返回 `kpis`、`quickQuestions`、`scopeLabel`。原来 api-index.md §2.5 的 `/api/ontology/v1/object-views/{kind}/{id}` 不再单独实现，api-index 需同步改。

| # | 方法 路径 | 入参 | 出参 / 规则 | mock 来源 |
|---|---|---|---|---|
| O5 | `GET /objects/{kind}/{id}`（合并了 `/object-views`） | `view=drawer\|full`（默认 full） | `{kind,kindLabel,id,code,title,status, groups:[{title,fields:[{label,value}]}], kpis:[{label,value,caliber}], quickQuestions[], scopeLabel}`；没找到返回 `NOT_FOUND`，不在范围内返回 `OUT_OF_SCOPE`。大区对象额外返回 `owner,provinces,stores,sales,repeatShare,parent` | `objectRegistry.resolveObject`、`FIELD_GROUPS`、`caliberOf`、`storeExtraKpi`、`quickQuestions`；`REGION_EC` |
| O6 | `GET /objects/{kind}/{id}/related` | `group?`、`page`、`size=8` | `[{key,title,denied?, items:[{kind,id,label,note,denied?}], total, page, size}]`；每组各自分页（每组就是一个 `{items,total,page,size}`）；没权限的对象类型整组返回 `denied`，不给计数 | `relationGroups`、`QA_REGION_RELATED` |
| O7 | `GET /objects/{kind}/{id}/timeline` | `page`、`size=8`（右栏取前 6 条） | `{items, total, page, size}`，`items` 是 `TlItem{time,text,desc,status}` / 右栏 `{level,title,time,desc}`；`unknown` 统一补一行「结果未知 · 待对账」 | `extraTimeline`、`QA_TIMELINE`、`QA_REGION_TIMELINE`、`QA_UNKNOWN_LABEL` |
| ~~O8~~ | **不是问数接口**：动作记录页签直接调动态的 `GET /api/ontology/v1/action-runs?objKind={kind}&objId={id}&page&size=14`（列表），点一行调 `GET /api/ontology/v1/action-runs/{id}`（详情，时间线来自 `action_run_log`），见第 8 节 X7 | — | 前端 `ActRecord{id,action,by,approver,time,result}` 由前端或 BFF 从 `action_run` 映射（`result` 见 1.3）；**问数不定义动作记录接口，也不建表** | `actionRecords` |
| O9 | `GET /objects/{kind}/{id}/actions` | — | `ObjAction{key,name,desc,fields,impact,irreversible,blocked?,approver?, state, stateTip}`；`blocked` 是提交条件不满足的原因（用 action-event 的试运行 `dry_run` 求值）；`state` 见 1.3；`stateTip` 是悬停文字（如「王敏 10-25 15:36 提交 · 待 赵磊 审批」） | `objActions`；`QaObjectPanel` 写死的 3 个动作 |

点「发起」后：X1 暂存（生成 `待确认` 的 `action_run` 和幂等键）→ 确认卡显示 → 「提交审批 / 确认执行」调 X2 确认 → 变成「待审批」或「执行中」；确认卡上「取消」→ X2b 撤回（原因写「确认卡取消」）[推断]，这一行恢复成「发起」。确认卡打开时这一行显示「待确认」。「对它提问」用 O4（`single`）。

---

## 7. R5 问数调优 `/build/ontology/qa-tuning?tab=cert|eval|feedback|freq`

**用途**：维护认证问答，跑评测，处理用户的踩和纠错，把高频问题设成认证问答。

| # | 方法 路径 | 入参 | 出参 / 规则 | mock 来源 |
|---|---|---|---|---|
| T1 | `GET /tuning/summary` | `ver?`（查询参数，带了即只读） | `{version, dataAsOf, recertCount, openFeedback, uncertFreq, evalTotal}`，即页签上的计数；`dataAsOf` 按 `now()` | `QaTuning.vue` |
| T2 | `GET /tuning/plan-options` | — | `{metrics:[{key,name,version,cr?}], dims[], times[]}`；`cr` 是影响该指标的未发布 CR；`key` 是 `T_METRICS` 的键 | `qa-tuning.ts` · `T_METRICS`、`TUNING_DIMS`、`TUNING_TIMES`（原来写死在 `QaTuning.vue`，已搬进 mock） |
| **认证问答** |||||
| T3 | `GET /tuning/certs` | `kw`（同时匹配同问法）、`status=已认证\|待重新认证\|待认证\|已停用`、`metric=<T_METRICS 键>`、`page`、`size=15`（URL `ckw`/`cst`/`cm`/`cpage`/`csize`） | `CertQa{id,question,variants,plan,planText,status,cr?,certifier,certifiedAt,hits,source,note?}`；排序：待重新认证 › 待认证 › 已认证 › 已停用 | `CERT_QAS` |
| T4 | `GET /tuning/certs/{id}` | — | 抽屉内容 | `CertTab` |
| T5 | `POST /tuning/certs` | `{question(≤40), plan}` | **新建后是 `待认证`**，不会直接变成已认证；重复返回 `DUPLICATE_KEY` [推断：前端新建时不查重] | `createCert` |
| T6 | `POST /tuning/certs/{id}:certify` | — | 指标有未发布的 CR 时：按当前版本锁定，`status='待重新认证'`，`note` 写「CR 发布后需再次认证」；没有就是 `已认证`；`certifier` = 当前用户，`certifiedAt` = `now()`。相同或相近的问题以后直接按它回答，并标注「已认证」 | `certify` |
| T7 | `PATCH /tuning/certs/{id}` | `{enabled: bool}` | **照前端**（`CertTab.toggle`）：任何非「已停用」状态（含待认证、待重新认证）都能停用 → `已停用`；启用一律 → `已认证`（前端不看原状态，也不看有没有未发布 CR）。确认卡字段「近 30 天命中」，停用是红色。[推断] 启用时如果原来是待认证或指标有未发布 CR，应恢复成原状态而不是直接已认证；第一期先按前端做，列入第 12 节等用户定 | `toggle` |
| T8 | `PUT /tuning/certs/{id}/variants` | `{variants: string[]}` | 加或删同问法；重复返回 `DUPLICATE_KEY` | `addVariant`、`removeVariant` |
| **评测题库** |||||
| T9 | `GET /tuning/eval/runs` | `trigger=每日定时\|手动运行\|发布前回放\|发布后回放\|审核通过后试跑`（按包含匹配）、`page`、`size=15`（URL `etr`/`epage`/`esize`） | `EvalRun{id,time,trigger,version,scope,total,passed,passRate,failCount,status,operator,cr?,crs?,note?}` | `EVAL_RUNS` |
| T10 | `GET /tuning/eval/runs/{id}` | `page`、`size=12`（前端抽屉本地分页 `FSIZE=12`，不进 URL） | 加上 `fails: {items: FailCase{qid,question,expect,actual,kind}[], total, page, size}`、`kindStats[{kind,n}]`；待运行的回放额外返回 `replayQuestions` 和每张 CR 的提示 | `FailCase`、`REPLAY_HINT` |
| T11 | `POST /tuning/eval/runs` | `{group: all\|销售\|退款\|履约\|毛利\|复购\|订单}` | 202，返回 `status='运行中'` 的 EvalRun（`trigger='手动运行'`，`time` = `now()`）；后台任务（procrastinate）逐题生成语义计划，跟期望比对，**不改任何口径** | `startRun` |
| T12 | `GET /tuning/eval/questions` | `group`、`last=通过\|失败`、`kw`、`page`、`size=15`（URL `eg`/`er`/`ekw`/`qpage`/`qsize`） | `EvalQ{id,question,group,expect,expectText,last,cr?}` + `groupCounts` | `EVAL_QS`、`EVAL_GROUPS` |
| **反馈与纠错** |||||
| T13 | `GET /tuning/feedbacks` | `kw`（问题或用户）、`status=待处理\|已转同义词\|已转认证问答\|待发布生效\|已忽略`、`fixKind=同义词\|认证问答`、`page`、`size=15`（URL `fkw`/`fst`/`fk`/`fpage`/`fsize`） | `Feedback{id,time,user,dept,question,answer,reason,fix:{kind,text,target?},status,resolvedBy?,handler?}`；`待处理` 排在前面 | `FEEDBACKS` |
| T14 | `GET /tuning/feedbacks/{id}` | — | 加上 `certBlock`（不能转认证问答的原因）、`existingCert?`、`coveredBy?`（已经覆盖这个词的同义词 CR）、`relatedEvalCount` | `certBlock`、`synCovered` |
| T15 | `POST /tuning/feedbacks/{id}:to-synonym` | `{join?: crId, title?}` | 已经被某张 CR 覆盖的：直接关联（CR 已通过 → `待发布生效`，否则 `已转同义词`）。没覆盖的：调静态模块建或加入变更单草稿（X3），**确认时才分配 CR 号**；**同一个词所有 `待处理` 的反馈一起改状态**；返回 `{crId, affected[]}` | `toSynonym`、`onStartChange` |
| T16 | `POST /tuning/feedbacks/{id}:to-cert` | — | 已经有同问题的认证问答：把用户原话加成它的同问法；没有：用找到的语义计划新建认证问答，原话也加成同问法。找不到语义计划返回 `CERT_NO_PLAN`；返回 `{certId, mode: new\|variant}`（新建的认证问答是 `待认证`） | `toCert`、`planFor` |
| T17 | `POST /tuning/feedbacks/{id}:ignore` / `:reopen` | — | `已忽略` ⇄ `待处理`；重新打开时清空处理人和处理结果 | `ignore`、`reopen` |
| **高频问题推荐** |||||
| T18 | `GET /tuning/freq` | `period=7\|30`（按 `now()` 往前数）、`cert=未认证\|已认证`、`kw`、`page`、`size=15`（URL `hp`/`hc`/`hkw`/`hpage`/`hsize`） | `FreqQ{id,rank,question,hits7,hits30,users,plan,planText,consistency,lastAsked,certId?,certStatus?}`；按所选周期的命中次数排名 | `FREQ_QS` |
| T19 | `POST /tuning/freq/{id}:certify` | — | 同 T6，`source='高频问题推荐'`；确认卡显示命中次数，一致率低于 70% 时加一句提醒 | `FreqTab.certify` |
| T20 | `GET /tuning/synonyms` | — | 已经走了变更单的同义词 `TunedSynonym{word,target,cr}`，给 T14 判断是否已覆盖 | `qa-tuning.ts` · `TUNED_SYNONYMS`（静态 `ef3d6dd` 从 `state.ts` 搬进 mock） |

**高频统计 [推断]**：每天从 `qa_query_log` 聚合，周期按 `now()` 往前数 7 / 30 天。问题先归一，再用 pgvector 聚类近义问法。`consistency` = 这一类问题里最常见的那份语义计划占比。只统计正式身份的提问，不统计预览和评测的。

**联调实现（qa-joint，认证问答 T1/T3–T8、高频问题 T18/T19 已落；评测题库 T9–T12、反馈与纠错 T13–T17 前端仍是 mock）**：
- T5 返回 201 + `CertQa`（同 T4），`status='待认证'`、`source='手动新建'`；查重按问题**忽略大小写**比较 [推断]；编号 `CQ-` + 现有最大序号 + 1（3 位）[推断]；`plan.metric` 不是 T2 里的指标 → 422 `VALIDATION_FAILED`；指标有未发布 CR 时带上 `cr`，`note` 同前端 `createCert` 文案；同问题的高频问题（`qa_freq_question`）顺带关联到新认证问答 [推断]。前端撞重名时提示「已有认证问答「…」」（warning，不关弹窗）。
- T6 只允许从 `待认证` / `待重新认证` 发起，其他状态 409 `STATE_INVALID` [推断]；`certifier` = 有效用户名、`certifiedAt` = `now()`；`cr` 仍未发布 → `待重新认证`，`note` =「已按 {版本} 认证；{CR} 发布后需再次认证」；否则 `已认证`，清掉 `cr` / `note`。
- T7 按上表照前端：`enabled=false` → `已停用`，`enabled=true` → `已认证`。
- T8 整体替换同问法列表（后台按差集删 / 加）；列表内重复、或者跟**别的认证问答**的同问法重复 → `DUPLICATE_KEY` [推断：同问法全局唯一，否则命中时不知道按哪条答]。
- T19 这条高频问题已经关联了认证问答（不论状态）→ 409 `STATE_INVALID` [推断]；否则按 T5 新建（`source='高频问题推荐'`，`hits` = 近 30 天命中）再按 T6 认证，返回 `CertQa`。
- 以上写接口都写审计（`qa.cert.create` / `qa.cert.certify` / `qa.cert.toggle` / `qa.cert.variants` / `qa.freq.certify`），都受只读规则约束（`?ver=` / `?previewAs=` 下 403 `READONLY`）。
- 前端：开关 `qa` 切真接口时，认证问答页签的列表 / 筛选 / 翻页走 T3（服务端分页），抽屉 T4，新建 T5、认证 T6、停用启用 T7、同问法 T8；高频问题页签走 T18（`hp`/`hc`/`hkw`/翻页）、设为认证问答 T19；页签计数里「待重新认证」「未认证」取 T1，「评测题库」「反馈与纠错」计数仍取 mock（这两个页签未接）。写完后统一刷新当前列表、抽屉和计数。

### 7.1 状态流转（写的是接口值；库里的码见 1.3）

```
反馈：踩(W12) → 待处理
  待处理 → 已转同义词（CR 是草稿或审核中）→ 待发布生效（CR 已通过）→ [待用户定：发布后的终态，第 11 节]
  待处理 → 已转认证问答
  待处理 → 已忽略 → (reopen) 待处理
认证问答：T5 新建 → 待认证 → T6 → 已认证；指标有未发布 CR → 待重新认证；CR 发布 → 待重新认证（X4 事件触发）→ T6 → 已认证
         任何非已停用 → 已停用（T7）；已停用 → 已认证（T7，照前端，见 T7 的 [推断]）
评测运行：每天 02:00 每日定时、手动运行 T11、审核通过后试跑、发布前回放、发布后回放 → 待运行 → 运行中 → 已完成
页面：正常 ⇄ 刷新失败（保留上次结果）；指标有未发布 CR → 口径待更新 → 发布后重算 → 正常
动作（动态的 action_run，问数只引用）：X1 stage → 待确认(staged) → X2 confirm → 待审批(approving) / 执行中(executing) → 成功 / 失败 / 结果未知 → 已对账；确认卡取消 → 已撤回(withdrawn) [推断]
```

---

## 8. 跟其他模块的接口（不算进 57 个）

动态、动作、事件都归 `ontology` 模块：动作类型 `/api/ontology/v1/action-types/...`、事件类型 `/api/ontology/v1/event-types/...`、动作执行 `/api/ontology/v1/action-runs`。数据连接和外部系统 `/api/data/v1`；用户、角色、配置、审计 `/api/admin/v1`。

| # | 归谁 | 接口 | 谁在用 |
|---|---|---|---|
| X1 | 动态 | `POST /api/ontology/v1/action-runs:stage` `{action, objKind, objId, objLabel, params, source}`（camelCase，跟 mock `actions.ts` 的 `StageInput` 一致；`initiator`、`at` 由服务端按当前用户和 `now()` 填，问数来源另带 `qaSession`） → `action_run`（`status=staged` 待确认）+ 确认卡字段（动作类型、参数、影响、预算、提交条件、审批人、写回、**幂等键**）。**幂等键由动态在 stage 时生成**（规则：动作编号去掉 `ACT-` 前缀转小写，前面加 `act-`，如 `ACT-20261025-0007` → `act-20261025-0007`；重新提交依次追加 `-r2`、`-r3`…），确认卡上显示的就是它；**问数不再传 `idempotencyKey`**。提交条件预检**复用 action-event 内部的试运行 `dry_run(action_type_id, params, ver?)`**，不满足返回 `SUBMISSION_CRITERIA_FAILED`（422，`details.failed[{name, fail}]`）。 | 工作台「建议动作」和右栏「发起」、对象详情「发起」（打开确认卡时调用） |
| X2 | 动态 | `POST /api/ontology/v1/action-runs/{id}:confirm` → `{id, status}`：有审批人 → `approving`（问数显示「待审批」，确认卡按钮「提交审批」）；没有 → `executing`（「执行中」，按钮「确认执行」）。外部系统超时标记成 `unknown`「结果未知」，进对账，不自动重试。 | 确认卡「提交审批 / 确认执行」 |
| X2b | 动态 | `POST /api/ontology/v1/action-runs/{id}:withdraw` `{reason:'确认卡取消'}` → `withdrawn` [推断：前端取消只是关卡片，见第 12 节第 15 条] | 确认卡「取消」 |
| X1/X2/X2b 权限 | 动态 | 工作台和对象详情发起的 stage / confirm / withdraw **都按对象权限里的 `actions` 校验**（该对象类型对当前用户授权了这个动作才放行，没有返回 403 `NO_PERMISSION`）；O9 的「发起」是否置灰用同一份授权。只读模式下三者都返回 403 `READONLY` | — |
| X3 | 静态 | `POST /api/ontology/v1/change-requests`、`POST /api/ontology/v1/change-requests/{id}/targets`、`GET /api/ontology/v1/change-requests?status=draft` | T15 转同义词 |
| X4 | 版本层 | 发布事件 `release.published{ver, parentVer, kind: "release"\|"rollback", rollbackOf, changeOrderIds}`（**已定**：版本层 `versioning.create_version` 写，骨架 `0c35c1d` 已实现）：在 `create_version` 内、同一事务里调 `app/common/outbox.emit(session, "release.published", payload, event_id="ontology_release:<ver>:release.published")` 写进 outbox 表 `outbox_event`（`event_id` 唯一，订阅方按它去重），发布和整版回滚都会发（回滚时 `kind="rollback"`、`rollbackOf` = 被回滚的版本，内容 = 它的上一版）；由 `app.jobs.dispatch_outbox` 分发给 `app/common/outbox.subscribe("release.published", handler)` 登记的订阅者（失败 `attempts+1`、记 `last_error`）。**问数只在启动时 `subscribe`，不轮询、不写别的模块的表**。问数不负责任何快照 section，不实现 `build_section` / `restore_section`。收到事件后对 `qa_object_type_view`：`kind="release"` 且 `changeOrderIds` 动到了对象类型 / 属性 / 指标的展示定义时，按新版本快照追加一行 `release_id` = 新版本；没动到就不写，读的时候按 9.2 回退规则取；`kind="rollback"` 不写新行，读的时候按 `rollbackOf` 的上一版取 [推断] | 认证问答改成待重新认证、页面口径待更新、评测回放、反馈变成待发布生效 |
| X5 | admin | `GET /api/admin/v1/users?kw`、`/api/admin/v1/roles`、`/api/admin/v1/audit-logs`（写审计）、权限审批流、预览会话 | P9/P10 选人，W16，1.1 |
| X7 | 动态 | `GET /api/ontology/v1/action-runs`（统一列表，按 `objKind`、`objId` / `qaSession` / `status` 过滤，分页 `page,size`）、`GET /api/ontology/v1/action-runs/{id}`（详情 + 时间线，时间线来自 `action_run_log`）。问数页面里所有动作记录列表和详情都调这两个，问数**不定义自己的动作记录接口或表** | 对象详情「动作记录」页签、工作台右栏动作状态（O9 的 `state` 也由最近一条在途 run 推出） |
| X6 | 骨架 | `auth.row_filter(user, object_type)`（进程内，返回行级表达式；None = 不限制）+ `sql_guard.row_filters_for(user)`（编出各受控表谓词 `{table: (pred, params)}`）：预览时传被预览人。订单经 `order_belongs_to_store` 一跳按 `store.big_region`；`order_line` 经 `order_id` 继承订单谓词（两跳）；`product` 受控（`c646bd5` 已实现）：不是 `defaultDeny`、又没配品类范围的角色才不限，有品类范围按 `CATEGORY_SCOPE_ALIAS` 过滤，`defaultDeny` 角色（R-05/R-06/R-08）没配品类范围时商品不可见（见 2.2）；订单.门店名范围（`c646bd5` 已实现，见 2.2）；多条范围按 §1.5 合并（`c646bd5` `sql_guard.effective_expr` 已实现）：只算对该对象有查看权限的角色，`defaultDeny` 没配范围的不给看，任一角色不限就看全部，否则所有范围取并集（跨角色 OR，同角色内也 OR [推断]，`roles.ts:114`）（见 2.2）；未声明的关系拒绝；无角色用户返回恒假谓词（没有行）。`auth.row_filter` 仍是桩 **[待骨架实现]**（按用户角色组装 `RoleAccess` 调 `effective_expr`）：只有本体管理员（`RENGU_DEMO_ADMIN_CODES`，默认 `U010001`）不限制，其他人一律 `DENY_ALL` | 2.2 security、O1/O2/O4、P 系列权限指纹 |

**数据归属**：`action_run` 归动态（字段见 dynamic-api：`id, action_type_id, type_version, source_type, …, qa_session_id, obj_kind, obj_id, params, initiator, confirmer, approver, idem_key UNIQUE, status, …`），问数**不建 `action_record` 表**，对象详情的动作记录和右栏都经 X7 读它。`action_run.qa_session_id` 外键到 `qa_session(id) ON DELETE SET NULL`：这个外键写在**问数的迁移**里（问数迁移在动态之后跑，表已存在）；种子时动态先写 `action_run`，`qa_session_id` 留空，问数种子写完 `qa_session` 后按 mock 的会话编号回填，对不上的保持空。


---

## 9. 种子数据与表结构：把前端 mock 灌进数据库

**约定**：种子脚本放在 `backend/app/modules/qa/seeds/`，**直接从现有前端 mock 导入，不另编数据**。

**怎么导入**（团队约定，统一由 PDE 维护导出脚本，各模块不再自己跑 esbuild / tsx）：
1. `npm run export-mock`（PDE 编写，`scripts/export-mock.mjs`）：把 `frontend/src/mock/*` 原样导出到 `backend/seeds/json/<分组>/<导出名>.json`。分组按 export-mock 的映射，**问数分组 `qa` = `qa.ts`、`qa-pages.ts`、`qa-tuning.ts`、`objects-qa.ts`**，所以问数读 `backend/seeds/json/qa/`；门店、商品、订单在 `common` 分组，由 biz 种子读。时间保持 mock 里的北京时间原值，不按入库时间平移。
   - 函数类 mock（export-mock 跳过函数）按派生配置逐个调用，导出到 **`backend/seeds/json/qa/derived/<函数名>.json`**，格式统一是 `[{args, result}]`（`args` 是调用参数数组，`result` 是返回值），清单见下面「函数算出的数据」。
   - mock 里没有、只为满足外键的手写数据放 `backend/seeds/extra/qa/`（例如 `repeat_sales`），`is_seed=true`。
   - SQL 级评测题库（PDE 定）放 `backend/seeds/extra/qa/eval/`，灌进 `qa_eval_question`（`kind='sql'`，带 `gold_sql`）；题库内容和评测说明见 `qa-eval.md`。
   - 指标中文名 → `metric` 的对照（8 个组合，都是当前版本）：

     | mock 里的 `metric` + `version` | `metric.code` | `version` |
     |---|---|---|
     | 销售额（不含税） v3 | `gmv` | 3 |
     | 毛利率 v2 | `gross_margin` | 2 |
     | 有效订单数 v1 | `order_cnt` | 1 |
     | 退款率 v2 | `refund_rate` | 2 |
     | 出库及时率 v1 | `ship_ontime` | 1 |
     | 客单价（不含税） v1 | `atv` | 1 |
     | 复购客户销售额（不含税） v3（`QA_METRIC.ex`） | `repeat_sales` | 3 |
     | 复购客户销售额（含税） v1（`QA_METRIC.in`） | `repeat_sales_tax` | 1 |

   - 每个导出文件对应哪张表见 9.1 的映射表。
2. `backend/app/modules/qa/seeds/*.py` 读 `backend/seeds/json/qa/` 写库：中文枚举按 1.3 转成库里的码，人员列按 9.2「人员列」分开处理：**外键列**（当前登录用户）用骨架 `code_by_name(name)` 换成 `sys_user`，**查不到直接报错**；**软引用列**原样写 mock 里的姓名，不查 `sys_user`、不报错；mock 的编号写进 `code`（`id` 由库生成），外键按 `code` 查出 `id` 再写；指标按 `(metric.code, version int)` 查出 `metric_id`：mock 的中文名先按 `metrics.ts` / `QA_METRIC` 的 `name → id` 换成 code，`"v3"` 换成 `3`（`T_METRICS` 的键经 `drawerId` 映射，`repeat`/`repeat_tax` 经 `QA_METRIC`）；外键（含人员外键列）查不到直接报错，不静默跳过（人员外键列的 mock 值已按 9.2 改写或剔除，见那里的核对表）。迁移跑完后由骨架统一的 `make seed` 执行。
3. 种子可以重复执行：按业务键（一般是 `code`）**批量** upsert（骨架 `SeedSpec.key`；每张表按批 `INSERT … ON CONFLICT … DO UPDATE`，不逐行提交），种子行由 loader 统一标 `is_seed=true`；`make seed` 跑两次结果相同。前端 mock 改了之后重新执行 `npm run export-mock`。
4. **当前时间**：种子环境的 `now()` 由骨架固定为 `2026-10-25 15:42`（Asia/Shanghai）。问数里的今天、近 7 / 30 天、上个月、环比、数据截至、高频统计周期、下次刷新时间都按 `now()` 算，这样跟 mock 显示一致。
- 函数算出的数据（`export-mock` 会跳过函数，需在派生配置里逐个调用后导出到 `backend/seeds/json/qa/derived/<函数名>.json`，每个文件是 `[{args, result}]`）：
  - `objects-qa.OBJ_TYPES` 的 39 个函数字段：3 个类型各 `rows()`、`id(row)`、`title(row)`、`kw(row)`；门店 `facets[0..3].get`、`columns[0..8].get`（`columns.sales` 用 `storeSales(s.id).sales`），商品 `facets[0..1].get`、`columns[0..4].get`，订单 `facets[0..1].get`、`columns[0..4].get`（订单列用 `storeById`）：按每个类型调用 `rows()`，再对每行调用各 `get` → `qa_object_type_view`（facet / column 定义，`get` 记成 `{key, sourceProperty}`）+ biz 演示表的行。
  - `objects-qa.storeSales(id)`：按每个门店 id 调用 → **不入库**：biz 不建 `store_sales`（`objects-qa.ts:60` 是随机生成的 9 月数据，不能从订单推出）；O3 门店列「销售额」改成按 gmv 实时算或先不显示 [推断]。
  - `objects-qa.caliberOf(label)`：按 `FIELD_GROUPS` 和 KPI 里每个指标显示名调用 → O5 `kpis[].caliber`（口径说明，写进 `qa_object_type_view.kpis`）。
  - `objects-qa.storeExtraKpi(id)`：按每个门店 id 调用 → O5 门店的扩展 KPI（biz 侧派生，或物化进 `qa_object_type_view`）。
  - `objects-qa.relationGroups(kind, id, related?)`：按每个对象（3 类 × 各行）调用 → 静态 `link` + biz 外键核对（O6）。
  - `objects-qa.extraTimeline(kind, id, base?)`：按每个对象调用 → 动态 `event`（O7，由动态种子负责，这里只核对 id）。
  - `objects-qa.actionRecords(kind, id)`：按每个对象调用 → 动态 `action_run` + `action_run_log`（`source_type` 按记录来源，问数来源的填 `qa_session`；由动态种子负责）。
  - `objects-qa.objActions(kind, id)`：按每个对象调用 → 动态 `action_type`（按对象类型去重）+ 在途 `action_run`（`state` 非空的行）。
  - `objects-qa.quickQuestions(kind, title)`：按每个对象调用 → O5 `quickQuestions`（按类型模板化，存进 `qa_object_type_view.quick_questions`，标题占位）。
  - `objects-qa.answerFor(scope, question)`：按 `OBJ_SCOPE` 和每个快捷问题调用 → 只导出成验收对照 fixture，不入库（真回答走 2.2 流水线）。
  - `qa-tuning.pendingCrOf(metricKey)`：按 `T_METRICS` 每个键调用 → T2 `metrics[].cr`（核对静态 CR 种子）；`qa-tuning.crById(id)`：按 mock 里出现的每个 CR 号调用 → 核对静态 `change_request` 种子。
  - 公共的 `storeById`、`productBySku`、`orderById`：按订单、关联里出现的 id 调用 → biz 外键核对。
  - 纯计算的（`planText`、`certStatusText`、`fbStatusText`、`passRate`、`metricLabel`、`qaMetricLabel`、`inScope`、`previewDenied`、`objRow`、`objType`、`freqLabel`）不入库，后台按同样规则实时计算。
- 写库顺序：骨架 `app/seeds/registry.py` 的模块顺序是 admin（`sys_user`）→ data → integration（只写 `ext_system`）→ biz → versioning（`ontology_release`）→ static（`metric`、`change_request`）→ action → event → dynamic（`action_run`）→ integration_runs（`writeback_call`、`reconcile_task`）→ **qa**，问数最后（`integration_runs` 段由本体小白在骨架 `SEED_MODULES` 里加占位）。问数内部：`qa_object_type_view` → `qa_session` → `qa_turn`（+ `qa_turn_event`）→ `qa_favorite` → `qa_template` / `qa_template_card` → `qa_page` 及子表 → `qa_cert` / `qa_cert_variant` → `qa_eval_question` / `qa_eval_run` / `qa_eval_fail` → `qa_feedback` → `qa_freq_question` → `qa_synonym_link` → 回填 `action_run.qa_session_id`。
- 验收：切到真接口后，5 个页面显示的内容、计数和状态与现在 mock 一致。

### 9.1 种子集

| 种子集 | 目标表 | 前端来源（文件 · 导出） | 条数（实测） | 备注 |
|---|---|---|---|---|
| sessions | `qa_session` | `mock/qa.ts` · `QA_SESSION_LIST` | 103 | 拥有者都是王敏（`U010001`） |
| demo_turns | `qa_turn`、`qa_turn_event` | `qa.ts` · `QA_DEMO_TURNS`（第二个提交从 `QaConversation.vue`、`QaAnswer.vue`、`QaStoreAnswer.vue` 搬进 mock 的纯数据常量）；数据部分 `QA_PROVINCES`(7)、`QA_TREND`、`QA_TOTAL_*`、`QA_EC_STORES`(38)、`QA_APPLY` | 第一个会话 4 轮 + 1 条系统提示 | `qa_turn_event` 按回答内容生成一份事件序列，供回放 |
| favorites | `qa_favorite` | `qa.ts` · `QA_FAVORITE_LIST` | 18 | |
| scope_options | `qa_session_option` | `qa.ts` · `QA_SCOPE_OPTIONS`、`QA_CALIBER_OPTIONS`、`QA_METRIC` | 3 + 2 | 范围其实应该从用户的行级权限推出来 [推断] |
| permission_requests | `qa_permission_request`（问数自有，PDE 已定） | `qa.ts` · `QA_APPLY` | 0（只有预填模板） | |
| pages | `qa_page`、`qa_page_card`、`qa_page_share_member`、`qa_page_subscription` | `mock/qa-pages.ts` · `MY_PAGES`、`OTHER_PAGES`（卡片来自 `CARD_LIB`） | 27 + 5 个页面 / 197 张卡片 | 前 13 个页面跟 `QA_PAGE_LIST` 是同一批 |
| templates | `qa_template`、`qa_template_card` | `qa-pages.ts` · `PAGE_TEMPLATES` | 22 / 104 | |
| people | → admin `sys_user`（只给人员**外键列**做姓名 → `code` 映射，用骨架 `code_by_name(name)`，查不到或重名报错；软引用列不经这里） | `qa-pages.ts` · `PEOPLE`、`OWNER_DEPT`；`qa-tuning.ts` · `PEOPLE_T` | 12 | 骨架 `sys_user` = `ADMIN_USERS` 120 人 + `extra/system/SYS_USERS.json` 8 人（见下面「演示用户」）。PEOPLE 里 赵磊、周婷、周晓峰、刘洋、张伟、钱一鸣、马晨 7 人 `sys_user` 里没有：**不要 admin 补人**，外键列按 `sys_user` 全表（128 人，含 `ADMIN_USERS`；陈杰 U010763、苏刚 U010231 都在）用 `code_by_name` 映射，全表查不到的才按 9.2 核对表改派或剔除 [推断]；落在软引用列上的照存姓名 |
| certs | `qa_cert`、`qa_cert_variant` | `mock/qa-tuning.ts` · `CERT_QAS` | 30 | |
| eval | `qa_eval_question`、`qa_eval_run`、`qa_eval_fail` | `qa-tuning.ts` · `EVAL_QS`、`EVAL_RUNS` | 40 / 21 / 222 | |
| feedbacks | `qa_feedback` | `qa-tuning.ts` · `FEEDBACKS` | 26 | |
| freq | `qa_freq_question`（物化统计） | `qa-tuning.ts` · `FREQ_QS` | 32 | 上线后改成从 `qa_query_log` 聚合 |
| tuned_synonyms | `qa_synonym_link` | `qa-tuning.ts` · `TUNED_SYNONYMS`（静态 `ef3d6dd` 从 `state.ts` 搬进 mock，**由问数模块负责灌**） | 2 | **按 `word + cr` upsert** |
| tuning_options | 不建表，T2 直接返回 | `qa-tuning.ts` · `TUNING_DIMS`(12)、`TUNING_TIMES`(9)（第二个提交从 `QaTuning.vue` 搬进 mock） | 12 / 9 | |
| tuning_metrics | → 静态 `metric`（只引用） | `qa-tuning.ts` · `T_METRICS`；`qa.ts` · `QA_METRIC` | 9 | 见下面 repeat_sales |
| object_views | `qa_object_type_view` | `mock/objects-qa.ts` · `OBJ_TYPES`（facets/columns，函数见上）、`FIELD_GROUPS`、`KIND_LABEL`、`OBJ_SCOPE` | 3 类 | 跟静态的 `object_view`（对象视图）是两回事，**名字刻意区分** |
| biz_demo | → `biz` 模块的 `store / product / biz_order / order_line`（由 biz 种子负责，问数只读、只核对 id；没有 `store_sales`） | `mock/stores.ts` · `ALL_STORES`；`objects-qa.ts` · `OBJ_EXTRA_STORES`；`products.ts` · `PRODUCTS`；`orders.ts` · `ORDERS` | 门店 95（含扩充 57）/ 商品 12 / 订单 1286 / 订单行 3905 | 问数要查的「业务数据」，不是元数据。达梦和金仓测试库也灌这一份 |
| object_timeline / actions | → 动态 `event`、`action_run`、`action_run_log`、`action_type` | `qa.ts` · `QA_TIMELINE`、`QA_REGION_TIMELINE`、`REGION_EC`；`objects-qa.ts` · `extraTimeline`、`actionRecords`、`objActions` | 6 / 26 / 按对象生成 | 由动态模块的种子负责，这里只核对 id 是否一致 |
| relations | → 静态 `link` + biz | `objects-qa.ts` · `relationGroups`；`qa.ts` · `QA_REGION_RELATED` | 38 | |
| query_log | `qa_query_log` | 没有 mock | 0 | 高频统计和一致率靠它 |

**演示用户**（骨架 `backend/seeds/extra/system/SYS_USERS.json`，问数种子和接口示例统一用编号）：`U010001` 王敏（演示身份 admin、会话 / 页面拥有者）、`U010002` 陈立军、`U010003` 何静、`U010004` 刘婷、`U010005` 孙浩然 [推断]、`U010006` 李娜（演示身份 viewer）[推断]、`U010007` 赵蕾 [推断]、`U010008` 吴佳 [推断]；另有 `ADMIN_USERS` 里的 `U010231` 苏刚，是前端「以此用户预览」的默认对象（`preview.ts:14`，`?previewAs=U010231`）。[推断] = mock 里没有编号，骨架补的。

**`repeat_sales`（v3）和 `repeat_sales_tax`（v1）**：问数 mock（`QA_METRIC`、`T_METRICS.repeat/repeat_tax`）引用了它们，但**不在 `metrics.ts` 里，也不会加进去**（前端 mock 不改）。后台种子在 `backend/seeds/extra/qa/` 单独补这两个指标，`is_seed=true`，只为满足 `qa_*.metric_id` 的外键。`T_METRICS` 的键（`repeat`、`margin` 等）不是指标 API 名：有 `drawerId` 的按 `drawerId` 映射（`margin` → `gross_margin`），`repeat`/`repeat_tax` 按 `QA_METRIC.ex.id`/`in.id` 映射。

**对象类数据量与分页**：`OBJ_TYPES` 的 `rows()` 指向公共数据——门店 `stores.ts` · `ALL_STORES`（95 家）、商品 `PRODUCTS`（12 个）、订单 `ORDERS`（1286 单），种子由 biz 模块灌，问数只核对 id。`actionRecords` / `objActions` 是函数，export-mock 按全部 (kind, id) 组合展开导出，共 1394 组（95 + 12 + 1286 + 大区 1）。前端这些列表都是**客户端分页**（一次拿全量、本地翻页），所以 O9 第一期**返回全量**；对象详情调 X7（按 `objKind`、`objId` 过滤）**不传 `page` 时返回该对象的全部记录**，传了 `page`/`size` 才分页，等前端改成服务端分页再传 [推断]。

#### 9.1.1 导出文件 → 表（`backend/seeds/json/qa/`）

export-mock 的 `qa` 分组共 48 个导出（第二个提交后加上 `QA_DEMO_TURNS`、`QA_DEMO_CANNED`、`TUNING_DIMS`、`TUNING_TIMES` 共 52 个）。每个文件的去向如下；「不入库」的是常量、显示文字或当前时间，后台写在代码或配置里，种子不灌。

| 导出文件 | 来源 | 去向 | upsert 业务键 |
|---|---|---|---|
| `QA_SESSION_LIST` | qa | `qa_session` | `code` |
| `QA_SESSIONS` | qa | 不入库：前端没有引用（旧版侧栏数据，只有 2 条跟 `QA_SESSION_LIST` 同名） | — |
| `QA_DEMO_TURNS` | qa | `qa_turn`（`snapshot` 存整轮）+ `qa_turn_event`（按回答生成事件序列），挂在 `QA_SESSION_LIST[0]` 下 | `(session_id, seq)` |
| `QA_DEMO_CANNED` | qa | 不入库：原型里新追加轮次用的示例回答，真接口走 W6/W7 | — |
| `QA_PROVINCES`、`QA_TREND`、`QA_TOTAL_SALES`、`QA_TOTAL_CUSTOMERS`、`QA_EC_STORES` | qa | 不单独建表：写进对应 `qa_turn.snapshot`（回答里的表格、趋势、合计）；门店数据同时核对 biz 门店销售 | — |
| `QA_FAVORITE_LIST` | qa | `qa_favorite` | `(owner_id, question)` |
| `QA_FAVORITES` | qa | 不入库：是 `QA_FAVORITE_LIST` 按使用次数取的前 3 条，接口现算 | — |
| `QA_SCOPE_OPTIONS`、`QA_CALIBER_OPTIONS` | qa | `qa_session_option`（`kind` = scope / caliber） | `(kind, option_value)` |
| `QA_METRIC` | qa | 不入库：`ex`/`in` → `metric_id` 的映射，种子脚本和 W17 用 | — |
| `QA_APPLY` | qa | 不入库：W16 表单的预填值；`durations` 是 `duration_days` 的取值（CHECK 30/90/180） | — |
| `QA_PAGE_LIST` | qa | `qa_page`（跟 `MY_PAGES` 前 13 个是同一批，只核对 id） | `code` |
| `QA_MY_PAGES` | qa | 不入库：`MY_PAGES` 的 2 个页面名，接口现算 | — |
| `QA_TIMELINE`、`QA_REGION_TIMELINE`、`REGION_EC` | qa | → 动态 `event`（动态种子负责，问数只核对） | — |
| `QA_REGION_RELATED` | qa | → 静态 `link` + biz（只核对） | — |
| `QA_TODAY`、`QA_UNKNOWN_LABEL` | qa | 不入库：`now()` 的日期；显示文字 | — |
| `MY_PAGES`、`OTHER_PAGES` | qa-pages | `qa_page` + `qa_page_card` + `qa_page_share_member` + `qa_page_subscription` | `code`；卡片 `(page_id, card_key)`；成员 `(page_id, user_id)`；订阅 `page_id` |
| `PAGE_TEMPLATES` | qa-pages | `qa_template` + `qa_template_card` | `code`；卡片 `(template_id, card_key)` |
| `PAGE_SCOPES` | qa-pages | 不建表：页面「适用范围」的可选值，接口从用户的行级范围推出（种子环境就是这 6 个）[推断] | — |
| `PEOPLE`、`OWNER_DEPT` | qa-pages | → `sys_user`（外键列的姓名 → 用户编号 `code` 映射；`sys_user` 全表查不到的按 9.2 核对表处理） | — |
| `PAGES_CUTOFF`、`TUNING_CUTOFF`、`OBJ_CUTOFF` | 各文件 | 不入库：数据截至时间，按 `now()` 和刷新记录算 | — |
| `CERT_QAS` | qa-tuning | `qa_cert` + `qa_cert_variant` | `code`；同问法 `lower(question)` |
| `EVAL_QS` | qa-tuning | `qa_eval_question` | `code` |
| `EVAL_GROUPS` | qa-tuning | 不建表：`qa_eval_question.group_name` 的取值（CHECK） | — |
| `EVAL_RUNS` | qa-tuning | `qa_eval_run` + `qa_eval_fail`（`fails[]` 展开） | `code`；失败 `(run_id, question_id)` |
| `FEEDBACKS` | qa-tuning | `qa_feedback` | `code` |
| `FREQ_QS` | qa-tuning | `qa_freq_question` | `code` |
| `TUNED_SYNONYMS` | qa-tuning | `qa_synonym_link` | `(word, cr_id)` |
| `T_METRICS` | qa-tuning | → 静态 `metric`（只引用；键经 `drawerId` / `QA_METRIC` 映射） | — |
| `CUR_VERSION`、`NEXT_VERSION`、`NEXT_RELEASE_CRS`、`CR_METRIC_TOUCHES` | qa-tuning | → 静态 `ontology_release` / `change_request` / `cr_target`（只核对） | — |
| `PEOPLE_T` | qa-tuning | 不建表：调优页的人（认证人、处理人、评测操作人）都是软引用列，原样存姓名（周晓峰不在 `sys_user` 也不影响） | — |
| `TUNING_DIMS`、`TUNING_TIMES` | qa-tuning | 不建表：T2 直接返回 | — |
| `OBJ_TYPES`、`FIELD_GROUPS`、`KIND_LABEL` | objects-qa | `qa_object_type_view`（39 个函数字段走 `derived/`） | `(kind, release_id)` |
| `OBJ_SCOPE` | objects-qa | 不入库：演示用的可见范围文字，接口由 `auth.row_filter` 的结果生成 | — |
| `OBJ_EXTRA_STORES` | objects-qa | → biz 门店（biz 种子从 `seeds/json/qa/` 读这一份，问数不写） | — |
| `derived/*.json` | objects-qa、qa-tuning | 见上面「函数算出的数据」 | — |

### 9.2 表结构（PostgreSQL；接口值 ↔ 库码见 1.3）

**命名和主键（PDE 定稿）**
- 表名**不加模块前缀**，用单数 snake_case 业务名词；问数专用的表用 `qa_session`、`qa_turn` 这类名字（`qa_` 在这里是业务名词的一部分，不是模块前缀）。表名和列名都避开 SQL 关键字（所以用 `run_at` 不用 `time`、`trigger_label` 不用 `trigger`、`last_result` 不用 `last`、`send_when` 不用 `when`、`row_count` 不用 `rows`、`description` 不用 `desc`、`option_value` 不用 `value`、`event_name` 不用 `event`、`payload` 不用 `data`、`facet_defs` / `column_defs` 不用 `facets` / `columns`）。
- **每张表一个代理主键 `id bigint generated always as identity`**（PDE 定，所有表一律如此）。有业务编号的（`S0001`、`P001`、`CQ-001`、`ER-0023`、`FB-248`）放在 `code varchar(32)`，带唯一约束；**接口只暴露 `code`**（字段名仍叫 `id`，跟 mock 一致），代理 `id` 不出接口。mock 里的组合键一律改成**代理主键 + 业务键唯一约束**，不建复合主键。
- **业务键一律用完整唯一约束**；部分唯一索引 `… WHERE NOT is_seed`（骨架 `unique_except_seed`）只用在「种子数据里确有重复、必须例外」的键上——问数的种子逐表核对过没有这种重复，所以下表全部是完整唯一约束。**表之间的外键一律指向代理主键 `id`**。
- 运行时记录「按哪个本体版本产生」一律存 `release_id bigint`（不再用 `ontology_ver varchar`）。**现状：真外键 `→ ontology_release(id)`，`ON DELETE NO ACTION`**（CONVENTIONS §6；迁移 `qa_0002`，模型用骨架 `OntologyVersionMixin`；`qa_0001` 里原是软引用，骨架把 `test_releases_are_append_only_in_db` 改成 `TRUNCATE … CASCADE` 后补上）：问数是 `qa_turn.release_id`、`qa_eval_run.release_id`、`qa_object_type_view.release_id`，列可空，种子不填（仍为 NULL）；接口连表返回版本号 `ver`。
- 外部表：指标 `metric`（静态）、版本 `ontology_release`（**版本层，本体小白**）、用户表 `sys_user`（admin）、`change_request`、`action_run`（动态）、biz 的 `store` / `product` / `biz_order`。指标存 `metric_id → metric(id)`，版本 `release_id` 是真外键 `→ ontology_release(id)`（`qa_0002`，见上一条）；用户外键**只有当前登录用户列** `*_id → sys_user(id)`：`qa_session.owner_id`、`qa_favorite.owner_id`、`qa_permission_request.requester_id`、`qa_page.owner_id`、`qa_page_share_member.user_id`、`qa_feedback.user_id`（可空）、`qa_query_log.user_id`（可空）（跟 qa-impl `models.py` 一致）；审批人 `approver`、认证人 `certifier`、评测操作人 `operator`、反馈处理人 `handler` 等按 9.2「人员列」存人名软引用 `varchar`，**不是外键**（同第 34 行）；变更单存 `cr_id → change_request(id)`（`qa_cert`、`qa_eval_question`、`qa_feedback`、`qa_synonym_link`）；指向外表的外键都不级联（默认 NO ACTION，跟 CONVENTIONS §6 一致；指向 `ontology_release` 的也是 NO ACTION）；接口返回的 `metric`/`version`/`ver` 连表取，当前用户列的人名连 `sys_user` 取，软引用列原样返回姓名（原第 11 节第 15 条的复合外键问题因此不再存在）。

**公共列**
- **[推断] 待后续补齐**（qa-impl 现状）：① 页面卡片、门店等数据是种子从 mock 原样复制进 qa 自己的表，读取时**还没有按权限 / 行级范围过滤**；② O1 及 T 系列接口**还不按 `?ver` 取数**，`router.py:219` 等处只把 `ctx.ver` 原样回显到 `version`。③ P3 `GET /pages/{pid}`（及 P1 `view=full`）的无权限标记**已实现**：查看人（被共享人，或预览时的被预览人）不是页面拥有者、且 `auth.row_filter(查看人, 'store')` 不为 None 时，整页卡片标 `{denied:true}`、不带数值，显示「无权限」（qa-impl `service.py` `cards_denied_for` / `denied_card`，`router.py` `get_page` 的 EXTENSION POINT）；拥有者看自己的页面照旧全量。骨架 `row_filter` 现在是桩（管理员 None、其他人 DENY_ALL），所以非管理员的被共享人一律看到无权限。**只有按查看人重算卡片值**推迟到 `row_filter` 真正接入后（门店对象 `store` 为 [推断]）。后续补：卡片和门店数据接行过滤，O1/T 系列按请求版本读。
- qa 迁移 `qa_0001` 共建 **22 张**表。
- **可变表**（会改、会删的业务表）：`created_at timestamptz`、`updated_at timestamptz`、`created_by varchar(64)`（操作人 `CurrentUser.id`，即用户编号 `sys_user.code`，跟骨架 `MutableTableMixin` 一致，不是外键）、`is_seed bool default false`、`lock_version int default 1`（接口叫 `lockVersion`，改的时候带上，不一致返回 `VERSION_CONFLICT`）。
- **只追加的表**（日志、按版本的快照、评测失败明细）：`created_at`、`created_by`、`is_seed`，**没有 `lock_version`，也不做软删除**；迁移里挂骨架的 `enable_append_only` 触发器（拒 DELETE、TRUNCATE 和改内容的 UPDATE）。所以**只追加表上不能有 `ON DELETE CASCADE` / `SET NULL` 的外键**（父表删除会触发 DELETE/UPDATE 被拦下）：`qa_eval_fail` 的外键不级联，`qa_query_log.turn_id` 不加外键。`qa_turn_event` 要随会话物理删除（W5），所以归可变表、不挂触发器，服务层只 INSERT。
- **软删除只用在前端有「删除 / 恢复」的地方**：问数前端的删除（会话、页面、常用问题「移出」）都写着「不可恢复」，没有恢复入口，所以**问数的表都不加 `deleted_at`，直接物理删除**（会话删了级联删轮次和事件；已固定的卡片不受影响）。
- **时间一律存 `timestamptz`**，日期存 `date`。接口按 mock 里每个字段原来的格式返回（见 9.3），默认 `YYYY-MM-DD HH:mm`（北京时间）。

| 表 | 类型 | 列（不含公共列） | 主键 / 外键 / 唯一 / 索引 |
|---|---|---|---|
| `qa_session` | 可变 | `id bigint`, `code varchar(32)`, `owner_id`, `title varchar(60)`, `scope varchar(60)`, `caliber varchar(8)`（ex/in，接口值 不含税/含税）, `last_at timestamptz` | PK `id`；UQ `code`；FK `owner_id → sys_user(id)`；IX `(owner_id, last_at desc)`；IX gin `title gin_trgm_ops`（W1 搜索） |
| `qa_session_option` | 可变 | `id bigint`, `kind varchar(16)`（scope/caliber）, `option_value varchar(60)`, `label`, `sort_order int` | PK `id`；UQ `(kind, option_value)` |
| `qa_turn` | 可变（`vote`、`status` 会变） | `id bigint`, `code varchar(32)`, `session_id`, `seq int`, `kind varchar(12)`（answer/denied/empty/system/failed）, `question text`, `refs jsonb`, `sem_plan jsonb`, `snapshot jsonb`（3.3 的完整回答快照）, `metric_id`, `release_id`, `status varchar(12)`（streaming/done/failed）, `vote varchar(4)`（''/up/down）, `caliber_variant varchar(8)`, `asked_at timestamptz`, `elapsed_ms int` | PK `id`；UQ `code`；FK `session_id → qa_session ON DELETE CASCADE`；FK `metric_id → metric(id)`；FK `release_id → ontology_release(id)`（NO ACTION，`qa_0002`）；UQ `(session_id, seq)` |
| `qa_turn_event` | 可变（服务层只 INSERT；不挂只追加触发器，因为删会话要级联删掉） | `id bigint`, `turn_id`, `seq int`（= SSE `id`）, `event_name varchar(16)`, `payload jsonb` | PK `id`；UQ `(turn_id, seq)`（续传按它查）；FK `turn_id → qa_turn ON DELETE CASCADE` |
| `qa_favorite` | 可变 | `id bigint`, `code varchar(32)`, `owner_id`, `question text`, `uses int`, `last_used timestamptz` | PK `id`；UQ `code`；FK `owner_id → sys_user(id)`；UQ `(owner_id, question)`；IX `(owner_id, uses desc)` |
| `qa_permission_request` | 可变 | `id bigint`, `code varchar(32)`（`PR-YYYYMMDD-NNNN`，按当天序号 [推断]）, `requester_id`, `scope`, `object_label`, `reason varchar(200)`, `duration_days int`（30/90/180）, `source_turn_id`, `approver varchar(64)`（人名软引用，非外键）, `status varchar(12)`, `decided_at timestamptz` | PK `id`；UQ `code`；FK `requester_id → sys_user(id)`；FK `source_turn_id → qa_turn ON DELETE SET NULL`；IX `(requester_id, status)`；问数自有（PDE 已定） |
| `qa_page` | 可变 | `id bigint`, `code varchar(32)`, `owner_id`, `name varchar(30)`, `scope`, `source_kind varchar(12)`, `source_ref`, `status varchar(16)`, `status_note`, `refresh jsonb`（RefreshPlan）, `next_run timestamptz`, `last_refresh timestamptz`, `data_as_of timestamptz`, `share_mode varchar(8)`, `share_link bool`, `allow_export bool`, `link_token` | PK `id`；UQ `code`；FK `owner_id → sys_user(id)`；**UQ `(owner_id, name)`**；IX `(owner_id, updated_at desc)`；IX `next_run`（定时刷新） |
| `qa_page_card` | 可变 | `id bigint`, `page_id`, `card_key`, `sort_order int`, `title`, `viz varchar(8)`, `sem_plan jsonb`（**冻结的语义计划**）, `metric_id`（**冻结的指标版本**）, `pending_cr`, `source_turn_id`, `result_cache jsonb`, `perm_fingerprint` | PK `id`；UQ `(page_id, card_key)`；FK `page_id → qa_page ON DELETE CASCADE`；FK `metric_id → metric(id)`；`source_turn_id` 不加外键（删会话不影响卡片） |
| `qa_page_share_member` | 可变 | `id bigint`, `page_id`, `user_id`, `perm varchar(8)`（view/edit） | PK `id`；UQ `(page_id, user_id)`；FK `page_id → qa_page ON DELETE CASCADE`；FK `user_id → sys_user(id)` |
| `qa_page_subscription` | 可变 | `id bigint`, `page_id`, `enabled bool`, `channels varchar[]`, `send_when varchar(16)`, `recipients varchar[]`（人名软引用数组，非外键） | PK `id`；UQ `page_id`；FK `page_id → qa_page ON DELETE CASCADE` |
| `qa_template` | 可变 | `id bigint`, `code varchar(32)`, `name`, `scene`, `description`, `provider varchar(64)`（提供方名称软引用，如「数据平台（官方）」「刘婷」，非外键）, `official bool`, `uses int` | PK `id`；UQ `code`；UQ `name`；IX `scene` |
| `qa_template_card` | 可变 | `id bigint`, `template_id`, `card_key`, `sort_order`, `title`, `viz`, `sem_plan jsonb`, `metric_id` | PK `id`；UQ `(template_id, card_key)`；FK `template_id → qa_template ON DELETE CASCADE` |
| `qa_cert` | 可变 | `id bigint`, `code varchar(32)`, `question varchar(40)`, `sem_plan jsonb`, `metric_id`, `status varchar(12)`, `cr_id`, `certifier varchar(64)`（人名软引用，非外键）, `certified_at timestamptz`, `hits int`, `source varchar(20)`, `note`, `embedding vector` | PK `id`；UQ `code`；**UQ `lower(question)`**；FK `metric_id → metric(id)`；IX `status`；IX ivfflat `embedding` |
| `qa_cert_variant` | 可变 | `id bigint`, `cert_id`, `question varchar(40)` | PK `id`；**UQ `lower(question)`**（同问法全局唯一，也不能跟 `qa_cert.question` 重复，由服务层校验）；FK `cert_id → qa_cert ON DELETE CASCADE` |
| `qa_eval_question` | 可变 | `id bigint`, `code varchar(32)`, `question`, `group_name varchar(8)`, `kind varchar(8)`（`semplan` / `sql`，默认 `semplan`；mock 的 `EVAL_QS` 全是 `semplan`，`backend/seeds/extra/qa/eval/` 的题是 `sql`）, `gold_sql text`（可空，`kind='sql'` 时必填）, `expect jsonb`（SemPlan，`kind='semplan'` 时用）, `last_result varchar(4)`（pass/fail）, `cr_id` | PK `id`；UQ `code`；UQ `question`；CK `kind <> 'sql' OR gold_sql IS NOT NULL`；IX `(kind, group_name)` |
| `qa_eval_run` | 可变（状态会变） | `id bigint`, `code varchar(32)`, `run_at timestamptz`（还没跑的为空）, `time_label varchar(16)`（mock 里 `'发布前自动'`、`'审核通过后'` 这类排队中的文字，`run_at` 为空时返回它）, `trigger_label varchar(40)`, `release_id`, `scope`, `total int`, `passed int`, `status varchar(8)`, `operator varchar(64)`（人名软引用，非外键；含「系统」）, `crs varchar[]`, `note` | PK `id`；UQ `code`；FK `release_id → ontology_release(id)`（NO ACTION，`qa_0002`）；IX `run_at desc nulls first` |
| `qa_eval_fail` | 只追加 | `id bigint`, `run_id`, `question_id`, `actual jsonb`, `kind varchar(24)` | PK `id`；UQ `(run_id, question_id)`；FK `run_id → qa_eval_run`、`question_id → qa_eval_question`（都不级联：评测记录前端没有删除入口）；IX `(run_id, kind)` |
| `qa_feedback` | 可变 | `id bigint`, `code varchar(32)`, `turn_id`, `fed_back_at timestamptz`（接口 `time`）, `user_id`（提交人，可空 [推断]）, `user_name varchar(64)`（提交人姓名快照，接口 `user`）, `dept`, `question`, `answer`, `reason`, `fix_kind varchar(8)`, `fix_text`, `fix_target`, `status varchar(16)`, `cr_id`, `cert_id`, `handler varchar(64)`（人名软引用，非外键）, `resolved_by varchar(32)`（mock `resolvedBy` 是 CR 编号如 `CR-0008`，**不是人**；存编号软引用 [推断]） | PK `id`；UQ `code`；FK `user_id → sys_user(id)`；FK `turn_id → qa_turn ON DELETE SET NULL`；FK `cert_id → qa_cert`；IX `(status, fed_back_at desc)`；IX `fix_text`（同词批量改状态） |
| `qa_freq_question` | 可变（物化统计，定时重算） | `id bigint`, `code varchar(32)`, `question`, `hits7 int`, `hits30 int`, `users int`, `sem_plan jsonb`, `consistency numeric(4,3)`, `last_asked timestamptz`, `cert_id`, `computed_at timestamptz` | PK `id`；UQ `code`；UQ `question`；FK `cert_id → qa_cert ON DELETE SET NULL` |
| `qa_synonym_link` | 可变 | `id bigint`, `word`, `target`, `cr_id` | PK `id`；UQ `(word, cr_id) NULLS NOT DISTINCT`（`cr_id` 为空时也唯一；PG15+，项目用 PG17）；FK `cr_id → change_request(id)`（种子按 `word + cr` upsert） |
| `qa_object_type_view` | 只追加（按版本） | `id bigint`, `kind varchar(16)`, `release_id`, `label`, `unit`, `facet_defs jsonb`, `column_defs jsonb`, `field_groups jsonb`, `kpis jsonb`, `quick_questions jsonb` | PK `id`；UQ `(kind, release_id)`（NULLS NOT DISTINCT）；FK `release_id → ontology_release(id)`（NO ACTION，`qa_0002`）；**跟静态 `object_view` 区分命名**。**读取回退** [推断]：新版本发布时不复制行，只有展示定义变了才追加；按请求版本（不带 `?ver=` 就是当前版本）取每个 `kind` 里 `release_id` ≤ 该版本 id 的最新一行（`ontology_release` 只追加，id 单调递增）；请求版本是回滚生成的，先换成它恢复的那一版（`rollback_of_id` 的 `parent_id`，可递归）再取。新行只由 X4 `release.published` 的处理追加（见第 8 节）；一期只有种子那一版 |
| `qa_query_log` | 只追加 | `id bigint`, `turn_id`, `user_id`, `question`, `norm_question`, `sem_plan jsonb`, `plan_hash`, `metric_id`, `cert_id`, `asked_at timestamptz`, `elapsed_ms`, `row_count int`, `kind varchar(12)`, `embedding vector` | PK `id`；FK `user_id → sys_user(id)`（不级联）；`turn_id` 不加外键（只追加表，删会话后保留日志，`turn_id` 可能指向已删的轮次）；IX `(asked_at)`；IX `(norm_question, asked_at)`；预览和评测不写 |
| 外表引用 | — | 动态 `action_run.qa_session_id` | FK `→ qa_session(id) ON DELETE SET NULL`（列由动态建；外键约束由问数迁移补上，因为问数迁移在后；种子由问数回填，见第 8 节） |

**人员列**（规则同静态 / 动态：只有前端语义是「当前登录用户」的列才是 `sys_user` 外键，种子用 `code_by_name` 映射、必须查得到；定义类的负责人 / 处理人 / 审核人存人名软引用。外键列按 `sys_user` 全表映射（`ADMIN_USERS` 120 人 + `SYS_USERS` 8 人 = 128 人，骨架 `0c35c1d`，无重名）；PEOPLE / mock 里全表查不到的是 周婷、赵磊、周晓峰、刘洋、张伟、钱一鸣、马晨（陈杰 = U010763，查得到）；mock 核对基于 master `/workspace/ui-vue/src/mock` 的 `qa.ts`、`qa-pages.ts`、`qa-tuning.ts`）

| 列 | 方式 | mock 取值 | `sys_user` 查不到的 | 种子怎么处理 |
|---|---|---|---|---|
| `qa_session.owner_id` | 外键 | 无 owner 字段，隐含当前用户王敏 | 无 | 全部 → U010001 |
| `qa_favorite.owner_id` | 外键 | 同上，王敏 | 无 | 全部 → U010001 |
| `qa_permission_request.requester_id` | 外键 | 无种子行（W16 运行时写当前用户） | — | — |
| `qa_page.owner_id` | 外键 | 王敏（`MY_PAGES` 27 张）、苏刚（P901–P903）、陈杰（P904）、周婷（P905） | 周婷（P905） | P904 陈杰照写（U010763）；**P905 → 李娜 U010006** [推断]（`OTHER_PAGES` 是「别人共享给我」的演示，换成查得到的非王敏用户，页面内容不变） |
| `qa_page_share_member.user_id` | 外键 | 27 行，涉及 陈杰、赵磊、刘婷、陈立军、周婷、周晓峰、何静、刘洋、苏刚 | 赵磊、周婷、周晓峰、刘洋，共 11 行（P001 赵磊；P007 赵磊、周婷；P010 周晓峰；P013 周婷；P015 周晓峰、刘洋；P023 周婷；P027 赵磊、周婷；P904 赵磊） | **剔除这 11 行，其余 16 行照写**（含 P001、P902 的陈杰）[推断]；剔除后只有 P007（所在部门）成员为空，P001 留陈杰、P902 留陈杰 |
| `qa_query_log.user_id` | 外键 | 无种子 | — | — |
| `qa_feedback.user_id` / `user_name` | 外键（可空）+ 姓名快照 | `FEEDBACKS.user` 26 个随机姓名 | 23 人（查得到的只有李娜 U010006、何静 U010003、冯雪 U010910） | `user_name` 原样存；`user_id` 只给这 3 行填，其余 23 行为空 [推断]；运行时 W12 踩一律填当前用户 |
| `qa_permission_request.approver` | 软引用 | `QA_APPLY.approver`「刘洋（数据权限管理员）」 | 刘洋 | 原样存 |
| `qa_page_subscription.recipients` | 软引用数组 | 王敏、陈杰、赵磊、刘婷、陈立军、周婷、周晓峰（21 个，5 个 `sys_user` 查不到） | 赵磊、周婷、周晓峰 | 原样存 |
| `qa_template.provider` | 软引用 | 数据平台（官方）、刘婷 | — | 原样存 |
| `qa_cert.certifier` | 软引用 | 刘婷、何静、王敏、孙浩然、周晓峰、陈立军（30 行，4 行周晓峰） | 周晓峰 | 原样存 |
| `qa_eval_run.operator` | 软引用 | 系统（19）、王敏、周晓峰 | 系统、周晓峰 | 原样存 |
| `qa_feedback.handler` | 软引用 | 周晓峰、陈立军、刘婷、何静、孙浩然、王敏（11 行有值，4 行周晓峰） | 周晓峰 | 原样存 |
| `qa_feedback.resolved_by` | CR 编号软引用（不是人） | CR-0008、CR-0015 | — | 原样存 |
| 各表 `created_by` | 公共列，用户编号 `varchar(64)`，非外键 | — | — | 种子按骨架默认 |

注：核对用 `git show 0c35c1d:backend/seeds/json/system/ADMIN_USERS.json` + `backend/seeds/extra/system/SYS_USERS.json` 按 `name` 精确匹配；软引用列不受影响。

### 9.3 时间字段的接口格式

库里一律 `timestamptz`（日期 `date`），接口按 mock 每个字段的原格式输出和接收（北京时间），不另用 ISO 8601。**问数 mock 里没有带秒的时间字段**（逐个导出文件检查过），所以没有要输出秒的字段；默认格式 `YYYY-MM-DD HH:mm` 的不再逐个列出（`certifiedAt`、反馈 `time`、`lastAsked`、页面 `dataAsOf` / `lastRefresh` / `MY_PAGES.updatedAt`、时间线 `time`、`modifiedAt`、各 `*_CUTOFF`），只列格式不同的：

| 字段（mock） | 接口格式 | 库列 | 说明 |
|---|---|---|---|
| — | `YYYY-MM-DD HH:mm:ss`（带秒） | — | 问数没有；以后 mock 加了秒的字段再补进这张表 |
| `QaSession.date`（`QA_SESSION_LIST`） | `YYYY-MM-DD` | `qa_session.last_at` | 只取日期 |
| `QA_DEMO_TURNS[].time` | `HH:mm` | `qa_turn.asked_at` | 只显示时分；没有 `time` 的轮次不返回 |
| `QaFavorite.lastUsed` | `MM-DD` | `qa_favorite.last_used` | |
| `QaPage.updatedAt`（`QA_PAGE_LIST`，左栏简版） | `MM-DD` | `qa_page.updated_at` | 完整版 `MY_PAGES.updatedAt` 是默认格式 |
| `QaTemplate.updatedAt` | `YYYY-MM-DD` | `qa_template.updated_at` | |
| `RefreshPlan.time` | `HH:mm`，或 `'整点'`、`'--'` | `qa_page.refresh` jsonb 里原样存 | 不是时间戳 |
| `EVAL_RUNS[].time` | 默认格式，或 `'发布前自动'` / `'审核通过后'` | `run_at` / `time_label` | `run_at` 为空时返回 `time_label` |
| `REGION_EC.since`、卡片 `labels[]` | `MM-DD` | 动态 / 卡片快照 | 不是问数的列，原样透传 |
| `OBJ_EXTRA_STORES[].openedAt`、`QA_TODAY` | `YYYY-MM-DD` | biz `date` 列 / `now()` 的日期 | |

## 10. 评审用：按钮 → 接口 → 状态变化

| 页面 | 按钮 / 交互 | 接口 | 状态变化 |
|---|---|---|---|
| 工作台 | 新建会话 | W2 | 左栏顶部多一条「新会话」，对话区显示空状态 |
| 工作台 | 左栏点会话 | W3 | 切换当前会话，对话区换成该会话全部轮次（一次返回） |
| 工作台 | 搜索会话 / 时间筛选 / 翻页 | W1 | 回到第 1 页（翻页除外） |
| 工作台 | 会话 ⋯ → 重命名 | W4 `{title}` | 标题更新 |
| 工作台 | 会话 ⋯ → 删除（二次确认） | W5 | 会话消失，焦点移到第一条；卡片保留 |
| 工作台 | 发送 / Enter / 追问词 | W6 + W7 | 追加一轮；第一问把会话标题设成问题的前 20 个字 |
| 工作台 | 常用问题抽屉「提问」 | 无（前端只关闭抽屉） | — |
| 工作台 | 左栏贴底常用问题 / 我的页面条目 | 无（没有点击处理） | — |
| 工作台 | 修改范围或口径 | W4 `{scope,caliber}` | 插入一条系统提示，下一问开始生效 |
| 工作台 | 澄清条：不含税 / 含税 / 两者对比 | W9 | 结论和指标名切换，明细仍按不含税 |
| 工作台 | 依据 ▾ | 无（basis 已经在 SSE 里） | 展开依据表 |
| 工作台 | 查看原因 | 无（insight） | 弹层 |
| 工作台 | 表格 / 图表切换 | 无 | — |
| 工作台 | 结果表翻页 | W10 | `?tblPage=`，合计行不变 |
| 工作台 | 导出 | W11 | 下载文件，写审计 |
| 工作台 | 赞 / 踩 / 再点一次取消 | W12 | 踩：调优多一条「待处理」反馈 |
| 工作台 | 复制 | 无 | — |
| 工作台 | 固定到页面（已有页面 / 新建页面） | P1 brief + P7；新建页面时 P2 → P7 | 页面卡片数 +1 |
| 工作台 | 建议动作 / 右栏「发起」 | X1 stage → 确认卡（显示幂等键）→ X2 confirm | 待确认 →（提交审批）待审批 /（确认执行）执行中；提交条件不满足返回 `SUBMISSION_CRITERIA_FAILED`，「发起」置灰带原因 |
| 工作台 / 对象详情 | 确认卡「取消」 | X2b withdraw [推断] | 已撤回；这一行恢复成「发起」 |
| 工作台 | 右栏页签「摘要 / 指标 / 关联」 | 无（本地切换） | — |
| 工作台 | 右栏时间线「全部」、「我的页面 · 全部」抽屉里的「打开」 | 无（没有点击处理）[第二期] | — |
| 工作台 | 申请权限 → 选时长 → 提交申请 | W16（`'90 天'` → `90`） | 这一轮显示「已申请 · 待审批」；理由为空时前端不提交 |
| 工作台 | 申请权限抽屉「取消」 | 无 | — |
| 工作台 | 改查 10 月 | 无（把 `empty.rewrite` 填进输入框） | 输入框有内容 |
| 工作台 | 分享 → 复制链接 | 无（打开链接时用 W3，按打开人的权限） | — |
| 工作台 | 把会话整理成页面 | 跳转 → P2 | 我的页面打开新建气泡，名称已填好 |
| 工作台 | 常用问题「管理」→ 移出 | W13 / W15 | 从列表消失 |
| 工作台 | 打开对象详情 | O5 `view=drawer`（抽屉）；完整视图 O5 `view=full` | `?drawer=region:REG-EC` |
| 工作台 | 引用对象、⋯ | — | **[第二期]**，置灰 |
| 我的页面 | 新建页面 | P2 | 列表顶部多一行 |
| 我的页面 | 筛选 / 搜索 / 翻页 | P1 | — |
| 我的页面 | 行点击 → 抽屉 | P3 | `?page_id=` |
| 我的页面 | 刷新 / 立即刷新 | P6 | 数据截至时间更新；刷新失败 → 正常或口径待更新 |
| 我的页面 | 重命名 | P4 | 名称更新；重名给 warning |
| 我的页面 | 删除（确认卡，红色） | P5 | 行消失，抽屉关闭 |
| 我的页面 | 调整卡片 → 拖拽 / 移除 → 保存顺序 | P8 | 有移除时先弹确认卡 |
| 我的页面 | 刷新与订阅 → 保存 / 还原 | P9 / 无 | 下次刷新时间更新 |
| 我的页面 | 共享 → 添加成员 / 改权限 / 移除 / 链接 / 导出 → 保存共享设置 | P10 | 有新增可见人、新开链接或新开导出时先弹确认卡；仅自己时链接置灰 |
| 我的页面 | 共享 → 复制链接 | 无（`linkUrl`） | — |
| 我的页面 | 模板库：筛选 / 预览 | P11 / P12 | `?tpl=` |
| 我的页面 | 用此模板新建（名称、口径、刷新文字） | P2 `{template}`（刷新文字按第 4 节表转 `RefreshPlan`） | 新页面出现，抽屉打开；有 pending 卡片时状态口径待更新 |
| 我的页面 | 从工作台跳来（`?newName=`） | 无（打开新建气泡，名称已填）→ P2 | — |
| 对象搜索 | 换类型 / 搜索 / 勾分面 / 清除 / 翻页 | O1 / O2 | 换类型时清掉筛选；分面计数刷新 |
| 对象搜索 | 导出 / 导出所选 | O3 | 下载 CSV |
| 对象搜索 | 用这批对象提问 → 建议问题 / 发送 | O4 | 浮层里出现回答，`?ask=1`；历史版本下浮层只读，预览下允许 |
| 对象搜索 | 行点击 / 完整视图 | O5 | 打开抽屉 / 跳到详情 |
| 对象详情 | 页签切换、关联分组翻页、时间线翻页 | O5–O7 | — |
| 对象详情 | 动作记录翻页 / 点一行 | X7 `GET /api/ontology/v1/action-runs` / `…/{id}` | 详情带 `action_run_log` 时间线 |
| 对象详情 | 可执行动作「发起」 | O9 + X1 + X2 | 待确认 → 待审批 / 执行中 |
| 对象详情 | 快捷问题 / 自己输入问题 | O4 `single` | 浮层里出现回答 |
| 问数调优 | 新建认证问答 → 认证 | T2 + T5 | 多一条「待认证」，打开它的抽屉 |
| 问数调优 | 运行评测（选范围 → 确认卡） | T11 | 运行记录多一条「运行中」，跳到 eval 页签 |
| 问数调优 | 认证 / 重新认证 | T6 | 已认证；如果 CR 还没发布，仍是待重新认证，并写说明 |
| 问数调优 | 停用 / 启用（确认卡） | T7 | 非已停用 → 已停用；已停用 → 已认证（照前端） |
| 问数调优 | 抽屉：添加同问法 / 删除同问法 | T8 | 同问法标签增减 |
| 问数调优 | 运行记录行点击 → 失败用例翻页 | T10 | `?run=` |
| 问数调优 | 题目视图筛选 | T12 | — |
| 问数调优 | 转为同义词（发起变更弹窗：新建 / 加入已有） | T15（+X3） | 同一个词的待处理反馈一起变成已转同义词，或者待发布生效 |
| 问数调优 | 转为认证问答 | T16 | 已转认证问答；生成新的认证问答或同问法 |
| 问数调优 | 忽略 / 重新打开 | T17 | 已忽略 ⇄ 待处理 |
| 问数调优 | 认证问答筛选 / 搜索 / 翻页 | T3 | `?cst=&cm=&ckw=&cpage=` |
| 问数调优 | 行点击 → 抽屉 | T4 | — |
| 问数调优 | 运行记录按触发方式筛选 | T9 | `?etr=` |
| 问数调优 | 反馈筛选 / 行点击 → 抽屉 | T13 / T14 | `?fst=&fk=&fkw=` |
| 问数调优 | 页签计数、版本、数据截至 | T1 | 切换版本时重取 |
| 问数调优 | 高频：近 7 天 / 近 30 天、筛选 | T18 | 排名重算（按 `now()`） |
| 问数调优 | 设为认证问答 | T19 | 「认证」列变成链接，点了跳到 cert 页签 |

---

## 11. 待定问题（只列不答）

1. **大模型部署（外部 API 还是本地 GPU）**：决定 `text` 是逐字流式还是模板拼接；`status` 各阶段的超时；外部模型时**只能发 schema 和元数据，不能发业务行数据**；pgvector 的向量维度和嵌入模型；评测一次「预计 3 分钟」能不能做到；并发上限。
2. **信创 / 元数据库**：元数据库如果用达梦或金仓，pgvector 和 procrastinate（只依赖 PG）都要换掉；国产中间件（如 TongWeb）前面的反向代理要关掉 SSE 缓冲；如果要求 Java 技术栈，FastAPI 方案要重新评估。
3. **是否对外分发**：分发涉及组件许可证；SSO 和多租户；`sql` 事件给不给客户的管理员看；离线安装包里要不要带种子数据。
4. **达梦 / 金仓测试库**：sqlglot 没有原生的达梦方言（只能走 Oracle 兼容模式），金仓要看是 PG 模式还是 Oracle 模式；行级谓词注入和导出要在两种库上各测一遍；`biz_demo` 种子要能灌进两种库。
5. 分享会话、共享页面、订阅都按**查看人**的权限重新执行，查询量会成倍增加，缓存策略要定。
6. **[待用户定]** 预览身份下能不能提问（1.1 提到的前端不一致）。建议：允许，按被预览人的权限回答，不落会话、不进高频统计和 `qa_query_log`。
7. **[待用户定]** 反馈在「待发布生效」之后的终态：CR 发布后变成什么（前端没有定义）。
8. 踩的时候要不要让用户填说明（现在没有输入框，`reason` 和 `fix` 只能由后台生成）[推断]。
9. 常用问题是用户手动加入还是按使用次数自动生成。
10. ~~权限申请存哪~~ **已定**：存问数自有的 `qa_permission_request`，本条关闭。
11. 企业微信订阅推送接哪个通道（技术栈调研里只有飞书和钉钉）。
12. ~~X1/X2 的路径~~ **已定**：动作路径统一用冒号形式（`action-runs:stage`、`/{id}:confirm`、`:withdraw` 等，见第 1 节），本条关闭。
13. T7 启用时恢复成什么状态（见第 12 节）。
14. `nav.ts` 的问数调优备注写「高频问题推荐第二期」，但页面上高频页签已经能用；本文按页面做（T18、T19 第一期）。
15. ~~指标外键~~ **已定**：按代理主键规则存 `metric_id → metric(id)`，接口连表返回 `metric@version`，本条关闭。
16. ~~用户表表名~~ **已定**：`sys_user`，本条关闭。
17. ~~门店别名表~~ **已定：用户——不加门店别名表，R-06 0 行是预期（测试数据）**。订单.门店范围值是简称（`roles.ts:26`），种子门店是全称，按名原样比匹配 0 行，R-06 门店店长（`roles.ts:69`）看不到任何订单（叠加别的角色时按 §1.5 跨角色 OR，见 2.2）。同时用户已定：元数据库**先用 PostgreSQL**（达梦 / 金仓见 #2、#4，后议）；大模型**走外部 API**（百炼 `qwen-flash`，密钥只从环境变量 `DASHSCOPE_API_KEY` 读，见 #1），本条关闭。

---

## 12. [推断] 清单（前端没有、文档补上的规则，给用户确认）

| # | 位置 | 文档的规则 | 前端现状 |
|---|---|---|---|
| 1 | W4 重命名、W5 删除 | 历史版本和预览下拒 | 会话 ⋯ 菜单不判断只读 |
| 2 | W11 导出 | 预览下拒（`EXPORT_FORBIDDEN`） | 导出按钮不判断预览 |
| 3 | W15 移出常用 | 历史版本和预览下拒 | 「移出」不判断只读 |
| 4 | W16 申请权限 | **是否在只读模式下收紧**：现在 `?ver=` 和 `?previewAs=` 下都放行（只读模式下允许的写操作白名单，跟 api-index §8.1-9 一致）。要不要改成只读下拒（前端同步置灰）；保留预览下放行的话，申请人记被预览人还是管理员（建议记管理员本人、审计里带被预览人） | `QaConversation.vue:55` 的「申请权限」按钮和 `QaApplyDrawer.vue:34` 的「提交申请」都不判断只读或预览；`Workbench.vue:122,129` 打开抽屉、提交也不拦 |
| 5 | W12 取消踩 | 撤掉还没处理的那条反馈 | 前端只改赞踩状态 |
| 6 | W14 | `uses`/`lastUsed` 按提问命中累加 | 前端没有加入常用的入口（[第二期]） |
| 7 | 分享会话 | 打开人按自己的权限重算；拥有者用快照 | 前端只复制链接 |
| 8 | 读接口的 `?ver=` | 只影响本体元数据，业务数据和快照不随版本变 | 前端没有区分 |
| 9 | P2、T5 | 新建时查重名（`DUPLICATE_KEY`） | 前端只有重命名、同问法查重 |
| 10 | P2 新建空白页面 | `refresh` 默认每天 08:00 | 前端空白页没有刷新设置 |
| 11 | P4–P10 | 只有拥有者或「可编辑」成员能改，删除和共享只限拥有者 | 前端只有拥有者的视图 |
| 12 | P10 | 响应带 `newViewers`、`newGrants` 供核对 | 确认卡由前端算 |
| 13 | T7 启用 | 原来是待认证或指标有未发布 CR 时，应恢复成原状态（第一期先照前端：一律已认证） | `toggle` 一律设成已认证 |
| 14 | T11 | 评测用评测身份，不注入行级条件 | — |
| 15 | X2b | 确认卡取消时 `:withdraw`，`action_run` 变已撤回 | 前端取消只是关卡片 |
| 16 | 1.3 | `withdrawn`/`rejected`/`reconciled` 按动态的中文名返回；回答 `failed` | mock 的 `ActRecord.result` 和 `QA_DEMO_TURNS.kind` 里没有 |
| 17 | 1.4 | 每个接口的页面权限级别、API 密钥范围、行级过滤 | 前端只按页面权限控制导航 |
| 18 | 高频统计 | 每天从 `qa_query_log` 聚合、pgvector 聚类、不统计预览和评测 | 只有 mock 结果 |
| 19 | 范围选项 | 从用户的行级权限推出来 | mock 写死 3 个 |
| 20 | 踩的说明 | `reason`/`fix` 由后台生成 | 没有输入框（第 11 节第 8 条） |
| 21 | O5 对象详情 `{id}` | 可以接受门店编码（code）代替 id，后台先把 code 归一成 id 再查，响应里统一返回 id **（未实现，用户定）** | `ObjectDetail.vue:152-153` 的提问范围直接用 URL 里的 `id`；`stores.ts:52` `storeById` 同时按 `s.id === id \|\| s.code === id` 匹配，所以前端用编码也能打开门店 |
| 22 | 对象详情的动作记录（X7 动态 `action-runs?objKind=&objId=`）、O9 可执行动作 | 只对 `kind = store / product / order / region` 有意义；其他 kind 返回空列表或 `VALIDATION_FAILED` **（未实现，用户定）** | `objects-qa.ts` `actionRecords` 对未知 kind 走 `NAMES[kind] ?? NAMES.store` 回落成门店的动作；`objActions` 也只写了这 4 类 |
| 23 | 所有可变表的写接口 | 带 `lockVersion`，不一致返回 `VERSION_CONFLICT` 409 | 前端没有并发冲突提示，也不传版本号 |
| 24 | `PAGE_SCOPES`、范围选项 | 不建表，从用户的行级范围推出 | mock 写死 6 个 / 3 个 |
| 25 | 删除会话、页面、常用问题 | 物理删除，不做软删除 | 前端写着「不可恢复」，没有恢复入口 |
| 26 | 行级范围 | 订单一跳到门店、订单行经订单继承（两跳），未声明的关系拒绝（`SQL_REJECTED`）；商品.品类范围按 `CATEGORY_SCOPE_ALIAS` 换一级类目 [推断]（`c646bd5` 已实现），订单行不按品类过滤、只继承订单（静态裁定，已定）；订单.门店范围按门店名原样比（`c646bd5` 已实现），mock 简称对不上种子全称，R-06 门店店长看不到任何订单，**已定（用户）：不加门店别名表，0 行是预期**（11 节 #17）；多条范围：只算有查看权限的角色、`defaultDeny` 没配范围的不给看，任一不限看全部，否则所有范围取并集（跨角色 OR，同角色内也 OR [推断]，`roles.ts:114`）（`c646bd5` `effective_expr` 已实现）；商品：`defaultDeny` 角色没配品类范围时不可见（`c646bd5` 已实现）；`auth.row_filter` 仍是桩、未接 `effective_expr` [待骨架实现]；会员范围一期拒绝（`static-api.md` §1.5 L435，该格 [推断]） | 前端没有这类提示 |
| 27 | `qa_permission_request.code` | 编号 `PR-YYYYMMDD-NNNN`，按当天序号 | 前端只显示「已提交」，没有编号 |
| 28 | `qa_object_type_view` | 新版本不复制行，按「`release_id` ≤ 请求版本的最新一行」回退，回滚版本先换成它恢复的那一版 | 前端 `OBJ_TYPES` 不分版本 |
| 29 | `SQL_REJECTED` | 网关拒绝生成的 SQL 时按回答失败显示 | 前端没有这种情况 |
| 30 | `qa_eval_question.kind` / `gold_sql` | 评测题分 `semplan` / `sql` 两种，SQL 题带标准 SQL（PDE 定） | 前端 `EvalQ` 没有这两个字段，T12 不返回 |
| 31 | X4 `release.published` 的处理 | 只有对象类型 / 属性 / 指标的展示定义变了才给 `qa_object_type_view` 追加新行；回滚（`kind="rollback"`）不写行（事件由版本层经 `outbox_event` 发，已定） | 前端没有 |
| 32 | 人员列（9.2「人员列」） | 外键列按 `sys_user` 全表映射，只处理全表查不到的人：`OTHER_PAGES` P905 拥有者周婷 → 李娜（P904 陈杰照写）；共享成员里赵磊、周婷、周晓峰、刘洋共 11 行剔除，P007 成员变空 | mock 里是别的姓名 |
| 33 | `qa_feedback.user_id` | 提交人外键可空，另存 `user_name` 快照；种子只有李娜、何静、冯雪 3 行有 `user_id` | mock 只有姓名 |
| 34 | `qa_feedback.resolved_by` | 存 CR 编号（mock 就是 `CR-xxxx`），不是人；跟 `cr_id` 的关系待确认 | mock 字段名像人 |
