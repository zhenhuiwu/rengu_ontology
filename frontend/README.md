# 本体智能平台 · 前端骨架（ui-vue）

纯前端，多人共用一个工程写页面、截图评审。没有后端，数据全部来自 `src/mock/`。

技术栈：Vue 3 + Vite 5 + TypeScript + Vue Router 4 + Pinia + Ant Design Vue 4 + vue-echarts（ECharts 5）+ @antv/x6。

## 启动

```bash
cd /workspace/ui-vue
npm install --registry=https://registry.npmmirror.com   # 首次
npm run dev          # http://localhost:5280（--strictPort，端口被占会直接报错）
npm run typecheck    # vue-tsc 类型检查
npm run build && npm run preview   # 生产构建，预览端口 5281
```

## 截图与验收

```bash
npm run shot -- / /build/ontology/object-types          # 指定路由
npm run shot -- "/?drawer=metric:gross_margin"          # 带抽屉状态（含 ? 的路由要加引号）
npm run shot -- --all                                   # 首页 + 全部 27 个二级页面
SHOT_BASE=http://localhost:5281 npm run shot -- /       # 对 preview 截图（也可用 --base）
npm run shot -- --skip-check /                          # 只截图不验收
npm run shot -- "/admin/system/users#click=停用"         # 交互状态：点击文字后截图，多步用 > 分隔（一屏/字号检查按点击前的页面）
```

- 截图规格：viewport 1920×1080、deviceScaleFactor 1.3333，输出 2560×1440 PNG 到 `shots/`，文件名 = 路由和状态 + `__时分_短提交号`（`/` → `home__1642_e09a371.png`，`/build/ontology/object-types?tab=props` → `build_ontology_object-types__tab-props__1642_e09a371.png`）。写入前会删掉同一路由+状态的旧图，`shots/` 里每个状态只保留最新一张；发图前看文件名里的提交号对不对得上。
- 每张截图之前会自动跑两个检查，并打印结果：
  - `/workspace/ui/check_onescreen.py`：2560×1440、1920×1080 两档下页面不出现滚动条，主区内最多一个滚动区，且不嵌套。
  - `/workspace/ui/check_fonts.py`：字号只能是 12/14/16/20/24，字重只能是 400/600。
- 任一检查不通过、或页面有控制台报错，最后汇总标 FAIL，退出码为 1。图照样会截，方便对照修改。
- 需要先启动 dev server（或 preview）。

## 新增一个页面

1. 在 `src/nav.ts` 里找到对应页面（27 个二级页面已全部登记，路由已经有了），加上 `component`：
   ```ts
   { key: 'metrics', title: '指标口径', path: '/build/ontology/metrics', ..., component: () => import('@/pages/ontology/Metrics.vue') },
   ```
   没有 `component` 的页面会显示占位页（标题 +“待设计”）。导航、面包屑、页面标题都从 `nav.ts` 生成，**不要另外改 router**。
2. 新建 `src/pages/<模块>/<页面>.vue`。根元素用 `.panel` 或 flex 布局把高度撑满；内容超出时，只让**一个**区域滚动（加 `.scroll-area`），或者用表格分页、`a-table` 的 `scroll.y`。
3. 页面右上角的操作按钮用 Teleport 放进页头：
   ```vue
   <Teleport defer to="#page-extra"><a-button>导出</a-button></Teleport>
   ```
4. `npm run shot -- /你的路由`，两项检查都 PASS 后再发图。

参考实现：`src/pages/Home.vue`（指标条 + 表格 + 图表）、`src/pages/ontology/ObjectTypes.vue`（左列表右详情 + 分组页签）、`src/pages/ontology/OntologyOverview.vue`（X6 画布）。

## 公共组件

