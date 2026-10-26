# 前后端联调（第 1、2 步）

> 分支 `integ`，起点 `5b8fc21`，现已变基到共享 master `07c4c00`。后台骨架就是共享 master `07c4c00` 里的 `backend/`。接口约定见 [api-index.md](./api-index.md)。
> 结论：**默认 mock，界面和数据完全不变**；切到 real 后，第一条竖切「发布记录 / 当前版本」走真接口，订单只做了调用和适配，没接页面。

## 1. 怎么把两边跑起来

### 1.1 后台骨架（本机）

```bash
# 骨架就是仓库里的 backend/（共享 master 07c4c00）；在 integ 工作区里直接用（backend/.venv 已在 .gitignore）
cd backend && uv sync

# 用自己的临时库（本机 PG 5440，账号 rengu/rengu），不要连外部库
PGPASSWORD=rengu psql -h 127.0.0.1 -p 5440 -U rengu -d postgres -c "CREATE DATABASE rengu_integ_xxx"
export RENGU_DATABASE_URL=postgresql+asyncpg://rengu:rengu@127.0.0.1:5440/rengu_integ_xxx
uv run alembic upgrade head            # 到 0003
uv run python -m app.cli seed          # 默认 seed，不报错
# 07c4c00 还没有模块登记快照 section：seed 的 versioning 一步记一行「跳过：还没有模块登记快照 section…」，
# 不写 ontology_release。所以默认 seed 后 GET /releases = {"items":[],"current":null}，
# /releases/current = {"current":null,"next":"v2026.10.1","release":null}——前端按「发布记录为空」处理（见 §2）。
RENGU_DEV_USER=U010001 uv run uvicorn app.main:app --host 127.0.0.1 --port 8010
```

用完：停掉 uvicorn，`DROP DATABASE rengu_integ_xxx`。

### 1.2 前端

```bash
npm run dev                                                  # mock（默认），和以前一样
VITE_API_MODE=real VITE_API_PROXY_TARGET=http://127.0.0.1:8010 npm run dev   # 真接口
VITE_API_REAL_MODULES=versioning VITE_API_PROXY_TARGET=http://127.0.0.1:8010 npm run dev  # 只切发布记录（versioning）
```

5280 被占用时加 `-- --port 5290`。变量也可以写进 `.env.local`（已加入 .gitignore），参考 `.env.example`。

## 2. 开关

| 变量 | 取值 | 说明 |
|---|---|---|
| `VITE_API_MODE` | `mock`（默认）/ `real` | mock：全部走 `src/mock`，**不发任何 /api 请求**，启动流程和以前一样同步挂载。real：已接线模块全部走真接口 |
| `VITE_API_REAL_MODULES` | 逗号分隔的**开关键**（见下表），例如 `versioning,biz` | **照字面执行**：不设或留空 → mock 模式全 mock、real 模式全部真接口；设了非空值 → 两种模式都只切列出的键，全是未知键时一个都不切（不会退回“全部”）。未知键（例如旧写法 `ontology`、`event`）忽略并在控制台 warn |
| `VITE_API_PROXY_TARGET` | URL，默认 `http://127.0.0.1:8000` | 只给开发服务器用：`/api` → 这个地址（vite.config.ts `server.proxy`） |

**开关键 ≠ 接口前缀。** 开关按负责的后台模块切；地址仍按 api-index §1.1 的前缀拼 `/api/<前缀>/v1`。static / dynamic / action / versioning 同在 `/api/ontology/v1` 下，但各自独立切换。

| 开关键 | 负责 | 接口前缀 | 已接线的接口文件 |
|---|---|---|---|
| `static` | 静态本体（对象类型、关系、指标、维度、业务知识…） | `ontology` | — |
| `dynamic` | 动态本体（规则、告警），以及动作执行记录 `/action-runs/…` | `ontology` | — |
| `action` | 动作类型、事件类型、事件、投递（`/action-types/…`、`/event-types/…`、`/events/…`），含 `GET /action-types/{id}/writeback`；**没有单独的 `event` 键** | `ontology` | — |
| `versioning` | 发布记录、快照 | `ontology` | `releases.ts` |
| `qa` | 问数 | `qa` | — |
| `biz` | 演示业务数据 | `biz` | `biz.ts`（未接页面） |
| `admin` | 系统管理 | `admin` | — |
| `data` | 数据接入 | `data` | — |
| `integration` | 外部系统 `/ext-systems`（动作类型写回页的外部系统下拉用 `/ext-systems?writeback=1`）、`/writeback-calls/{id}:mark-checked`；integration 模块在 `ontology` 前缀下**没有**路由 | `data` | — |

