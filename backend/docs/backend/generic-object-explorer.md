# 「使用」区通用化设计：对象搜索 / 对象详情 / 问数范围口径

> 作者：Palantir专家 ｜ 基于 master `69daf37` ｜ 状态：待 PDE 定稿
> 标记：【官方】Palantir 文档原文（附链接）；【笔者归纳】据官方文档整理；【推断】本项目自定，未经官方确认。
> 原则：页面不写任何业务字段，全部由本体定义驱动；mock 数据一条不改，门店只是一套演示类型。

## 1. 问题（现状）

| 位置 | 写死的内容 | 代码位置 |
|---|---|---|
| 对象搜索 | 类型只有 门店/商品/订单；分面 区域/城市/业态/状态（含 `华东一区…五区` 顺序）、表格列、关键词占位符、提问建议，都是按类型手写的 | `src/mock/objects-qa.ts` 的 `OBJ_TYPES`（facets / columns / kwPlaceholder），`ObjectSearch.vue` |
| 对象详情 | 属性、关联分组（所属大区、同城门店…）、可执行动作都按 kind 手写 | `components/objectRegistry.ts`、`objects-qa.ts` 的 `relationGroups` / `objActions` |
| 问数输入框 | `范围：华东大区/华东一区/华东二区`、`口径：不含税/含税` 是固定选项 | `src/mock/qa.ts` 的 `QA_SCOPE_OPTIONS` / `QA_CALIBER_OPTIONS`；后端 `/session-options` 读的 `qa_option` 也是固定行 |

## 2. Palantir 对照