| 组件 | 用法 |
| --- | --- |
| `ObjRef` | `<ObjRef kind="order" :id="o.id">{{ o.id }}</ObjRef>`：链接样式，悬停出卡片，点击打开详情抽屉。用于表格里的对象 ID、名称。 |
| `HoverCard` | `<HoverCard kind="metric" id="gross_margin">毛利率</HoverCard>`：只读悬停卡（宽 300），默认带虚线下划线提示可悬停。 |
| `DetailDrawer` | 全局只挂一个（在 AppLayout 里），状态放在 URL：`?drawer=<kind>:<id>`，刷新、分享、截图都能复现。代码里用 `useDrawer().open('store', 'SH0031')` / `close()`。页签：属性、时间线、关联对象。 |
| `DrawerPanel` | DetailDrawer 内部用的一层抽屉（宽 640），页面里不直接用。第 2 层有返回按钮和上一层的名称链接。 |
| `StatusTimeline` | `<StatusTimeline :items="[{ time, text, desc?, status? }]" :selected="i" />`，按下面的“时间线配色”上色。 |
| `AppPagination` | 统一分页，`variant="full" / "simple" / "cursor"`，见“分页规范”。 |
| `VersionPicker` / `VersionBanner` | 顶栏版本选择和历史版本横幅，布局自动挂载。 |
| `NoticeList` | 顶栏通知列表。 |
| `ConfirmActionModal` | 所有动作共用的确认卡，全局只挂一个。`const ok = await useConfirmStore().ask({ title, target, fields, impact, irreversible })`；`irreversible: true` 时必须勾选确认才能执行。 |
| `PageHeader` | 面包屑 + 标题，由 AppLayout 自动渲染，页面里不用再写。 |
| `GroupedTabs` | 分组页签（对象类型的“定义 / 行为 / 使用与治理”）：`<GroupedTabs v-model="tab" :groups="OBJECT_TYPE_TAB_GROUPS" />`。 |
| `EChart` | `<EChart :option="opt" />`，SVG 渲染，坐标轴文字已统一为 12px。颜色用 `theme.ts` 里的 `BRAND` / `GRAY`。 |
| `GraphCanvas` | X6 空画布（点阵网格、拖拽平移、Ctrl+滚轮缩放、缩放按钮），`@ready="(graph) => ..."` 拿到 Graph 实例。 |

抽屉和悬停卡支持的对象在 `src/components/objectRegistry.ts`（`store`、`product`、`order`、`metric`、`region` 等）。新增对象类型时，在这里加一个 resolver。默认页签是“属性 + 时间线”（有关联对象时再加“关联对象”）；resolver 可以多返回 `tabs: ObjTab[]`，比如门店/区域的“指标”页签（2–4 行 `kpis`），`pinToProps: true` 时这几行也会出现在属性页签顶部。

## 详情抽屉（最多两层）

URL 格式：`?drawer=<kind>:<id>&drawer2=<kind>:<id>`。第 1 层是 `drawer`，第 2 层叠在第 1 层上面是 `drawer2`。

```ts
const { current, current2, top, depth, open, back, close } = useDrawer();
open('store', 'NJ0076');                  // 页面里调用：打开第 1 层，并清掉第 2 层
open('order', 'SO…');                     // 在抽屉里面调用（比如抽屉里的 ObjRef）：自动打开第 2 层
open('order', 'SO…', { level: 1 });       // 强制指定层级
back();                                   // 关掉第 2 层，回到第 1 层
close();                                  // 全部关闭
```

- 不写 `level` 时：组件在抽屉里面（DrawerPanel 用 `provide(DRAWER_LEVEL)` 注入了层级）就开第 2 层，否则开第 1 层。所以 `ObjRef` 写法不变，放在抽屉里自动变成第 2 层。
- 在第 2 层再打开一个对象，会替换第 2 层，不会出现第 3 层。
- `current` 仍然是第 1 层（老代码不用改），`current2` 是第 2 层，`top` 是最上面那层，`depth` 是 0/1/2。
- 浏览器后退：打开抽屉用 push，所以后退会先关第 2 层，再关第 1 层。返回按钮和关闭按钮在上一条历史正好是目标地址时用 `router.back()`，否则用 `replace`，不会多出历史记录。点遮罩或按 Esc：第 2 层是返回，第 1 层是关闭。

## 区域与权限

- 侧栏顶部“使用 / 构建 / 管理”切换。每个区记住上次停留的页面（Pinia + localStorage `ui-vue:lastRoute`，不含 drawer/confirm 等参数），切回来时回到那一页。
- 用户权限在 `useAppStore().permissions`，默认用户（王敏）三个区都有。没有权限的区不显示按钮，直接输入地址会回到首页。
- 演示：`?as=viewer` 切到只读用户（李娜，只有“使用”），`?as=admin` 切回。身份存在 sessionStorage，只影响当前标签页。

## 税额口径

