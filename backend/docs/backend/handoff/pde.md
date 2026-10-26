# PDE工程师 交接文档（分支 pde-integ，基于 master b688d5d）

## 1. 已完成

| 提交 | 内容 |
|---|---|
| efdc19a | app store 增加 `ontologyNextVersion`：real 模式取 `/releases/current` 的 `next`，mock 回退 `NEXT_VERSION` |
| c78b025 | 本体总览 / 发布记录 / 对象历史：当前版本号、下一版本号改读 app store，不再读 mock 常量 |
| e579df9 | 问数调优「当前版本」、顶栏帮助菜单版本号改读 app store |
| da41377 | 问数卡片预览：后台返回 denied 的卡片只显示「无权限」（灰色，文案同 PageDrawer） |
| 215819d | `src/api/http.ts` 的 `apiClient` 补 `put` / `patch` / `delete` |
| 本次 | **首页看板**：后端 `GET /api/biz/v1/dashboard/kpis`、`GET /api/biz/v1/stores/ranking`；首页 KPI 条、门店排行、最近订单表在开关 `biz` 为真接口时读后台 |

### 首页看板接口（前缀 `/api/biz/v1`，api-index #44 / #46）

- `GET /dashboard/kpis?ids=gmv,order_cnt,gross_margin,ship_ontime[&cutoff=YYYY-MM-DD HH:mm[:ss]]`
  → `{items: [{id, name, unit, value, prev}], dataAsOf}`。`items` 按 `ids` 顺序，不存在的编号跳过；
  `value` / `prev` 读 `metric` 表（静态 seed 从 mock `METRICS` 灌入，值不改）。`dataAsOf` = min(cutoff, 当前时间) 截到分钟（同 `/orders`）。
- `GET /stores/ranking?from=&to=&page=&size=` → `{items: [{rank, id, name, city, cnt, amount}], total, page, size, dataAsOf}`。
  - 口径 = Home.vue:77-81：区间内订单按门店汇总，`amount = exTax(Σ实付)`（÷1.13，按 JS `Math.round` 到分）；销售额降序，同额按门店 seed 顺序。
  - 门店集合 **[推断]**：「有过订单的门店」（库里门店表是全量 95 家，前端只排核心 12 家，订单只在这 12 家）。
  - 页码越界按前端收拢到最后一页。`to` 只到分钟时含整分钟。参数格式错 → 422 `VALIDATION_FAILED`。
- 都要登录（`CurrentUserDep`，未知 / 停用身份 401）；订单经 `apply_row_filter`（初版不裁剪）。
- **没有新表、没有迁移**（数据全部来自已有的 `metric`、`store`、`biz_order`），迁移链不受影响。

### 前端

- `src/api/dashboard.ts`：`fetchKpis`、`fetchStoreRanking`（开关键 `biz`）。
- `src/api/biz.ts`：`OrderQuery` 加 `kw` / `cutoff`，`OrderDto` 加 `lineCount`，新增 `listOrderDtos`。
- `src/pages/Home.vue`：mock 模式行为和界面完全不变；`VITE_API_REAL_MODULES=biz` 时 KPI、排行（含「全部 N 家」抽屉的服务端分页）、订单表（筛选 + 分页走服务端，cutoff `2026-10-24 23:00:59`）读接口；任一接口失败保留 mock 并 `console.warn`（后台没起来时注明）。

## 2. 未完成

- **近 30 日销售额趋势图**仍读 mock `DAILY_GMV_EX`。api-index 把它放在 ontology 的 `/metrics/{id}/daily`（静态模块负责），首页等静态提供后再切。
- 「导出 CSV / Excel」按钮仍是前端提示，没有后台导出接口（一期未排）。
- 页头「数据截至 2026-10-24 23:00」仍是前端常量；接口已返回 `dataAsOf`，需要时可改读。
- 合并到 master 时 `api-index.md` #44 / #46 可补一句「已实现：app/modules/dashboard」（需和 `/workspace/api-spec/后台接口需求.md` 保持一致）。

## 3. 代码在哪

- 后端：`backend/app/modules/dashboard/`（`router.py` / `service.py` / `schemas.py`），挂载在 `backend/app/api.py` 的 `MOUNTS`（`("biz", dashboard_router)`，一行）。
- 测试：`backend/tests/test_dashboard.py`（和 Home.vue 现算逐项对比、分页收拢、422、401、只读身份 200）。
- 前端：`src/api/dashboard.ts`、`src/api/biz.ts`、`src/pages/Home.vue`。

## 4. 下一步怎么接

1. 静态提供 `/metrics/{id}/daily` 后，在 `Home.vue` 的 `trendOption` 上照 KPI 的写法：`shallowRef` 先放 mock，`isDashboardReal()` 时拉接口替换。
2. 需要多租户 / 行级范围时，`service.store_ranking` 已经经过 `apply_row_filter`，只需在基础层实现过滤即可，接口不用改。
3. 订单量变大后，排行可改成 SQL `GROUP BY`（现在 1286 单在 Python 里汇总，为了和前端 JS 浮点口径逐分一致）。

## 本体总览接 `/ontology/graph`（分支 pde-r2）

- 文件：`src/api/overview.ts`（新，开关键 `static`，`fetchGraph` / `mockGraph` 适配成 LINK_OBJECTS / LINKS 形状）、`src/pages/ontology/OntologyOverview.vue`（只改取数和重画，界面不变）。
- **真接口**（`static` 开关打开时）：对象关系缩略图的节点、边，以及「N 个对象 · M 条关系」；`?ver=` 由 http.ts 自动带上，切换版本时重新拉取重画。后台没起来 → `fallBackIfDown` 回退 mock 并提示一次「后台未启动，暂时显示演示数据」；其他错误保留 mock 并 `console.warn`。
- **仍是 mock**：
  - 草稿对象（储值卡）及连到它的边：接口 nodes 只有已发布对象类型，草稿在变更单里（变更单发起接口未做），先保留 mock 草稿节点 / 边 [推断]。
  - 六张资源卡片的计数和健康度、待处理列表（变更单 + 数据质量）、最近发布列表、数据截至：graph 接口不提供，需各模块的统计接口。
  - 节点坐标 `POS` 仍是前端常量；新增对象没坐标时落在中心。
- **接口缺口**（给静态）：handoff/static.md 第 17 条——`?ver=` 不按版本筛节点（只按版本筛边）；nodes 没有 `draft` / 质量失败标记；没有卡片所需的统计字段。

- （评审 palantir-r2，非阻塞）事件类型页“近期事件”在非后台断开类错误时显示空列表且不自动重试，直到切换筛选或翻页。
