# 本体智能平台 · 后台接口需求（按前端页面整理）v0.2

> 读者：本体小白（后台：Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2 + PostgreSQL 17/pgvector + sqlglot，见 `/workspace/ontology-xiaobai/Python技术栈调研.md`）
> 来源：前端 `/workspace/ui-vue` master `5fb26ab`（只读整理，没有改代码和 mock）。字段名、类型、枚举全部取自 `src/mock/*.ts` 的 TS 类型和页面用法，每个接口都标了 mock 文件。
> 作者：PDE工程师 · 2026-10-25（UTC+8）
>
> **分工说明**
> - **静态本体页**（对象类型的定义部分、关系类型、指标口径、维度与字典、业务知识、变更与发布）的业务规则、表结构、变更单状态机、校验项 V1–V17、发布事务，**以静态本体负责人的 `/workspace/ui-vue/docs/backend/static-api.md`（v0.2）为准**。本文对这些页只补“读接口和字段清单”（static-api.md §3 写明“字段清单以 PDE 汇总为准”），并在 §5 列出两份文档和前端代码之间的差异。
> - **动态本体页**（告警处理、动作中心、规则与自动化、情景推演、对象类型的生命周期、动作、自动化页签）**以动态本体负责人的 dynamic-api.md 为准**（branch `dynamic` commit `cc3f949`：`git -C /workspace/ui-vue show cc3f949:docs/backend/dynamic-api.md`）。接口路径（`/action-runs`、`:start-action`、`:batch-close` 等）、错误码（`NOT_ASSIGNEE`、`SCENARIO_*` 等）本文都跟它一致。各页的“差异”小节列出它和前端代码不一致的地方。
> - **动作类型、事件类型**以 Palantir专家的 `docs/backend/action-event-api.md` 为准（master `736fe56`）；**问数五页**（工作台、我的页面、对象搜索、对象详情、问数调优）以问数负责人的 `docs/backend/qa-api.md` 为准（master `1782512`）。§2.2–2.5、§3.7、§3.12、§3.14 只列接口表和种子来源，并标出差异。
> - **已定的约定**（PDE 决定）：
>   - 前缀统一为 `/api/<模块>/v1`；
>   - 种子数据从 `npm run export-mock` 导出的 `backend/seeds/json/<模块>/` 读取；
>   - 定时和异步任务用 procrastinate；
>   - 分页跟随前端：页面上有“‹ 1/N ›”或页码的列表，一律用 `page/size` 并返回 `total`；只有已经在用 `useCursorQuery` 的界面（审计日志）才用游标。
>   - 四份负责人文档里和这些决定冲突的写法，按决定处理，冲突汇总在 §5。
> - **种子数据**：用户要求把前端现有的 mock 数据导入数据库。每个接口表都有“种子数据”一列，写明由 `src/mock` 下哪个文件、哪个导出提供；整体方案见 §6。
> - 标 **【待确认】** 的地方是推测，或者前端没有体现。前端没有的功能不写。

---

## 0. 怎么读这份文档

- 共 28 个路由：首页 + `src/nav.ts` 里的 27 个二级页面（`src/router/index.ts` 由 nav 生成）。另外有 404 页，不需要接口。
- 每页分四块：① 用途和路由 ② 操作流程 ③ 接口表 ④ 后台要给的状态。
- 接口表的列：**方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock）**。路径省略了模块前缀 `/api/<模块>/v1`，每页属于哪个模块见 §1.1 的模块表（标题里写了前缀的，以标题为准）。
- “返回”一列写的是 TS 类型名和 mock 文件，例如 `Datasource（mock/datasources.ts）`。后台的 Pydantic 模型按这些类型定义，前端换接口时只需要把 mock 的实现换掉（`mock/paginate.ts` 的注释已经这样约定）。
- §5 是四份负责人文档和前端代码之间的差异汇总，§6 是种子数据导入方案，§7 是去重后的接口总表（由各页接口表脚本汇总，带完整前缀），§8 是待确认问题汇总。

---

## 1. 公共约定

### 1.1 基础

| 项 | 约定 |
|---|---|
| 前缀 | `/api/<模块>/v1`，JSON，UTF-8。模块划分见下表 |
| 字段命名 | 和前端 TS 一致，用 **camelCase**（如 `lastTest`、`idemKey`）。Pydantic 可以用 `alias_generator=to_camel` + `populate_by_name=True`，数据库里仍然用 snake_case。**冲突**：qa-api.md、dynamic-api.md 用 camelCase；static-api.md、action-event-api.md 用 snake_case。【待确认：全站统一一种，建议 camelCase】 |
| 枚举 | 前端直接用中文枚举值（如 `'待确认' \| '审批中'`、`'已发布' \| '草稿'`）。qa-api.md §1.3 和 dynamic-api.md 用英文 code，并带一个 `label` 字段放中文；action-event-api.md 直接返回中文。**建议统一为英文 code + `label`**，前端改成按 code 判断。【待确认】 |
| 时间 | **ISO 8601，带 `+08:00`**（例如 `2026-10-24T23:00:00+08:00`），和 qa-api.md、dynamic-api.md 一致。mock 里的 `YYYY-MM-DD HH:mm` 在导入种子时按 Asia/Shanghai 转换。前端显示时要格式化回原样 |
| 金额和数字 | 返回原始数值（number），千分位、两位小数、`--` 空值由前端 `mock/format.ts` 处理。空值返回 `null` |
| 租户 | 顶栏可以切换租户（`stores/app.ts` 的 `TENANTS`：hd/hn/hq）。建议用请求头 `X-Tenant-Id` 传，所有数据按租户隔离【待确认】 |
| 数据截至 | 很多页面的页头显示“数据截至 2026-10-24 23:00”。相关接口在响应里带 `dataAsOf`（或者模块级字段，如 `cutoff`） |

**模块划分**（前缀 `/api/<模块>/v1`）

| 模块 | 页面 | 负责人文档 |
|---|---|---|
| `ontology` | 本体总览、对象类型、关系类型、动作类型、函数、指标、维度、业务知识、事件类型、规则、变更与发布，以及告警、动作中心、情景推演、生命周期；顶栏的版本列表 | static-api.md、dynamic-api.md、action-event-api.md（三份都用 `/api/ontology/v1`） |
| `qa` | 问数工作台、我的页面、对象搜索、对象详情、问数调优 | qa-api.md |
| `data` | 数据连接、外部系统、数据质量 | 本文【待确认模块名】 |
| `admin` | 用户与组织、角色与权限、审计日志、API 密钥、系统设置、安全策略，以及 `/me`、租户、通知、预览会话、首页看板 | 本文【待确认模块名；首页看板也可能单独成 `biz` 模块】 |

注意：`/ext-systems` 在 §3.2（data 模块）。动作类型编辑写回配置时也要调它，跨模块调用时写完整路径 `/api/data/v1/ext-systems`。

### 1.2 错误格式

```json
{ "code": "READONLY_HISTORY", "message": "正在查看历史版本 v2026.10.2，只读", "details": { }, "traceId": "..." }
```

- `message` 要能直接显示给用户。前端会把它放进确认卡的“阻断原因”或者按钮悬停提示里，所以要写成完整的中文句子，比如“该动作由规则 R-027 暂存，指定确认人为 吴佳，只有确认人可以确认提交或撤回”。
- HTTP 状态码：400 参数错误，401 未登录，403 无权限或只读，404 不存在，409 状态冲突或被阻断，422 校验失败。
- 公共错误码：和 static-api.md §4 统一，在那份的基础上补充。

| code | HTTP | 场景 |
|---|---|---|
| `VALIDATION_FAILED` | 422 | 表单校验，`details.fields[{field,message}]` |
| `NOT_FOUND` | 404 | |
| `NO_PERMISSION` | 403 | 功能级、对象级、行级都没有权限 |
| `READONLY_HISTORY` | 403 | 带历史版本标记的写请求（§1.7） |
| `READONLY_PREVIEW` | 403 | 预览身份下的写请求（§1.8） |
| `ACTION_BLOCKED` | 409 | 通用前置条件不满足，`details.reasons[]` 对应确认卡的 `blocked` |
| `DUPLICATE_KEY` / `DUPLICATE_NAME` | 409 | 重名，比如“角色名已存在”“已有同名页面”“已有相同问法” |
| `NOT_CONFIRMER` | 403 | 规则暂存的动作，不是指定确认人 |
| `NOT_OWNER` | 403 | 别人的方案、别人的告警（只有本人处理中的告警才能关闭或发起动作） |
| `STATE_INVALID` | 409 | 状态机不允许，比如对“已撤回”的动作发起对账 |
| static-api.md 里的码 | — | `NOT_IN_DRAFT`、`CR_LOCKED`、`CR_STATE_INVALID`、`NOT_REVIEWER`、`SELF_REVIEW`、`NOT_ONTOLOGY_ADMIN`、`CHECK_BLOCKED`、`MERGE_BLOCKED`、`TARGET_IN_OTHER_CR`、`VERSION_CONFLICT`、`ROLLBACK_NOT_LATEST` |

### 1.3 分页

前端有两种分页，见 `src/composables/usePagedQuery.ts` 和 `src/mock/paginate.ts`。

| 类型 | 请求 | 响应 | 使用的页面 |
|---|---|---|---|
| 页码分页（full / simple 两种外观，接口相同） | `page`（从 1 开始）、`size` | `PageResult<T> = { items: T[]; total: number }`（可以多返回 `page`、`size`，和 static-api.md 一致） | 除下面两个以外的所有列表 |
| 游标分页 | `cursor`（不透明字符串，首页不传）、`size` | `CursorResult<T> = { items: T[]; nextCursor: string \| null }`，**不返回总数** | **只有审计日志**（全前端只有 `src/pages/admin/AuditLog.vue` 用 `useCursorQuery`） |

- **决定**：分页方式跟随前端。页面上有“‹ 1/N ›”或页码的列表，一律用 `page/size` 并返回 `{items,total,page,size}`。其他文档里写成游标的，按这个改：action-event-api.md 的近期事件（E6）和投递记录、qa-api.md W3 的轮次。
- 参数名统一用 `size`。qa-api.md 用的是 `pageSize`，需要改（§5）。

- `size` 由前端按容器高度决定（每页的默认值写在各页里，比如首页 15、告警 12、动作中心 16、用户 17、API 密钥 18、审计 20）。后台只限制上限，建议 200。
- 筛选条件变化时前端自动回到第 1 页，后台不用管。
- 有些列表前端现在是一次取全量、在本地切片（例如数据连接左栏、函数左栏、变更单左栏每页 17）。接后台时一律改成服务端分页，接口仍然是 `page/size`。
- 同一页有多个列表时，前端用前缀区分 URL 参数（如 `?upage=`、`?mpage=`、`?rpage=`），后台接口一律只认 `page/size`。

### 1.4 身份与 API 密钥

- **界面登录**：前端没有登录页。演示身份用 `?as=admin|viewer` 切换（`stores/app.ts` 的 `PERSONAS`）。后台需要一个 `GET /me`，返回当前用户、能进入的区（`use|build|admin`）、各页面的功能级别（`FuncLevel = 'none'|'view'|'manage'`）。登录方式（SSO/账号密码/MFA）【待确认】；安全策略页有密码规则、锁定、会话时长、MFA 的配置（§4.5），登录实现要读这些配置。
- **API 密钥**（外部系统调用，见“API 密钥”页）：`Authorization: Bearer <secret>`。数据库只存哈希，另外存 `prefix` 和 `last4` 用于展示。每个密钥必须绑定身份：`角色` 或 `服务账号 + 数据范围`（`KeyBinding`，mock/apikeys.ts）。按 `scopes`（`KEY_SCOPES`：问数查询、对象读取、指标读取、动作执行、数据导出、元数据读取、Webhook 回调）控制能调用哪些接口，并检查 `ipAllow` 和 `expiresAt`。每次调用记一条 `KeyCall`（时间、接口、IP、状态码、耗时）。
- **权限模型**（mock/roles.ts，角色页维护）：
  - 功能权限：`Role.func[pageKey]`，取值 none/view/manage。view 只能读，写接口要求 manage。
  - 对象权限：`ObjPerm{objType, view, export, actions[]}`，控制能不能看、能不能导出、能执行哪些动作。
  - 行级范围：`RowScope{objType, prop, op:'='|'∈', values[]}`。只能基于对象类型里标为“权限依据”的属性（`SCOPE_PROPS`）。查询时注入谓词（调研文档 §3.7：pycasbin 管功能权限，行级谓词自己生成）。
  - `defaultDeny`：业务查看类角色，没有配置行级范围的对象类型**一行都不可见**。对应“未配置范围”状态。
  - 指标先按基础对象的范围过滤，再叠加维度对象的范围（Roles.vue 注释）。
  - 列级：敏感属性（`ObjProp.sensitive`）默认脱敏，个别角色可以看明文（PermissionsTab）。
  - 多个角色合并为“有效权限” `EffectiveAccess{rs, func, objs, areasIn}`。

### 1.5 审计

- 所有写接口、开始/结束预览、密钥吊销、配置保存都写一条 `AuditRow{id,time,actor,action,target,ip,result:'成功'|'失败'|'拒绝'}`（mock/admin.ts）。
- 只读模式下被拒绝的写请求也要记一条，结果为“拒绝”【待确认，和 static-api.md 的推断一致】。
- 用 API 密钥调用时，actor 写成 `API 密钥 xxx（角色 / 服务账号 xxx）`（mock/apikeys.ts `keyActor`）。

### 1.6 确认卡与阻断

- 前端所有写操作都先弹一张公共确认卡（`stores/confirm.ts` 的 `ConfirmRequest`：title、target、fields、impact、irreversible、blocked、picker）。
- 确认卡只是前端的交互，**后台必须重新校验前置条件**，不满足时返回 409 `ACTION_BLOCKED`（或者具体的码），`message` 就是阻断原因。
- 确认卡上显示的数字（影响人数、预算、改动数、校验摘要、影响分析等）应该由后台给出。需要的地方另设 `:precheck` 或 `GET .../impact` 接口，各页单独写明。
- `irreversible: true` 的操作都是写外部系统（动作提交、重新提交）。请求要带幂等键（请求头 `Idempotency-Key`，或者 body 里的 `idemKey`），重复提交时返回同一条记录。

### 1.7 历史版本 `?ver=`（只读）

- 顶栏的版本选择器列出 `RELEASES`（mock/releases.ts）。选了旧版本以后，URL 带 `?ver=v2026.10.2`，**整站只读**：`useReadonly().readonly = true`，所有写按钮置灰，悬停显示“正在查看历史版本 xxx，只读”。切换页面时会一直带着 `?ver=`。
- 后台：
  - 本体类资源（对象、关系、指标、维度、知识、动作类型、事件类型、规则、生命周期）的读接口接受 `release=<ver>`（和 static-api.md 统一用 `release` 这个名字），返回该版本的快照。
  - 前端在历史版本模式下，所有请求带请求头 `X-View-Release: <ver>`。只要请求带这个头，后台的**写接口一律返回 403 `READONLY_HISTORY`**，包括系统管理、数据连接这类和版本无关的页面，因为前端在这些页面也是整页只读。
  - 历史版本里的问数：口径框显示“按该版本默认”。已经回答过的消息保留回答当时的依据（本体版本），不会改写。
  - 本体总览在历史版本下不显示当前的待处理事项，最近发布只列到所选版本为止。

### 1.8 以此用户预览 `?previewAs=`（只读）

- 入口：角色与权限 → 成员 → 以此用户预览。URL `?previewAs=U010231` 直接进入，`?previewAs=off` 退出（`stores/preview.ts`）。按现在的设计，进入后会跳到该用户第一个有权限的页面（通常是问数工作台）。
- 预览期间：
  - 顶部横幅显示“正在以 XXX 的权限预览 · 退出预览”。
  - 左侧导航只显示该用户有权限的页面。
  - 读数据按**被预览用户**的功能权限和行级范围过滤；对象搜索、问数会显示“数据范围：订单 大区 = 华东”。
  - 所有写操作被阻断。确认卡会自动带上原因：“正在以 X 的权限预览，只读，不能执行”。
  - 提问照常可用，回答只包含范围内的数据。导出禁用。
- 后台：
  - `POST /preview-sessions {userId}`：只有对“角色与权限”有 manage 权限的人能调用。返回 `PreviewSession{user, roles, func, objs, startedAt, returnTo}`，并写审计“开始预览”。
  - 之后前端每个请求都带 **`X-Preview-As: <userId>`**（和 qa-api.md §1.1 一致；本文 v0.1 写的 `X-Preview-Session` 作废）。后台先确认调用人有一个进行中的预览会话、并且会话的对象就是这个 userId，再按被预览用户的 `EffectiveAccess` 计算读权限；写接口一律返回 403 `READONLY_PREVIEW`，导出返回 `EXPORT_FORBIDDEN`。
  - `DELETE /preview-sessions/{id}`：退出预览，写审计“结束预览”。
  - 预览是按浏览器标签页隔离的（存在 sessionStorage 里），后台按会话 id 区分即可。

### 1.8b 后台任务（procrastinate）

用 procrastinate，队列放在 PostgreSQL 里，不需要额外的中间件。需要后台任务的地方：

| 任务 | 触发 | 来源 |
|---|---|---|
| 情景推演运行 | `POST /scenarios/{id}:run` | dynamic-api.md |
| 结果未知的动作自动对账 | 每 15 分钟一次，每条最多查 3 次，之后转人工 | dynamic-api.md、action-event-api.md §5 |
| 规则定时触发、事件规则消费 | cron 或事件 | dynamic-api.md |
| 问数评测运行 | `POST /tuning/eval/runs` | qa-api.md T11 |
| 页面刷新和订阅推送 | 定时或手动 `POST /pages/{pid}/refresh` | qa-api.md P6/P9 |
| 数据连接测试、同步，数据质量检查 | 按钮或定时 | §3.1、§3.3 |
| 发布后的评测回放 | 变更单发布 | static-api.md、qa-api.md |

所有任务都要可以重入（同一个任务执行两次结果一样），状态写进对应的业务表，前端轮询读取。

### 1.9 变更单与草稿视图 `?view=draft`（本体定义类页面共用）

- 规则（`pages/ontology/parts/useDraftView.ts`）：已发布的版本只读，修改的唯一入口是“发起变更”（新建变更单或加入已有变更单）。之后切到 `?view=draft` 草稿视图才能编辑；改动用灰色的“新增 / 修改 / 删除”标出；发布前问数仍然使用已发布的版本。
- 用到这套规则的 `TargetKind`：`object | metric | action | event | rule | link | dimension | knowledge`。
- 接口**完全沿用 static-api.md §3.1**：`POST /change-requests`、`POST /change-requests/{id}/targets`、`GET /{kind}/{id}?view=draft&cr=`、`PUT /change-requests/{id}/items`、`DELETE /change-requests/{id}/targets/{kind}/{targetId}`、`:submit` 等。动作类型、事件类型、规则三个动态页也走这套（static-api.md 待定问题 12，本文按“走同一套”来写）。
- 草稿提示条（DraftBanner）显示 `DraftCR{id,title,owner,updatedAt,changes}`（mock/releases.ts），上面有“提交审核”和“放弃草稿”两个操作。

### 1.10 公共对象悬停卡与详情抽屉

- 任意页面里的对象名都可以悬停或点击，打开公共抽屉（URL `?drawer=kind:id`，最多两层 `&drawer2=`）。数据结构是 `ObjView{kind,kindLabel,id,title,code,status?,brief[],fields[],timeline?,tabs?,related?}`（`components/objectRegistry.ts`）。
- kind 有：`region | store | product | order | metric | object-type | rule | action`（动作执行记录）`| event`（事件记录）。
- 读权限和行级范围同样生效；预览时，范围外的对象要返回 403，前端会提示“XX 无权查看这个门店”。

