# PDE工程师 交接文档（分支 pde-integ，基于 master b688d5d）

## 2026-10-26 A项功能权限收尾

- `auth.resolve_user`只聚合DB实际角色func，`require_func/check_func`按view/manage真实403；缺角色默认拒绝，`demo_admin_codes`不授func，prod身份入口仍401未接入。未扩展行级权限或生产登录。
- CR目标按八类定义页面manage；整单检查包括旧touches，发布/回滚服务内再验releases:manage，保留既有管理员/owner/reviewer和全局READONLY。create等返回整单接口仍需releases:view。
- 构建表单负责人候选`GET /build/owners?kind=object|metric|dimension|action|event`只返回启用用户{id,name}，对应目标页manage；不依赖管理用户页权限。当前用户真实姓名用已有`/api/admin/v1/me/row-scope`。
- 默认开发账号原始SYS_USERS无DB角色关系。root已仅为开发库U010001→R-01作显式幂等关联及同事务审计；代码没有运行时bootstrap/姓名旁路。测试使用显式DB成员夹具`tests/function_access_support.py`；其余老测试需按调用角色补充夹具，最终全量由root统一。

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

- **首页订单实付折算趋势已接数据库**：`GET /api/biz/v1/dashboard/sales/daily`，按下单日汇总实付÷1.13，缺日补0；真实模式不回演示。它不冒充含退款/逐行税率的通用 `/metrics/{id}/daily`，该指标执行能力仍待实现。确切时间/金额口径见 `dashboard-api.md`。
- 「导出 CSV / Excel」按钮仍是前端提示，没有后台导出接口（一期未排）。
- 已完成：首页真实模式的页头、订单标题及排行说明读取 KPI / 订单 / 排行响应 `dataAsOf` 的共同截止（取最早值，任一未成功则显示“—”）；mock 保留原日期。请求 cutoff 及指标口径不变，切身份 / 预览 / 历史版本重拉并丢弃迟到响应，不从 KPI 更新时间推断。
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

- qa-tuning（合并审）非阻塞：①评测无并发互斥，且 ER- 编号 max+1 并发可撞号；②进程重启后 run 卡"运行中"（qa.md 已记）；③/feedbacks 列表逐行构建详情（N+1）；④:reopen 不撤销 :to-cert 已加的同问法；⑤后台任务最多轮询 5s 等请求事务提交；⑥qa service 跨模块只读引用 static.ChangeRequest，后续宜走 static 提供的查询函数。

- （合并 dynamic-r3 后，PDE 共享文件）①`src/api/static.ts` 导出 `registerDraft(cr)`（参数只要单头 id/title/owner/updatedAt/changes，CRFull / 动态的 CrFullDto 都能传）；`src/api/dynamic.ts` 的 `registerCR` 改为记 lockVersion 后调 `registerDraft`，不再复制登记逻辑。`src/api/action.ts` 仍有自己的 `registerDraft`（changes 取 DRAFTS 现值、不用响应里的 changes，语义不同），未改，留给 Palantir 决定是否换用。②`objectRegistry` 加 `canResolve(kind)`（只看有没有解析器，不告警）；`ObjBehaviorLifecycle.vue` 的 `refKind` 加了 `canResolve` 判断：注册表解析不了的对象类型（member / promotion / refund 等）显示纯文本，不再在开发环境告警（handoff/dynamic.md 未完成第 8 条、给 PDE 两条可勾掉）。

## 下一轮 PDE 未完成项（2026-10-26，master 合并 palantir-r3 之后）

1. **本体总览**：六张资源卡片（计数 / 健康度）、「待处理」列表、「最近发布」列表仍读 mock，需各模块提供统计接口后再切。
2. `src/api/action.ts` 仍保留自己的 `registerDraft`（重复登记逻辑），应改为调用 `src/api/static.ts` 的 `registerDraft`（与 dynamic.ts 的 `registerCR` 写法一致）。
3. 事件抽屉可改为读 `GET /events/{id}`（palantir-r3 已提供），不再从列表行拼详情。
4. `ObjectTypesBody.vue` 的对象 target 清理（放弃草稿后撤登记 / 清本地）待补，与 `useDraftEdits.ts` 的做法对齐。

## 评审 palantir-r3 非阻塞意见

- 静态 ①–③、动态 ①–③ 共 6 条已记在 `handoff/palantir.md` §5（归 Palantir专家，下一轮处理），此处不重复。

## 总览真实聚合接线（2026-10-26）