- 增值税率按 **13%**（`TAX_RATE`，`src/mock/metrics.ts`），不含税 = 含税 ÷ 1.13。
- `ORDERS` 里的金额都是含税实付金额。首页 KPI 和趋势图用不含税：指标 `gmv` = 销售额（不含税），`gmv_tax` = 销售额（含税）；`DAILY_GMV` 是含税日数据，`DAILY_GMV_EX` 是不含税日数据。
- 首页不显示面包屑（`meta.crumbs: []`），其他页面照常显示。

## 时间线配色

只有三种颜色：**灰** = 正常/已完成，**橙** = 需要关注，**红** = 失败。**蓝**只给当前选中的节点。不再使用紫色等其他颜色。结果未知按橙色显示，用问号图标，并写“结果未知 · 待对账”。颜色用 `theme.ts` 的 `TIMELINE_TONES` / `timelineTone()` 和 `tokens.css` 的 `--tl-normal / --tl-attention / --tl-failed / --tl-current`，统一用 `StatusTimeline` 组件。

## 分页规范

**什么时候分页**：数据会超过一屏的列表都分页，不做无限滚动。总数为 0 时显示 antd `Empty`，不显示分页条。

| 形式 | 样子 | 用在哪里 |
| --- | --- | --- |
| `full` | 共 1,286 条 + 页码 + 每页条数 + 跳转，放在右下角 | 页面主表格、卡片网格（首页最近订单、用户与组织） |
| `simple` | 共 N 条 ‹ 1/3 › | 左侧主列表、抽屉、侧栏（指标列表、抽屉里的关联对象和时间线） |
| `cursor` | 只有 上一页 / 下一页，不显示总数 | 审计日志、事件流等海量流水 |

- 每页条数选项是 `[一屏默认条数, 10, 20, 50]`，默认值排第一，重复的去掉，不提供 100。一屏默认条数的选项文字是“一屏（21）”，其余是“20 条/页”（AppPagination 统一处理）。
- 页数超过 10 页才显示跳转框。
- 默认条数 = 1920×1080 下一屏正好放下的行数（首页 15，用户 21，审计 21），各页面自己定。表格容器固定加 `.paged-scroll`：放得下时不滚动；选了更大的条数、或顶部出现历史版本横幅时，它是主区唯一的滚动区（表头吸顶），分页条始终可见。
- 页码和条数放在 URL（`?page=2&size=50`，默认值不写），筛选条件变化时回到第 1 页：`const pq = usePagedQuery({ defaultSize: 15, filters: [status, kw] })`，一页里有两个分页时传 `prefix`。cursor 形式用 `useCursorQuery({ size, filters })`（`?cursor=`）。
- Mock 用 `paginate(rows, { page, size }) → { items, total }` 或 `paginateCursor(rows, { cursor, size }) → { items, nextCursor }`。

```vue
<AppPagination v-model:page="pq.page.value" v-model:size="pq.size.value"
  variant="full" :total="paged.total" :default-size="pq.defaultSize" />
<AppPagination v-model:page="page" variant="simple" :total="list.length" :size="10" unit="个指标" />
<AppPagination variant="cursor" :has-prev="cq.hasPrev.value" :has-next="!!res.nextCursor"
  @prev="cq.prev()" @next="cq.next(res.nextCursor)" />
```

几种特殊情况：

1. **问数回答里的表格**：先显示前 10 行，写“共 N 行”，用 simple 形式，导出按钮放在旁边：
   `<AppPagination variant="simple" unit="行" :total="n" :size="10" v-model:page="p"><template #extra><a-button size="small">导出</a-button></template></AppPagination>`
2. **对象类型的属性定义列表**（建模页面）：用 simple 分页，上方加按属性名搜索。
3. **单个对象在抽屉里的属性值**：不分页，按分组显示，分组可以折叠。
4. **抽屉主体里的列表**（关联对象、时间线）：直接在抽屉里用 simple 分页（关联对象每页 10 条，时间线每页 8 条），不跳转到别处。
5. **悬停卡和小的侧边卡片**：只显示前几条，底部放“查看全部 N 条”，点击打开对应的详情抽屉。

## 版本、草稿与编辑权限

**历史版本**：顶栏“本体 v2026.10.3”可点开发布记录（mock 5 个版本，`RELEASES`）。选旧版本后 URL 带 `?ver=v2026.10.1`，`useAppStore().viewMode === 'history'`，顶部出现 28px 横幅“正在查看历史版本 v… · 只读 · 返回当前版本”（AppLayout 自动显示）。历史版本里切换页面会自动带上 `?ver=`，点“返回当前版本”才退出。代码里切换：`useVersion().view(ver)` / `exit()`。