### 1.11 顶栏与全局

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /me | — | `{user{name,role}, permissions: AreaKey[], func: Record<pageKey,FuncLevel>, tenant}`（stores/app.ts） | — | 读 | 预览时仍然返回真实用户，另外带 `preview` 字段 | `stores/app.ts` `PERSONAS`（不在 src/mock 下）+ `roles.ts` `effectiveAccess` |
| GET | /tenants | — | `{value,label}[]`（stores/app.ts `TENANTS`） | — | 读 | | `stores/app.ts` `TENANTS`（不在 src/mock 下） |
| GET | /releases | — | `Release[]`（mock/releases.ts），另外带 `current: 'v2026.10.3'`、`publishedAt` | — | 读 | 版本选择器、历史版本横幅都用 | `releases.ts` `RELEASES` |
| GET | /notices | `page,size` | `PageResult<Notice>`（mock/releases.ts），另外带 `unread` | 页码 | 读 | 铃铛角标显示未读数 | `releases.ts` `NOTICES` |
| POST | /notices:read-all | — | `{unread:0}` | — | 写 | “全部标为已读”；点击单条通知时也调用 `POST /notices/{id}:read` | — |
| POST | /notices/{id}:read | — | `Notice` | — | 写 | | — |
| GET | /object-views/{kind}/{id} | `release?` | `ObjView`（components/objectRegistry.ts） | — | 读 | 悬停卡和抽屉共用，按行级范围过滤 | `components/objectRegistry.ts` 的解析器 → 数据来自 `stores.ts`/`products.ts`/`orders.ts`/`actions.ts`/`dynamic.ts`/`actionEvent.ts`（不单独导入） |
| POST | /preview-sessions | `{userId}` | `PreviewSession`（stores/preview.ts） | — | 写 | 需要角色页 manage 权限；写审计 | — |
| DELETE | /preview-sessions/{id} | — | `{returnTo}` | — | 写 | 写审计 | — |

- 顶栏搜索（`TopBar.vue`）现在只在前端过滤页面名称，选“提问：xxx”会跳到问数工作台的 `?q=`。**不需要后台接口**。
- “使用帮助”菜单只弹提示，也不需要接口。

### 1.12 后台要给的“显示状态”汇总（颜色由前端决定，后台只给状态值）

| 状态 | 出现在 | 前端颜色 | 后台需要给的数据 |
|---|---|---|---|
| 待确认 | 动作中心、规则暂存、情景推演转出的动作、问数建议动作 | 橙 | `ActionRecord.status='待确认'`，带 `source.confirmer` |
| 审核中 | 变更单，以及被它改到的对象、指标、规则等（状态由所在变更单推导） | 橙 | 列表行带 `crStatus` 或者 `pendingCr{id,status}` |
| 结果未知 | 动作执行、外部调用、时间线 | 橙 + 问号 | `status='结果未知'`、`unknownReason` |
| 发布校验「警告」 | 变更与发布 | 橙 | `CheckRow.result='警告'` |
| 门店权限「未配置范围」 | 角色与权限、对象类型的权限页签 | 橙 | 角色是 `defaultDeny` 并且该对象类型没有 RowScope 时，返回 `scope:'未配置范围'` 或 `scopeConfigured:false` |
| 配置已改动 / 变更中 | 情景推演（配置已改动，需要重新运行）、函数 / 动作类型（变更中 · v2） | 橙 | `PubStatus='变更中'`、`FnVersion.status='变更中'` + `cr` |
| 维度「有歧义」 | 维度的同义词与问数映射 | **灰**，原因放在悬停提示 | `DimMapping.conflict`（说明）+ `resolve`（处理方式） |
| 业务知识同义词冲突 | 业务知识 | **灰**，原因放在悬停提示 | `GET /knowledge/synonym-conflicts`（`synonymConflicts`、`CONFLICT_RULE`，mock/knowledge.ts） |
| 失败 | 质量检查、执行失败、HTTP 非 200、投递失败 | 红 | 对应的 status 或 result |
| 草稿 / 草稿中 | 各本体资源 | 灰 | |
| 待支付、待处理（告警） | 首页订单、告警 | 橙 | |

---

## 2. 使用区

### 2.1 首页 `/`

1. **用途**：经营概览示例页，包括 KPI 条、近 30 日销售趋势、订单表、门店排行。只能看，不能改。
2. **操作流程**
   1. 打开页面，看 4 个 KPI（gmv、order_cnt、gross_margin、ship_ontime 的当前值和环比）、日销售趋势、门店销售 Top10。数据截至 2026-10-24 23:00。
   2. 订单表：按状态、渠道筛选，按订单号或门店名称搜索，分页（默认 15 条，URL 是 `?page=&size=`）。
   3. 点击订单号或门店名，打开公共抽屉（`?drawer=order:SO…`）。订单动作（补发出库单）在抽屉里，经确认卡执行（见 §2.4、§2.5 的动作接口）。
   4. 门店排行点“全部”，打开抽屉（`?rank=all`），抽屉内分页，每页 10 条。
   5. “导出”下拉：提交异步导出任务，提示“完成后可在下载中心获取”。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /dashboard/kpis | `ids=gmv,order_cnt,gross_margin,ship_ontime` | `Metric[]`（mock/metrics.ts，用到 `value,prev,unit`）+ `dataAsOf` | — | 读 | 按行级范围 | `metrics.ts` `METRICS` |
| GET | /metrics/{id}/daily | `from,to,taxMode?` | `{date,value}[]`（mock/metrics.ts `DAILY_GMV_EX`） | — | 读 | 首页和指标页的趋势共用 | `metrics.ts` `DAILY_GMV`、`DAILY_GMV_EX` |
| GET | /orders | `status,channel,kw,page,size,cutoff?` | `PageResult<Order>`（mock/orders.ts），行内补 `storeName` | 页码 | 读 | kw 匹配订单号或门店名；只返回数据截至时间以前的订单 | `orders.ts` `ORDERS` |
| GET | /stores/ranking | `from,to,page,size` | `PageResult<{rank,id,name,city,cnt,amount}>`（Home.vue，amount 是不含税金额） | 页码 | 读 | | 由 `orders.ts` `ORDERS` + `stores.ts` `ALL_STORES` 统计（Home.vue），不单独导入 |
| POST | /exports | `{kind, params}` | `{jobId}` | — | 写 | 异步任务；“下载中心”页面前端还没有做【待确认】；预览时禁用 | — |

4. **状态**：订单状态 `OrderStatus`，其中“待支付”为橙。

### 2.2 问数工作台 `/use/qa/workbench`（问数：**以 qa-api.md 为准**，模块前缀 `/api/qa/v1`）

> 权威来源：`/workspace/ui-vue/docs/backend/qa-api.md`（master `1782512`，问数负责人） 的 §2（W1–W16）、§3（SSE 流式回答，Turn 结构）、§10（按钮 → 接口对照）。本节只列接口和种子数据，并标出差异。

1. **用途**：自然语言提问。三栏布局：左栏会话列表，中间对话区，右栏当前对象和可执行的动作。
2. **流程（简述）**：
   - 左栏会话（`?sessQ/sessRange/sessPage`，每页 12 条）：新建、重命名、删除；常用问题和“我的页面”抽屉（每页 10 条）。
   - 提问：先返回 202，再用 SSE 流式返回回答。范围和口径是会话级设置，中途修改会插入一条系统轮次。
   - 澄清条可以切换含税或不含税（重算）；结果表超过 10 行才分页，可以导出。
   - 赞或踩（踩会生成调优反馈）；固定到页面；越权时可以申请权限。
   - 建议动作：确认后提交，状态变为待审批。
   - 历史版本和预览模式下只读。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /sessions | `q,range,page,size` | `PageResult<QaSession>` | 页码 | 读 | 只返回本人的会话 | `qa.ts` `QA_SESSION_LIST`（103） |
| POST | /sessions | `{scope,caliber}` | `QaSession` | — | 写 | | — |
| GET | /sessions/{sid} | — | `{…, turns: Turn[]}` | 不分页（见差异） | 读 | 打开分享链接的人按自己的权限重新执行 | 现在写死在 `QaConversation.vue` / `QaAnswer.vue` / `QaStoreAnswer.vue`；数据部分来自 `qa.ts` `QA_PROVINCES/QA_TREND/QA_TOTAL_*/QA_EC_STORES`（见差异） |
| PATCH | /sessions/{sid} | `{title?}` 或 `{scope?,caliber?}` | `QaSession` | — | 写 | 改范围或口径时追加一条系统轮次 | — |
| DELETE | /sessions/{sid} | — | 204 | — | 写 | | — |
| POST | /sessions/{sid}/turns | `{question,refs?,clientId}` | 202 `{turnId,streamUrl}` | — | 写 | 历史版本下拒绝；预览时是否允许提问见差异 | — |
| GET | /turns/{tid}/stream | `Last-Event-ID` | SSE 事件流（qa-api.md §3.1） | — | 读 | | — |
| POST | /turns/{tid}/cancel | — | — | — | 写 | 【推断】前端没有停止按钮 | — |
| POST | /turns/{tid}/recalc | `{caliber:ex\|in\|both}` | `Answer`（同步返回） | — | 写 | | — |
| GET | /turns/{tid}/result | `page,size` | `{columns,items,total,summary}` | 页码 | 读 | 合计按全部数据计算 | `qa.ts` `QA_EC_STORES`（38） |
| GET | /turns/{tid}/export | `format=xlsx\|csv` | 文件 | — | 读 | 预览时返回 `EXPORT_FORBIDDEN`；写审计 | — |
| PUT | /turns/{tid}/vote | `{vote,reason?}` | — | — | 写 | 踩会生成一条反馈 | — |
| GET | /favorites | `q,page,size` | `PageResult<QaFavorite>` | 页码 | 读 | | `qa.ts` `QA_FAVORITE_LIST`（18） |
| POST | /favorites | `{question}` | `QaFavorite` | — | 写 | 【推断】前端还没有“加入常用”的入口 | — |
| DELETE | /favorites/{id} | — | 204 | — | 写 | | — |
| POST | /permission-requests | `{scope,metric,reason,duration,sourceTurnId}` | `{id,status,approver}` | — | 写 | | `qa.ts` `QA_APPLY`（只是预填模板，0 条） |
| GET | /session-options | — | `{scopes,calibers,metric}` | — | 读 | qa-api.md 里没有单列这个接口，本文补充【待确认】 | `qa.ts` `QA_SCOPE_OPTIONS`、`QA_CALIBER_OPTIONS`、`QA_METRIC` |

- 右栏（当前对象、时间线、可执行动作）复用 §2.5 的 O5/O7/O9。“固定到页面”用 §2.3 的 `GET /pages?view=brief` 和 `POST /pages/{pid}/cards`。
- 发起动作调用动态模块：qa-api.md §8 的 X1/X2，见下面的差异。
4. **状态**：`answer.status = answered|clarify|denied|empty|failed`；“已申请 · 待审批”；动作状态 `pending_confirm|pending_approval|unknown…`（qa-api.md §1.3）。
5. **差异**
   - **分页参数名**：qa-api.md 用的是 `pageSize`，响应是 `{items,total,page,pageSize}`；其他文档和前端 URL（`usePagedQuery`）用的都是 `size`。按 PDE 决定统一用 `page/size` 并返回 `total`，**qa-api.md 需要把 `pageSize` 改成 `size`**。
   - **W3 的轮次游标**（`before`/`limit`）：前端对话区没有加载更早的消息，也没有用 `useCursorQuery`。按“只有已经用 `useCursorQuery` 的界面才用游标”的决定，第一版一次返回全部轮次。
   - **发起动作的接口**：qa-api.md X1/X2 写的是 `POST /actions/{actionKey}:preview` 和 `POST /actions/submissions`，dynamic-api.md 是 `POST /action-runs:stage` 加 `:confirm`，两者路径不一致，需要问数和动态负责人对齐。另外，问数确认卡点“确认提交”后直接变成**待审批**，等于把暂存和确认合成一步，要和动态负责人确认。
   - **种子缺口**：演示会话的 4 轮回答写死在 `.vue` 里，不在 `src/mock` 下。qa-api.md 建议把它们搬进 `mock/qa.ts`（`QA_DEMO_TURNS`），这需要改前端，而本次不允许改 mock。【待确认】
   - **预览时能不能提问**：工作台在预览时不能提问，对象页在预览时可以，两处不一致（qa-api.md §1.1、待定问题 6）。

### 2.3 我的页面与模板库 `/use/qa/pages`（问数：**以 qa-api.md 为准**，前缀 `/api/qa/v1`）

> 权威来源：`/workspace/ui-vue/docs/backend/qa-api.md`（master `1782512`，问数负责人） §4（P1–P12）。

1. **用途**：管理从问数固定下来的看板页面，以及官方模板。页签：我的页面 / 模板库（`?tab=templates`）。
2. **流程（简述）**：新建（空白页面，或者用模板新建）→ 行内操作：刷新、重命名、共享、删除 → 页面抽屉（`?page_id=&ptab=preview|schedule|share`）：调整卡片顺序或移除卡片、设置刷新与订阅、设置共享 → 模板预览（`?tpl=`）。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /pages | `kw,sourceKind,share,status,page,size,view=brief\|full` | `PageResult<QaPageFull>` | 页码 | 读 | 本人的页面 + 共享给本人的页面 | `qa-pages.ts` `MY_PAGES`、`OTHER_PAGES`、`CARD_LIB`（27+5 个页面 / 197 张卡片） |
| POST | /pages | `{name,scope}` 或 `{name,scope,template{id,caliber,refresh}}` | `QaPageFull` | — | 写 | 重名返回 `NAME_CONFLICT` | — |
| GET | /pages/{pid} | — | `QaPageFull` | — | 读 | 卡片数据按查看人的权限裁剪 | 同 GET /pages |
| PATCH | /pages/{pid} | `{name}` | `QaPageFull` | — | 写 | 重名返回 `NAME_CONFLICT` | — |
| DELETE | /pages/{pid} | — | 204 | — | 写 | 同时停掉定时刷新和订阅 | — |
| POST | /pages/{pid}/refresh | — | `{lastRefresh,dataAsOf,status,statusNote}` 或 202 `{jobId}` | — | 写 | 后台任务用 procrastinate | — |
| POST | /pages/{pid}/cards | `{turnId,tableId?/chartId?,title}` | `PageCard` | — | 写 | 固定到页面：冻结语义计划和 `metric@version` | — |
| PUT | /pages/{pid}/cards | `{order: cardKey[]}` | `{cards,removed[]}` | — | 写 | | — |
| PUT | /pages/{pid}/schedule | `{refresh,sub}` | `{refresh,sub,nextRun}` | — | 写 | 开启订阅时至少一位接收人 | — |
| PUT | /pages/{pid}/share | `ShareSetting` | `ShareSetting` + `linkUrl` | — | 写 | | — |
| GET | /templates | `kw,scene,page,size` | `PageResult<QaTemplate>` | 页码 | 读 | | `qa-pages.ts` `PAGE_TEMPLATES`（22 个模板 / 104 张卡片） |
| GET | /templates/{id} | — | `QaTemplate`（含全部卡片） | — | 读 | | 同上 |

- 选接收人、选成员：用 §4.1 的 `GET /users?kw`。人名按 `qa-pages.ts` `PEOPLE`、`OWNER_DEPT` 映射到用户表。
4. **状态**：页面状态 `ok|refresh_failed|caliber_pending`；卡片的 `pending`。
5. **差异**：本文之前写的 `/qa-pages`、`/people` 作废，改用 qa-api.md 的 `/pages`、`/templates` 和 PDE 的 `/users`。

### 2.4 对象搜索 `/use/objects/search`（问数：**以 qa-api.md 为准**，前缀 `/api/qa/v1`）

> 权威来源：`/workspace/ui-vue/docs/backend/qa-api.md`（master `1782512`，问数负责人） §5（O1–O4）。

1. **用途**：按对象类型和关键词浏览门店、商品、订单，左侧是带计数的分面筛选（每个分面的计数 = 其他分面和关键词生效后的结果，不算本分面自己）。可以导出，也可以“用这批对象提问”（`?ask=1`）。预览时按被预览用户的范围过滤，并显示数据范围。
2. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /objects/types | — | `[{kind,label,unit,kwPlaceholder,facets,columns}]` | — | 读 | | `objects-qa.ts` `OBJ_TYPES`、`FIELD_GROUPS`、`KIND_LABEL`、`OBJ_SCOPE` |
| POST | /objects/{kind}/search | `{kw,facets,page,size}` | `{items,total,facets[{key,label,vals[{v,n}]}],scopeText}` | 页码 | 读 | 按行级范围过滤 | 业务数据：`stores.ts` `ALL_STORES` + `objects-qa.ts` `OBJ_EXTRA_STORES`/`storeSales`（门店 95）、`products.ts` `PRODUCTS`（12）、`orders.ts` `ORDERS`（1286） |
| POST | /objects/{kind}/export | `{kw,facets,ids?}` | CSV（UTF-8 BOM） | — | 读 | 预览时返回 `EXPORT_FORBIDDEN`；写审计 | 同上 |
| POST | /scoped-ask | `{scope{kind,label,filter?,ids?,single?},question}` | `AskAnswer` | — | 写（生成回答） | 范围只传筛选条件，不传整批 ID | `objects-qa.ts`（`AskAnswer` 由函数生成） |

3. **差异**：本文之前写的 `/object-search/*`、`/object-ask` 作废，以 qa-api.md 为准。搜索用 POST 是因为分面条件比较复杂；分页参数仍统一用 `size`。

### 2.5 对象详情 `/use/objects/detail`（问数：**以 qa-api.md 为准**，前缀 `/api/qa/v1`）

> 权威来源：`/workspace/ui-vue/docs/backend/qa-api.md`（master `1782512`，问数负责人） §6（O5–O9）。

1. **用途**：单个对象的完整视图。URL：`?kind=&id=&tab=overview|related|timeline|actions`。右栏有“可执行动作”和“对它提问”（用 O4，`single`）。对象不在范围内时整页显示“无权查看”。
2. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /objects/{kind}/{id} | — | `{kind,kindLabel,id,code,title,status,groups,kpis,quickQuestions,scopeLabel}` | — | 读 | 找不到返回 `NOT_FOUND`，不在范围内返回 `OUT_OF_SCOPE` | `objects-qa.ts`（`FIELD_GROUPS`、`caliberOf`）+ `stores.ts`/`products.ts`/`orders.ts`；问数工作台右栏用 `qa.ts` `REGION_EC` |
| GET | /objects/{kind}/{id}/related | `group?,page,size` | `RelGroup[]`（每组单独分页） | 页码 | 读 | 没有权限的对象类型整组返回 `denied` | `objects-qa.ts` `relationGroups`；`qa.ts` `QA_REGION_RELATED`（38） |
| GET | /objects/{kind}/{id}/timeline | `page,size` | `PageResult<TlItem>` | 页码 | 读 | | `objects-qa.ts` `extraTimeline`；`qa.ts` `QA_TIMELINE`、`QA_REGION_TIMELINE` |
| GET | /objects/{kind}/{id}/action-records | `page,size` | `PageResult<ActRecord>` | 页码 | 读 | | `objects-qa.ts` `actionRecords`（写入动态模块的 `action_run`） |
| GET | /objects/{kind}/{id}/actions | — | `ObjAction[]`（含 `state`、`stateTip`、`blocked`） | — | 读 | 按对象权限里的 `actions` 过滤 | `objects-qa.ts` `objActions` |

- “发起”动作：调用动态模块（qa-api.md 的 X1/X2）。被阻断时返回 `ACTION_BLOCKED`。
3. **差异**：
   - qa-api.md 把对象详情放在 `/api/qa/v1/objects/...`；公共抽屉（§1.10）是另一个接口 `/object-views/{kind}/{id}`。两者都返回同一个对象，建议后台用同一个服务实现，只做两种输出格式【待确认：抽屉是否直接用 O5】。
   - 本文之前写的 `POST /objects/{kind}/{id}/actions/{actionKey}` 作废，改走动态模块（见 §2.2 差异）。

### 2.6 告警处理 `/use/ops/alerts`（动态本体：**以 dynamic-api.md 为准**）