action / dynamic / integration 三行的归属已由 Palantir专家确认（action-event-api.md:140、:168）：动作、事件相关接口都在 `/api/ontology/v1` 下，归 `action`；`/action-runs` 归 `dynamic`；外部系统挂 `/api/data/v1`，归 `integration`。

每个接口文件用 `apiClient(开关键)` 声明归属，前缀自动取 `SWITCH_PREFIX[开关键]`，例如 `releases.ts`：`apiClient('versioning')` → `/api/ontology/v1`，`biz.ts`：`apiClient('biz')` → `/api/biz/v1`。开关由客户端自己把关：开关没切到真接口时 `get/post` 不发请求，直接抛 `ApiError(0, 'MOCK_ONLY')`。`http.ts` 不再导出裸的 `http` / `request`，新加接口文件只能经 `apiClient`。

判断入口：`src/api/config.ts` 的 `isReal(key)` / `anyReal()`、`SWITCH_PREFIX`（开关键 → 前缀）。构建时写死（`import.meta.env`），换模式要重启 dev / 重新 build。

real 模式下启动数据（目前只有发布记录）的三种回退，都保留 mock 的发布记录、不改 `app.ontologyVersion`，页面挂载后用 `message.warning` 提示：

| 情况 | 判断 | 提示 |
|---|---|---|
| 发布记录为空 | `/releases` 的 `items` 为空，或 `current` 为 null（默认 seed 就是这样） | 发布记录为空，暂时显示演示数据（控制台 `console.warn`） |
| 后台未启动 | fetch 失败（`NETWORK_ERROR`），或 Vite 代理连不上后台返回的非 JSON 500/502/503/504（`HTTP_5xx`）；后台自己的 5xx 带统一错误体，不算 | 后台未启动，发布记录暂时显示演示数据（控制台 `console.error`） |
| 其他失败 | 其余 `ApiError` | 发布记录接口加载失败，暂时显示演示数据（`<code>`） |

有模块走真接口时取消 `message` 的 `maxCount: 1`，多条启动提示不会互相顶掉；全 mock 时仍是 `maxCount: 1`，和以前一样。mock 模式不走这段，不发请求，界面不变。

模板里取 `RELEASES` 首 / 末元素的地方都做了空保护（`HistoryTab.vue` 的 `RELEASES[RELEASES.length - 1]?.ver`；tsconfig 是 ES2020，没有 `.at()`）。

## 3. 接口层（src/api）

| 文件 | 内容 |
|---|---|
| `http.ts` | fetch 封装 + `apiClient(开关键)`（前缀取 `SWITCH_PREFIX`，开关没切真接口时拒绝请求）+ `isBackendDown(e)`。地址 `/api/<前缀>/v1<path>`；请求头 `X-Demo-As`；只读模式自动带 `?ver=` / `?previewAs=`；错误体 `{code,message,details,requestId}` → `ApiError`（`status/code/details/requestId`，`readonlyMode` 取 403 READONLY 的 `details.mode`）；连不上后台 → `ApiError(0,'NETWORK_ERROR')`，非 JSON 错误 → `HTTP_<status>`；`PageResult<T>` = `{items,total,page,size}` |
| `config.ts` | 开关解析（开关键、`SWITCH_PREFIX`） |
| `index.ts` | `setupApi(pinia)`：注册请求上下文（演示身份 admin→`U010001`，viewer→`U010006`；`app.viewVersion` → `ver`；预览会话 `session.user.id` → `previewAs`）。`bootstrapApi(pinia)`：real 模式下挂载前先按 `?as=` 定身份，再拉启动数据；不 reject，返回提示文字（为空 / 后台未启动 / 失败） |
| `releases.ts` | 开关 `versioning`。发布记录数据源 + 适配（见 §4） |
| `biz.ts` | 开关 `biz`。订单列表 / 详情调用 + 适配，未接页面（见 §5） |

请求上下文不直接引用 store（避免 store ↔ api 循环引用），由 `setupApi` 注入。

## 4. 现在哪些是真接口

骨架（master 07c4c00 的 backend/）实际实现的接口：

| 接口 | 前端 | 状态 |
|---|---|---|
| `GET /api/ontology/v1/releases` | `RELEASES`（顶栏版本选择、本体总览历史、对象「修订记录」、生命周期历史时间） | **已接**，开关 `versioning`（启动时拉取，整体替换；为空时保留 mock） |
| `GET /api/ontology/v1/releases/current` | app store `ontologyVersion` / `ontologyPublishedAt` | **已接**，开关 `versioning` |
| `GET /api/ontology/v1/releases/{ver}` | — | 有调用价值但页面没有对应位置，未接 |
| `GET /api/ontology/v1/releases/{ver}/snapshot` | — | 还没有模块登记快照 section（M3），未接 |
| `GET /api/biz/v1/orders`（page,size,storeId,status,channel） | `listOrders()` | 只有调用 + 适配，未接页面 |
| `GET /api/biz/v1/orders/{id}` | `getOrder()` | 同上 |
| `GET /healthz`、`/readyz` | — | 不经 /api |