- 新增 `GET /api/ontology/v1/ontology/overview`、`/ontology/todos`（api-index §3.4）：六类真实资源计数、已有运行健康、未发布变更单待处理分页。历史定义取对应 section，历史待处理为空；事件健康排除测试事件。最近发布复用真实 `/releases`，真图谱不再混入演示草稿，历史节点取 objectTypes 快照。
- 当前没有持久化数据质量检查模型，返回 `qualityConfigured=false`，页面明确“尚未配置”，不把演示质量检查或健康 0 当真实结果。
- 历史记录（已由下文2026-10-26真实订单趋势接线替代）：原合同是 `/metrics/{id}/daily?from=&to=&taxMode=`，返回原 `DAILY_GMV`/`DAILY_GMV_EX`。这两组数据仅在前端生成，没有对应种子、表或运行统计；现有订单按日聚合会得到另一套数值，不能冒充原指标日统计。本轮未新增迁移或公式种子，待明确真实日指标数据来源再接。

## 源表内省 → 新对象定义草稿：只读审查，二期未实现

- `Connections.vue` 已有库表清单，但 `DsTable.cols` / `DsTableOut.cols` 只是字段数量。`data/connector.py::_list` 虽读取 `get_multi_columns`，目前只保留数量，没有列名、类型、可空性、主键信息的前端接口。
- 新对象实际入口在 `ObjectTypesBody.vue` 顶栏“从数据源导入 / 新建对象类型”。两按钮此前只更改 `StartChangeModal` 的目标文案，确认仍调用 `cr.start(opts)`，目标取当前对象 `current.id`；动作 / 事件页顶部“新建”同样仍取 `cur.id`。这四个未接入入口现统一禁用并加提示（真 / 演示模式均如此），避免将新建操作误写成修改当前类型；已有类型的“发起变更”保留。
- 已有后端能力：`static-api.md` §2.5 / `writeback.py` 支持 `target {kind:object,id,isNew:true,name}`，`def` 段一个字段一行，`properties` 段一个属性一行，全部 `op=add`；审核发布前只存 CR，发布时新建定义。`static-api.md` 的 `POST /object-types:import-preview` 仍明确是二期，尚无完整请求 / 响应及新类型表单。
- 最小后续范围：在现有库表行增加“导入为对象草稿”，复用 `StartChangeModal` 选择新建 / 加入 CR；按已保存数据源 ID 只读获取所选 schema/table 完整列元数据，生成定义与属性草稿，再跳变更单审阅。必须补稳定编码与冲突校验、受支持类型和主键映射规则；真实内省目前只支持 PostgreSQL / PG 兼容金仓，不能对其他连接类型假装成功。手工新建对象没有现成名称 / API / 主键表单，不能随导入接线声称已实现。
- 权限边界：表名、`source_column` 仅记录定义来源；不自动登记 `governed_tables()`、不改属性 `backing_column`、不改 QA `bindings.json`，不授予任意源表可执行 SQL 权限。新对象即使发布，未受控绑定前仍应为 `bound=false / 未接入数据`。本轮未实现导入接口、表单、向导或迁移。


### 首页真实模式数据边界更新（2026-10-26）

用户已要求全部使用数据库数据，覆盖此前保留演示销售曲线的决定。Home 真模式订单 / 排行 / KPI 不再传固定演示 cutoff，趋势默认后台当前时间近30日，不漏固定日期后的新订单；原共同 dataAsOf 最早截止算法不变。KPI仍为数据库已配置指标值，文案不再伪称固定日期同期实时聚合。接口错误 / 空数据不会回演示值，显式 mock 模式保留原示例。详见 `dashboard-api.md`。


### 我的质量检查真实接线（2026-10-26）

`data_quality_0006` 新建空 quality_check / quality_run 表，不导入演示检查或随机历史。DataQuality.vue 真实模式全程使用当前创建者的数据库检查定义及真实运行，空库显示未配置，接口失败不回演示；显式mock模式保留演示。4种检查按固定4类业务对象映射执行，未映射组合明确不可用；本轮同步完成并返回completed，自动调度未接入且UI禁用。质量结果有通过/失败/无数据，定义新建未运行不算通过，检测快照与业务源时间分开。完整接口、字段、阈值语法及总览查询helper见 `data-quality-api.md`。此前“没有质量表”记录已由本节替代，总览由root接最新可见真实运行。


### 质量定义CR闭环（2026-10-26，替代上节直接新建定义）

质量规则已补创建/编辑/删除草稿，复用既有字段与StartChangeModal；`quality/def`发布写回，审核发布才生效。删除仅发布后退休，保留历史运行，pending可从实际CR恢复；未发布或退休不可运行。稳定创建者、preview有效权限、历史快照与发布时间边界均隔离；快照qualityChecks只存定义，整版回滚不删运行。quality_cr_0011只新增可空retired_at，ROOT已应用开发库；后续ROOT static_0012继续单head，禁止重seed已有数据。列表及历史改SQL真实分页，full默认10；不显示技术实现说明。接口完整契约与窄测见data-quality-api.md，最终全量/截图由ROOT统一。