> 权威来源：`git -C /workspace/ui-vue show cc3f949:docs/backend/dynamic-api.md`（branch `dynamic`，v0.1） 的 §2.2（告警状态机）、§2.6（去重与冷却）、§6.1（A1–A10 对照清单）。本节只列读写接口的字段和种子数据，并标出差异。

1. **用途**：处理规则触发的告警。左栏告警列表，右栏详情（触发依据、处理记录、可执行动作）。URL：`?status=open|doing|closed|all&alert=&page=&size=`，每页 12 条。
2. **流程（简述）**：认领、批量认领 → 转派（关联动作在途时锁定） → 关闭为已解决或已忽略（原因必填，只能关闭本人处理中的告警）、批量关闭 → 发起规则绑定的动作（确认卡，提交后直接进入“审批中”；动作成功后告警自动变为已解决）。
3. **接口**（路径沿用 dynamic-api.md §4）

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /alerts | `status, level, ruleId, kw, page,size` | `PageResult<Alert>`（mock/dynamic.ts）+ `counts` | 页码 | 读 | 按行级范围过滤 | `dynamic.ts` `ALERTS` |
| GET | /alerts/{id} | — | `Alert` + `rule: RuleRef`、`metric?`、`recent7d`、`log[{time,text,who}]`、`action?` | — | 读 | | `dynamic.ts` `ALERTS`、`alertTimeline` |
| POST | /alerts/{id}:claim | — | `Alert` | — | 写 | 只针对待处理 | — |
| POST | /alerts:batch-claim | `{ids[]}` | `{claimed}` | — | 写 | | — |
| POST | /alerts/{id}:transfer | `{to}` | `Alert` | — | 写 | 动作在途时返回 `ALERT_LOCKED_BY_ACTION` | — |
| POST | /alerts/{id}:close | `{as:'已解决'\|'已忽略', reason}` | `Alert` | — | 写 | 不是本人时返回 `NOT_ASSIGNEE` | — |
| POST | /alerts:batch-close | `{ids[], as, reason}` | `{closed}` | — | 写 | 只作用于本人处理中的告警 | — |
| POST | /alerts/{id}:start-action | 请求头 `Idempotency-Key`（`act-<告警号>`） | `ActionRecord`（mock/actions.ts） | — | 写 | | — |
| GET | /rules/refs | — | `RuleRef[]`（mock/dynamic.ts `RULES`） | — | 读 | 规则筛选下拉 | `dynamic.ts` `RULES` |
| GET | /people | — | `string[]`（Alerts.vue `PEOPLE`；以后改成从用户表取） | — | 读 | 转派选人 | `Alerts.vue` `PEOPLE`（不在 src/mock 下）→ 用户表 `admin.ts` `ADMIN_USERS` |

4. **状态**：“待处理”为橙；级别“高”只加粗。
5. **差异**
   - 页签值：前端 URL 是 `?status=doing`，dynamic-api.md 写的是 `processing`。建议接口接受前端的 `open|doing|closed|all`，或者由前端做映射。
   - 转派选人：前端写死了 `PEOPLE` 列表，后台需要提供人员接口。dynamic-api.md 没有写这个接口。

### 2.7 动作中心 `/use/ops/actions`（动态本体：**以 dynamic-api.md 为准**；动作类型的定义归 Palantir专家）

> 权威来源：`git -C /workspace/ui-vue show cc3f949:docs/backend/dynamic-api.md`（branch `dynamic`，v0.1） 的 §2.1（动作状态机和幂等键规则）、§6.2（C1–C10）。

1. **用途**：所有动作的确认、执行记录和对账。URL：`?tab=pending|records|unknown&act=&page=&size=`，每页 16 条。
2. **流程（简述）**：
   - 待确认：确认提交，或者撤回（撤回要填原因）。只有确认人能操作。
   - 失败：重新提交，生成新动作和 `-rN` 的新幂等键，原记录保留。
   - 结果未知：人工对账，结论为外部已成功、外部未执行或仍无法确认，说明必填。
   - 系统每 15 分钟按幂等键自动查询一次，任何情况下都不重试写入。
3. **接口**（路径沿用 dynamic-api.md：`/action-runs`）

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /action-runs | `tab, status, action, sysId, kw, ruleId?, page,size` | `PageResult<ActionRecord>`（mock/actions.ts）+ `counts` | 页码 | 读 | | `actions.ts` `ACTIONS` + `actionEvent.ts` `runsOf()`（合并，见 §6） |
| GET | /action-runs/{id} | — | `ActionRecord`（含 log），另外带“能否操作”和原因 | — | 读 | | 同上 |
| POST | /action-runs:stage | `StageInput`（mock/actions.ts） | `ActionRecord`（待确认） | — | 写 | 规则、推演、问数调用；对象详情页发起动作也用它【待确认】 | — |
| POST | /action-runs/{id}:confirm | — | `ActionRecord` | — | 写 | 不是确认人时返回 `NOT_CONFIRMER` | — |
| POST | /action-runs/{id}:withdraw | `{reason}` | `ActionRecord` | — | 写 | | — |
| POST | /action-runs/{id}:resubmit | — | `{newAction, old}` | — | 写 | 已经重新提交过时返回 `ALREADY_RESUBMITTED` | — |
| POST | /action-runs/{id}:reconcile | `{result, note}` | `ActionRecord` | — | 写 | | — |
| POST | /action-runs/{id}:approve | — | `ActionRecord` | — | 写 | **前端没有页面**，dynamic-api.md 标为待定 | — |
| GET | /actions/meta | — | `ACTION_NAMES, ACTION_SYSTEMS, ACTION_META`（mock/actions.ts） | — | 读 | 筛选下拉 | `actions.ts` `ACTION_NAMES`、`ACTION_SYSTEMS`、`ACTION_META` |

4. **状态**：待确认（橙）、结果未知（橙 + 问号）、失败（红）。
5. **差异**：
   - 对账接口的结论字段：前端变量叫 `reconAs`，dynamic-api.md 的入参叫 `result`。两者都可以，以接口为准。
   - 重新提交和对账的操作人：前端没有做限制（dynamic-api.md 待定问题 9）。

### 2.8 情景推演 `/use/ops/scenarios`（动态本体：**以 dynamic-api.md 为准**）

> 权威来源：`git -C /workspace/ui-vue show cc3f949:docs/backend/dynamic-api.md`（branch `dynamic`，v0.1） 的 §2.5、§6.4（S1–S10）。

1. **用途**：调整驱动因素，推演 4、8 或 12 周的经营结果，并可以把选定方案转为暂存动作。URL：`?sc=&mode=cmp&cmp=a,b,c&pick=&page=&impage=`。列表每页 12 条，影响明细每页 6 条。
2. **流程（简述）**：
   - 新建方案、复制方案、改名。
   - 运行或重新运行：异步执行；有旧结果时要确认覆盖。
   - 对比：最多 3 个已完成的方案。
   - 转为暂存动作：先预览建议动作，勾选后确认，在动作中心生成待确认的动作，之后方案配置锁定。
   - 只有方案的所有者能改、运行、转动作；别人的方案只能复制。历史版本下只读。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /scenarios | `status,kw,page,size` | `PageResult<Scenario>`（mock/scenarios.ts） | 页码 | 读 | | `scenarios.ts` `SCENARIOS` |
| GET | /scenarios/options | — | `{areas: SC_AREAS, categories: SC_CATEGORIES, promoTypes: PROMO_TYPES, baseDrivers: BASE_DRIVERS()}` | — | 读 | dynamic-api.md 没有写，本文补充 | `scenarios.ts` `SC_AREAS`、`SC_CATEGORIES`、`PROMO_TYPES`、`BASE_DRIVERS` |
| GET | /scenarios/{id} | — | `Scenario` | — | 读 | 轮询运行状态 | `scenarios.ts` `SCENARIOS` |
| GET | /scenarios/{id}/result | — | `ScResult` | — | 读 | | `scenarios.ts` `simulate()` 的计算结果（已运行的方案在导入时算好存入） |
| GET | /scenarios/{id}/impacts | `page,size` | `PageResult<ScImpact>` | 页码 | 读 | | 同上 |
| GET | /scenarios:compare | `ids=a,b,c` | `Scenario[]`（含 result） | — | 读 | 最多 3 个，超过返回 `COMPARE_LIMIT` | 同上 |
| POST | /scenarios | `{from?}` | `Scenario` | — | 写 | | — |
| PATCH | /scenarios/{id} | `{name}` | `Scenario` | — | 写 | 不是所有者时返回 `SCENARIO_NOT_OWNER` | — |
| POST | /scenarios/{id}:run | `{scope, drivers}` | `{status:'运行中'}` | — | 写 | 可能返回 `SCENARIO_RUNNING`、`SCENARIO_NO_DRIVER`、`SCENARIO_STAGED_LOCKED` | — |
| GET | /scenarios/{id}/stage-plan | — | `StagePlan[]`（`stagePlanOf`） | — | 读 | | `scenarios.ts` `stagePlanOf()`（计算） |
| POST | /scenarios/{id}:stage | `{keys[]}` | `{actionIds[]}` | — | 写 | 配置改过还没重新运行时返回 `SCENARIO_CONFIG_DIRTY` | — |

4. **状态**：“失败”为红；“配置已改动，请先重新运行”为橙；已转动作后锁定。
5. **差异**：没有发现冲突。

## 3. 构建区

### 3.1 数据连接 `/build/data/connections`

1. **用途**：管理数据源（PostgreSQL、达梦 DM8、人大金仓 KingbaseES、MySQL、ERP）：连接参数、库表、同步计划。
2. **操作流程**
   1. 左栏：按类型筛选，按名称、主机、库名搜索，分页。URL：`?ds=DS-001&tab=params|tables|sync&form=new|edit`。
   2. 详情页签：
      - 连接参数，显示最近一次测试结果。
      - 库表：按表名或说明搜索，列出行数、字段数、映射的对象类型；有些表标为“参考表”。
      - 同步计划：同步模式、cron、窗口、增量游标字段、下次运行时间，以及运行记录（`DsRun`）。
   3. 测试连接（用已保存的配置）。
   4. 新建或编辑放在抽屉里。不同类型显示不同字段：达梦显示兼容模式，金仓显示 database_mode，ERP 显示系统和 appId。表单里也可以“测试连接”，用的是还没保存的参数。编辑时密码留空表示不修改。有未保存的修改时，关闭抽屉要确认。
   5. 删除要确认。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /datasources | `type,kw,page,size` | `PageResult<Datasource>`（mock/datasources.ts，**不返回密码**） | 页码 | 读 | | `datasources.ts` `DATASOURCES` |
| GET | /datasources/meta | — | `{types: DS_TYPES, defaultPort: DS_DEFAULT_PORT, dmCompat: DM_COMPAT, kbModes: KB_MODES, erpSystems: ERP_SYSTEMS}` | — | 读 | | `datasources.ts` `DS_TYPES`、`DS_DEFAULT_PORT`、`DM_COMPAT`、`KB_MODES`、`ERP_SYSTEMS` |
| GET | /datasources/{id} | — | `Datasource` | — | 读 | | `datasources.ts` `DATASOURCES` |
| GET | /datasources/{id}/tables | `kw,page,size` | `PageResult<DsTable>` | 页码 | 读 | | `datasources.ts` `dsTables()` |
| POST | /datasources | `Datasource` 的可写字段 + `password` | `Datasource` | — | 写 | 必填：名称、主机、库名、用户名、密码；密码加密存储 | — |
| PUT | /datasources/{id} | 同上，`password` 可选 | `Datasource` | — | 写 | | — |
| DELETE | /datasources/{id} | — | 204 | — | 写 | 被对象类型映射引用时返回 409【待确认】 | — |
| POST | /datasources/{id}:test | — | `DsTest{time,ok,ms,msg}`，同时更新 `status` | — | 写 | 默认 10 秒超时 | — |
| POST | /datasources:test | 未保存的连接参数 | `DsTest` | — | 读 | 表单里的测试连接；历史版本下也按只读处理 | — |

- 注意：sqlglot 没有达梦、金仓方言（调研文档 §6-4）。按用户要求，达梦和金仓测试库现在不搭，这里只保证接口可用。
4. **状态**：`DsStatus = 正常 | 异常 | 未测试`；`DsRun.result` 中的“失败”为红。

### 3.2 外部系统 `/build/data/external`

1. **用途**：维护动作写回用的系统（CRM、WMS、OMS、OA、ERP、短信、企业微信、顺丰、美团等）：配置、幂等、超时和重试，以及调用记录。
2. **操作流程**
   1. 左栏系统列表；右栏配置。“关联动作”按动作类型的 `sysId` 计算；不做写回的集成显示用途说明。
   2. 最近调用：按结果筛选（`?result=成功|失败|结果未知`），分页。点“详情”打开调用抽屉（`?call=`），显示请求体和响应体。
   3. 结果未知：如果是写回调用，就“去动作中心对账”（`?tab=unknown&act=ACT…`）；如果是不支持幂等的通知类调用，就“人工核对”（确认卡），把结果标为成功。
   4. 测试调用：确认后发一次连通性请求，结果计入最近调用。系统停用时被阻断。
   5. 新建或编辑放在抽屉里（`?edit=new|EXT-WMS`），字段有：名称、类别、厂商、环境、地址、认证方式和详情、是否幂等和幂等键、超时、最大重试次数（不幂等时为 0）、限流、负责人、状态。保存前确认，表单有错误时被阻断。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /ext-systems | `page,size` | `PageResult<ExtSystem>`（mock/extsys.ts） | 页码 | 读 | 认证密钥只返回掩码【待确认】 | `extsys.ts` `EXT_SYSTEMS` |
| GET | /ext-systems/{id} | — | `ExtSystem` + `actions: {id,name}[]`（动作类型里 sysId 指向它的） + `ops: ExtOp[]` | — | 读 | | `extsys.ts` `EXT_SYSTEMS` + `actionEvent.ts` `ACTION_TYPES` |
| POST | /ext-systems | `ExtSystem` 的可写字段 | `ExtSystem` | — | 写 | 校验规则见 ExternalSystems.vue 的 formErrors | — |
| PUT | /ext-systems/{id} | 同上 | `ExtSystem` | — | 写 | | — |
| GET | /ext-systems/{id}/calls | `result,page,size` | `PageResult<ExtCall>`（mock/extsys.ts） | 页码 | 读 | | `extsys.ts` `extCalls()` |
| GET | /ext-calls/{callId} | — | `ExtCall` + `request`、`response`（原始报文） | — | 读 | | `extsys.ts` `extCalls()` |
| POST | /ext-systems/{id}:test | — | `ExtCall` | — | 写 | 停用的系统返回 409 | — |
| POST | /ext-calls/{callId}:mark-checked | `{note?}` | `ExtCall`（result=成功） | — | 写 | 只针对不支持幂等、结果未知的调用 | — |

4. **状态**：`CallResult` 中结果未知为橙、失败为红；`ExtSystem.status = 正常 | 降级 | 停用`。

### 3.3 数据质量 `/build/data/quality`

1. **用途**：按对象类型配置的质量检查（新鲜度、空值率、唯一性、引用完整性）。
2. **操作流程**
   1. 检查项表：按对象类型、检查类型、结果筛选，分页。URL：`?type=&kind=&result=&check=QC-045`。
   2. 右栏：选中检查项的运行历史，分页；“立即运行”。
   3. 页头：“全部运行”（提示预计 3 分钟完成），“新建检查”（名称、对象类型、类型、阈值、调度：每 2 小时、每天 23:30、每周一 06:00）。
   4. 失败检查的对象类型名可以点击，打开对象类型抽屉（`?drawer=object-type:inventory`）。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /quality-checks | `typeKey,kind,result,page,size` | `PageResult<QCheck>`（mock/quality.ts）+ `dataCutoff` | 页码 | 读 | | `quality.ts` `QUALITY_CHECKS`、`DATA_CUTOFF` |
| GET | /quality-checks/{id}/runs | `page,size` | `PageResult<QRun>` | 页码 | 读 | | `quality.ts` `QUALITY_CHECKS`（`runs`） |
| POST | /quality-checks | `{name,typeKey,kind,threshold,schedule}` | `QCheck` | — | 写 | | — |
| POST | /quality-checks/{id}:run | — | `{jobId}` | — | 写 | 异步 | — |
| POST | /quality-checks:run-all | — | `{jobId, estimate}` | — | 写 | | — |
| GET | /object-types/brief | — | `QObjType[]`（mock/quality.ts `Q_OBJECT_TYPES`） | — | 读 | 对象类型下拉；多个页面共用 | `quality.ts` `Q_OBJECT_TYPES` |

4. **状态**：`CheckResult = 通过 | 失败`，失败为红；`failSince`。

### 3.4 本体总览 `/build/ontology/overview`

1. **用途**：六类资源（对象、关系、动作、事件、指标、维度）的数量和健康度，待处理事项，对象关系缩略图，最近发布。
2. **操作流程**
   1. 资源卡片：数量，以及健康提示（草稿数、质量检查失败数、孤儿记录数、结果未知数、执行失败数、投递失败数）。点击进入对应页面。
   2. 待处理：未发布的变更单（草稿、审核中、已通过待发布）和失败的质量检查。可以按“全部 / 变更 / 数据质量”筛选，分页。点击跳到“变更与发布”或“数据质量”。
   3. 关系缩略图（只读），点击对象进入对象类型页。
   4. 页头“发起变更”打开 StartChangeModal，创建变更单。
   5. 历史版本：不显示待处理，最近发布只列到所选版本为止。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /ontology/overview | `release?` | `{cards: {key,title,count,unit,health:{n,text,tone:'normal'\|'attention'\|'failed'}[]}[]}`（OntologyOverview.vue） | — | 读 | 颜色由 tone 决定 | 由各本体表统计（Overview.vue），不单独导入 |
| GET | /ontology/todos | `kind=全部\|变更\|数据质量, page,size` | `PageResult<{id,kind,title,who,status,tone,time}>` | 页码 | 读 | 历史版本下返回空 | `changes.ts` `CHANGE_REQUESTS` + `dynamic.ts` `ALERTS` 等统计【推断】 |
| GET | /ontology/graph | `release?` | `{nodes: LinkObject[], edges: {from,to,linkId}[]}`（mock/links.ts） | — | 读 | 节点坐标暂时由前端写死【待确认：要不要存布局】 | `links.ts` `LINK_OBJECTS`、`LINKS` |
| GET | /releases | 见 §1.11 | | | 读 | 最近发布 | `releases.ts` `RELEASES` |
| POST | /change-requests | 见 static-api.md §3.1 | | | 写 | | — |

### 3.5 对象类型 `/build/ontology/object-types`（定义部分以 static-api.md 为准；生命周期、动作、自动化页签以 dynamic-api.md §2.3、§6.5 为准）

1. **用途**：对象类型的定义。页签分三组：定义（概览、属性、关系、数据源与映射），行为（生命周期、动作、自动化），使用与治理（指标、视图、权限、版本历史）。URL：`?type=&tab=&view=draft`。
2. **操作流程（简述）**
   - 列表：搜索、分页；状态由变更单推导（审核中为橙，草稿中为灰）。
   - 发起变更或加入变更单后进入草稿：新增属性（显示名、API 名、类型、分组）、设为或取消敏感（权限页签）、生命周期里新增状态、新增、修改或删除迁移。
   - 从数据源导入、新建对象类型：先发起变更。
   - 重新采集元数据。
   - “预览数据”“批量编辑”按钮置灰，属于第二期。
