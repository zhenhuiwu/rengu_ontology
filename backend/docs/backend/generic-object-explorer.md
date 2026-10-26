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

## 4. 属性标记（本版必需 vs 下一轮可选）

`meta` 里每个属性的 `key, name, dataType, searchable, filterable, sortable, listed, order, visibility, format, unit` 本版**全部必需**；只有 `long_text` 和动作类型 `bulk` 是「下一轮可选」。

| 标记（接口字段） | 作用 | 现状（`static.models.Property` / `ObjProp`） | 本版处理 |
|---|---|---|---|
| 主键 | 对象 id | ✅ `is_pk` / `pk` | 沿用 |
| 标题字段 → `titleProp` | 结果表第一列、详情标题 | ✅ `is_title` / `title` | 沿用；每个类型恰好一个，校验里检查 |
| 维度 / 敏感 / 枚举 / 分组 | 问数拆分、打码、分面取值、详情分组 | ✅ `is_dimension`、`is_sensitive`、`enum_name`、`prop_group` | 沿用；维度**不等于**可筛选 |
| 显示顺序 `order` | 列和分面顺序 | ✅ `seq` | 沿用 `seq`，接口里叫 `order` |
| **可见性** `visibility` | prominent / normal / hidden | ❌ 缺 | 静态补，默认 normal（必需） |
| **可搜索** `searchable` | 关键词匹配和占位符 | ❌ 缺 | 静态补（必需） |
| **可筛选** `filterable` | 生成左栏分面（相当于 Selectable + Low cardinality） | ❌ 缺 | 静态补（必需） |
| **可排序** `sortable` | 表头排序 | ❌ 缺 | 静态补，数值和日期默认 true（必需） |
| **列表展示** `listed` | 默认结果列 | ❌ 缺 | 静态补（必需） |
| **值格式** `format` / `unit` | 金额、百分比、单位、千分位 | ❌ 缺（现在在前端按 key 判断） | 静态补，如 `money:1` / `pct` / `int` / `raw`【推断】（必需） |
| **指标口径变体** `variants`（指标上） | 问数「口径」选项，如含税/不含税 | ❌ 缺（只有 `version`、`def_rows`） | 静态补（必需）【推断】 |
| 长文本 `long_text` | 详情单独成区 | ❌ 缺 | 下一轮可选 |
| 动作类型 `bulk` | 结果页批量动作 | ❌ 缺 | 下一轮可选【推断】 |

- 【推断】约束：`filterable`、`sortable`、`listed` 只在 `visibility ≠ hidden` 时有效；`filterable` 建议只用在枚举、布尔、日期和少量取值的字符串上，保存时提示，不强制。
- 新字段走变更单和发布快照，迁移 `down_revision` 指向当前 head `qa_0003`。演示类型的初始值写在种子数据里（门店：区域、城市、业态、状态可筛选；编码、店长、营业面积等列表展示），**不动 mock 文件**。

## 5. 接口需求（PDE 定稿）

| 接口 | 负责 | 返回 / 说明 |
|---|---|---|
| `GET /object-types/{id}/meta` | 静态 | `{ titleProp, properties: [{ key, name, dataType, searchable, filterable, sortable, listed, order, visibility, format, unit }] }`，字段全部必需 |
| `GET /object-types/{id}/facets?filters=...` | 静态 | `[{ key, kind: 'enum', values: [{ value, count }] } \| { key, kind: 'range', min, max }]`。取值和计数由后台按实际数据算，并随已选筛选联动；页面不写任何取值（如「华东一区…五区」） |
| 指标 `variants` | 静态 | 指标定义上的口径变体，问数「口径」从这里取 |
| `GET /api/admin/v1/me/row-scopes?objectType=` | 本体小白 | 当前用户的行级范围，供搜索过滤和问数「范围」默认值，取代写死的「华东二区」 |
| 租户 / 组织 | 本体小白 | 顶栏改读租户和组织，取代 `src/stores/app.ts` `TENANTS` 里写死的「鲜邻零售集团 / 华东事业部」 |

字段细节见 `handoff/static.md`。接口没出来前，页面先用前端 mock 里的元数据写成通用的。

## 6. 分工

- **静态本体负责人：** 第 4 节全部必需标记 + `meta` + `facets` + 指标 `variants`，排在变更单写接口之后。
- **本体小白：** 行级范围接口；顶栏租户/组织去掉写死的「鲜邻零售集团 / 华东事业部」。不做筛选项接口。
- **智能问数项目调研员：** 对象搜索、对象详情、问数工作台三页改成本体驱动（范围读行级范围，口径读 `variants`）。
- **动态本体负责人：** 「运行」下告警、动作中心、推演三页去掉写死的零售概念。
- **Palantir专家：** 对照本文交叉评审以上改动。