**只读状态**：给建模页面用的 `useReadonly()`，只管历史版本：

```ts
const { readonly, reason } = useReadonly();   // 历史版本 → readonly = true，reason = “正在查看历史版本 v…，只读”
```

已发布 / 草稿的编辑规则（发起变更、草稿横幅、StartChangeModal）在页面负责人写的 `src/pages/ontology/parts/useDraftView`，那边把 `readonly` 合进自己的禁用条件即可，例如 `:disabled="readonly || !isDraft"`。横幅高度 `var(--banner-h)`（28px），加上以后页面仍要通过一屏检查，截图时补一张 `?ver=v2026.10.1`。

## 通知

顶栏铃铛点开是通知列表（mock 7 条，`NOTICES`，超过 5 条用 simple 分页）。点一条：标为已读，在当前页用 `useDrawer().open(kind, id, { level: 1 })` 打开对应对象的抽屉，不跳转页面。角标是未读数（`app.unread`）。

## 未保存修改保护

```ts
const dirty = ref(false);
const guard = useUnsavedGuard(dirty);          // 也可以传函数：useUnsavedGuard(() => form.changed)
// 切换页签、切换左侧列表选中项时包一层：
const onTab = (k: string) => guard(() => { tab.value = k; });
const onSelect = (id: string) => guard(() => { selected.value = id; dirty.value = false; });
```

- 离开当前路由（侧栏、面包屑、浏览器后退）自动弹 `Modal.confirm`“有未保存的修改”（放弃修改 / 继续编辑）。只改 query（打开抽屉、翻页）不拦截。
- 关闭、刷新浏览器标签页时用浏览器自带提示。
- `guard(fn)` 返回 `Promise<boolean>`；需要单独弹确认时用 `confirmDiscard()`。

## 对象引用必须能解析

`ObjRef`、抽屉的关联对象引用了解析不到的对象时，开发环境会 `console.warn('[objectRegistry] 无法解析对象 …')`，`npm run shot` 把这条警告算作 FAIL。门店用 `ALL_STORES` / `storeById` 解析：核心 12 家 `STORES`（订单、首页排行、告警历史都按这 12 家生成，ID 不变）加问数结果表和华东大区关联的其余门店。新增引用前先在 `src/mock` 里补数据。

## Mock 数据（src/mock/）

一份零售数据集（另有管理区的 `ADMIN_USERS` 120 人、`AUDIT_LOGS` 512 条，在 `admin.ts`）：12 家门店（`STORES`）、12 个 SKU（`PRODUCTS`）、1286 条订单（`ORDERS`，固定种子，每次一样）、指标（`METRICS`、`DAILY_GMV`）。订单状态用“已出库”，不用“已发货”。格式化统一用 `fmtMoney` / `fmtInt` / `fmtPct` / `fmtDelta`，空值显示 `--`（`EMPTY`）。数字要像真实导出的数据：带小数、不凑整。

## 全局提示

`message` 已在 `src/main.ts` 统一配置：同时最多 1 条（`maxCount: 1`），距顶部 `calc(var(--topbar-h) + 8px)`，即顶栏 48px 下方 8px（共 56px，不挡搜索框；顶栏高度只认 `--topbar-h`）。页面里直接 `message.success('已保存')`，不要再各自 `config`。

## 页面归属表

| 路径 | 负责人 |
| --- | --- |
| 公共部分：`src/components`、`src/layouts`、`src/composables`、`src/stores`、`src/theme.ts`、`src/styles`、`src/nav.ts`、`src/router`、`src/mock/index.ts`、`scripts` | PDE工程师 |
| `src/pages/qa`、`src/mock/qa.ts` | 智能问数项目调研员 |
| `src/pages/ontology`（`ActionTypes.vue`、`EventTypes.vue` 除外）、`src/pages/ontology/parts` | 静态本体负责人 |
| `src/pages/ops`、`src/mock/dynamic.ts` | 动态本体负责人 |
| `src/pages/ontology/ActionTypes.vue`、`src/pages/ontology/EventTypes.vue` | Palantir专家 |
| 首页、`src/pages/admin/*`、数据接入页面、函数页面 | PDE工程师 |

规则：每个人只提交自己负责的路径（`git add <路径>`，不用 `git add -A`）；要改别人的文件，先在群里说一声。

## 命名约定