3. **接口**：写接口和业务规则见 static-api.md §3.2。下面只补读接口和字段：

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /object-types | `kw,page,size,release?` | `PageResult<LinkObject & {status, pendingCr?}>`（mock/links.ts `LINK_OBJECTS`） | 页码 | 读 | | `links.ts` `LINK_OBJECTS`（以 static-api.md 种子表为准） |
| GET | /object-types/{id} | `release?,view?,cr?` | `{meta: ObjMeta（mock/objectProps.ts）, brief: QObjType（mock/quality.ts）, props: ObjProp[], db}` | — | 读 | 草稿视图里每行带 `mark`（static-api.md） | `objectProps.ts` `objectMeta()` + `quality.ts` `Q_OBJECT_TYPES` |
| GET | /object-types/{id}/properties | `kw,release?,view?,cr?` | `ObjProp[]` + `diff: PropDiff[]` | — | 读 | | `objectProps.ts` `OBJECT_PROPS`、`draftPropDiff` |
| GET | /object-types/{id}/links | `release?` | `LinkType[]`（mock/links.ts） | — | 读 | | `links.ts` `LINKS` |
| GET | /object-types/{id}/mappings | `release?` | `{prop,sourceField,sourceType,transform}[]`（ObjectTypes.vue 的映射列） | — | 读 | | `objectProps.ts` `OBJECT_PROPS`（映射列） |
| POST | /object-types/{id}/mappings:refresh-metadata | — | `{jobId}` | — | 写 | 来自 static-api.md | — |
| GET | /object-types/{id}/lifecycle | `release?,view?,cr?` | `Lifecycle` + `draft?: LcDraft`（mock/lifecycle.ts） | — | 读 | 历史版本返回 `lifecycleAsOf` | `lifecycle.ts` `LIFECYCLES`、`LC_DRAFT_EDITS` |
| GET | /object-types/{id}/lifecycle/illegal | — | `LcIllegal[]` | — | 读 | | `lifecycle.ts` `illegalOf()`（计算） |
| GET | /object-types/{id}/lifecycle/changes | `illegal?,page,size` | `PageResult<LcChange>` | 页码 | 读 | `?lcpage=`；生命周期相关以 dynamic-api.md §2.3/§6.5 为准 | `lifecycle.ts` `changesOf()`（计算，导入时生成） |
| GET | /object-types/{id}/action-types | — | `ActionType[]` 的摘要（mock/actionEvent.ts） | — | 读 | 只读汇总 | `actionEvent.ts` `ACTION_TYPES` |
| GET | /object-types/{id}/action-runs | `status,action,page,size` | `PageResult<ActionRecord>` | 页码 | 读 | `?acpage=`；路径跟 dynamic-api.md | 同 `/action-runs` |
| GET | /object-types/{id}/rules | — | `RuleDef[]`（mock/rules.ts） | — | 读 | 跟 dynamic-api.md | `rules.ts` `RULE_DEFS` |
| GET | /object-types/{id}/event-types | — | `EventType[]`（mock/actionEvent.ts） | — | 读 | | `actionEvent.ts` `EVENT_TYPES` |
| GET | /object-types/{id}/rule-hits | `rule?,page,size` | `PageResult<Hit>`（mock/rules.ts） | 页码 | 读 | `?aupage=&aurule=` | `rules.ts` `hitsOf()`（计算，导入时生成） |
| GET | /object-types/{id}/usage | `kind=metric\|view\|cr,page,size` | 指标为 `Metric[]`，视图为 `ObjectView[]`（mock/objectViews.ts），版本历史为 `ChangeRequest[]` + `LegacyRev[]`（mock/objectHistory.ts） | 页码 | 读 | 来自 static-api.md | `metrics.ts` `METRICS`、`objectViews.ts` `viewsOf()`、`objectHistory.ts` `legacyRevs`、`changes.ts` |
| GET | /object-types/{id}/permissions | — | `{roles: {role,view,scope,export,actions,members}[], props: {api,name,sensitive}[]}`（PermissionsTab.vue，数据源 mock/roles.ts） | — | 读 | defaultDeny 角色没配范围时，`scope` 为“未配置范围” | `roles.ts` `ROLES`、`roleMembers` |

### 3.6 关系类型 `/build/ontology/link-types`（静态本体，以 static-api.md 为准）

1. **用途**：维护关系（基数、关联键、物理 JOIN、反向名称）、关联质量、问数路径。可以切换到“关系图”（`?mode=graph`）。URL：`?link=&tab=def|mapping|paths|usage|history&q=&src=&st=&view=draft`。
2. **流程**：搜索和筛选 → 选中关系 → 发起变更 → 草稿里编辑定义（名称；API 名只有新建的草稿可以改；目标对象、基数、可空、中间表、两端属性、反向名称）→ 保存到草稿 → 提交审核。新建关系从对象类型页的 `?from=` 进来，登记到所选变更单。
3. **接口**：写接口见 static-api.md §3.3。读接口：

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /link-types | `q,src,st,page,size,release?` | `PageResult<LinkType>`（mock/links.ts） | 页码 | 读 | | `links.ts` `LINKS` |
| GET | /link-types/{id} | `release?,view?,cr?` | `LinkType`（含 `quality`、`usage`、`history`） | — | 读 | | `links.ts` `LINKS` |
| GET | /link-types/{id}/quality | — | `LinkQuality` | — | 读 | 来自 static-api.md | `links.ts` `LINKS`（quality） |
| GET | /link-types/paths | `from,to` | `PathGroup[]` | — | 读 | 来自 static-api.md | `links.ts` `PATH_GROUPS` |
| GET | /link-types/{id}/usage | — | `LinkUsage[]` | — | 读 | 来自 static-api.md | `links.ts` `LINKS`（usage） |

### 3.7 动作类型 `/build/ontology/action-types`（Palantir：**以 action-event-api.md 为准**，前缀 `/api/ontology/v1`）

> 权威来源：`/workspace/ui-vue/docs/backend/action-event-api.md`（master `736fe56`，Palantir专家） §2.2、§3.1、§4.1、§7.3（A1–A15）。动作执行的状态流转归动态（dynamic-api.md）；外部系统适配器、对账任务、`submit`/`query_by_key` 内部接口归本模块（§1.1）。

1. **用途**：动作的定义。页签：概览、参数、提交条件、写回与副作用、执行记录、版本历史。URL：`?action=&tab=&ppage/psize&rpage/rsize&rs=&view=draft&ver=`。左栏每页 22 条。
2. **流程（简述）**：发起变更 → 在草稿里分节编辑（参数、提交条件、写回配置、本体编辑、副作用），每节都能“保存草稿” → 提交审核或放弃草稿 → 试运行（只校验提交条件、模拟调用外部系统）→ 执行记录抽屉。版本历史里的“对比”和“回滚”置灰（暂未开放）。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /action-types | `group,kw,page,size,release?` | `PageResult<ActionType 摘要>` + `groups[]` | 页码 | 读 | | `actionEvent.ts` `ACTION_TYPES`（17）、`ACTION_GROUPS` |
| GET | /action-types/{id} | `release?,view?,cr?` | 详情 + `draft{…}`（草稿视图） | — | 读 | | `actionEvent.ts` `ACTION_TYPES`；`RULE_ACTION` |
| GET | /action-types/{id}/params | `page,size,release?,view?,cr?` | `PageResult<ActionParam>` | 页码 | 读 | | `actionEvent.ts` `paramsOf()`（101） |
| GET | /action-types/{id}/submission-criteria | 同上 | `SubmitCond[]` | 不分页 | 读 | | `actionEvent.ts` `condsOf()`（59） |
| GET | /action-types/{id}/writeback | 同上 | `{config,edits,effects}` | — | 读 | | `actionEvent.ts` `writebackOf()`（本体编辑 36 条 / 副作用 47 条） |
| GET | /action-types/{id}/used-by | `release?` | `[{ruleId,ruleName,trigger,mode,alerts,openAlerts}]` | 不分页 | 读 | | `actionEvent.ts` `usedBy`、`RULE_ACTION`、`RULE_CONFIRMER`；`dynamic.ts` `RULES`、`ALERTS` |
| GET | /action-types/{id}/stats | `days=30` | `{total,ok,failed,unknown,pending,confirm,rate,avgMs,asOf}` | — | 读 | | `actionEvent.ts` `runStats()` |
| GET | /action-types/{id}/stats/daily | `days=14` | `{date,ok,unknown,failed}[]` | — | 读 | | `actionEvent.ts` `runDaily()`（原样存入 `action_run_daily_stat`） |
| GET | /action-types/{id}/versions | — | `TypeVersion[]` | 不分页 | 读 | | `actionEvent.ts` `versionsOf()`、`ACTION_VERSION_NOTES` |
| POST | /action-types/{id}:dry-run | `{params?,release?}` | `{passed,checked,failed[]}` | — | 写（不落库） | 草稿状态返回 `TYPE_IS_DRAFT`，条件不满足返回 `SUBMISSION_CRITERIA_FAILED` | — |
| GET | /action-runs | `actionType,status,page,size` | `PageResult<ActionRun>` | 页码 | 读 | 和动作中心共用一张 `action_run` 表 | `actionEvent.ts` `runsOf()`（`RUNS` 1849 条）+ `actions.ts` `ACTIONS` |
| GET | /action-runs/{id} | — | 抽屉字段 + `timeline[]` + `related[]` | — | 读 | | `actionEvent.ts` `runById()`；`actionEventResolvers.ts` `action()` |
| GET | /ext-systems | `writeback=1` | `ExtSystem[]` | 不分页 | 读 | 编辑写回时的下拉；接口在 §3.2 | `extsys.ts` `EXT_SYSTEMS` |
| PUT | /change-requests/{id}/items | `target{kind:'action'}`，`section ∈ params\|conds\|edits\|effects\|config` | 见 static-api.md | — | 写 | | — |

4. **状态**：`已发布|变更中|草稿`；执行结果 `RunStatus`。
5. **差异**：
   - action-event-api.md §3 开头写的前缀是 `/api/v1`，按决定改成 `/api/ontology/v1`。
   - 执行记录：本文之前写的 `/action-types/{id}/runs` 作废，改用 `/action-runs?actionType=`。`RUNS`（编号 2000 段）和 `actions.ts`（0413–0532）进同一张表，由谁写种子还没定（action-event-api.md 待定问题 5）。
   - 历史版本下：前端没有把“试运行”置灰（action-event-api.md 待定问题 7）；但该文档 §3.3 又写着带 `release` 的写接口一律返回 `READONLY_HISTORY`。试运行不落库，建议按读处理，在历史版本下也允许【待确认】。

### 3.8 函数 `/build/ontology/functions`

1. **用途**：维护可以被动作、规则调用的 Python 函数：签名（入参和出参按对象类型标注）、测试运行、版本历史。
2. **操作流程**
   1. 左栏：按函数名或中文名搜索，按状态筛选（已发布、草稿、已停用），分页。URL：`?fn=FN-003&tab=def|test|versions&form=new|edit`。
   2. 定义：签名、入参、出参、被谁使用、运行环境、超时、近 7 天调用量和平均耗时。
   3. 测试运行：输入 JSON（不是合法 JSON 时报错），点运行，显示输出。
   4. 版本历史：分页；“回滚到此版本”（确认）会生成一个新版本。
   5. 新建或编辑放在抽屉里：中文名、函数名（只有新建时能填，英文小写加下划线）、超时 1–60 秒、说明、代码。保存要确认，保存后生成新版本。有未保存的修改时关闭要确认。
   6. 函数**不走变更单**，直接保存成新版本。但 `FnVersion.status` 有“变更中”和 `cr` 字段【待确认：函数是否也纳入整版发布】。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /functions | `kw,status,page,size` | `PageResult<OntFunction>`（mock/functions.ts，列表可以不带 code） | 页码 | 读 | | `functions.ts` `FUNCTIONS`、`FN_PENDING` |
| GET | /functions/{id} | — | `OntFunction` + `signature` | — | 读 | | `functions.ts` `FUNCTIONS`、`fnSignature` |
| POST | /functions | `{name,title,timeoutS,desc,inputs,output,code}` | `OntFunction`（草稿） | — | 写 | name 唯一 | — |
| PUT | /functions/{id} | 同上（name 不能改） | `OntFunction`（version +1） | — | 写 | | — |
| POST | /functions/{id}:test | `{input: object}` | `{output, ms, logs?}` | — | 写（沙箱执行） | 要沙箱隔离并按 timeoutS 超时【待确认：沙箱方案】 | — |
| GET | /functions/{id}/versions | `page,size` | `PageResult<FnVersion>` | 页码 | 读 | | `functions.ts` `fnVersions()` |
| POST | /functions/{id}:rollback | `{toVersion}` | `OntFunction` | — | 写 | | — |

4. **状态**：`FnVersion.status = 当前 | 历史 | 变更中`，变更中为橙。

### 3.9 指标口径 `/build/ontology/metrics`（静态本体，以 static-api.md 为准）

1. **用途**：维护指标的定义（公式、单位、版本）、允许的维度、版本历史、引用与影响。URL：`?metric=&tab=def|dims|versions|refs&view=draft`。
2. **流程**：按名称或 API 名搜索，按分组（销售、利润、履约、库存、会员）筛选 → 试算近 30 日 → 发起变更进入草稿（“编辑定义”按钮目前没有绑定动作，见 §5 差异）→ 版本历史里“回滚口径版本”会生成一张新的变更单 → 新建或导入指标前先发起变更。
3. **接口**：写接口见 static-api.md §3.4。读接口：

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /metrics | `kw,group,page,size,release?` | `PageResult<Metric>`（mock/metrics.ts）+ `pendingCr?` | 页码 | 读 | | `metrics.ts` `METRICS` |
| GET | /metrics/{id} | `release?,view?,cr?` | `Metric` + `api, desc, formula`；`dims: {dim,level,source,defaultGrain,note}[]`；`disallowed: {dim,reason}[]`（Metrics.vue 的列） | — | 读 | | `metrics.ts` `METRICS` |
| POST | /metrics/{id}:trial | 见 static-api.md | | | 读（不落库） | | — （计算接口，读已导入的数据） |
| GET | /metrics/{id}/versions | 见 static-api.md | | 页码 | 读 | | 见 static-api.md 种子表 |
| GET | /metrics/{id}/refs | 见 static-api.md | | 页码 | 读 | | 见 static-api.md 种子表 |

### 3.10 维度与字典 `/build/ontology/dimensions`（静态本体，以 static-api.md 为准）

1. **用途**：维护维度和码表：定义、层级、取值字典、同义词与问数映射、使用情况、版本历史。URL 参数：`?dim=&tab=&k=&g=&q=&lv=&vq=&vs=&vp=&conf=1&try=&all=1&view=draft&ver=`。
2. **流程**：
   - 发起变更进入草稿。
   - 编辑问数默认设置：默认层级、未指定时怎么处理、空值显示为、排序。
   - 新增、编辑、停用或启用取值（停用要确认）。
   - 新增说法映射：匹配方式有精确、包含、多值；多值时要写 resolve。
   - “试一试”：输入问句里的说法看匹配结果（`all=1` 表示在全部维度中匹配）。
   - “只看冲突”。
   - “新建维度”按钮置灰。
   - 历史版本下显示 `levelsBefore`，取值按 `since ≤ 版本 < until` 过滤。
3. **接口**：写接口见 static-api.md §3.5。读接口：

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /dimensions | `q,kind,group,page,size,release?` | `PageResult<DimItem 摘要>`（mock/dimensions.ts） | 页码 | 读 | | `dimensions.ts` `DIM_ITEMS` |
| GET | /dimensions/{id} | `release?,view?,cr?` | `DimItem`（levels、qa、usage、history；不含 values 和 mappings） | — | 读 | | `dimensions.ts` `DIM_ITEMS` |
| GET | /dimensions/{id}/values | `level,q,status,page,size,release?,view?,cr?` | `PageResult<DimValue>` | 页码 | 读 | `?vp=` | `dimensions.ts` `DIM_ITEMS`（values） |
| GET | /dimensions/{id}/mappings | `conflict?,page,size` | `PageResult<DimMapping>` | 页码 | 读 | 来自 static-api.md | `dimensions.ts` `DIM_ITEMS`（mappings） |
| POST | /dimensions:match | 见 static-api.md | | | 读 | 试一试 | — （计算接口，读已导入的数据） |
| GET | /dimensions/{id}/metric-usage | — | `{metric, note}[]`（`metricUsage`） | — | 读 | | `dimensions.ts` `metricUsage` |

4. **状态**：映射的“有歧义”（`conflict` 字段），前端显示为**灰色**，原因放在悬停提示里。

### 3.11 业务知识 `/build/ontology/glossary`（静态本体，以 static-api.md 为准）

1. **用途**：维护术语和同义词、业务规则和计算说明、财务日历和大促期、挂在对象和指标上的口径说明。页签：内容、问数中的作用、引用、版本历史。URL：`?k=&tab=&q=&type=&st=&view=draft&ver=`。
2. **流程**：
   - 列表：搜索，按类型、状态（已发布、草稿中、审核中、已停用）筛选。
   - 发起变更后在草稿里编辑内容（名称、类型、定义、计算、同义词、适用的对象和指标、周期、负责人、过滤条件、财务日历区间）。
   - 停用：要确认，确认后记入变更单，等待审核。
   - 新建知识：登记到变更单。
   - “解析”试一试：输入一个问题，看问数怎么理解。
   - 同义词冲突显示为灰色，原因放在悬停提示里。
3. **接口**：写接口见 static-api.md §3.6。读接口：

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /knowledge | `q,type,st,page,size,release?` | `PageResult<KEntry>`（mock/knowledge.ts） | 页码 | 读 | 历史版本下只返回 `since ≤ ver` 的条目 | `knowledge.ts` `KNOWLEDGE`、`K_TYPES` |
| GET | /knowledge/{id} | `release?,view?,cr?` | `KEntry`（含 `draft`、`refs`、`history`、`periods`、`tries`） | — | 读 | | `knowledge.ts` `KNOWLEDGE` |
| POST | /knowledge:resolve | 见 static-api.md | | | 读 | | — （计算接口，读已导入的数据） |
| GET | /knowledge/synonym-conflicts | — | `synonymConflicts` + `CONFLICT_RULE` | — | 读 | 来自 static-api.md | `knowledge.ts` `synonymConflicts`、`CONFLICT_RULE` |
| GET | /knowledge/{id}/refs | — | `KRef[]` | — | 读 | 来自 static-api.md | `knowledge.ts` `KNOWLEDGE`（refs） |

### 3.12 事件类型 `/build/ontology/event-types`（Palantir：**以 action-event-api.md 为准**，前缀 `/api/ontology/v1`）

> 权威来源：`/workspace/ui-vue/docs/backend/action-event-api.md`（master `736fe56`，Palantir专家） §2.3、§3.2、§4.3、§5.3/5.4、§7.3（E1–E9）。

1. **用途**：定义事件（来源、对象、去重键和窗口、保留期、投递方式、顺序）、载荷字段、订阅方、近期事件、版本历史。URL：`?event=&tab=&fpage/fsize&range=&epage/esize&view=draft&ver=`。
2. **流程（简述）**：
   - 草稿里可以新增载荷字段（API 名不能和已有字段重复，包括信封字段）和订阅方。删除字段或改字段类型属于不兼容变更，需要所有订阅方确认。
   - 近期事件：按时间范围和结果筛选、分页，点击打开事件抽屉。
   - 模拟触发：只投递到订阅方的测试通道，草稿状态不能用。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /event-types | `source,kw,page,size,release?` | `PageResult<EventType 摘要>` + `sources[]` | 页码 | 读 | | `actionEvent.ts` `EVENT_TYPES`（15）、`EVENT_SOURCES` |