其余页面全部仍是 mock。

接法：页面原来读 `@/mock/releases` 的 `RELEASES` / `releaseByVer`，改成读 `@/api/releases`。那里是一个 `shallowReactive` 数组：mock 模式内容就是 mock 的 RELEASES（同值同序）；real 模式由 `loadReleases()` 替换。改动的文件只换了 import：
`components/VersionPicker.vue`、`stores/app.ts`、`pages/ontology/OntologyOverview.vue`、`parts/object/HistoryTab.vue`、`parts/object/ObjBehaviorLifecycle.vue`。
real 模式下接口为空或失败都不挡启动：保留 mock 数据并提示（见 §2）。

## 5. 不一致（前端 mock ↔ 骨架）

| # | 位置 | 前端 / api-index | 骨架实际 | 处理 |
|---|---|---|---|---|
| M1 | Release 字段 | `{ver,publishedAt,by,note}` | 多 `parentVer/kind/rollbackOf/changeOrderIds`；详情再多 `snapshotSha256/changeSummary` | 适配只取四个字段 |
| M2 | 待发布版本号 | `NEXT_VERSION` / `CURRENT_VERSION` 是 `mock/changes.ts` 常量，多处模板直接用 | `/releases/current` 返回 `current/next` | **未接**：常量非响应式，改要动 mock 文件和多处页面；目前两边值一致（v2026.10.3 / v2026.10.4） |
| M3 | 快照内容 | 历史版本视图按 mock 渲染 | 还没有模块登记快照 section；默认 seed 跳过 versioning，发布记录为空 | 未接 snapshot；发布记录为空时前端保留 mock 并提示 |
| M4 | 身份 | api-index §1.11 有 `GET /me` | 骨架没有 `/me`、没有 admin 用户接口（`/api/admin/v1/users` 404） | 身份只靠 `X-Demo-As`；viewer=U010006（李娜）是 [推断]，seed 里存在该用户 |
| M5 | 非管理员范围 | viewer 在 mock 里能看到订单 | 行级范围是桩，非管理员一律 DENY_ALL：U010006 查订单 `total: 0` | 订单未接页面；等 static 模块的范围求值 |
| M6 | `?ver` / `?previewAs` 校验 | api-index §1.7/§1.8 只规定带上这两个参数进入只读；**没有**规定不存在的版本怎么报错 | 上下文是桩：`/orders?ver=v9` 照样 200，`previewAs` 不校验；写请求的只读拦截无法验证 | **提议**：不存在的版本返回 404 `NOT_FOUND`，`details {ver}`，待 api-index 确认；前端照约定都带上 |
| M7 | 订单列表 | `Order.lines` 列表里就有 | 列表不带 `lines`，只有详情带；明细多 `lineNo/name` | 适配：列表 `lines: []`，明细取 `sku/qty/price` |
| M8 | 订单字段 | 无 `storeName/isTest`；`warehouse: string` | 多 `storeName`、`isTest`；`warehouse` 可空 | 适配保留 `storeName`，丢 `isTest`，`warehouse ?? ''` |
| M9 | 订单查询 | api-index §2.1 有 `kw`、`cutoff` | 骨架未实现 | 页面靠全量 ORDERS 同步统计，切过去会改变结果 → 订单不接页面 |
| M10 | 启动请求 | — | 启动数据在路由守卫之前拉取：带 `X-Demo-As`（已先按 `?as=` 定身份）。预览会话存在 sessionStorage，刷新后 store 一创建就有，所以启动请求**会**带 `previewAs`（`src/api/index.ts:24`）；`?ver=` 由路由守卫写进 store，首屏启动请求不带 | 发布记录与版本无关，可以接受；预览中刷新时发布记录按预览身份拉取 |

### 5.1 M1–M10 归属

| 负责 | 条目 |
|---|---|
| 骨架（biz） | M7、M9（列表 `lineCount`、`kw`、`cutoff`、`dataAsOf`；状态 / 渠道选「全部」时不传 `status` / `channel`） |
| PDE 后台文档 | M4、M5、M6 |
| PDE 前端 | M1、M2、M8、M10（M2 = `HistoryTab.vue:43,64` 等处改读 app store，不再读 `CURRENT_VERSION` / `NEXT_VERSION`） |
| 各模块 | M3（登记快照 section） |