- 页面 key：kebab-case，全局唯一，等于路由 name（如 `object-types`）。
- 路由：`/<区>/<模块>/<页面>`，区为 `use` / `build` / `admin`。
- 文件：页面 `src/pages/<模块>/PascalCase.vue`；公共组件 `src/components/PascalCase.vue`；组合函数 `src/composables/useXxx.ts`；store `src/stores/xxx.ts`（`useXxxStore`）。
- 组件内 class 用短横线，加页面前缀避免冲突（如 `.ot-list`、`.home-kpi`）。
- 界面文案用中文，术语与 v5 导航一致（指标口径、动作类型、变更与发布……）。

## 规则清单（提交截图前逐条过）

- [ ] **主题只在 `src/theme.ts`**（ConfigProvider token）和 `src/styles/tokens.css`（CSS 变量）。组件里不写颜色值，用 `var(--brand)`、`var(--g1…g10)`、`.c-1/.c-2/.c-3`。
- [ ] 只有一个品牌蓝 `#1f5fd1`，其余是灰阶。状态色（成功/警告/错误）只用于状态。
- [ ] 圆角 4，没有渐变，没有装饰性阴影（只有浮层有阴影）。
- [ ] **字号只用工具类**：`.t-12`（辅助说明、表格附注）、`.t-14`（正文，默认）、`.t-16`（区块标题）、`.t-20`（页面标题）、`.t-kpi`（24，只给少数几个关键指标数字）。**组件里禁止写 font-size**。
- [ ] 字重只有 400 和 600（`.fw-400` / `.fw-600`）。
- [ ] 数字列右对齐，加 `.num`（等宽数字）。
- [ ] 页面高度 100vh，没有页面级滚动条，主区里最多一个滚动区；2560×1440 和 1920×1080 都要一屏放下。
- [ ] 不加 emoji、插画、大色块、“AI 风”的渐变和光效，也不写营销口吻的文案。
- [ ] `npm run shot` 两项检查都 PASS，并且用肉眼看过截图。

## 目录

```
src/
  theme.ts              主题唯一来源（token、字号、颜色常量）
  nav.ts                v5 导航：3 区 6 模块 27 页，路由由此生成
  styles/tokens.css     CSS 变量 + 工具类（.t-12 … .t-kpi、.panel、.num …）
  layouts/              AppLayout（顶栏 + 左导航 + 页头）、TopBar、SideNav
  components/           DetailDrawer、HoverCard、ObjRef、ConfirmActionModal、PageHeader、GroupedTabs、EChart、GraphCanvas、objectRegistry
  composables/useDrawer.ts
  stores/               app（租户、版本、用户）、confirm（确认卡）
  mock/                 零售数据集
  pages/                Home、PlaceholderPage、NotFound、ontology/*
scripts/shot.py         截图 + 验收
shots/                  截图输出
```

## 协作方式：每人一个工作区（git worktree）

`master` 是集成主干，http://localhost:5280 跑的就是它。**不要再直接在 `/workspace/ui-vue` 里改文件。**

| 名字 | 负责人 | 工作区 | 开发端口 |
|---|---|---|---|
| pde | PDE工程师 | /workspace/ui-vue-wt/pde | 5286 |
| static | 静态本体负责人 | /workspace/ui-vue-wt/static | 5282 |
| dynamic | 动态本体负责人 | /workspace/ui-vue-wt/dynamic | 5283 |
| qa | 智能问数项目调研员 | /workspace/ui-vue-wt/qa | 5284 |
| palantir | Palantir专家 | /workspace/ui-vue-wt/palantir | 5285 |

1. 建工作区：`cd /workspace/ui-vue && scripts/wt.sh new <名字>`，会同时在自己的端口启动开发服务器。
2. 在自己的工作区里改、提交（`git -c user.name="角色名" commit`）。提交前会自动检查 `@/...` 引用的文件是否都存在，缺文件就拒绝提交。
3. 截图：`SHOT_BASE=http://localhost:<端口> npm run shot -- <路由>`。
4. 同步别人的改动：`scripts/wt.sh sync`。
5. 合入主干：`scripts/wt.sh publish`。它会先同步 master、做引用检查和完整构建，都通过才合入；不通过就不会影响别人。
6. 主分支叫 `master`。5281 留给 `npm run preview`，不要占用。
7. 审核看主干 5280 的页面；还没合入的页面可以看作者自己的端口。