| GET | /event-types/{id} | `release?,view?,cr?` | 详情 + `draft{…}` | — | 读 | | `actionEvent.ts` `EVENT_TYPES` |
| GET | /event-types/{id}/payload-fields | `page,size,view?,cr?` | `PageResult<PayloadField>` + `envelopeCount` | 页码 | 读 | 信封字段只读（改它返回 `ENVELOPE_READONLY`） | `actionEvent.ts` `ENVELOPE`（10）+ `EventType.specific` |
| GET | /event-types/{id}/subscribers | `view?,cr?` | `Subscriber[]` | 不分页 | 读 | | `actionEvent.ts` `subscribersOf()`（37） |
| GET | /event-types/{id}/stats | — | `{weekly,p95Ms,dedup,failed,partial,asOf,daily[]}` | — | 读 | | `actionEvent.ts` `eventDaily()`、`EventType.weekly`；健康指标按 `eventsOf()` 计算 |
| GET | /event-types/{id}/events | `range=24h\|7d,outcome,page,size` | `PageResult<EventRecord>`（含 `total`） | **页码**（见差异） | 读 | | `actionEvent.ts` `eventsOf()`（`EVENTS` 1243） |
| GET | /events/{id} | — | 抽屉字段 + `timeline[]` + `related[]` | — | 读 | | `actionEvent.ts` `eventById()`；`actionEventResolvers.ts` `event()` |
| GET | /event-deliveries | `eventId` | `Delivery[]` | 不分页（见差异） | 读 | | 由 `eventsOf()` 的 `outcome`/`subscribers` 推出来【推断】 |
| POST | /event-types/{id}:simulate | `{payload?}` | `{testEventId,deliveredTo}` | — | 写（测试通道） | 草稿状态返回 `TYPE_IS_DRAFT` | — |
| GET | /event-types/{id}/versions | — | `TypeVersion[]` | 不分页 | 读 | | `actionEvent.ts` `versionsOf()`、`EVENT_VERSION_NOTES` |
| PUT | /change-requests/{id}/items | `target{kind:'event'}`，`section ∈ payload\|subs` | 见 static-api.md | — | 写 | 不兼容变更返回 `INCOMPATIBLE_PAYLOAD_CHANGE` | — |

- 内部接口 `POST /internal/v1/events`（接收事件）不对前端开放，不计入本文的接口数。
4. **状态**：`EventOutcome` 中“投递失败”为红；“部分投递”。
5. **差异**：
   - **近期事件的分页**：action-event-api.md 写的是游标分页、不返回总数（它的待定问题 3 正好在问这个）。前端用的是 `usePagedQuery`，显示“‹ 1/N ›”。按 PDE 的决定，**用 `page/size` 并返回 `total`**，这个待定问题就此有了结论。
   - `GET /event-deliveries` 的游标：前端只在抽屉里一次显示一条事件的全部投递记录，没有列表分页，所以不分页。

### 3.13 规则与自动化 `/build/ontology/rules`（动态本体：**以 dynamic-api.md 为准**）

> 权威来源：`git -C /workspace/ui-vue show cc3f949:docs/backend/dynamic-api.md`（branch `dynamic`，v0.1） 的 §2.3、§2.4（回放试跑）、§2.6、§6.3（R1–R13）。

1. **用途**：维护告警和通知规则：条件、触发方式、自动暂存动作、命中历史、回放试跑、版本历史。URL：`?rule=&tab=cond|trigger|action|hits|replay|versions&hrange=&hpage&hit=&rrange=&rver=&run=1&rpage&view=draft`。左栏每页 16 条。
2. **流程（简述）**：
   - 发起变更进入草稿，编辑条件、触发方式、自动暂存设置（动作类型、确认人、参数映射）。必填映射不齐时不能保存到草稿。
   - 查看命中历史（7 天或 30 天）。
   - 回放试跑：不生成告警、不暂存动作、不写命中表。
   - 自动暂存的动作只进入“待确认”，从不自动执行。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /rules | `kw,trigger,status,page,size,release?` | `PageResult<RuleDef>`（mock/rules.ts）+ `pendingCr?` | 页码 | 读 | | `rules.ts` `RULE_DEFS`、`RULE_DRAFTS` |
| GET | /rules/{id} | `release?,view?,cr?` | `RuleDef` | — | 读 | | `rules.ts` `RULE_DEFS` |
| GET | /rules/{id}/hits | `range=7d\|30d,page,size` | `PageResult<Hit>`（mock/rules.ts `hitsOf`） | 页码 | 读 | | `rules.ts` `hitsOf()`（导入时生成） |
| GET | /rule-hits/{id} | — | `Hit`（含 `alert?`） | — | 读 | | 同上 + `dynamic.ts` `ALERTS` |
| POST | /rules/{id}:replay | `{range, version}` | `{actual,would,added,removed,deduped}` + `samples: PageResult<ReplaySample>` | 页码 | 写（不落库） | 历史版本下禁止 | — |
| GET | /rules/{id}/versions | — | `RuleVersion[]`（`ruleVersions`） | — | 读 | | `rules.ts` `ruleVersions` |
| POST | /rules/{id}:rollback | `{toVersion}` | `ChangeRequest` | — | 写 | 生成一张新的变更单 | — |
| GET | /rules/options | — | `{confirmers: CONFIRMERS, mappingSources: MAPPING_SOURCES}`（mock/rules.ts） | — | 读 | dynamic-api.md 没有写，本文补充 | `rules.ts` `CONFIRMERS`、`MAPPING_SOURCES` |
| PUT | /change-requests/{id}/items | `target{kind:'rule'}` | 见 static-api.md | — | 写 | | — |

4. **状态**：规则所在变更单为“审核中”时显示橙色；`ReplayDiff`。
5. **差异**：
   - 版本历史的“对比”和“回滚”：前端 Rules.vue 第 469 行这两个 `<a>` **没有绑定点击事件**，只有提示。dynamic-api.md 定义了 `:rollback`，接口可以先做，前端还需要补交互。
   - 回放结果：dynamic-api.md 写“分页 9 条”，前端用的是 `usePagedQuery`（`?rpage/rsize`），具体条数以前端为准。

### 3.14 问数调优 `/build/ontology/qa-tuning`（问数：**以 qa-api.md 为准**，前缀 `/api/qa/v1`）

> 权威来源：`/workspace/ui-vue/docs/backend/qa-api.md`（master `1782512`，问数负责人） §7（T1–T20）、§7.1（状态流转）。

1. **用途**：认证问答、评测题库、反馈与纠错、高频问题推荐。页签 `?tab=cert|eval|feedback|freq`，页签上带数量。
2. **流程（简述）**：
   - 新建认证问答，创建后是“待认证” → 认证；关联的变更单还没发布时，状态为“待重新认证”。
   - 停用、启用；增删同问法。
   - 运行评测：异步执行（procrastinate），不改任何口径。
   - 反馈：转为同义词（新建或加入变更单）、转为认证问答（没有语义计划时被阻断）、忽略或重新打开。
   - 高频问题：设为认证问答。
3. **接口**（T1–T20）

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /tuning/summary | `release?` | `{version,dataAsOf,recertCount,openFeedback,uncertFreq,evalTotal}` | — | 读 | | 由下面几组数据统计出来 |
| GET | /tuning/plan-options | — | `{metrics,dims,times}` | — | 读 | | `qa-tuning.ts` `T_METRICS`；`DIMS`、`TIMES` 现在写在 `QaTuning.vue` 里 |
| GET | /tuning/certs | `kw,status,metric,page,size` | `PageResult<CertQa>` | 页码 | 读 | | `qa-tuning.ts` `CERT_QAS`（30） |
| GET | /tuning/certs/{id} | — | `CertQa` | — | 读 | | 同上 |
| POST | /tuning/certs | `{question,plan}` | `CertQa`（pending） | — | 写 | 重复返回 `NAME_CONFLICT` | — |
| POST | /tuning/certs/{id}:certify | — | `CertQa` | — | 写 | 有未发布的变更单时状态为 `recert` | — |
| PATCH | /tuning/certs/{id} | `{enabled}` | `CertQa` | — | 写 | | — |
| PUT | /tuning/certs/{id}/variants | `{variants[]}` | `CertQa` | — | 写 | 重复返回 `NAME_CONFLICT` | — |
| GET | /tuning/eval/runs | `trigger,page,size` | `PageResult<EvalRun>` | 页码 | 读 | | `qa-tuning.ts` `EVAL_RUNS`（21） |
| GET | /tuning/eval/runs/{id} | `page,size` | `EvalRun` + `fails: PageResult<FailCase>` + `kindStats` | 页码 | 读 | | `qa-tuning.ts` `EVAL_RUNS`（失败用例 222）、`REPLAY_HINT` |
| POST | /tuning/eval/runs | `{group}` | 202 `EvalRun(running)` | — | 写 | | — |
| GET | /tuning/eval/questions | `group,last,kw,page,size` | `PageResult<EvalQ>` + `groupCounts` | 页码 | 读 | | `qa-tuning.ts` `EVAL_QS`（40）、`EVAL_GROUPS` |
| GET | /tuning/feedbacks | `kw,status,fixKind,page,size` | `PageResult<Feedback>` | 页码 | 读 | | `qa-tuning.ts` `FEEDBACKS`（26） |
| GET | /tuning/feedbacks/{id} | — | `Feedback` + `certBlock`、`existingCert?`、`coveredBy?` | — | 读 | | 同上 + `state.ts` `tuning.synonyms` |
| POST | /tuning/feedbacks/{id}:to-synonym | `{join?,title?}` | `{crId,feedback}` | — | 写 | 内部调用变更单接口 | — |
| POST | /tuning/feedbacks/{id}:to-cert | — | `{certId,feedback}` | — | 写 | 没有语义计划时返回 `CERT_NO_PLAN` | — |
| POST | /tuning/feedbacks/{id}:ignore | — | `Feedback` | — | 写 | | — |
| POST | /tuning/feedbacks/{id}:reopen | — | `Feedback` | — | 写 | | — |
| GET | /tuning/freq | `period=7\|30,cert,kw,page,size` | `PageResult<FreqQ>` | 页码 | 读 | | `qa-tuning.ts` `FREQ_QS`（32） |
| POST | /tuning/freq/{id}:certify | — | `CertQa` | — | 写 | | — |
| GET | /tuning/synonyms | — | `{word,target,cr}[]` | — | 读 | | `pages/ontology/qa-tuning/state.ts` `tuning.synonyms`（2 条，**不在 src/mock 下**） |

4. **状态**：认证 `pending|certified|recert|disabled`；反馈 `open|to_synonym|to_cert|await_release|ignored`；评测 `queued|running|done`。
5. **差异**：本文之前写的 `/cert-qas`、`/eval/*`、`/feedbacks`、`/freq-questions` 作废。种子缺口：`DIMS`、`TIMES` 在 `QaTuning.vue` 里，同义词关联在 `state.ts` 里，都不在 `src/mock` 下；`repeat_sales` 这几个指标还没有进公共的 `metrics.ts`。

### 3.15 变更与发布 `/build/ontology/releases`（静态本体，以 static-api.md 为准）

1. **用途**：变更单列表和详情（改动清单、发布校验、影响分析、评审记录），整版发布、回滚。URL：`?cr=&tab=diff|checks|impact|log&st=`。
2. **流程**：完全按 static-api.md §2.1 的状态图和 §2.2 的权限表。前端的确认卡依次是：提交审核、删除草稿、撤回提交、审核通过、驳回（5 个预设原因，必选）、撤回、改回草稿、整版发布（显示合并校验，有阻断时锁定）、回滚（只能回滚最近一次）。还有“重新校验”和“编辑”（跳到对象页，带 `?back=`）。
3. **接口**：全部见 static-api.md §3.1（`/change-requests*`、`/releases:merge-check`、`POST /releases`、`/releases/{ver}:rollback`）。返回字段对应 mock/changes.ts 的 `ChangeRequest`、`CRDetail{diffs: DiffRow[], checks: CheckRow[], impact{direct,defs,usage: ImpactItem[]}, trial?, migration?, touches, synonyms?, edit?, times, notes?}`、`ReviewEvent`。

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /change-requests | `status,kw,page,size` | `PageResult<ChangeRequest>` | 页码 | 读 | 来自 static-api.md | `changes.ts` `CHANGE_REQUESTS`；`releases.ts` `DRAFTS` |
| GET | /change-requests/{id} | — | `ChangeRequest` + `CRDetail` | — | 读 | 来自 static-api.md（拆成 /diffs /checks /impact /trial /events 也可以） | `changes.ts` `CR_DETAILS`、`crDetail`、`reviewLog` |
| POST | /change-requests/{id}:validate | — | `{jobId}` | — | 写 | | — |
| POST | /change-requests/{id}:submit | — | `ChangeRequest` | — | 写 | | — |
| POST | /change-requests/{id}:withdraw | — | `ChangeRequest` | — | 写 | | — |
| POST | /change-requests/{id}:approve | — | `ChangeRequest` | — | 写 | | — |
| POST | /change-requests/{id}:reject | `{reason}` | `ChangeRequest` | — | 写 | | — |
| POST | /change-requests/{id}:unapprove | — | `ChangeRequest` | — | 写 | | — |
| POST | /change-requests/{id}:reopen | — | `ChangeRequest` | — | 写 | | — |
| DELETE | /change-requests/{id} | — | 204 | — | 写 | | — |
| POST | /releases:merge-check | — | `{rows: CheckRow[], blocked}` | — | 读 | | — |
| POST | /releases | `{note, lockVersion}` | `Release` | — | 写 | 只有本体管理员 | — |
| POST | /releases/{ver}:rollback | — | `Release` | — | 写 | | — |

4. **状态**：`CRStatus` 中“审核中”为橙；`CheckResult` 中“阻断”为红、“警告”为橙。

### 3.16 本体定义类页面共用的变更单接口（只为汇总，详见 static-api.md §3.1）

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| POST | /change-requests | `{title, reason?, target{kind,id,isNew?}}` | `ChangeRequest` | — | 写 | 发起变更（新建变更单） | — |
| POST | /change-requests/{id}/targets | `{target{kind,id,isNew?}}` | `ChangeRequest` | — | 写 | 加入已有变更单 | — |
| PUT | /change-requests/{id}/items | `{target, section, rows[{key,op,after}], lockVersion}` | `{changes, lockVersion}` | — | 写 | 保存到草稿 | — |
| DELETE | /change-requests/{id}/targets/{kind}/{targetId} | — | 204 | — | 写 | 放弃草稿 | — |

---

## 4. 管理区

### 4.1 用户与组织 `/admin/system/users`

1. **用途**：用户管理：新建、编辑、批量导入、启用、停用。
2. **操作流程**
   1. 列表：按部门、状态（启用、停用、待激活）筛选，按姓名、账号或 ID 搜索，分页（`?page=&size=`，每页 17 条）。列包括“待办审批”数量。
   2. 新建或编辑放在抽屉里（`?user=new|U…`）：姓名、账号（只有新建时能填）、部门、角色、状态。
   3. 批量导入：先预览要导入的行，再导入（确认卡）。
   4. 启用（确认卡）。
   5. 停用（确认卡）：说明会话会保留为只读、名下的待办审批需要转交，并选择转交人。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /users | `dept,status,kw,page,size` | `PageResult<AdminUser & {pendingApprovals:number}>`（mock/admin.ts） | 页码 | 读 | | `admin.ts` `ADMIN_USERS`、`pendingApprovals` |
| GET | /depts | — | `string[]`（`ADMIN_DEPTS`） | — | 读 | | `admin.ts` `ADMIN_DEPTS` |
| POST | /users | `{name,account,dept,role,status}` | `AdminUser` | — | 写 | account 唯一 | — |
| PUT | /users/{id} | `{name,dept,role,status}` | `AdminUser` | — | 写 | | — |
| POST | /users:import-preview | 文件或行数据 | `{rows:{name,account,dept,role,error?}[]}` | — | 读 | 文件格式（Excel、CSV）【待确认】 | — |
| POST | /users:import | `{rows}` | `{imported:n}` | — | 写 | | — |
| POST | /users/{id}:enable | — | `AdminUser` | — | 写 | | — |
| POST | /users/{id}:disable | `{transferTo?}` | `AdminUser` | — | 写 | 有待办审批时 `transferTo` 必填【待确认】 | — |

### 4.2 角色与权限 `/admin/system/roles`

1. **用途**：维护角色的功能权限、对象与动作权限、行级范围、成员；以某个用户的身份预览。
2. **操作流程**
   1. 左栏角色列表；右栏页签 `?tab=func|obj|row|members`。URL：`?role=R-04&form=edit|scope|members&preview=U010231`。
   2. 功能权限：每个页面选 无 / 查看 / 管理。对象权限：可查看、可导出、可执行的动作。两者都在页面上直接改，有修改时底部出现保存条：放弃修改，或者保存（确认卡）。切换角色或离开页面要确认。**内置角色和历史版本只读**。
   3. 行级范围：添加范围（选“对象类型 · 属性”，再选取值），或删除范围（确认卡）。
   4. 成员：搜索，分页（`?mpage=&msize=`）；添加成员（可以多选）；移出角色（确认卡）；“以此用户预览”。
   5. 新建角色（名称、权限模板 `NR_PERMS`），复制角色（新角色没有成员），编辑名称和说明（`?form=edit`），删除角色（确认卡）。
   6. 预览抽屉（`?preview=U…`）：汇总该用户所有角色合并后的有效权限（对象类型、可见范围、导出、可执行动作），点“进入预览”。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /roles | — | `Role[]`（mock/roles.ts）+ `memberCount` | — | 读 | | `roles.ts` `ROLES` |
| GET | /roles/{id} | — | `Role` | — | 读 | | `roles.ts` `ROLES` |
| GET | /roles/meta | — | `{funcPages: FUNC_PAGES, permObjects: PERM_OBJECTS, scopeProps: SCOPE_PROPS, objActions}` | — | 读 | | `roles.ts` `FUNC_PAGES`、`PERM_OBJECTS`、`SCOPE_PROPS` |
| POST | /roles | `{name, desc?, perms?: string[], copyFrom?}` | `Role` | — | 写 | 角色名唯一 | — |
| PATCH | /roles/{id} | `{name,desc}` | `Role` | — | 写 | 内置角色返回 403 | — |
| PUT | /roles/{id}/permissions | `{func, objs: ObjPerm[]}` | `Role` | — | 写 | 内置角色返回 403 | — |
| DELETE | /roles/{id} | — | 204 | — | 写 | 内置角色不能删；有成员时的处理【待确认】 | — |
| POST | /roles/{id}/scopes | `RowScope`（不含 id） | `RowScope` | — | 写 | 属性必须是“权限依据” | — |
| DELETE | /roles/{id}/scopes/{scopeId} | — | 204 | — | 写 | | — |
| GET | /roles/{id}/members | `kw,page,size` | `PageResult<AdminUser>` | 页码 | 读 | | `roles.ts` `roleMembers`、`userRoles` + `admin.ts` `ADMIN_USERS` |
| POST | /roles/{id}/members | `{userIds[]}` | `{added:n}` | — | 写 | | — |
| DELETE | /roles/{id}/members/{userId} | — | 204 | — | 写 | | — |
| GET | /users/{id}/effective-access | — | `EffectiveAccess`（mock/roles.ts `effectiveAccess`） | — | 读 | 预览抽屉用；角色页草稿中的修改可以作为 body 传入【待确认】 | `roles.ts` `effectiveAccess()`（计算） |
| POST | /preview-sessions | 见 §1.11 | | | 写 | 进入预览 | — |

4. **状态**：defaultDeny 角色中没有配置范围的对象类型显示“未配置范围”（橙）。

### 4.3 审计日志 `/admin/system/audit`

1. **用途**：查看操作流水，用游标分页（数据量大，不显示总数）。
2. **操作流程**：时间范围必选（默认近 24 小时，不能清空）；按动作、结果（成功、失败、拒绝）筛选；可以按操作人或对象搜索（先选搜哪个字段）。分页只有“上一页 / 下一页”，游标写在 `?cursor=`，每页 20 条。可以导出。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /audit-logs | `from,to`（必填），`action,result,field=actor\|target,kw,cursor,size` | `CursorResult<AuditRow>`（mock/admin.ts） | 游标 | 读 | 按 `(time desc, id)` 做 keyset 分页 | `admin.ts` `AUDIT_LOGS` |
| GET | /audit-logs/actions | — | `string[]` | — | 读 | 动作下拉 | `admin.ts` `AUDIT_LOGS`（去重） |
| POST | /audit-logs/export | 同上筛选条件 | `{jobId}` | — | 读 | 导出本身也写一条审计 | — |

### 4.4 API 密钥 `/admin/system/api-keys`