## 6. 验证记录（2026-10-25）

第 1 步当时的后台：ontology-xiaobai 72204b0 提交树 @127.0.0.1:8010（versioning 用临时 section 补 seed），临时库 rengu_integ_pde。前端：`VITE_API_MODE=real VITE_API_PROXY_TARGET=http://127.0.0.1:8010 vite --port 5290`；对照 mock `vite --port 5291`。Playwright 打开页面记录 /api 请求：

- real `/`：`GET /api/ontology/v1/releases`、`/releases/current`，`X-Demo-As: U010001`，均 200（带 X-Request-ID）；版本选择列出 5 条，`v2026.10.3 当前`。
- real `/?as=viewer`：同两条请求，`X-Demo-As: U010006`。
- real `/?as=admin&ver=v2026.10.2`：顶栏「本体 v2026.10.2（历史）」；之后的请求自动带 `?ver=v2026.10.2`。
- real `/?as=admin&previewAs=U010231`：之后的请求带 `?previewAs=U010231`，`X-Demo-As` 仍是 U010001。
- `listOrders({size:2})`：total 1286；viewer total 0（M5）。错误映射：订单不存在 → `ApiError 404 NOT_FOUND details {id}`；`size=abc` → `422 VALIDATION_FAILED details.fields`；`/api/admin/v1/users` → `404 NOT_FOUND`；`X-Demo-As: U999` → `401 UNAUTHORIZED`。均带 requestId。
- mock `/`：**没有任何 /api 请求**，版本选择内容和 real 相同。
- `npm run build`（mock 和 `VITE_API_MODE=real` 各一次）通过。

### 6.1 复验（开关键拆分后，2026-10-25 晚）

同样的骨架 + 临时库，前端分别用 `VITE_API_MODE=real`、`VITE_API_REAL_MODULES=versioning`、mock 起 dev，Playwright 记录 /api 请求：

- 只切 `versioning`（`VITE_API_MODE` 不设）：只发 `/api/ontology/v1/releases`、`/releases/current`（200）；`isReal('versioning')=true`，同前缀的 `isReal('static')=false`、`isReal('biz')=false`；打开订单抽屉 `?drawer=order:…` 没有 `/api/biz` 请求（仍走 mock）。
- 代理指向没有后台的端口（real 模式）：两条请求失败 → 版本选择仍是 mock 的 5 条，页面出现 `message.warning`“发布记录接口加载失败，暂时显示演示数据（HTTP_500）”（Vite 代理连不上后台时返回 500）。
- real 全切：同两条请求 200，无提示。
- mock：没有任何 /api 请求，也没有提示。
- `npm run build` 与 `VITE_API_MODE=real npm run build` 均通过。


### 6.2 复验（第 2 步：变基到 07c4c00 后，2026-10-25 晚）

后台：integ 工作区 `backend/`（= master 07c4c00）@127.0.0.1:8012，临时库 rengu_integ_pde3，`alembic upgrade head` + **默认** `app.cli seed`（日志：versioning「跳过：还没有模块登记快照 section」）→ `/releases` = `{"items":[],"current":null}`。前端 5 个 dev 服务，Playwright 记录：

- real 全切：只发 `/releases`、`/releases/current`（200）；无 TypeError / pageerror；`RELEASES` 仍是 mock 的 5 条（v2026.10.3…v2026.09.3），`ontologyVersion` 仍为 v2026.10.3；提示“发布记录为空，暂时显示演示数据”。对象类型「版本历史」页签正常渲染。
- 只切 `versioning`：同样只有这两条 /api 请求，同样提示；此时 `apiClient('biz')` 的 `listOrders()` 直接抛 `MOCK_ONLY`，不发 `/api/biz` 请求。
- mock：0 条 /api 请求，无提示，无控制台警告。
- real + 代理指向没有后台的端口：两条请求得到代理的 500 → 提示“后台未启动，发布记录暂时显示演示数据”，发布记录保持 mock。
- real + `VITE_API_REAL_MODULES=foo,event`：0 条 /api 请求（照字面，一个都不切），控制台 warn 未知键 foo、event。
- `npm run typecheck`、`npm run build`、`VITE_API_MODE=real npm run build` 均通过。


## 2026-10-26 真实数据模式

默认 `VITE_API_MODE=real`，`.env.example` 同步为 real；只有显式 `mock` 才展示开发样例。真实模式后台连接错误不再自动切回演示数据，空表返回空态。版本、草稿、对象引用、管理候选均应以接口为准。缺少函数、API 密钥鉴权、通用指标执行器、通知执行器等能力时说明未接入，禁止本地假保存/假执行。开发库当前记录按用户要求保留，没有因页面清理删除数据。