- 【官方】Object Explorer 首页的全局搜索覆盖对象类型、属性和对象本身；也可以先选一个对象类型进入探索。[Getting started](https://palantir.com/docs/foundry/object-explorer/getting-started/)
- 【官方】探索视图里可按任意属性筛选、按关键词搜索，并支持 AND/OR。[Filter results](https://palantir.com/docs/foundry/object-explorer/filter-results/)
- 【官方】属性带 **Title key**（对象的显示名）和 **Primary key**；属性有 **Visibility**：prominent 优先显示，normal 正常显示，hidden 不出现在任何应用里。[Property metadata](https://palantir.com/docs/foundry/object-link-types/property-metadata/)
- 【官方】**Render hints**：Searchable 是 Selectable、Sortable、Low cardinality 的前提。Selectable 允许聚合（直方图）和精确匹配筛选；Sortable 允许字符串排序（数值和日期总能排序）；Low cardinality 表示取值少，部分组件只允许对它筛选。另有 Identifier、Long text、Keywords、Disable formatting。[Render hints](https://palantir.com/docs/foundry/object-link-types/metadata-render-hints/)
- 【官方】结果表只能按带 Sortable 的属性列排序；点标题列打开 Object View。[View results](https://palantir.com/docs/foundry/object-explorer/view-results/)
- 【官方】动作自动出现在三处：探索视图右上角的 Actions 下拉（只列接受该类型对象列表参数的批量动作）、Object View 右上角（单个和批量动作）、关联对象区。[Use actions](https://palantir.com/docs/foundry/action-types/use-actions/)
- 【笔者归纳】页面上没有业务字段，筛选、列、标题、动作都来自对象类型、属性和动作类型的元数据。
- 【推断】Palantir 没有"问数的范围和口径"这一概念。本项目里，范围对应维度加行级权限，口径对应指标定义的变体。

## 3. 页面结构（通用版）

**对象搜索 `/use/objects/search?type=<objectTypeApi>`**
- 顶栏：对象类型下拉（列出全部已发布、非 hidden 的对象类型，按分组）+ 关键词框。占位符按「可搜索」属性的显示名拼成，最多 3 个，例如「名称/编码/店长」。
- 左栏：分面。来自该类型里「可筛选」的属性，按「显示顺序」排；枚举值顺序取枚举定义，计数由后端聚合。数值和日期属性改用范围筛选【推断】。
- 右栏：结果表。第一列是标题属性（点击进详情），其余列取「列表展示」的属性；只有「可排序」的列能排序。右上角是「动作」下拉，列出 `target_kind` 等于当前类型、且支持批量的动作类型，加上导出和「用这批对象提问」。
- 提问建议取对象类型已有的 `sample_questions`，不再按 kind 手写。

**对象详情 `/use/objects/detail?type=&id=`**
- 标题行：标题属性的值 + 类型名。
- 概览：先显示 prominent 属性，再按 `prop_group` 分组显示其余非 hidden 属性；长文本单独成区，敏感属性按权限打码。
- 关联对象：按该类型的关系类型（`link_type` 的 from/to）分组，组名用关系类型的显示名。
- 右栏「可执行动作」：列出 `target_kind` 等于本类型的动作类型；参数默认值可以取当前对象的属性【官方，见 Use actions】。
- 指标卡：取 `dims` 里包含本类型维度的指标【推断】。

**UI 约定：** 不要用「主行 + 下方灰色说明行」的叠排。说明放进 ⓘ 提示或同一行的弱化尾注；现有的「可见范围放在筛选旁的 ⓘ 里」继续沿用。

## 4. 属性标记：已有 vs 需静态补

| 标记 | 作用 | 现状（`static.models.Property` / `ObjProp`） | 处理 |
|---|---|---|---|
| 主键 | 对象 id | ✅ `is_pk` / `pk` | 沿用 |
| 标题字段 | 结果表第一列、详情标题、悬停卡 | ✅ `is_title` / `title` | 沿用；每个类型必须恰好一个，在校验里加检查 |
| 维度 | 问数拆分 | ✅ `is_dimension` / `dim` | 沿用，**不等于可筛选** |
| 敏感 | 打码 | ✅ `is_sensitive` | 沿用 |
| 枚举 | 分面取值和顺序 | ✅ `enum_name` | 沿用 |
| 分组 / 顺序 | 详情分组、列和分面的顺序 | ✅ `prop_group`、`seq` | 沿用 `seq` 作为显示顺序 |
| **可见性** | prominent / normal / hidden | ❌ 缺 | 静态补 `visibility` 枚举，默认 normal |
| **可搜索** | 关键词匹配和占位符 | ❌ 缺 | 静态补 `searchable` |
| **可筛选** | 生成左栏分面（相当于 Selectable + Low cardinality） | ❌ 缺 | 静态补 `filterable` |
| **可排序** | 表头排序 | ❌ 缺 | 静态补 `sortable`（数值和日期默认 true） |
| **列表展示** | 默认结果列 | ❌ 缺 | 静态补 `list_visible`，加可选列宽 `list_width` |
| **值格式** | 金额、百分比、单位、千分位 | ❌ 缺（现在在前端按 key 判断） | 静态补 `format`（如 `money:1` / `pct` / `int` / `raw`）和 `unit`【推断】 |
| 长文本 | 详情单独成区 | ❌ 缺 | 可选，补 `long_text` |

- 【推断】约束：`filterable`、`sortable` 和 `list_visible` 只在 `visibility ≠ hidden` 时有效；`filterable` 只建议用在枚举、布尔、日期和少量取值的字符串上，保存时给出提示，不强制。
- 新字段走变更单和发布快照，迁移 `down_revision` 指向当前 head `qa_0003`。演示类型（门店、商品、订单）的初始值写在种子数据里，取值照现有前端的 facets/columns 来定（门店：区域、城市、业态、状态可筛选；编码、店长、营业面积等列表展示），**不动 mock 文件**。

## 5. 接口需求

| 接口 | 负责 | 说明 |
|---|---|---|
| `GET /api/ontology/v1/object-types/{id}/explorer-meta` | 静态 | 一次性返回标题属性、可搜索/可筛选/列表展示的属性（含顺序、格式、枚举值顺序）和关系类型列表 |
| `GET /api/ontology/v1/action-types?targetKind=&bulk=` | Palantir | 在现有列表接口上加过滤参数；动作类型要能标明支持单个还是批量【推断：新增 `bulk` 字段，下一轮做】 |
| `POST /api/data/v1/objects/{type}:search` | 问数（对象页） | 参数是关键词、各属性筛选、排序和分页；返回行和分面计数。服务端按行级范围过滤。路径前缀【推断】，由 PDE 定 |
| `GET /api/admin/v1/me/row-scopes?objectType=` | 本体小白 | 当前用户（或预览用户）在某对象类型上的行级范围，供搜索过滤和问数范围使用 |
| `GET /api/qa/v1/session-options` | 问数 | 改造：**范围**取用户行级范围里的维度值，再加该维度的层级（如大区→区）；**口径**取所选指标定义里的口径变体（静态需在指标上提供结构化的 `variants`，比如含税/不含税；现在只有 `version` 和 `def_rows`）【推断】 |

## 6. 分工

- **问数：** 对象搜索、对象详情改成读元数据（去掉 `OBJ_TYPES` 里的手写 facets/columns，mock 只当后台断开时的回退）；问数输入框的范围和口径改读上面的接口。
- **静态：** 第 4 节的 6 个新标记（可见性、可搜索、可筛选、可排序、列表展示、值格式）加迁移、变更单字段和 `explorer-meta` 接口；指标口径变体 `variants`。
- **本体小白：** 行级范围接口 `me/row-scopes`，并支持预览用户。
- **动态：** 自查「运行」区（告警、动作中心、推演）里写死的零售概念，列清单，记进 `handoff/dynamic.md`。
- **Palantir专家：** 动作类型加 `targetKind` / `bulk` 过滤；对照本文交叉评审以上改动。