1. **用途**：给外部系统发放 API 密钥。
2. **操作流程**
   1. 列表：按状态、权限范围筛选，按名称、前缀或创建人搜索，分页（每页 18 条）。
   2. 新建放在抽屉里（`?form=create`；带 `&from=AK-xxx` 时复制已有密钥的配置）。字段：名称、环境（生产、测试）、权限范围（多选）、绑定身份（选角色，或者选服务账号再选数据范围，**必填**）、有效期（30、90、180、365 天，或永不过期）、备注。
   3. 生成后，明文密钥**只显示这一次**，带复制按钮。要确认“已保存”才能关闭抽屉。
   4. 详情抽屉（`?key=AK-xxx`）：配置，以及最近调用（分页）。
   5. 吊销：确认卡，不能恢复。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /api-keys | `status,scope,kw,page,size` | `PageResult<ApiKey>`（mock/apikeys.ts，**不含明文**） | 页码 | 读 | | `apikeys.ts` `API_KEYS`（明文不入库，导入时算哈希） |
| GET | /api-keys/meta | — | `{scopes: KEY_SCOPES, serviceAccounts: SERVICE_ACCOUNTS, saScopes: SA_SCOPES, bindRoles: {role,scope}[]}` | — | 读 | 只列出配了行级范围的角色 | `apikeys.ts` `KEY_SCOPES`、`SERVICE_ACCOUNTS`、`SA_SCOPES` |
| GET | /api-keys/{id} | — | `ApiKey` | — | 读 | | `apikeys.ts` `API_KEYS` |
| GET | /api-keys/{id}/calls | `page,size` | `PageResult<KeyCall>` | 页码 | 读 | | `apikeys.ts` `keyCalls()`（导入时生成） |
| POST | /api-keys | `{name, env, scopes[], binding: KeyBinding, lifeDays, note?, ipAllow?}` | `ApiKey` + `secret`（只返回这一次） | — | 写 | 没有 binding 返回 422；数据库只存哈希 | — |
| POST | /api-keys/{id}:revoke | — | `ApiKey`（已吊销，带 `revokedAt`、`revokedBy`） | — | 写 | 不可逆 | — |

4. **状态**：`KeyStatus` 中“即将过期”用灰点加文字，不用橙；状态码非 200 的调用为红。

### 4.5 模型与系统配置 `/admin/system/settings`

1. **用途**：租户信息、大模型连接、通知渠道、安全策略。
2. **操作流程**
   1. 左侧四个分组 `?group=tenant|llm|notify|security`。**所有分组共用一份草稿**，切换分组不会丢失修改。有修改时底部出现保存条：放弃修改，或者保存（确认卡；有校验错误时被阻断）。离开页面要确认。
   2. 租户：名称、简称、默认组织、时区、币种、财年起始月、每周起始日、数据截止时间、联系人。
   3. 大模型：
      - 连接列表。新建或编辑放在抽屉里（`?llm=new|LLM-01`）。密钥只在填写的那一次可见，之后只显示掩码。
      - 测试连接（确认卡），返回延迟和模型版本。
      - 删除连接：如果它是某个用途的默认连接，会被阻断。
      - 每个用途（问数解析、向量化、结果解释）的默认连接。
      - 数据出域策略：只发字段名（schema）、发脱敏样例（masked）、发明细（detail）。选“发明细”要确认，而且只允许私有化部署的连接。还要设置样例行数和脱敏字段。
   4. 通知渠道（站内信、邮件、企业微信、钉钉、短信）：在抽屉里编辑（`?channel=email`），点“确定”后写进草稿，仍然需要保存。发送测试要确认，渠道没启用或者有未确定的修改时被阻断。每个事件选择发送渠道。
   5. 安全：密码长度和规则、过期天数、登录失败锁定、会话时长、MFA、IP 白名单、水印、导出审批、审计保留天数。
3. **接口**

| 方法 | 路径 | 参数 | 返回 | 分页 | 读/写 | 约束 | 种子（src/mock） |
|---|---|---|---|---|---|---|---|
| GET | /settings | — | `SystemSettings`（mock/settings.ts，密钥只返回 `keyMask`、`secret` 掩码）+ `updated: Record<group,string>`（`SETTINGS_UPDATED`） | — | 读 | | `settings.ts` `SYSTEM_SETTINGS`、`SETTINGS_UPDATED` |
| GET | /settings/meta | — | `{notifyEvents: NOTIFY_EVENTS, channelOrder, llmUsages, llmKinds, egressOptions, maskFields}` | — | 读 | | `settings.ts` `NOTIFY_EVENTS`、`CHANNEL_ORDER`、`LLM_USAGES`、`LLM_KINDS`、`EGRESS_OPTIONS`、`MASK_FIELDS` |
| PUT | /settings | `Partial<SystemSettings>`（只传改过的分组）；新填的密钥放在 `llm.connections[].apiKey` 里传 | `SystemSettings` | — | 写 | 校验：明细出域只允许私有化部署；不能删除正被用作默认连接的连接；写审计 | — |
| POST | /settings/llm:test | `LlmConnection` 的参数（可以是还没保存的）+ `apiKey?` | `LlmTest{at,ok,latencyMs,version,msg?}` | — | 写（发起外部请求） | 连接停用时返回 409 | — |
| POST | /settings/channels/{id}:test | — | `{lastTest,lastResult}` | — | 写 | 只能用已保存并且已启用的配置 | — |

- 注意：前端现在的注释写着“只在前端配置，不发网络请求”，测试连接是模拟的。接后台时，测试连接会真的请求外部模型。按安全约束，**用谁的模型 key 必须由用户明确授权**，后台不能内置任何人的 key。

---

## 5. 四份负责人文档和前端代码的差异

### 5.1 static-api.md（请静态本体负责人确认）

1. **字段命名**：static-api.md 用 snake_case（`base_release`、`lock_version`、`release_tag`、`target_kind`），前端 TS 和 mock 全部是 camelCase。建议接口 JSON 用 camelCase，数据库用 snake_case（四份文档的情况见 §5.5）。
2. **枚举值**：static-api.md 状态图里用的是 `draft / in_review / approved / published` 这类英文，前端判断用的是中文 `草稿 / 审核中 / 已通过 / 已发布 / 已驳回`（mock/changes.ts `CRStatus`）。接口需要定一种。v0.2 建议统一用英文 code 加 `label`（§1.1、§5.5）。
3. **分页响应**：static-api.md 是 `{items,total,page,size}`，前端 `PageResult` 只要求 `{items,total}`。两者兼容，本文采用 static-api.md 的超集写法。
4. **版本参数**：前端 URL 用的是 `?ver=`，static-api.md 的接口参数叫 `release`。本文跟 static-api.md，读接口用 `release`，另外用请求头 `X-View-Release` 拒绝写请求（§1.7）。static-api.md 没有说明“非本体页面在历史版本下也是只读”，前端是整站只读。
5. **对象类型页的行为和治理页签**：static-api.md §3.2 只覆盖了定义部分和 usage。生命周期（新增状态、新增、修改、删除迁移都走 CR）、动作汇总、自动化汇总、权限页签的读接口，static-api.md 没有写，本文 §3.5 补了。生命周期的编辑也走 `PUT /change-requests/{id}/items`，`section=lifecycle`【待确认】。
6. **指标“编辑定义”按钮**：前端 Metrics.vue 第 214 行的按钮没有点击处理（只有 disabled 绑定），所以草稿里改公式的界面还没有做；static-api.md 有 V8（改公式要升版本）。后台接口可以先按 CR items 预留。
7. **回滚语义**：前端 Releases.vue 是“生成一个新版本，内容等于上一版本”，和 static-api.md §2.2-6 一致。static-api.md 待定问题 5 提到 v0.1 的“只移动指针”，以前端为准。
8. **V15 迁移方式**：static-api.md 定为阻断，前端 mock 里多为警告（static-api.md 自己也标了）。
9. **函数页**：不在 static-api.md 的 CR 范围内。前端是直接保存成新版本，但 `FnVersion` 有“变更中 + cr”。需要确认函数要不要纳入整版发布。
10. **动作类型、事件类型、规则的 CR**：static-api.md 待定问题 12。前端这三页用的就是同一个 `useDraftView`，本文按“走同一套”来写。
11. **问数反馈转同义词生成的 CR**：前端是 `useDraftView.start` + `changes.ts registerChange`，单号取全局最大号加一（static-api.md 待定问题 11）。后台应该由变更单服务统一分配单号。

### 5.2 dynamic-api.md（cc3f949，请动态本体负责人确认）

1. **告警页签的值**：前端 URL 是 `?tab=doing`，dynamic-api.md 写的是 `processing`。接口用哪个都行，但前后端要统一【待确认】。
2. **转派选人**：`Alerts.vue` 里写死了 `PEOPLE`，没有人员接口。建议用 §4.1 的 `GET /users?kw`，本文 §2.6 的 `/people` 只是过渡。
3. **规则版本的“对比”和“回滚”**：`Rules.vue` 第 469 行附近这两个按钮没有点击处理，dynamic-api.md 却写了 `:rollback`。接口可以先留着，但前端没有入口。
4. **回放样本的分页**：dynamic-api.md 写的是每页 9 条，前端用 `usePagedQuery`，`size` 由前端传。后台按 `page/size` 实现即可。
5. **对账**：返回字段 dynamic-api.md 叫 `reconAs`，前端读的是 `result`；对账和重新提交对操作人没有限制（前端不检查是谁），需要定权限【待确认】。
6. **`:approve`（审批通过）**：前端没有任何页面调用它。
7. **种子目录**：dynamic-api.md §5 写的是 `backend/app/modules/<模块>/seeds/`，按 PDE 决定改为 `backend/seeds/json/<模块>/`（§6）。

### 5.3 action-event-api.md（736fe56，请 Palantir专家确认）

1. **前缀**：文档 §3 写的是 `/api/v1`，按决定改为 `/api/ontology/v1`。
2. **近期事件分页**（它的待定问题 3）：前端用 `usePagedQuery`，默认每页 14 条，显示“‹ 1/N ›”。按决定，**用 `page/size` 并返回 `total`，不用游标**。事件表按天分区，`count(*)` 可能慢，可以限定在 `range`（24 小时或 7 天）内统计，或者返回估算值。
3. **`/event-deliveries`**：前端只在抽屉里一次显示全部投递记录，不分页。
4. **`action_run` 共用一张表**（它的待定问题 5）：`actionEvent.ts` 的 `RUNS`（1849 条）和 `actions.ts` 的 `ACTIONS`（141 条）都要导入。编号段不冲突（2000 段和 0413–0532），但字段不完全一样，需要动态和 Palantir 两位商定由谁的种子脚本写入、字段怎么对齐。
5. **`runDaily()` 和 `eventDaily()`**：这两个按天统计的数字和明细记录对不上（统计是另外随机生成的）。按“和前端显示一致”的原则，单独存一张按天统计的表；以后从明细重新算时，数字会变。
6. **历史版本下的试运行和模拟触发**：前端没有置灰。本文建议当作读接口处理，在历史版本下也允许；可是该文档 §3.3 写的是一律返回 `READONLY_HISTORY`【待确认】。
7. **字段命名**：用的是 snake_case（`target_api`、`idem_key_rule`），和前端 camelCase 不一致（§5.5）。
8. **种子目录**：`backend/app/modules/action|event/seeds/` 改为 `backend/seeds/json/ontology/`（§6）。

### 5.4 qa-api.md（1782512，请问数负责人确认）

1. **分页参数**：用 `pageSize`，响应是 `{items,total,page,pageSize}`；前端 URL 和其他三份文档都用 `size`。按决定改为 `size`。
2. **W3 轮次游标**（`before`/`limit`/`nextCursor`）：前端对话区没有加载更早消息的功能，也没有用 `useCursorQuery`，所以一次返回全部。
3. **发起动作**：X1/X2 写的是 `POST /actions/{actionKey}:preview` 和 `POST /actions/submissions`，dynamic-api.md 是 `POST /action-runs:stage` 加 `/{id}:confirm`。路径和语义（问数确认后直接变成“待审批”）都要和动态对齐。
4. **预览请求头**：qa-api.md 用 `X-Preview-As: <userId>`，本文 v0.1 用的是 `X-Preview-Session`。**v0.2 改用 `X-Preview-As`**（§1.8）。
5. **预览时能不能提问**：工作台在预览时不能提问，对象页的“对它提问”（ObjAskModal）却可以，前端不一致（它的待定问题 6）。
6. **种子缺口**：演示会话的 4 轮回答写死在 `QaConversation.vue`、`QaAnswer.vue`、`QaStoreAnswer.vue` 里；`DIMS`、`TIMES` 在 `QaTuning.vue` 里；同义词关联在 `pages/ontology/qa-tuning/state.ts` 里；`repeat_sales` 这几个指标还不在 `metrics.ts` 里。要把它们搬进 `src/mock` 就得改前端，本次不允许改【待确认谁来搬】。
7. **种子目录和导出脚本**：qa-api.md 写的是 `frontend/scripts/dump-mock.ts` → `backend/app/modules/qa/seeds/data/`，按决定改为 `npm run export-mock` → `backend/seeds/json/qa/`。
8. **对象详情**：放在 `/api/qa/v1/objects/{kind}/{id}`，和公共抽屉的 `/object-views/{kind}/{id}` 重复（§2.5）。

### 5.5 四份文档之间的不一致（请 PDE 统一）

| 项 | static | dynamic | action-event | qa | 本文建议 |
|---|---|---|---|---|---|
| 前缀 | `/api/ontology/v1` | `/api/ontology/v1` | `/api/v1` ✗ | `/api/qa/v1` | `/api/<模块>/v1`（已定） |
| 字段命名 | snake_case | camelCase | snake_case | camelCase | camelCase【待确认】 |
| 枚举 | 英文 | 英文 code + label | 中文 | 英文 code + label | 英文 code + `label`【待确认】 |
| 分页参数 | `page/size` | `page/size` | `page/size`；事件用游标 ✗ | `page/pageSize` ✗ | `page/size` + `total`（已定） |
| 种子目录 | — | `backend/app/modules/<m>/seeds/` ✗ | 同左 ✗ | `backend/app/modules/qa/seeds/data/` ✗ | `backend/seeds/json/<模块>/`（已定） |
| 动作发起 | — | `/action-runs:stage` + `:confirm` | 引用 dynamic | `/actions/{key}:preview`、`/actions/submissions` ✗ | 以 dynamic-api.md 为准【待确认】 |
| 预览请求头 | — | — | — | `X-Preview-As` | `X-Preview-As` |

---

## 6. 种子数据导入（mock → 数据库）

**目标**：后台接好以后，页面显示的数据和现在用 mock 时**完全一样**。本次只写方案，**没有执行任何导出或导入，也没有改仓库**。

1. **导出**：`npm run export-mock`。注意：master 的 `package.json` 里**还没有这个脚本**，需要新增，这算前端仓库的改动，需要用户同意【待确认】。
   - 做法：用 esbuild 或 `npx tsx` 在 Node 里 import `src/mock/*`，把常量导出和计算函数的结果写成 JSON，放到 `backend/seeds/json/<模块>/`（`ontology/`、`qa/`、`data/`、`admin/`）。
   - qa 和 dynamic 负责人都已经验证过可以在 Node 下运行，不依赖浏览器。
   - JSON 文件命名建议用 `<mock 文件>.<导出名>.json`，例如 `ontology/actionEvent.ACTION_TYPES.json`。
2. **导入**：后台提供一个统一的 `seed` 命令，在数据库迁移之后执行。读 JSON，按自然主键 upsert，可以重复执行。
   - 中文枚举在导入时转成 code。
   - 人名按 `admin.ts` `ADMIN_USERS`、`qa-pages.ts` `PEOPLE`、`qa-tuning.ts` `PEOPLE_T` 映射到用户表。
   - 指标显示名映射到 `metric@version`。
3. **确定性**：mock 用 `format.ts` 的 `seeded()` 生成伪随机数，每次结果相同，所以导出是可重现的。不要在后台用 Python 重新实现这些生成逻辑，直接用导出的 JSON。
4. **计算出来的数据**：有些数据是 mock 函数现场算的，导出时要按“全部对象 × 全部参数”调用一遍，把结果作为明细导入：
   - `actionEvent.ts`：`runsOf`、`eventsOf`、`paramsOf`、`condsOf`、`writebackOf`、`subscribersOf`、`versionsOf`
   - `rules.ts` `hitsOf`、`lifecycle.ts` `changesOf`
   - `extsys.ts` `extCalls`、`apikeys.ts` `keyCalls`、`datasources.ts` `dsTables`、`functions.ts` `fnVersions`
   - 下面这些是纯计算，后台要实现成真正的逻辑，**不导入结果**：
     - `scenarios.ts` `simulate` / `stagePlanOf`（已运行方案的结果除外，要导入）
     - `roles.ts` `effectiveAccess`
     - `rules.ts` `replay`
     - `paginate.ts`
5. **时间**：保留 mock 里的原始时间（演示的“今天”是 2026-10-25，数据截至 2026-10-24 23:00），按 Asia/Shanghai 转成带时区的时间戳，**不要平移到导入当天**。导入后把每张表的序列设成“最大编号 + 1”（例如变更单号、动作编号 `A-…`、`EV-…`），保证新建的编号接在 mock 后面。
6. **敏感数据**：`API_KEYS` 只导入前缀、后 4 位和哈希；数据连接密码、渠道密钥用演示占位值加密保存，接口只返回掩码。
7. **每个模块的来源**：

| 模块 | mock 文件 |
|---|---|
| ontology | `links.ts`、`objectProps.ts`、`objectViews.ts`、`objectHistory.ts`、`metrics.ts`、`dimensions.ts`、`knowledge.ts`、`functions.ts`、`changes.ts`、`releases.ts`、`actionEvent.ts`、`actions.ts`、`dynamic.ts`、`rules.ts`、`scenarios.ts`、`lifecycle.ts` |
| qa | `qa.ts`、`qa-pages.ts`、`qa-tuning.ts`、`objects-qa.ts`；业务对象 `stores.ts`、`products.ts`、`orders.ts` |
| data | `datasources.ts`、`extsys.ts`、`quality.ts` |
| admin | `admin.ts`、`roles.ts`、`apikeys.ts`、`settings.ts`；`releases.ts` `NOTICES` |

8. **已知缺口**（导出脚本拿不到，或者数据对不上）：
   - 问数演示轮次写死在 `.vue` 里；`QaTuning.vue` 的 `DIMS`、`TIMES`；`qa-tuning/state.ts` 的同义词；`stores/app.ts` 的 `TENANTS`、`PERSONAS`；`Alerts.vue` 的 `PEOPLE`。这些都不在 `src/mock` 下，要么搬进 mock（需要改前端），要么在 seed 里手写一份【待确认】。
   - `runDaily`、`eventDaily` 和明细对不上（§5.3-5）。
   - `RUNS` 和 `ACTIONS` 合并进同一张 `action_run` 表（§5.3-4）。
   - 业务数据（门店、商品、订单）是演示数据。正式环境应该从数据连接同步过来，种子只用于演示和测试环境。


---

## 7. 接口总表（去重，脚本从各页接口表汇总）

- 各页接口表共 262 行；按“模块 + 方法 + 路径”去重后共 **253 个接口**：`admin` 46、`data` 22、`ontology` 126、`qa` 59；读 151，写 102。
- 不含内部接口（`POST /internal/v1/events`，外部系统适配器的 `submit`/`query_by_key`），也不含 static-api.md 里有、但前端页面没有用到的接口；完整清单以各负责人文档为准。

| # | 模块 | 方法 | 完整路径 | 读/写 | 页面（章节） | 种子（src/mock） |
|---|---|---|---|---|---|---|
| 1 | admin | GET | `/api/admin/v1/api-keys` | 读 | 4.4 | `apikeys.ts` `API_KEYS`（明文不入库，导入时算哈希） |
| 2 | admin | POST | `/api/admin/v1/api-keys` | 写 | 4.4 | — |
| 3 | admin | GET | `/api/admin/v1/api-keys/meta` | 读 | 4.4 | `apikeys.ts` `KEY_SCOPES`、`SERVICE_ACCOUNTS`、`SA_SCOPES` |
| 4 | admin | GET | `/api/admin/v1/api-keys/{id}` | 读 | 4.4 | `apikeys.ts` `API_KEYS` |
| 5 | admin | GET | `/api/admin/v1/api-keys/{id}/calls` | 读 | 4.4 | `apikeys.ts` `keyCalls()`（导入时生成） |
| 6 | admin | POST | `/api/admin/v1/api-keys/{id}:revoke` | 写 | 4.4 | — |
| 7 | admin | GET | `/api/admin/v1/audit-logs` | 读 | 4.3 | `admin.ts` `AUDIT_LOGS` |
| 8 | admin | GET | `/api/admin/v1/audit-logs/actions` | 读 | 4.3 | `admin.ts` `AUDIT_LOGS`（去重） |
| 9 | admin | POST | `/api/admin/v1/audit-logs/export` | 读 | 4.3 | — |
| 10 | admin | GET | `/api/admin/v1/dashboard/kpis` | 读 | 2.1 | `metrics.ts` `METRICS` |
| 11 | admin | GET | `/api/admin/v1/depts` | 读 | 4.1 | `admin.ts` `ADMIN_DEPTS` |
| 12 | admin | POST | `/api/admin/v1/exports` | 写 | 2.1 | — |
| 13 | admin | GET | `/api/admin/v1/me` | 读 | 1.11 | `stores/app.ts` `PERSONAS`（不在 src/mock 下）+ `roles.ts` `effectiveAccess` |
| 14 | admin | GET | `/api/admin/v1/notices` | 读 | 1.11 | `releases.ts` `NOTICES` |
| 15 | admin | POST | `/api/admin/v1/notices/{id}:read` | 写 | 1.11 | — |
| 16 | admin | POST | `/api/admin/v1/notices:read-all` | 写 | 1.11 | — |
| 17 | admin | GET | `/api/admin/v1/orders` | 读 | 2.1 | `orders.ts` `ORDERS` |
| 18 | admin | POST | `/api/admin/v1/preview-sessions` | 写 | 1.11、4.2 | — |
| 19 | admin | DELETE | `/api/admin/v1/preview-sessions/{id}` | 写 | 1.11 | — |
| 20 | admin | GET | `/api/admin/v1/roles` | 读 | 4.2 | `roles.ts` `ROLES` |
| 21 | admin | POST | `/api/admin/v1/roles` | 写 | 4.2 | — |
| 22 | admin | GET | `/api/admin/v1/roles/meta` | 读 | 4.2 | `roles.ts` `FUNC_PAGES`、`PERM_OBJECTS`、`SCOPE_PROPS` |
| 23 | admin | DELETE | `/api/admin/v1/roles/{id}` | 写 | 4.2 | — |
| 24 | admin | GET | `/api/admin/v1/roles/{id}` | 读 | 4.2 | `roles.ts` `ROLES` |
| 25 | admin | PATCH | `/api/admin/v1/roles/{id}` | 写 | 4.2 | — |
| 26 | admin | GET | `/api/admin/v1/roles/{id}/members` | 读 | 4.2 | `roles.ts` `roleMembers`、`userRoles` + `admin.ts` `ADMIN_USERS` |
| 27 | admin | POST | `/api/admin/v1/roles/{id}/members` | 写 | 4.2 | — |
| 28 | admin | DELETE | `/api/admin/v1/roles/{id}/members/{userId}` | 写 | 4.2 | — |
| 29 | admin | PUT | `/api/admin/v1/roles/{id}/permissions` | 写 | 4.2 | — |
| 30 | admin | POST | `/api/admin/v1/roles/{id}/scopes` | 写 | 4.2 | — |
| 31 | admin | DELETE | `/api/admin/v1/roles/{id}/scopes/{scopeId}` | 写 | 4.2 | — |
| 32 | admin | GET | `/api/admin/v1/settings` | 读 | 4.5 | `settings.ts` `SYSTEM_SETTINGS`、`SETTINGS_UPDATED` |
| 33 | admin | PUT | `/api/admin/v1/settings` | 写 | 4.5 | — |
| 34 | admin | POST | `/api/admin/v1/settings/channels/{id}:test` | 写 | 4.5 | — |
| 35 | admin | POST | `/api/admin/v1/settings/llm:test` | 写（发起外部请求） | 4.5 | — |
| 36 | admin | GET | `/api/admin/v1/settings/meta` | 读 | 4.5 | `settings.ts` `NOTIFY_EVENTS`、`CHANNEL_ORDER`、`LLM_USAGES`、`LLM_KINDS`、`EGRESS_OPTIONS`、`MASK_FIELDS` |
| 37 | admin | GET | `/api/admin/v1/stores/ranking` | 读 | 2.1 | 由 `orders.ts` `ORDERS` + `stores.ts` `ALL_STORES` 统计（Home.vue），不单独导入 |
| 38 | admin | GET | `/api/admin/v1/tenants` | 读 | 1.11 | `stores/app.ts` `TENANTS`（不在 src/mock 下） |
| 39 | admin | GET | `/api/admin/v1/users` | 读 | 4.1 | `admin.ts` `ADMIN_USERS`、`pendingApprovals` |
| 40 | admin | POST | `/api/admin/v1/users` | 写 | 4.1 | — |
| 41 | admin | PUT | `/api/admin/v1/users/{id}` | 写 | 4.1 | — |
| 42 | admin | GET | `/api/admin/v1/users/{id}/effective-access` | 读 | 4.2 | `roles.ts` `effectiveAccess()`（计算） |
| 43 | admin | POST | `/api/admin/v1/users/{id}:disable` | 写 | 4.1 | — |
| 44 | admin | POST | `/api/admin/v1/users/{id}:enable` | 写 | 4.1 | — |
| 45 | admin | POST | `/api/admin/v1/users:import` | 写 | 4.1 | — |
| 46 | admin | POST | `/api/admin/v1/users:import-preview` | 读 | 4.1 | — |
| 47 | data | GET | `/api/data/v1/datasources` | 读 | 3.1 | `datasources.ts` `DATASOURCES` |
| 48 | data | POST | `/api/data/v1/datasources` | 写 | 3.1 | — |
| 49 | data | GET | `/api/data/v1/datasources/meta` | 读 | 3.1 | `datasources.ts` `DS_TYPES`、`DS_DEFAULT_PORT`、`DM_COMPAT`、`KB_MODES`、`ERP_SYSTEMS` |
| 50 | data | DELETE | `/api/data/v1/datasources/{id}` | 写 | 3.1 | — |
| 51 | data | GET | `/api/data/v1/datasources/{id}` | 读 | 3.1 | `datasources.ts` `DATASOURCES` |
| 52 | data | PUT | `/api/data/v1/datasources/{id}` | 写 | 3.1 | — |
| 53 | data | GET | `/api/data/v1/datasources/{id}/tables` | 读 | 3.1 | `datasources.ts` `dsTables()` |
| 54 | data | POST | `/api/data/v1/datasources/{id}:test` | 写 | 3.1 | — |
| 55 | data | POST | `/api/data/v1/datasources:test` | 读 | 3.1 | — |
| 56 | data | GET | `/api/data/v1/ext-calls/{callId}` | 读 | 3.2 | `extsys.ts` `extCalls()` |
| 57 | data | POST | `/api/data/v1/ext-calls/{callId}:mark-checked` | 写 | 3.2 | — |
| 58 | data | GET | `/api/data/v1/ext-systems` | 读 | 3.2、3.7 | `extsys.ts` `EXT_SYSTEMS` |
| 59 | data | POST | `/api/data/v1/ext-systems` | 写 | 3.2 | — |
| 60 | data | GET | `/api/data/v1/ext-systems/{id}` | 读 | 3.2 | `extsys.ts` `EXT_SYSTEMS` + `actionEvent.ts` `ACTION_TYPES` |
| 61 | data | PUT | `/api/data/v1/ext-systems/{id}` | 写 | 3.2 | — |
| 62 | data | GET | `/api/data/v1/ext-systems/{id}/calls` | 读 | 3.2 | `extsys.ts` `extCalls()` |
| 63 | data | POST | `/api/data/v1/ext-systems/{id}:test` | 写 | 3.2 | — |
| 64 | data | GET | `/api/data/v1/quality-checks` | 读 | 3.3 | `quality.ts` `QUALITY_CHECKS`、`DATA_CUTOFF` |
| 65 | data | POST | `/api/data/v1/quality-checks` | 写 | 3.3 | — |
| 66 | data | GET | `/api/data/v1/quality-checks/{id}/runs` | 读 | 3.3 | `quality.ts` `QUALITY_CHECKS`（`runs`） |
| 67 | data | POST | `/api/data/v1/quality-checks/{id}:run` | 写 | 3.3 | — |
| 68 | data | POST | `/api/data/v1/quality-checks:run-all` | 写 | 3.3 | — |
| 69 | ontology | GET | `/api/ontology/v1/action-runs` | 读 | 2.7、3.7 | `actions.ts` `ACTIONS` + `actionEvent.ts` `runsOf()`（合并，见 §6） |
| 70 | ontology | GET | `/api/ontology/v1/action-runs/{id}` | 读 | 2.7、3.7 | 同上 |
| 71 | ontology | POST | `/api/ontology/v1/action-runs/{id}:approve` | 写 | 2.7 | — |
| 72 | ontology | POST | `/api/ontology/v1/action-runs/{id}:confirm` | 写 | 2.7 | — |
| 73 | ontology | POST | `/api/ontology/v1/action-runs/{id}:reconcile` | 写 | 2.7 | — |
| 74 | ontology | POST | `/api/ontology/v1/action-runs/{id}:resubmit` | 写 | 2.7 | — |
| 75 | ontology | POST | `/api/ontology/v1/action-runs/{id}:withdraw` | 写 | 2.7 | — |
| 76 | ontology | POST | `/api/ontology/v1/action-runs:stage` | 写 | 2.7 | — |
| 77 | ontology | GET | `/api/ontology/v1/action-types` | 读 | 3.7 | `actionEvent.ts` `ACTION_TYPES`（17）、`ACTION_GROUPS` |
| 78 | ontology | GET | `/api/ontology/v1/action-types/{id}` | 读 | 3.7 | `actionEvent.ts` `ACTION_TYPES`；`RULE_ACTION` |
| 79 | ontology | GET | `/api/ontology/v1/action-types/{id}/params` | 读 | 3.7 | `actionEvent.ts` `paramsOf()`（101） |
| 80 | ontology | GET | `/api/ontology/v1/action-types/{id}/stats` | 读 | 3.7 | `actionEvent.ts` `runStats()` |
| 81 | ontology | GET | `/api/ontology/v1/action-types/{id}/stats/daily` | 读 | 3.7 | `actionEvent.ts` `runDaily()`（原样存入 `action_run_daily_stat`） |
| 82 | ontology | GET | `/api/ontology/v1/action-types/{id}/submission-criteria` | 读 | 3.7 | `actionEvent.ts` `condsOf()`（59） |
| 83 | ontology | GET | `/api/ontology/v1/action-types/{id}/used-by` | 读 | 3.7 | `actionEvent.ts` `usedBy`、`RULE_ACTION`、`RULE_CONFIRMER`；`dynamic.ts` `RULES`、`ALERTS` |
| 84 | ontology | GET | `/api/ontology/v1/action-types/{id}/versions` | 读 | 3.7 | `actionEvent.ts` `versionsOf()`、`ACTION_VERSION_NOTES` |
| 85 | ontology | GET | `/api/ontology/v1/action-types/{id}/writeback` | 读 | 3.7 | `actionEvent.ts` `writebackOf()`（本体编辑 36 条 / 副作用 47 条） |
| 86 | ontology | POST | `/api/ontology/v1/action-types/{id}:dry-run` | 写（不落库） | 3.7 | — |
| 87 | ontology | GET | `/api/ontology/v1/actions/meta` | 读 | 2.7 | `actions.ts` `ACTION_NAMES`、`ACTION_SYSTEMS`、`ACTION_META` |
| 88 | ontology | GET | `/api/ontology/v1/alerts` | 读 | 2.6 | `dynamic.ts` `ALERTS` |
| 89 | ontology | GET | `/api/ontology/v1/alerts/{id}` | 读 | 2.6 | `dynamic.ts` `ALERTS`、`alertTimeline` |
| 90 | ontology | POST | `/api/ontology/v1/alerts/{id}:claim` | 写 | 2.6 | — |
| 91 | ontology | POST | `/api/ontology/v1/alerts/{id}:close` | 写 | 2.6 | — |
| 92 | ontology | POST | `/api/ontology/v1/alerts/{id}:start-action` | 写 | 2.6 | — |
| 93 | ontology | POST | `/api/ontology/v1/alerts/{id}:transfer` | 写 | 2.6 | — |
| 94 | ontology | POST | `/api/ontology/v1/alerts:batch-claim` | 写 | 2.6 | — |
| 95 | ontology | POST | `/api/ontology/v1/alerts:batch-close` | 写 | 2.6 | — |
| 96 | ontology | GET | `/api/ontology/v1/change-requests` | 读 | 3.15 | `changes.ts` `CHANGE_REQUESTS`；`releases.ts` `DRAFTS` |
| 97 | ontology | POST | `/api/ontology/v1/change-requests` | 写 | 3.4、3.16 | — |
| 98 | ontology | DELETE | `/api/ontology/v1/change-requests/{id}` | 写 | 3.15 | — |
| 99 | ontology | GET | `/api/ontology/v1/change-requests/{id}` | 读 | 3.15 | `changes.ts` `CR_DETAILS`、`crDetail`、`reviewLog` |
| 100 | ontology | PUT | `/api/ontology/v1/change-requests/{id}/items` | 写 | 3.7、3.12、3.13、3.16 | — |
| 101 | ontology | POST | `/api/ontology/v1/change-requests/{id}/targets` | 写 | 3.16 | — |
| 102 | ontology | DELETE | `/api/ontology/v1/change-requests/{id}/targets/{kind}/{targetId}` | 写 | 3.16 | — |
| 103 | ontology | POST | `/api/ontology/v1/change-requests/{id}:approve` | 写 | 3.15 | — |
| 104 | ontology | POST | `/api/ontology/v1/change-requests/{id}:reject` | 写 | 3.15 | — |
| 105 | ontology | POST | `/api/ontology/v1/change-requests/{id}:reopen` | 写 | 3.15 | — |
| 106 | ontology | POST | `/api/ontology/v1/change-requests/{id}:submit` | 写 | 3.15 | — |
| 107 | ontology | POST | `/api/ontology/v1/change-requests/{id}:unapprove` | 写 | 3.15 | — |
| 108 | ontology | POST | `/api/ontology/v1/change-requests/{id}:validate` | 写 | 3.15 | — |
| 109 | ontology | POST | `/api/ontology/v1/change-requests/{id}:withdraw` | 写 | 3.15 | — |
| 110 | ontology | GET | `/api/ontology/v1/dimensions` | 读 | 3.10 | `dimensions.ts` `DIM_ITEMS` |
| 111 | ontology | GET | `/api/ontology/v1/dimensions/{id}` | 读 | 3.10 | `dimensions.ts` `DIM_ITEMS` |
| 112 | ontology | GET | `/api/ontology/v1/dimensions/{id}/mappings` | 读 | 3.10 | `dimensions.ts` `DIM_ITEMS`（mappings） |
| 113 | ontology | GET | `/api/ontology/v1/dimensions/{id}/metric-usage` | 读 | 3.10 | `dimensions.ts` `metricUsage` |
| 114 | ontology | GET | `/api/ontology/v1/dimensions/{id}/values` | 读 | 3.10 | `dimensions.ts` `DIM_ITEMS`（values） |
| 115 | ontology | POST | `/api/ontology/v1/dimensions:match` | 读 | 3.10 | — （计算接口，读已导入的数据） |
| 116 | ontology | GET | `/api/ontology/v1/event-deliveries` | 读 | 3.12 | 由 `eventsOf()` 的 `outcome`/`subscribers` 推出来【推断】 |
| 117 | ontology | GET | `/api/ontology/v1/event-types` | 读 | 3.12 | `actionEvent.ts` `EVENT_TYPES`（15）、`EVENT_SOURCES` |
| 118 | ontology | GET | `/api/ontology/v1/event-types/{id}` | 读 | 3.12 | `actionEvent.ts` `EVENT_TYPES` |
| 119 | ontology | GET | `/api/ontology/v1/event-types/{id}/events` | 读 | 3.12 | `actionEvent.ts` `eventsOf()`（`EVENTS` 1243） |
| 120 | ontology | GET | `/api/ontology/v1/event-types/{id}/payload-fields` | 读 | 3.12 | `actionEvent.ts` `ENVELOPE`（10）+ `EventType.specific` |
| 121 | ontology | GET | `/api/ontology/v1/event-types/{id}/stats` | 读 | 3.12 | `actionEvent.ts` `eventDaily()`、`EventType.weekly`；健康指标按 `eventsOf()` 计算 |
| 122 | ontology | GET | `/api/ontology/v1/event-types/{id}/subscribers` | 读 | 3.12 | `actionEvent.ts` `subscribersOf()`（37） |
| 123 | ontology | GET | `/api/ontology/v1/event-types/{id}/versions` | 读 | 3.12 | `actionEvent.ts` `versionsOf()`、`EVENT_VERSION_NOTES` |
| 124 | ontology | POST | `/api/ontology/v1/event-types/{id}:simulate` | 写（测试通道） | 3.12 | — |
| 125 | ontology | GET | `/api/ontology/v1/events/{id}` | 读 | 3.12 | `actionEvent.ts` `eventById()`；`actionEventResolvers.ts` `event()` |
| 126 | ontology | GET | `/api/ontology/v1/functions` | 读 | 3.8 | `functions.ts` `FUNCTIONS`、`FN_PENDING` |
| 127 | ontology | POST | `/api/ontology/v1/functions` | 写 | 3.8 | — |
| 128 | ontology | GET | `/api/ontology/v1/functions/{id}` | 读 | 3.8 | `functions.ts` `FUNCTIONS`、`fnSignature` |
| 129 | ontology | PUT | `/api/ontology/v1/functions/{id}` | 写 | 3.8 | — |
| 130 | ontology | GET | `/api/ontology/v1/functions/{id}/versions` | 读 | 3.8 | `functions.ts` `fnVersions()` |
| 131 | ontology | POST | `/api/ontology/v1/functions/{id}:rollback` | 写 | 3.8 | — |
| 132 | ontology | POST | `/api/ontology/v1/functions/{id}:test` | 写（沙箱执行） | 3.8 | — |
| 133 | ontology | GET | `/api/ontology/v1/knowledge` | 读 | 3.11 | `knowledge.ts` `KNOWLEDGE`、`K_TYPES` |
| 134 | ontology | GET | `/api/ontology/v1/knowledge/synonym-conflicts` | 读 | 3.11 | `knowledge.ts` `synonymConflicts`、`CONFLICT_RULE` |
| 135 | ontology | GET | `/api/ontology/v1/knowledge/{id}` | 读 | 3.11 | `knowledge.ts` `KNOWLEDGE` |
| 136 | ontology | GET | `/api/ontology/v1/knowledge/{id}/refs` | 读 | 3.11 | `knowledge.ts` `KNOWLEDGE`（refs） |
| 137 | ontology | POST | `/api/ontology/v1/knowledge:resolve` | 读 | 3.11 | — （计算接口，读已导入的数据） |
| 138 | ontology | GET | `/api/ontology/v1/link-types` | 读 | 3.6 | `links.ts` `LINKS` |
| 139 | ontology | GET | `/api/ontology/v1/link-types/paths` | 读 | 3.6 | `links.ts` `PATH_GROUPS` |
| 140 | ontology | GET | `/api/ontology/v1/link-types/{id}` | 读 | 3.6 | `links.ts` `LINKS` |
| 141 | ontology | GET | `/api/ontology/v1/link-types/{id}/quality` | 读 | 3.6 | `links.ts` `LINKS`（quality） |
| 142 | ontology | GET | `/api/ontology/v1/link-types/{id}/usage` | 读 | 3.6 | `links.ts` `LINKS`（usage） |
| 143 | ontology | GET | `/api/ontology/v1/metrics` | 读 | 3.9 | `metrics.ts` `METRICS` |
| 144 | ontology | GET | `/api/ontology/v1/metrics/{id}` | 读 | 3.9 | `metrics.ts` `METRICS` |
| 145 | ontology | GET | `/api/ontology/v1/metrics/{id}/daily` | 读 | 2.1 | `metrics.ts` `DAILY_GMV`、`DAILY_GMV_EX` |
| 146 | ontology | GET | `/api/ontology/v1/metrics/{id}/refs` | 读 | 3.9 | 见 static-api.md 种子表 |
| 147 | ontology | GET | `/api/ontology/v1/metrics/{id}/versions` | 读 | 3.9 | 见 static-api.md 种子表 |
| 148 | ontology | POST | `/api/ontology/v1/metrics/{id}:trial` | 读（不落库） | 3.9 | — （计算接口，读已导入的数据） |
| 149 | ontology | GET | `/api/ontology/v1/object-types` | 读 | 3.5 | `links.ts` `LINK_OBJECTS`（以 static-api.md 种子表为准） |
| 150 | ontology | GET | `/api/ontology/v1/object-types/brief` | 读 | 3.3 | `quality.ts` `Q_OBJECT_TYPES` |
| 151 | ontology | GET | `/api/ontology/v1/object-types/{id}` | 读 | 3.5 | `objectProps.ts` `objectMeta()` + `quality.ts` `Q_OBJECT_TYPES` |
| 152 | ontology | GET | `/api/ontology/v1/object-types/{id}/action-runs` | 读 | 3.5 | 同 `/action-runs` |
| 153 | ontology | GET | `/api/ontology/v1/object-types/{id}/action-types` | 读 | 3.5 | `actionEvent.ts` `ACTION_TYPES` |
| 154 | ontology | GET | `/api/ontology/v1/object-types/{id}/event-types` | 读 | 3.5 | `actionEvent.ts` `EVENT_TYPES` |
| 155 | ontology | GET | `/api/ontology/v1/object-types/{id}/lifecycle` | 读 | 3.5 | `lifecycle.ts` `LIFECYCLES`、`LC_DRAFT_EDITS` |
| 156 | ontology | GET | `/api/ontology/v1/object-types/{id}/lifecycle/changes` | 读 | 3.5 | `lifecycle.ts` `changesOf()`（计算，导入时生成） |
| 157 | ontology | GET | `/api/ontology/v1/object-types/{id}/lifecycle/illegal` | 读 | 3.5 | `lifecycle.ts` `illegalOf()`（计算） |
| 158 | ontology | GET | `/api/ontology/v1/object-types/{id}/links` | 读 | 3.5 | `links.ts` `LINKS` |
| 159 | ontology | GET | `/api/ontology/v1/object-types/{id}/mappings` | 读 | 3.5 | `objectProps.ts` `OBJECT_PROPS`（映射列） |
| 160 | ontology | POST | `/api/ontology/v1/object-types/{id}/mappings:refresh-metadata` | 写 | 3.5 | — |
| 161 | ontology | GET | `/api/ontology/v1/object-types/{id}/permissions` | 读 | 3.5 | `roles.ts` `ROLES`、`roleMembers` |
| 162 | ontology | GET | `/api/ontology/v1/object-types/{id}/properties` | 读 | 3.5 | `objectProps.ts` `OBJECT_PROPS`、`draftPropDiff` |
| 163 | ontology | GET | `/api/ontology/v1/object-types/{id}/rule-hits` | 读 | 3.5 | `rules.ts` `hitsOf()`（计算，导入时生成） |
| 164 | ontology | GET | `/api/ontology/v1/object-types/{id}/rules` | 读 | 3.5 | `rules.ts` `RULE_DEFS` |
| 165 | ontology | GET | `/api/ontology/v1/object-types/{id}/usage` | 读 | 3.5 | `metrics.ts` `METRICS`、`objectViews.ts` `viewsOf()`、`objectHistory.ts` `legacyRevs`、`changes.ts` |
| 166 | ontology | GET | `/api/ontology/v1/object-views/{kind}/{id}` | 读 | 1.11 | `components/objectRegistry.ts` 的解析器 → 数据来自 `stores.ts`/`products.ts`/`orders.ts`/`actions.ts`/`dynamic.ts`/`actionEvent.ts`（不单独导入） |
| 167 | ontology | GET | `/api/ontology/v1/ontology/graph` | 读 | 3.4 | `links.ts` `LINK_OBJECTS`、`LINKS` |
| 168 | ontology | GET | `/api/ontology/v1/ontology/overview` | 读 | 3.4 | 由各本体表统计（Overview.vue），不单独导入 |
| 169 | ontology | GET | `/api/ontology/v1/ontology/todos` | 读 | 3.4 | `changes.ts` `CHANGE_REQUESTS` + `dynamic.ts` `ALERTS` 等统计【推断】 |
| 170 | ontology | GET | `/api/ontology/v1/people` | 读 | 2.6 | `Alerts.vue` `PEOPLE`（不在 src/mock 下）→ 用户表 `admin.ts` `ADMIN_USERS` |
| 171 | ontology | GET | `/api/ontology/v1/releases` | 读 | 1.11、3.4 | `releases.ts` `RELEASES` |
| 172 | ontology | POST | `/api/ontology/v1/releases` | 写 | 3.15 | — |
| 173 | ontology | POST | `/api/ontology/v1/releases/{ver}:rollback` | 写 | 3.15 | — |
| 174 | ontology | POST | `/api/ontology/v1/releases:merge-check` | 读 | 3.15 | — |
| 175 | ontology | GET | `/api/ontology/v1/rule-hits/{id}` | 读 | 3.13 | 同上 + `dynamic.ts` `ALERTS` |
| 176 | ontology | GET | `/api/ontology/v1/rules` | 读 | 3.13 | `rules.ts` `RULE_DEFS`、`RULE_DRAFTS` |
| 177 | ontology | GET | `/api/ontology/v1/rules/options` | 读 | 3.13 | `rules.ts` `CONFIRMERS`、`MAPPING_SOURCES` |
| 178 | ontology | GET | `/api/ontology/v1/rules/refs` | 读 | 2.6 | `dynamic.ts` `RULES` |
| 179 | ontology | GET | `/api/ontology/v1/rules/{id}` | 读 | 3.13 | `rules.ts` `RULE_DEFS` |
| 180 | ontology | GET | `/api/ontology/v1/rules/{id}/hits` | 读 | 3.13 | `rules.ts` `hitsOf()`（导入时生成） |
| 181 | ontology | GET | `/api/ontology/v1/rules/{id}/versions` | 读 | 3.13 | `rules.ts` `ruleVersions` |
| 182 | ontology | POST | `/api/ontology/v1/rules/{id}:replay` | 写（不落库） | 3.13 | — |
| 183 | ontology | POST | `/api/ontology/v1/rules/{id}:rollback` | 写 | 3.13 | — |
| 184 | ontology | GET | `/api/ontology/v1/scenarios` | 读 | 2.8 | `scenarios.ts` `SCENARIOS` |
| 185 | ontology | POST | `/api/ontology/v1/scenarios` | 写 | 2.8 | — |
| 186 | ontology | GET | `/api/ontology/v1/scenarios/options` | 读 | 2.8 | `scenarios.ts` `SC_AREAS`、`SC_CATEGORIES`、`PROMO_TYPES`、`BASE_DRIVERS` |
| 187 | ontology | GET | `/api/ontology/v1/scenarios/{id}` | 读 | 2.8 | `scenarios.ts` `SCENARIOS` |
| 188 | ontology | PATCH | `/api/ontology/v1/scenarios/{id}` | 写 | 2.8 | — |
| 189 | ontology | GET | `/api/ontology/v1/scenarios/{id}/impacts` | 读 | 2.8 | 同上 |
| 190 | ontology | GET | `/api/ontology/v1/scenarios/{id}/result` | 读 | 2.8 | `scenarios.ts` `simulate()` 的计算结果（已运行的方案在导入时算好存入） |
| 191 | ontology | GET | `/api/ontology/v1/scenarios/{id}/stage-plan` | 读 | 2.8 | `scenarios.ts` `stagePlanOf()`（计算） |
| 192 | ontology | POST | `/api/ontology/v1/scenarios/{id}:run` | 写 | 2.8 | — |
| 193 | ontology | POST | `/api/ontology/v1/scenarios/{id}:stage` | 写 | 2.8 | — |
| 194 | ontology | GET | `/api/ontology/v1/scenarios:compare` | 读 | 2.8 | 同上 |
| 195 | qa | GET | `/api/qa/v1/favorites` | 读 | 2.2 | `qa.ts` `QA_FAVORITE_LIST`（18） |
| 196 | qa | POST | `/api/qa/v1/favorites` | 写 | 2.2 | — |
| 197 | qa | DELETE | `/api/qa/v1/favorites/{id}` | 写 | 2.2 | — |
| 198 | qa | GET | `/api/qa/v1/objects/types` | 读 | 2.4 | `objects-qa.ts` `OBJ_TYPES`、`FIELD_GROUPS`、`KIND_LABEL`、`OBJ_SCOPE` |
| 199 | qa | POST | `/api/qa/v1/objects/{kind}/export` | 读 | 2.4 | 同上 |
| 200 | qa | POST | `/api/qa/v1/objects/{kind}/search` | 读 | 2.4 | 业务数据：`stores.ts` `ALL_STORES` + `objects-qa.ts` `OBJ_EXTRA_STORES`/`storeSales`（门店 95）、`products.ts` `PRODUCTS`（12）、`orders.ts` `ORDERS`（1286） |
| 201 | qa | GET | `/api/qa/v1/objects/{kind}/{id}` | 读 | 2.5 | `objects-qa.ts`（`FIELD_GROUPS`、`caliberOf`）+ `stores.ts`/`products.ts`/`orders.ts`；问数工作台右栏用 `qa.ts` `REGION_EC` |
| 202 | qa | GET | `/api/qa/v1/objects/{kind}/{id}/action-records` | 读 | 2.5 | `objects-qa.ts` `actionRecords`（写入动态模块的 `action_run`） |
| 203 | qa | GET | `/api/qa/v1/objects/{kind}/{id}/actions` | 读 | 2.5 | `objects-qa.ts` `objActions` |
| 204 | qa | GET | `/api/qa/v1/objects/{kind}/{id}/related` | 读 | 2.5 | `objects-qa.ts` `relationGroups`；`qa.ts` `QA_REGION_RELATED`（38） |
| 205 | qa | GET | `/api/qa/v1/objects/{kind}/{id}/timeline` | 读 | 2.5 | `objects-qa.ts` `extraTimeline`；`qa.ts` `QA_TIMELINE`、`QA_REGION_TIMELINE` |
| 206 | qa | GET | `/api/qa/v1/pages` | 读 | 2.3 | `qa-pages.ts` `MY_PAGES`、`OTHER_PAGES`、`CARD_LIB`（27+5 个页面 / 197 张卡片） |
| 207 | qa | POST | `/api/qa/v1/pages` | 写 | 2.3 | — |
| 208 | qa | DELETE | `/api/qa/v1/pages/{pid}` | 写 | 2.3 | — |
| 209 | qa | GET | `/api/qa/v1/pages/{pid}` | 读 | 2.3 | 同 GET /pages |
| 210 | qa | PATCH | `/api/qa/v1/pages/{pid}` | 写 | 2.3 | — |
| 211 | qa | POST | `/api/qa/v1/pages/{pid}/cards` | 写 | 2.3 | — |
| 212 | qa | PUT | `/api/qa/v1/pages/{pid}/cards` | 写 | 2.3 | — |
| 213 | qa | POST | `/api/qa/v1/pages/{pid}/refresh` | 写 | 2.3 | — |
| 214 | qa | PUT | `/api/qa/v1/pages/{pid}/schedule` | 写 | 2.3 | — |
| 215 | qa | PUT | `/api/qa/v1/pages/{pid}/share` | 写 | 2.3 | — |
| 216 | qa | POST | `/api/qa/v1/permission-requests` | 写 | 2.2 | `qa.ts` `QA_APPLY`（只是预填模板，0 条） |
| 217 | qa | POST | `/api/qa/v1/scoped-ask` | 写（生成回答） | 2.4 | `objects-qa.ts`（`AskAnswer` 由函数生成） |
| 218 | qa | GET | `/api/qa/v1/session-options` | 读 | 2.2 | `qa.ts` `QA_SCOPE_OPTIONS`、`QA_CALIBER_OPTIONS`、`QA_METRIC` |
| 219 | qa | GET | `/api/qa/v1/sessions` | 读 | 2.2 | `qa.ts` `QA_SESSION_LIST`（103） |
| 220 | qa | POST | `/api/qa/v1/sessions` | 写 | 2.2 | — |
| 221 | qa | DELETE | `/api/qa/v1/sessions/{sid}` | 写 | 2.2 | — |
| 222 | qa | GET | `/api/qa/v1/sessions/{sid}` | 读 | 2.2 | 现在写死在 `QaConversation.vue` / `QaAnswer.vue` / `QaStoreAnswer.vue`；数据部分来自 `qa.ts` `QA_PROVINCES/QA_TREND/QA_TOTAL_*/QA_EC_STORES`（见差异） |
| 223 | qa | PATCH | `/api/qa/v1/sessions/{sid}` | 写 | 2.2 | — |
| 224 | qa | POST | `/api/qa/v1/sessions/{sid}/turns` | 写 | 2.2 | — |
| 225 | qa | GET | `/api/qa/v1/templates` | 读 | 2.3 | `qa-pages.ts` `PAGE_TEMPLATES`（22 个模板 / 104 张卡片） |
| 226 | qa | GET | `/api/qa/v1/templates/{id}` | 读 | 2.3 | 同上 |
| 227 | qa | GET | `/api/qa/v1/tuning/certs` | 读 | 3.14 | `qa-tuning.ts` `CERT_QAS`（30） |
| 228 | qa | POST | `/api/qa/v1/tuning/certs` | 写 | 3.14 | — |
| 229 | qa | GET | `/api/qa/v1/tuning/certs/{id}` | 读 | 3.14 | 同上 |
| 230 | qa | PATCH | `/api/qa/v1/tuning/certs/{id}` | 写 | 3.14 | — |
| 231 | qa | PUT | `/api/qa/v1/tuning/certs/{id}/variants` | 写 | 3.14 | — |
| 232 | qa | POST | `/api/qa/v1/tuning/certs/{id}:certify` | 写 | 3.14 | — |
| 233 | qa | GET | `/api/qa/v1/tuning/eval/questions` | 读 | 3.14 | `qa-tuning.ts` `EVAL_QS`（40）、`EVAL_GROUPS` |
| 234 | qa | GET | `/api/qa/v1/tuning/eval/runs` | 读 | 3.14 | `qa-tuning.ts` `EVAL_RUNS`（21） |
| 235 | qa | POST | `/api/qa/v1/tuning/eval/runs` | 写 | 3.14 | — |
| 236 | qa | GET | `/api/qa/v1/tuning/eval/runs/{id}` | 读 | 3.14 | `qa-tuning.ts` `EVAL_RUNS`（失败用例 222）、`REPLAY_HINT` |
| 237 | qa | GET | `/api/qa/v1/tuning/feedbacks` | 读 | 3.14 | `qa-tuning.ts` `FEEDBACKS`（26） |
| 238 | qa | GET | `/api/qa/v1/tuning/feedbacks/{id}` | 读 | 3.14 | 同上 + `state.ts` `tuning.synonyms` |
| 239 | qa | POST | `/api/qa/v1/tuning/feedbacks/{id}:ignore` | 写 | 3.14 | — |
| 240 | qa | POST | `/api/qa/v1/tuning/feedbacks/{id}:reopen` | 写 | 3.14 | — |
| 241 | qa | POST | `/api/qa/v1/tuning/feedbacks/{id}:to-cert` | 写 | 3.14 | — |
| 242 | qa | POST | `/api/qa/v1/tuning/feedbacks/{id}:to-synonym` | 写 | 3.14 | — |
| 243 | qa | GET | `/api/qa/v1/tuning/freq` | 读 | 3.14 | `qa-tuning.ts` `FREQ_QS`（32） |
| 244 | qa | POST | `/api/qa/v1/tuning/freq/{id}:certify` | 写 | 3.14 | — |
| 245 | qa | GET | `/api/qa/v1/tuning/plan-options` | 读 | 3.14 | `qa-tuning.ts` `T_METRICS`；`DIMS`、`TIMES` 现在写在 `QaTuning.vue` 里 |
| 246 | qa | GET | `/api/qa/v1/tuning/summary` | 读 | 3.14 | 由下面几组数据统计出来 |
| 247 | qa | GET | `/api/qa/v1/tuning/synonyms` | 读 | 3.14 | `pages/ontology/qa-tuning/state.ts` `tuning.synonyms`（2 条，**不在 src/mock 下**） |
| 248 | qa | POST | `/api/qa/v1/turns/{tid}/cancel` | 写 | 2.2 | — |
| 249 | qa | GET | `/api/qa/v1/turns/{tid}/export` | 读 | 2.2 | — |
| 250 | qa | POST | `/api/qa/v1/turns/{tid}/recalc` | 写 | 2.2 | — |
| 251 | qa | GET | `/api/qa/v1/turns/{tid}/result` | 读 | 2.2 | `qa.ts` `QA_EC_STORES`（38） |
| 252 | qa | GET | `/api/qa/v1/turns/{tid}/stream` | 读 | 2.2 | — |
| 253 | qa | PUT | `/api/qa/v1/turns/{tid}/vote` | 写 | 2.2 | — |

---

## 8. 待确认问题汇总

**需要 PDE 或用户决定**
1. 全站字段命名统一成 camelCase 还是 snake_case（§1.1、§5.5）。
2. 枚举统一成英文 code + `label`，还是继续用中文（§1.1）。
3. `data`、`admin` 这两个模块名；首页看板是否单独成 `biz` 模块（§1.1）。
4. 新增 `npm run export-mock` 脚本属于前端仓库改动，需要用户同意；不在 `src/mock` 下的数据（问数演示轮次、`DIMS`/`TIMES`、`state.ts` 同义词、`TENANTS`/`PERSONAS`、`Alerts.vue` 的 `PEOPLE`）由谁搬进 mock（§6-8）。
5. 登录方式（SSO / 账号密码 / MFA）和租户请求头 `X-Tenant-Id`（§1.1、§1.4）。
6. 只读模式下被拒绝的写请求要不要记审计（§1.5）。

**需要负责人之间对齐**
7. 发起动作用哪套接口：qa-api.md X1/X2 还是 dynamic-api.md `/action-runs:stage` + `:confirm`；问数确认后直接“待审批”是否合理（§5.4-3）。
8. `action_run` 由谁写种子，`RUNS` 和 `ACTIONS` 字段怎么对齐（§5.3-4）。
9. 对象详情 O5 和公共抽屉 `/object-views` 是否合并（§2.5）。
10. 预览时能不能提问：工作台和对象页要统一（§5.4-5）。
11. 历史版本下试运行和模拟触发是否允许（§5.3-6）。
12. 告警页签用 `doing` 还是 `processing`；对账和重新提交的操作人权限（§5.2）。
13. 函数是否纳入变更单和整版发布（§5.1-9）；生命周期编辑是否走 `section=lifecycle`（§5.1-5）。
14. 按天统计（`runDaily`/`eventDaily`）和明细对不上，是保留 mock 原样，还是从明细重算（§5.3-5）。

**前端还没有的功能（接口先预留或不做）**
15. 下载中心（`POST /exports`）、问数“停止生成”（W8）、“加入常用问题”（W13）、规则版本“对比”和“回滚”、指标“编辑定义”、动作审批 `:approve`。
16. 数据连接被引用时能否删除；角色还有成员时能否删除；停用用户时是否必须转交待办（§3.1、§4.1、§4.2）。
17. 函数测试的沙箱方案（§3.8）；外部系统认证密钥的掩码规则（§3.2）。
18. 本体图谱节点坐标要不要存（§3.4）。

各负责人文档自己的待定问题（static 若干、dynamic §7 共 18 条、action-event §8 共 11 条、qa §11 共 11 条）仍以原文档为准。上面只列了和本文、前端代码或其他文档有交叉的。
