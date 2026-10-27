# 静态本体交接（static-integ2 → static-r3 → static-r4）

## 2026-10-26 静态后端真分页与轻工作流目录

- 当前5类列表、属性/取值/说法、对象usage、CR列表及JSON refs/history均后端筛选分页，默认10；保留完整brief/catalog、所选编辑基线和无参数CR数组。目录必须显式完整，不以当前页冒充选项全集。
- 新只读SQL在read_pages.py、mapping_pages.py、object_usage_pages.py；repo.page共用count/限页/focus排名，adapter隔离安全JSON展开、合并、聚合和文本匹配。无新迁移、无开发库写入；历史只投影对应快照节并请求内缓存，缺节不回当前全表。
- properties/values/mappings先叠真实CR再筛选计数，isNew空基线有真实目标校验；说法派生和多维冲突使用完整词集，值停用/pub/草稿边界保留。历史定义与当前运行引用分开，必要运行接口标runtimeFromCurrent。
- GET /change-requests?workflow=true为完整轻目录：摘要+detail.touches+targets，不加载diffs/events/items；忽略页参数避免截断。普通CR page与full旧调用均兼容，full按本页批量补子表。新增knowledge/link history及link-types/catalog，详见static-api §3.0。
- 专项test_static_pagination.py覆盖SQL限页/focus、CR保存后过滤/计数、自动映射跨维冲突、JSON边界、历史不读当前定义、完整workflow与usage；另复核既有静态读、对象真实历史及指标版本窄测。前端分页由QA/PDE负责，未改mock值或删除库记录。

## 2026-10-26 指标真实口径版本与回滚

- 已实现 `POST /metrics/{id}:rollback`，只创建/加入真实草稿 CR，审核并随整版发布后生效；复用当前确认卡与 StartChangeModal，不新增字段表单。接口的可选 CR 参数、返回和错误详见 static-api.md §3.4。
- `metric/def` 发布事务保存完整定义：名称/API/分组/单位/真实负责人、公式、维度、显式变体和状态。真实发布人/时间/CR/本体发布版本记入现有 `version_rows` JSON，新 metrics 快照归档这些真实版本；无迁移。新口径版本取全部历史最大值加一，显式版本须更大，无变化不虚增。
- 首次编辑只能捕获当前可见定义基线，标记 `baseline:true`、空 release，明确记录时间并非原发布时间；旧 seed 说明行仍保留，无快照则 `canRollback:false`，不推造历史口径。版本歧义禁止恢复。
- 回滚 CR 写完整差异和新的口径版本行；加入必须带 lockVersion，任意段已有该指标内容则409，当前定义不动。非法字段或发布失败整次事务回滚，版本账本也不留下半条。
- 整版本体回滚恢复定义但不删除真实账本；没有引用的新指标仍按原语义物理消失，重建可从既有发布快照取回账本并继续递增，避免撞旧口径版本。旧 seed 快照保持原形，不改 append-only 记录。
- 专项：`tests/test_metric_versions.py`；兼容 `test_metric_variants.py`、`test_static_writeback.py` 与静态 seed/GET 读取相关窄测。前端 MetricsBody/static.ts 由前端 agent 接线，后端没有改 mock 数据。

负责人：静态本体负责人。分支：`static-integ`（变基在 pde-integ `215819d` 上）→ `static-integ2`（叠在上面）→ `static-r3`（变基到 master `48a7094`，只做变更单写接口）。
接口设计细节见 `docs/backend/static-api.md`；本文件只写做到哪、代码在哪、怎么接着做。
标注沿用 static-api.md：**[推断]** = 前端没有限制或没有体现，按页面行为推出来的。

> **没有切换的页面仍然走 mock。** 真接口只在 `VITE_API_REAL_MODULES` 包含 `static`（或 `VITE_API_MODE=real`）时生效；
> 这个开关只在本地 `.env.local` 设置，不提交。没有开关时所有页面和以前完全一样（不发请求）。

## static-r4（基于 master `b475283`，迁移头仍 static_0003，无新迁移）

### 发布写回钩子（`app/modules/static/writeback.py`）
- `POST /releases`：拿 `ontology.publish` 锁 → 合并校验 → `apply_crs` 写回定义表 → `create_version` 组快照 → CR 置已发布。全在一个事务里。
- 钩子登记：`@register(kind, section)`，签名 `async (ctx: Ctx, tid: str, rows: list[CrItem]) -> set[str]`；`ctx` 有 `session/cr/ver(vNext)/now/by/is_new`；返回改过的快照节名（如 `{"terms"}`），这些节会按定义表重建（`sections._Base.build_section` 看 `session.info["static_dirty_sections"]`）。出错用 `writeback._bad(...)` 抛 409 `WRITEBACK_FAILED`。
- 已登记：`knowledge/content`、`knowledge/status`、`object/properties`、`object/def`（= `object/config`）、`link/def`、`metric/def`、`dimension/values`、`dimension/mappings`、`dimension/qa`、`dimension/def`（= `dimension/config`）（字段与行格式见 static-api.md §2.5）。新建：`cr_target.is_new` 且表里没有 → 对象类型 / 关系 / 指标 / 维度按默认值建（`Ctx.name` = target_name 兜底显示名）；新关系必须带 `from`/`to` 行（`LinkTypesBody.vue` 草稿保存会自动带 `from`）；新维度必须带 def 段 `object` 行（维度页面还没有新建入口）。没登记的跳过，发布事件说明末尾写「；未写回定义表：kind/section」。**动态 / Palantir 要写回自己的表**：调 `writeback.register_writeback(kind, section, fn)`（契约见 static-api.md §2.5；静态没改别的模块，谁登记谁在自己模块里调）。测试 `test_register_writeback_public`。
- 回滚：7 节 `restore_section` 都已做（`writeback.restore_*`）；回滚掉的版本里新建的对象 / 指标 / 维度 / 关系物理删（对象、关系先置草稿，`restore_qa_paths` 最后 `_restore_drop` 删，仍被引用则留草稿）；物理删过的关系按快照重建。测试：`test_static_writeback.py` 7 个。

### 单号加锁（`cr_write.next_cr_code`）
- 取 CR-NNNN 前先 `get_adapter(session).xact_lock(session, "static.cr_code")`（PG = `pg_advisory_xact_lock`，同事务可重入，提交/回滚自动释放），再取 max+1。新建单、回滚单都走它。
- 其他模块要「取最大号 +1」照此写：固定锁名 → xact_lock → 查 max → insert，同一事务。没写并发测试（两连接测试成本高），靠锁保证。

### lockVersion 只 +1
- `CrService.transition` 在 `session.no_autoflush` 里做完状态流转（含 `run_checks(event=False)`，它不再 flush），最后只 flush 一次。测试：`tests/test_static_writeback.py`。

### explorer 422 中文
- 筛选值转换失败不再返回 Python 异常原文，按列类型给中文提示（日期时间 / 日期 / 整数 / 数值）。

### 前端
- `Releases.vue`：real 模式变更单载入失败（`crFallback`）→ 顶部警告条，所有写按钮禁用；重新载入成功后恢复。

### 本轮没做
- 规则 / 动作 / 事件 / 生命周期写回（其他负责人，可用 `register_writeback` 挂）；指标 def、新建维度、新建对象类型页面没有编辑入口（后端行格式先定，见 §2.5）；
- 回滚不恢复运行数据（指标当前值、取值计数、关系质量）；重建的对象 / 指标只有快照里的定义字段；
- 单号并发测试。

### 本轮评审遗留（下一轮做）
- `writeback.py:773-775` 注释与 `:780` 代码不一致，改注释或代码（PDE）。
- `restore_metrics`（`writeback.py` ~`:972`）直接物理删除，未查引用；应先查引用，被引用则保留草稿（PDE）。
- dynamic-r3 合并后补一条规则经 `register_writeback` 写回的联合测试（PDE）。
- 发布遇到未登记写回的 kind/section 会跳过但仍把单子标成已发布，只在说明里写一句（`writeback.py:133-137`）；发布响应返回 `skipped`，前端给明显提示（Palantir）。
- qa 引用了 `explorer.py` 私有函数 `_bad` / `_scalar`（及 `facet_kind` / `facetable`）；改为公开，或经 `app/contracts/static.py` 导出（qa-pages 评审）。
- 枚举类字符串筛选值没有长度上限，日期 / 数值限 64 字符，统一上限（qa）。
- 指标 `variants` 由显示名后缀推出，改为显式维护；`GET /metrics` 返回 `variants`；`variant_index` 按 status 和 `ver` 过滤。
- 对象浏览标记的后端变更单 / 快照 / 历史读取 / 回滚已补齐（见第 18 条）；前端编辑入口为 `DefinitionEditor` 属性行的标记勾选，static-t5 另加对象类型页「批量编辑」。

## 变更单写接口契约（static-r3，先定形状，供动态 / Palantir 对接）

> 前缀 `/api/ontology/v1`。**目标通用**：`target.kind` 取 `object | link | metric | dimension | knowledge | action | event | rule`，
> `target.id` = 该定义的业务编码（`member`、`K-018`、`R-007`、`adjust_region_budget`…），后台不校验目标是否存在（跨模块不做外键，同 §1.2）。
> `section` / `key` 由各页面自定（静态：`properties|links|mapping|values|mappings|def`；动作：`params|conds|edits|effects|config`；
> 事件：`payload|subs`；规则：`conds|mapping|config`；生命周期：`lc_states|lc_transitions`，同 static-api.md §1.2 CR_ITEM 注释）。
> 写接口一律带 `releases:manage`（权限桩，目前不拦）；带 `?ver=` / `?previewAs=` 时骨架统一 403 `READONLY`。
> 所有错误体照骨架：`{code, message, details}`。

**公共形状**

```jsonc
// TargetIn / TargetOut
{ "kind": "rule", "id": "R-007", "isNew": false,           // isNew 可省，默认 false（本单新建的定义填 true）
  "name": "毛利率预警" }                                    // 可省：显示名（≤128，存 cr_target.target_name，改动列表「对象」列用；TargetOut 有才出）
// ItemIn（PUT items 的 rows[]）
{ "key": "promo_price", "op": "add|update|delete",
  "before": <任意 JSON，可省>, "after": <任意 JSON，delete 时可省>,
  "type": "属性", "field": "促销价",                       // 可省：改动列表的「类型」「字段」列；省略时 type 按 kind 取中文名、field = section
  "beforeText": "", "afterText": "¥12.80" }                // 可省：改动列表显示的文字；省略时由 before/after 转成字符串
// ItemOut
{ "target": {"kind","id"}, "section": "properties", "key": "promo_price", "op": "add",
  "before": null, "after": {...}, "type": "属性", "field": "促销价", "updatedBy": "王敏", "updatedAt": "2026-10-26 09:10" }
// CRFull = 现有 GET /change-requests/{id} 的返回（mock ChangeRequest 一行 + detail + events），另加：
{ ..., "targets": [TargetOut], "lockVersion": 3 }
```
- `detail.diffs` = 种子里的改动（mock CR_DETAILS）+ cr_item 生成的改动行（key = `i<cr_item.id>`，「对象」列取 `name`，没有时用 id），`changes` = 两者条数之和（种子单子的改动数不变）。
- `detail.touches` 自动加 `"{kind}:{id}"`（合并校验 M1 用）；`detail.edit` 没有时取第一个目标（Releases.vue「编辑草稿」跳转用）。

| # | 接口 | 请求体 / 参数 | 成功 | 错误 |
|---|---|---|---|---|
| 1 | `POST /change-requests` 发起变更（新建） | `{title(1–40), reason?="", target?: TargetIn, level?="一般", reviewer?}` | 201 CRFull；`status=草稿`、owner = 当前用户、单号用 CR 序列；写事件「{me} 创建变更单」 | 422 `VALIDATION_FAILED`；409 `TARGET_IN_OTHER_CR`（`details{crId,kind,id}`，目标已在别的草稿里） |
| 2 | `POST /change-requests/{id}/targets` 加入已有 | `TargetIn` | 200 CRFull；已在本单里时幂等 | 404；409 `CR_LOCKED`（不是草稿，同其他写接口）；409 `TARGET_IN_OTHER_CR` |
| 3 | `DELETE /change-requests/{id}/targets/{kind}/{targetId}` 放弃本对象草稿 | — | 204；删掉该目标的全部 cr_item 和登记 | 404（单子或目标不在单里）；409 `CR_LOCKED`（不是草稿） |
| 4 | `PUT /change-requests/{id}/items` 保存到草稿 | `{target: TargetIn, section, rows: ItemIn[], lockVersion?}` | 200 `{id, changes, lockVersion, items: ItemOut[]}`（items = 该目标该 section 保存后的全部行）| 404；409 `CR_LOCKED`（不是草稿，`details.status`）；409 `VERSION_CONFLICT`（`lockVersion` 不等于当前）；409 `TARGET_IN_OTHER_CR`；422（op 不认识 / 同一 section 内 key 重复） |
| 5 | `GET /change-requests/{id}/items?kind&targetId&section` 读回草稿 | 三个筛选都可省 | 200 `ItemOut[]`（不分页，按保存顺序） | 404 |
| 6 | `POST /change-requests/{id}:validate` 重新校验 | — | 200 `{id, checkedAt, checks: CheckRow[17], summary:"阻断 1 · 通过 16", blocked}`；写 cr_check、`detail.checks`、`detail.times.checked`，事件「系统 完成发布校验」 | 404；409 `CR_STATE`（只允许草稿 / 已驳回） |
| 7 | `GET /change-requests/{id}/diffs?page&size` 改动 | 每页默认 10（`Releases.vue:83`） | 200 骨架 Page `{items: DiffRow[], total, page, size}`，`DiffRow{key,type,target,field,kind(新增/修改/删除),before,after}` | 404 |
| 8 | `PATCH /change-requests/{id}` 改单头 | `{title?, reason?, reviewer?, level?, migration?, lockVersion?}` | 200 CRFull | 404；409 `CR_LOCKED`；409 `VERSION_CONFLICT` |
| 9 | `GET /change-requests/draft-targets?status=草稿` 草稿登记 | `status` 可省（默认草稿） | 200 `{"CR-0011": {"metric": ["gross_margin"], "rule": ["R-007"], ...}}`，同 mock `DRAFT_TARGETS` 形状 | — |

规则（[推断] 的都按前端没限制处理）：
- **PUT items 是整段替换**：同一 `(target, section)` 的旧行全部换成 `rows`（对应 `useDraftEdits.save()` 把整段 working 存进 saved）；`rows=[]` 清空这一段。只传有改动标记的行（mark ≠ ''），没改的行不要传。目标没登记时自动登记（`isNew` 取请求里的）。
- 每次 PUT / targets / DELETE targets / PATCH 成功后 `lockVersion` **恰好 +1**（PUT 顺带登记新目标也只 +1）；`lockVersion` 不传就不校验。前端 `crSave` 读回时取 `lockVersion` 并在 PUT 里带上。
- **改动后校验作废**：PUT items、加入新目标、放弃目标后 `detail.checks=[]`（= 未校验，`times.checked` 去掉，cr_check 计算行删除；种子基线先保存）。
- **提交审核** `:submit` / **审核通过** `:approve` 在同一事务里**服务端重算校验**（不信 `detail.checks`），有阻断 → 409 `CR_BLOCKED`（`details{id, blocked:[校验码]}`，事务回滚、状态不变）；改动 0 → `CR_EMPTY`；`reviewer` 为空 → 409 `CR_NO_REVIEWER`（顺序：CR_EMPTY → CR_BLOCKED → CR_NO_REVIEWER）。新建时不传 `reviewer` 默认取本体管理员（演示身份王敏）；当前用户就是王敏时留空，需 PATCH 指定 [推断]。
- **校验** `:validate` 同步完成（static-api.md 原写异步 `jobId`，改同步 [推断]）。规则（初版，按 cr_item 推）：V7 非新建目标在 `properties|links` 段 `op=delete` → 阻断；V11 `after` 里 `desc|definition|description` 为空串 → 警告；V15 级别重大且没写迁移方式 → 警告；V12 信息（改动数、涉及定义数）；没有对应种类目标的项给「不适用」（V3/V13 关系、V8 指标、V4/V10 维度、V16 对象）；其余通过。种子单子取「种子结果与计算结果」里更严重的一档（不会把 mock 里的阻断洗掉）。
- 草稿 → 审核中后所有写接口返回 `CR_LOCKED`；驳回后 `:reopen` 回草稿即可继续写。
- **问数 T15 转同义词（qa → X3）**：新建 `POST /change-requests {title, reason, target:{kind:"knowledge", id:"K-018", name?, isNew?}}`（新词 `isNew:true`、id 由调用方给）或加入 `POST /change-requests/{id}/targets`，再 `PUT items {target, section:"synonyms", rows:[{key:"<同义词>", op:"add", after:"<同义词>"}]}`；词已在别的单子里时 409 `TARGET_IN_OTHER_CR`，按 `details.crId` 改为关联 / 加入；草稿下拉 `GET /change-requests?status=草稿`（中文状态，qa-api.md X3 写的 `status=draft` 会 422）。测试 `test_qa_feedback_to_synonym_knowledge_target`。
- 删除草稿单（已有 `DELETE /change-requests/{id}`）连同 cr_target / cr_item / cr_check 级联删除。
- 发布时 `build_section` 还没按 cr_item 展开（见「未完成」），cr_item 目前只用于草稿保存、改动列表、校验和合并校验。
- 种子单子的种子校验结果第一次 `:validate` 时存成 cr_check `target_db='seed'` 行（只存非通过的），以后每次与计算结果取更严重的一档；计算结果是 `target_db=''` 行，每次整单重写 [推断]。
- `TARGET_IN_OTHER_CR` 的“占用”指别的单子状态是草稿 / 审核中 / 已通过 / 已驳回（已发布不占用）[推断]。
- 已实现（static-r3，`cr_write.py` + `router.py` 末段），与上表一致；测试 `tests/test_static_cr_write.py`。

## 下一轮契约：其他种类（动作 / 事件 / 规则 / 生命周期）接入变更单

本轮只有静态种类真正接线；下一轮按下面的钩子扩展，形状与上面的写接口不变。
- **发布写回钩子（每种 kind 一个）**：`async def apply_cr_items(session, kind, target_id, items: list[CrItem], *, release_id)`，由 `PublishService.publish` 在**发布事务内**对本次所有已通过 CR 的 cr_item 按 kind 分组调用，写回各自定义表；失败整次发布回滚。静态 kind 由静态实现（本轮还没做，见未完成第 9 条）；`action` / `event` 由 Palantir 实现，`rule` / 生命周期由动态实现。登记方式建议同 `SECTION_OWNERS`（模块启动时注册 kind → 钩子）[推断]。
- **校验钩子（每种 kind 一个）**：`async def validate_cr_items(session, kind, targets, items) -> dict[校验码, (结果, 说明)]`，由 `run_checks` 合并（取更严重的一档），`:validate` / `:submit` / `:approve` 都会调。现在 V7 只看 `properties|links` 段的删除；动作参数、事件 payload、规则条件的“已发布字段不能删”等由各自钩子给出。
- **新建对象的行约定**：目标 `isNew:true`，所有行 `op=add`；配置类（`config` / `def` / `content`）**一个字段一行**（key = 字段名，after = 值）；列表类（`params` / `payload` / `conds` / `properties`）**一个条目一行**（key = 条目的业务键，after = 整条）。改已有对象时同样按此粒度出 update / delete 行，`before` 填原值。
- **前端读真实变更单**（PDE 的 `useDraftView`）：real 模式启动时用 `GET /change-requests/draft-targets` 和 `GET /change-requests?status=草稿` 替换 mock `DRAFT_TARGETS` / `DRAFTS`，草稿视图用 `GET /change-requests/{id}/items` 叠加；各页只调 `useStaticCr` 风格的 start / save / submit / discard。
- 本轮留下：`_next_code` 取最大号 +1 没加锁，并发新建可能撞号（单号唯一约束冲突）[推断]；下一轮用 advisory lock 或序列。

## 已完成

后台（`backend/app/modules/static/`，前缀 `/api/ontology/v1`）：
- **定义表**（static_0001）：对象类型、属性、字段映射、关系类型、问数路径（`onto_qa_path` / `_step` / `_example`）、指标及口径版本、维度 / 层级 / 取值 / 说法映射、业务知识及适用范围等；种子由 export-mock JSON 灌入。
- **总览图谱 `GET /ontology/graph`**（`service.py` `graph`）：`{nodes: LinkObject[], edges: {from,to,linkId,status}[]}`，nodes = 已发布对象类型（同 mock LINK_OBJECTS 去掉草稿储值卡），edges = 两端都在 nodes 里的关系，支持 `?ver=`。
- **26 个只读 GET**：对象类型 7、关系类型 5、指标 4、维度 6、业务知识 4（`router.py:86-267`），支持 `?ver=` 读历史版本（§2.7）和 `?previewAs=`，返回记录与 mock 同形（`shapes.py`）。
- **7 个快照 section**（`sections.py`）：objectTypes、properties、links、metrics、dimensions、terms、**qaPaths**（已在骨架 `SECTION_OWNERS` 登记）；`seed_section` 按版本规则裁剪，`build_section` 发布时组装。
- **static_0002**（`change_request` + `cr_event`，down = `event_0002`；dynamic_0002 会接在它后面，**不要改名或重新编号**）。
- **合并校验 / 整版发布 / 回滚**：`POST /releases:merge-check`、`POST /releases`、`POST /releases/{ver}:rollback`（`publish.py`，经版本层 `create_version`）。
- **变更单**：`GET /change-requests`（`?status`、`full=true`、`kw`；带 `page`/`size`/`focusId` 任一时按骨架 `PageParams.clamp` / `Page.of` 分页，每页默认 17，`focusId` 返回所在页；都不带返回整个数组，Releases.vue 现在仍整表载入）、`GET /change-requests/{id}`；6 个状态动作 `:submit` / `:withdraw` / `:approve` / `:reject` / `:unapprove` / `:reopen`；`DELETE /change-requests/{id}`（`crs.py`）。

- **通用对象浏览元数据**（static_0004，分支 `static-meta`）：属性显示标记（visibility/searchable/filterable/sortable/list_visible/list_width/format/unit/backing_column）+ 指标 `variants`；`GET /object-types/{id}/meta`、`GET /object-types/{id}/facets?filters=`（`explorer.py`）。形状见 static-api.md §3.2.1。
  - **迁移 `static_0004_explorer_meta.py` 现在 `down_revision='qa_0003'`；叠到 static-r3 上时由 PDE/静态改成 `'static_0003'`**（static-r3 新增 static_0003 并改 models.py；本次 models.py 只在 Property / Metric 末尾加列，冲突应只是相邻行）。

前端：
- **5 个页面切真接口**：对象类型、关系类型、指标、维度、业务知识。页面外面包一层 `parts/StaticGate.vue` 整表载入（`src/api/static.ts` `loadStatic`），页面本体移到 `*Body.vue`，mock 模式下渲染不变。
- **变更与发布（Releases.vue）**：真接口模式下载入变更单列表和详情；6 个动作、删除草稿、发布、回滚都写库，成功后重新载入，失败弹错误提示、页面不变；发布 / 回滚后按 `/releases/current` 刷新顶栏版本号。

**static-r3：变更单写接口**（契约见上节，`8b0a27e` 先提交）
- 后台：迁移 `static_0003`（down = `qa_0003`）建 `cr_target`（种子 = mock DRAFT_TARGETS）、`cr_item`、`cr_check`、`cr_impact`、`cr_trial`（后两张只建表）；`cr_write.py` `CrWriteService`；`crs.py` 详情加 `targets` / `lockVersion`、`detail.diffs` 合入 cr_item 行、`:submit` 加 `CR_NO_REVIEWER`；路由 9 个（上表）。
- 前端：`src/api/static.ts` 加 9 个接口函数和 `crStart` / `crSave` / `crSubmit` / `crDiscard`；新文件 `src/pages/ontology/parts/useStaticCr.ts`；5 个定义页 real 模式下“发起变更 / 加入”“保存到草稿”“停用”“提交审核”“放弃草稿”先写库再改页面（失败弹错、页面不变），mock 模式行为不变、不发请求：
  - 对象类型：发起 / 加入、新增属性（`properties` 段，key = API 名）；
  - 关系类型：发起 / 加入（含新建关系 `isNew`）、定义（`def` 段，一个字段一行）；
  - 指标：发起 / 加入（页面没有别的编辑入口）；
  - 维度与码表：发起 / 加入、取值新增 / 修改 / 停用启用（`values` 段，key = 层级:编码）、新增说法（`mappings` 段）、问数设置（`qa` 段）；
  - 业务知识：发起 / 加入（含新建条目 `isNew`）、内容（`content` 段）、停用（`status` 段）。
  - `crSave` 先 `GET items` 读回该段，再合并本次改动整段 PUT：同一 key 保留最早的 `before`，改回原值的 update 行去掉。
- 审查修正（Palantir）：`:submit` / `:approve` 服务端重算校验；PUT / 目标增删作废旧校验；`/targets` 非草稿改 `CR_LOCKED`；lockVersion 每次写恰好 +1、前端 PUT 带 lockVersion；`:validate` 只限草稿 / 已驳回。
- 验证（static-r3，审查前）：pytest 596 passed / 2 skipped（库 `rengu_test_static_r3`，静态 72 个含写接口 18 个）；ruff check / format 通过；alembic 单 head `static_0003`，upgrade 后 check 无差异；vue-tsc、`npm run build` 通过。

验证（`743ec27`）：pytest 399 passed / 1 skipped（库 `rengu_test_static_integ`）；ruff 通过；alembic 单 head `static_0002`；vue-tsc、`npm run build` 通过；浏览器脚本验证真接口删除草稿后刷新仍有效，mock 模式不发请求。

## 未完成

每条：缺什么 / 为什么 / 从哪开始。

1. **回滚恢复定义表**（static-r4 已做全部 7 节 `restore_section`；下面是当初的评估，留作背景）
   - 缺：静态 section 没有实现 `restore_section`，整版回滚只复制快照、生成新版本，定义表不变，回滚后当前读接口仍是回滚前的定义。
   - 为什么：恢复要把快照节反写回十几张有外键的表（对象→属性→映射、关系→路径→步骤等），风险大，本期先不做 [推断，第二期]。
   - 评估（01:16，本轮决定不做）：版本层已在 `app/modules/versioning/sections.py:144` `restore_sections` 里用 `session.begin_nested()` 包好、按登记顺序调钩子，只差静态这边实现。难点是快照节是**有损**的（`shapes.py` 输出形状，不含运行数据；草稿行仍在定义表里；history 是合并后的数组），要为 7 节各写一个“快照 → 多张子表”的反向映射。以种子为例，回滚 v2026.10.3 时只有 `terms` 不同（K-011 是 v2026.10.3 新建，K-010 的 history 多一条），其余 6 节相同。
   - 建议做法：先只做 `TermsSection.restore_section`——content 里没有的知识设 `stopped_release` 或删除（连同 `knowledge_applies`），有的按 content 更新定义列；测试：回滚后 `GET /knowledge` 返回旧定义、K-011 不见。其余节照此逐个加，每节一个测试。
   - 从哪开始：`app/contracts/versioning.py:115` `RestorableSection.restore_section(session, content, *, release_id)`；在 `sections.py` 各 `*Section` 类上实现（按外键顺序删 / 插）。版本层回滚时在同一事务里调用。注意 static-api.md §2.2-6 写的签名少了 `release_id`，以 contracts 为准。
2. **`onto_qa_path` 唯一约束冗余没清理**
   - 缺：表上同时有 `(from_object_id, to_object_id, seq)` 和 `(group_key, seq)` 两个唯一约束；应保留前者、删掉 `uq_onto_qa_path_group_key_seq`。
   - 为什么：原计划在 static_0002 里顺带清理，但 static_0002 只做了变更单表；`group_key` 作为 seed 业务键仍在用。
   - 从哪开始：`app/modules/static/models.py:337` 删掉 `UniqueConstraint("group_key", "seq")`；在 static_0002 的 upgrade/downgrade 里 drop/create `uq_onto_qa_path_group_key_seq`（原约束建在 `static_0001_static_definitions.py:791`）。迁移文件名和 revision 不改。
3. ~~`GET /ontology/graph` 没做~~ **已完成**（见「已完成」）。总览页本身仍没切换（第 8 条）；储值卡这类草稿对象不在 nodes 里 [推断]。
4. ~~变更单列表不分页~~ **已完成**（见「已完成」）。仍不随 `?ver` 变 [推断]。
5. **`lockVersion` 收下但没校验**
   - 缺：`POST /releases` 的 `lockVersion` 没和当前版本比对。
   - 为什么：发布串行靠版本层的事务锁保证 [推断]；前端也没有传可比较的值。
   - 从哪开始：`schemas.py:37-41` `PublishIn.lock_version`、`publish.py:135` `PublishService.publish`；不一致时返回 409。
6. **`CR_DETAILS.json` 是一次性导出的**（PDE 的文件）
   - 缺：`backend/seeds/extra/static/CR_DETAILS.json` 是用 vite ssrLoadModule 手工导出的，`npm run export-mock` 不导出 `CR_DETAILS`。
   - 为什么：export-mock 是 PDE 的脚本，我不能改。
   - 从哪开始：`scripts/export-mock.mjs`（PDE）把 `src/mock/changes.ts` 的 `CR_DETAILS` 导到 `json/`；之后改 `app/modules/static/seeds/__init__.py:504`（`extra:CR_DETAILS.json`）改读 `json/`，再删掉 extra 文件。
7. **启动时当前版本只在 versioning 切真接口时才取**（PDE 的文件）
   - 缺：`src/api/index.ts:40-44` 经 `loadReleases()`（`src/api/releases.ts:41`，`versioning` 开关）读 `/releases/current`；只切 `static` 时，启动时的顶栏版本号仍是 mock。
   - 为什么：index.ts / releases.ts 是 PDE 的文件；Releases.vue 只在发布 / 回滚之后用 `fetchCurrentAfterWrite` 刷新。
   - 从哪开始：PDE 决定 static 真接口时是否也调 `/releases/current`。
8. **总览页（OntologyOverview.vue）没切换**
   - 缺：仍读 mock（links / actionEvent / metrics / objectProps / changes / quality / releases）。
   - 为什么：这是共用页，数据跨静态 / 动态 / 质量模块。
   - 从哪开始：graph 接口已有（`/ontology/graph`）；页面里 `LINK_OBJECTS` / `LINKS` 换成接口数据，其余卡片和各模块负责人商定。
9. ~~变更单写路径没做~~ **接口和定义页保存已完成（static-r3）**，剩下：
   - **发布不展开 cr_item**：`sections.py` `build_section` 仍是 base 不为空就沿用上一版，草稿行发布后不进定义表 / 快照。从 `publish.py` `PublishService.publish` 取本次 CR 的 cr_item，按 kind/section 写回定义表开始。
   - **影响分析 / 试跑**：`cr_impact`、`cr_trial` 只建表没写，详情仍用 `detail.impact` / `detail.trial`（种子原样）。
   - **页面读草稿视图仍是前端内存**：刷新后草稿视图里的改动丢失（库里有）；需要按 `GET /change-requests/{id}/items` 把行叠回页面（各页 mark 逻辑不同，逐页做）。
   - **`useDraftView` 的登记表读 mock `DRAFT_TARGETS`**（PDE 共用文件）：real 模式应在载入时用 `GET /change-requests/draft-targets` 替换；`DRAFTS` 不是 reactive，新建单子后“加入已有”下拉要重开页面才看到（`StartChangeModal` 的 computed 只跟 `created`）。改法：useDraftView 加一个 `syncTargets(map)`、`DRAFTS` 包 reactive（PDE 决定）。
   - **StartChangeModal 的“变更说明”没传出来**（共用组件），所以 `reason` 目前不落库。
   - **权限**：写接口暂挂 `releases:manage`，`require_func` 是桩不拦；应改成目标页:manage（按 kind 映射页面）。
   - 新建关系 / 知识的 id 仍是前端生成（`new_link_N`、`K-NNN`），后台不分配 [推断]。
10. **早期定义页写接口待办（历史记录）**：指标口径回滚已于2026-10-26实现，见本文顶部；其余列举的接口由各专项交接记录当前完成度（早期名单曾含指标试算、维度匹配试算、知识解析、对象导入和函数接口）。
11. **对象历史 `kind=cr` 没接**：`service.py:211` TODO，现在可以从 cr_target 查。
18. **本轮没做**（PDE 指示单独提）：第 1 条 restore_section、第 15 条 Releases.vue `crFallback`；Releases.vue 的“重新校验”按钮也还没接 `:validate`。
12. **`semantic_map` 契约已实现（static-t5）**：`app/modules/static/semantic_map.py`，`app/contracts/static.py` 导出（static-api.md §3.7）。
13. **草稿项仍在定义表**：草稿 / 已驳回的关系、知识、取值仍放在定义表里，用 `status` / `draft_cr` 区分（`models.py:12-25`）；按 §1.3 应移到 cr_item，依赖第 9 条。
14. **种子里已删除的 CR**：开发库 `rengu_static_integ` 里 CR-0013、CR-0012 在测试中被删了，需要的话重新灌种子。
15. **Releases.vue 变更单回退 mock 时写按钮仍走真接口**（审核意见，`Releases.vue:39-47`）
   - 缺：`loadCRs` 失败时列表显示 mock 变更单，但提交 / 审批 / 删除 / 发布按钮仍调真接口，会 404/409。
   - 从哪开始：失败时记一个 `crFallback` 标记，为 true 时禁用写按钮或提示「演示数据」。
16. **发布 / 回滚后端不校验权限**：前端只用 `isAdmin` 拦，`require_func` 目前不拦（权限是初版）。从 `router.py` 发布 / 回滚路由加功能码开始。
17. **`/ontology/graph?ver=` 不按版本筛节点**：从快照 `objects` / `links` 节取，对应第 8 条总览页切换。

### [推断] 决定清单（前端没定义，后台自行定的；请用户 / PDE 确认）

- 变更单错误码：状态不对 409 `CR_STATE`、有阻断 409 `CR_BLOCKED`、没有改动 409 `CR_EMPTY`（`crs.py:14`）。
- 删除草稿是**硬删**（表没有软删除列，前端也没有恢复入口），`cr_event` 级联删除；static-api.md 原写的是软删（`crs.py:16`）。
- 只有 owner 能提交 / 编辑、回滚只限管理员：**都不做**（前端不限）。驳回原因后台不限定取值。
- 变更单列表不带分页参数时返回整个数组（兼容现有前端），带了才分页；不随 `?ver` 变（变更单不进快照）（`router.py` `list_change_requests`）。
- `note` 可省，不传时用变更单标题拼一句；`lockVersion` 不校验（`schemas.py:37`、`publish.py:149`）。
- 合并校验数据取 `change_request.detail`（mock CR_DETAILS 原样）（`publish.py:4`）。
- 回滚单：提交人 = 审核人 = 本人，直接已发布，改动 0；回滚只复制快照，不改定义表。
- `cr_event` 原样存 `ReviewEvent{time,text,desc}`，另加 `action` 码；owner / reviewer 存姓名（mock 里的周晓峰不在 sys_user）。
- 版本号列存字符串、不建外键（mock 里有不在 RELEASES 里的旧版本）；变更单号在定义表里存字符串（`models.py:12-25`）。
- `link_type` 两端同时存对象 code 和可空外键（草稿关系指向还不是对象类型的 stored_card）。
- `dim_mapping` 按维度单独建，统一 synonym 表以后再做；`knowledge.synonyms` 存 jsonb；`dim_level` 只存当前层级，`levelsBefore` 整块存 jsonb。
- `onto_qa_path` 冗余存 `group_key`；组级 V13 说明只存在默认路径行；`path=-1` 用 `path_id` 为空表示。
- 对象类型状态、指标列表状态另起枚举；对象的 `lifecycle` 加了 experimental / deprecated。
- 种子：export-mock 没导出的页内常量照抄（ORDER_MAP_ROWS、Metrics.vue DENY、ObjectTypes.vue TYPES 草稿状态）；非对象关系端点只存 code；METRIC_DEF 等共用示例存到每个指标（`seeds/__init__.py`）。
- 快照里没有该节（版本早于静态登记 section）时按当前表 + §2.7 规则现算（`service.py:6`）。
- `GET /dimensions/{id}?full=true` 返回完整 DimItem，给前端整表载入用。
- `/ontology/graph`：nodes 只取已发布对象类型，草稿储值卡不出；edges 多带 `status`。
- 前端：草稿对象（如 CR-0012 的储值卡）静态接口不返回，前端保留 mock 的草稿项（`src/api/static.ts:62`）。
- 行级范围：「商品.品类」范围值按名称最接近的一级类目映射（`contracts/static.py`）；「会员.常去门店所属大区」按注册门店所属大区（static-api.md §5-15）。
- 其余见 static-api.md 正文的 [推断] 标注和 §5 第 18 条。

18. **对象浏览属性标记生命周期已补齐（2026-10-26）**：`searchable/filterable/sortable/listed/listWidth/visibility/format/unit/order` 经对象 properties 变更单写回、属性接口、当前快照、新发布快照、整版回滚和 `/meta?ver=` 全链路保存。布尔/可见性/整数范围/可空格式与单位按定义类型校验；hidden 不可筛选/排序/列表展示，敏感属性不可分面，受控 `backing_column` 不开放变更。既有种子版本快照保持旧形状以兼容 append-only 发布表；旧快照读取/恢复只由静态初值和原始种子顺序补齐标记，不借用当前修改。新发布复用旧节时也会补齐字段。验证见 `test_static_explorer.py`、`test_static_writeback.py`。**仍待后续**：指标 `variants` 仅在 meta.metrics 返回，`GET /metrics` 未加显式变体字段（同第 21 条）。
19. **facets 只覆盖 sql_guard 登记过的类型**（门店、订单、商品等）；其他类型 `hasData:false`、空取值。新类型要先在 sql_guard 登记业务表，再在 `explorer_meta.json` 写 `backing`。行级范围 `row_filter` 目前返回 None（本体小白接入后自动生效）。
20. **long_text 已做（static-t5）**；动作 `bulk` 属动作模块（`action/models.py` ActionType 无 bulk 字段）与对象使用页，静态不做，已报告。
21. **（下一轮）指标 `variants` 是按显示名末尾「（…）」推出来的**（`explorer.metric_variants`）：名字不规范的指标不会成组。应改为在指标定义 / 变更单里显式维护变体，种子只做初值。
22. **（下一轮）行级范围的分面测试**：现在只用 monkeypatch `auth.row_filter` 证明 facets 调了 `apply_row_filter`（`test_facets_apply_row_filter`）；本体小白接入真实 `row_filter` 后补一条按角色范围的端到端测试（含 DENY_ALL → 全部计数 0）。

## 给 PDE（static-r3 合并时注意）

- 迁移 `static_0003` 的 `down_revision` 合并时已由 PDE 改为 `"admin_0004"`（原为 `qa_0003`），revision 名不变。
- 动了 PDE 的共用文件：`src/pages/ontology/parts/useDraftView.ts` 只**新增**导出 `forgetTarget(cr, kind, id)`（放弃草稿后从登记表删目标），没改已有函数。
- 需要 PDE 改的共用文件（我没动）：`StartChangeModal` 把“变更说明”随 `confirm` 发出来；`useDraftView` 加从服务器同步登记表的入口、`DRAFTS` 变 reactive（见未完成第 9 条）；OntologyOverview.vue 按指示没碰。
- `require_func` 是桩，写接口暂用 `releases:manage`；权限落地时改成目标页功能码。

## 代码在哪

| 部分 | 文件 |
|---|---|
| 路由 | `backend/app/modules/static/router.py`（只读 GET 用 `get()` 包装；变更单 / 发布写接口在后半段） |
| 只读取数 | `service.py`（筛选 / 分页 / `?ver=`）、`repo.py`（整表读取）、`shapes.py`（ORM → mock 形状）、`history.py`（§2.7 版本规则） |
| 快照 section | `sections.py`（7 节）；契约 `app/contracts/versioning.py`、`app/contracts/static.py` |
| 变更单 | `crs.py`（`ChangeRequestService`：list / detail / transition / delete）；写接口 `cr_write.py`（`CrWriteService`） |
| 发布 / 回滚 | `publish.py`（`merge_check`、`PublishService.publish / rollback`） |
| 模型 / 入参 | `models.py`、`schemas.py` |
| 种子 | `app/modules/static/seeds/__init__.py`；数据 `backend/seeds/json/`（export-mock）+ `backend/seeds/extra/static/CR_DETAILS.json` |
| 迁移 | `backend/migrations/versions/static_0001_static_definitions.py`、`static_0002_change_request.py` |
| 对象浏览 | `explorer.py`（标记默认值、variants 分组、meta / facets）、`seeds/explorer_meta.json`、`static_0004_explorer_meta.py`、`tests/test_static_explorer.py` |
| 测试 | `backend/tests/test_static.py`（读接口）、`test_static_cr.py`（变更单）、`test_static_publish.py`（发布 / 回滚） |

| 迁移 | `backend/migrations/versions/static_0001_static_definitions.py`、`static_0002_change_request.py`、`static_0003_cr_target_item_check.py` |
| 测试 | `backend/tests/test_static.py`（读接口）、`test_static_cr.py`（变更单）、`test_static_publish.py`（发布 / 回滚）、`test_static_cr_write.py`（变更单写接口） |
| 前端 API | `src/api/static.ts`（`loadStatic`、`fetchChangeRequests`、`crAction`、`deleteCR`、发布 / 回滚、`fetchCurrentAfterWrite`） |
| 前端页面 | `src/pages/ontology/{ObjectTypes,LinkTypes,Metrics,Dimensions,Glossary}.vue`（包 `parts/StaticGate.vue`）+ 对应 `*Body.vue`；`Releases.vue`；写库接线 `parts/useStaticCr.ts` |
| 接口文档 | `docs/backend/static-api.md` |

## 下一步怎么接

1. 本地起服务：开发库 `DATABASE_URL=postgresql+asyncpg://…/rengu_static_integ`，`alembic upgrade head` 后灌种子；前端在 `.env.local` 设 `VITE_API_REAL_MODULES=static`（**不提交**），代理到后台。
2. 测试：`RENGU_TEST_DB=<自己的测试库> pytest -q`，`ruff check . && ruff format --check .`，`npx vue-tsc --noEmit`，`npm run build`。
3. 建议顺序：第 1 条（restore_section，在 `begin_nested` 里按外键顺序写，测试：回滚后 GET 返回旧定义）→ 第 15 条 → 第 9 条剩余（发布展开 cr_item）。
4. 新迁移接在当时的单 head 后面；不要改 static_0002 的 revision。
5. 每做完一条，更新本文件「已完成 / 未完成」，并在 static-api.md 对应行写「已实现」。

## 2026-10-26 静态试算与轻工作流补充

- 5个Body与Releases的工作流摘要统一使用`GET /change-requests?workflow=true`；完整轻目录不受page/size/focus截断，不载入items/events/整份detail。列表保持真实SQL分页，单选详情单独读取。
- 指标现有“试算”按钮接`POST /metrics/{id}:trial {crId?,range:"30d"}`，不落库、不回退mock。按指定变更单已保存行覆盖公式/版本，当前公式不改；前端按身份/版本/选中指标/草稿与lockVersion隔离异步结果。mock原行为不改。
- 公开`app.contracts.static.evaluate_metric(session,user,code,*,ver=None,cr_id=None,days=30,period="day")`；days限定1..93，period为day/month；结果rows.value是十进制字符串或null，空数据不补日期/零值，dataCutoff是实际可见数据最大时间。只执行单受治理对象的SUM/COUNT/AVG/MIN/MAX、四则、明确属性标量比较；经sql_guard注入继承行级范围。
- 当前演示指标存在缺退款/成本实体、自然语言公式、共用的时间定义不匹配等，真实试算明确422 `METRIC_TRIAL_UNAVAILABLE`；不推造税率、默认业务过滤或缺失历史映射。没有新增时间字段表单，完整执行定义的设计仍待用户决定。历史快照没有完整执行映射时不借用当前映射。纯POST允许ver/preview；真正写操作仍统一403。
- 现有“重新校验”及提交/通过前校验同事务生成真实静态影响引用，写cr_impact并刷新detail.impact；覆盖关系端点、维度归属、公式明确提及、允许维度、显式variants及知识适用范围。detail.analysisScope明确当前仅静态结构，运行模块未连接的下游计数不推测。
- 单指标已保存formula/version改动，可执行时按近90日真实数据重聚合为月，分别执行旧/新口径，写cr_trial与detail.trial；缺执行语义时无虚假对比，detail.trialUnavailable说明原因、V14警告。多个指标不能混合不同单位，按现有cr_trial每月唯一模型明确未执行；如需同时对比须先设计指标分组。
- 试跑值带trialActor；CR详情/full列表仅原试算用户可见业务数值，preview按预览用户判断；其他人保留不可见原因。再保存/放弃目标会清除生成的旧分析与试跑，防止展示过期值。
- semantic_map未发现前端入口/调用者，没有发明HTTP或表单；进程内契约已于 static-t5 实现。
- 窄验证：独立库`rengu_static_finish_test`的`test_static_trials.py`、`test_static_cr_write.py`及workflow轻目录专项通过；`vue-tsc --noEmit`通过。全量统一验收由主任务完成。

### 2026-10-26 后续补齐

- 真实行级权限已接数据库角色成员/对象授权/范围，未知或无授权默认拒绝，不再使用“登录即全量”的row_filter桩；详情见`backend/docs/backend/row-access.md`。新测试包含真实业务API、分面、继承、撤权与错误映射；开发角色与业务记录未改。
- 指标现有表单可用最小公式：新指标填写`COUNT(order.created_at) WHERE order.status <> 已取消`、单位单、v1、真实负责人。公式明确COUNT唯一日期属性、且没有既有时间配置时，直接按该明确事件时间试算；不加新表单、不选默认业务日期。闭环测试创建→保存CR→审核→发布→试算→编辑公式*2/v2→发布→试算通过，实际新指标`def_rows=[]`。原演示指标不改，错误/缺失执行定义继续明确422。
- 影响分析通过公开`app.contracts.dynamic.definition_usages`合并真实已发布规则和当前生命周期引用，可由静态结构影响扩展到下游；不读演示命中计数。试跑记录权限摘要，当前身份/数据权限变化后隐藏旧业务数值。
- Releases改动清单dpage接既有`/change-requests/{id}/diffs`真实SQL分页；后端旧JSON按ordinality在前、CrItem按seq/id在后，SQL UNION/COUNT/LIMIT/OFFSET后仅装本页行/目标名，默认10并越界收拢，不加载events/完整detail。选单GET支持`includeDiffs=false`省去全量CrItem；默认true不影响完整编辑缓冲。专项验证30条混合旧/新改动、页2/末页、轻详情、完整兼容均通过。

### 2026-10-26 完整维度回滚与遗留快照校验

- 完整恢复先校验全部目标维度的层级格式、取值键唯一性及有效取值的层级引用，校验失败不删除/修改现有定义。层级与取值按目标快照在同一事务恢复；仅编辑层级时，当前有效取值的删层防护继续保留。
- 快照外的当前取值仍保留为失效历史，不物理丢失；恢复后所有有效取值须引用目标层级。`restore_sections`任一节失败时，先前节的写回一并回滚。
- 原始`v2026.09.3`的`org`快照只有“省份/门店”层级，却含6条有效“大区”取值，属于已有不一致历史数据。本轮不改mock、seed、开发库或猜造历史省份数据；对此快照明确`WRITEBACK_FAILED`，不能宣称已成功回滚。动态section单测仅登记其自身builders；静态专项单独证明坏旧快照拒绝和全事务原子性。
- 专项：`test_dimension_levels.py`与动态section历史恢复节点通过；测试库为独立`rengu_reg_roles_test`。

## 2026-10-27 新基线（4a1293b → f3b8119）逐条复核

| 原条目 | 状态 | 依据 |
|---|---|---|
| 未完成1 回滚恢复定义表 | 完成 | writeback.restore_*；维度整版恢复先校验（handoff 末节） |
| 2 onto_qa_path 冗余唯一约束 | 完成 | migrations/static_0012_qa_path_unique.py；models.py 已无 group_key 唯一约束 |
| 5 POST /releases lockVersion | 完成 | publish.py:148-153 不一致 409 |
| 6 CR_DETAILS.json 一次性导出 | 未做（PDE） | seeds/extra/static/CR_DETAILS.json 仍在；seeds/__init__.py:529 |
| 7 启动时读 /releases/current | 未做（PDE） | 仅 api/releases.ts（versioning 开关）+ static.ts fetchCurrentAfterWrite |
| 8 总览页切真接口 | 部分 | OntologyOverview 图谱/概览/待办/发布走 api/overview.ts；仍 import mock links/actionEvent/metrics/dimensions/changes/quality（:22-29，mock 分支） |
| 9a 发布展开 cr_item | 完成 | writeback.apply_crs（publish.py:168） |
| 9b cr_impact / cr_trial | 完成 | handoff「试算与轻工作流补充」 |
| 9c 页面刷新恢复草稿视图 | 完成 | build-definition-forms.md「刷新与并发」 |
| 9d useDraftView 登记表读 mock | 完成 | useDraftView.ts:27 real 模式不读 DRAFT_TARGETS |
| 9e StartChangeModal 说明不传 | 完成 | StartChangeModal.vue:29 emit desc |
| 9f 写接口权限 | 部分 | require_func 已真实校验（common/auth.py:227），但 CR 写仍统一 releases:manage，未按目标页映射 |
| 9g 新建关系/知识 id 前端生成 | 完成 | targets:allocate（build-definition-forms.md） |
| 11 对象历史 kind=cr | 完成 | object_usage_pages.py:47 |
| 12 semantic_map 契约 | 完成（static-t5） | static/semantic_map.py；contracts/static.py 导出 |
| 13 草稿项仍在定义表 | 评估后不做（static-t5 设计说明） | models.py:15-17、559、584 draft_cr |
| 15 Releases crFallback | 完成 | static-r4 |
| 16 发布/回滚后端权限 | 完成 | releases manage 真实校验 + /admin/v1/me 能力 |
| 17 graph ?ver= | 完成 | service.py graph 按快照 |
| 19 facets 仅 sql_guard 登记类型 | 未变（设计限制） | explorer.py |
| 20 long_text / 动作 bulk | long_text 完成（static-t5）；bulk 属动作模块 | explorer.LONG_TEXT；static-api.md §3 |
| 21 指标 variants 显式维护 | 完成 | build-completion-checklist B；shapes.py:229 GET /metrics 返回 variants |
| 22 行级范围分面端到端测试 | 部分 | 真实 row_filter 已接（row-access.md），分面 DENY_ALL 端到端用例未见 |
| 评审：writeback 注释与代码不一致 | 待核（行号已漂移） | writeback.py |
| 评审：restore_metrics 查引用 | 完成 | writeback.py:1242-1267 |
| 评审：发布 skipped 提示 | 完成（后端） | router.py:426 返回 skipped |
| 评审：qa 引用 explorer 私有函数 | 完成 | 已公开 bad_filter/scalar_filter/facet_kind/facetable |
| 评审：筛选值长度上限 | 完成 | explorer.py:402 FILTER_TEXT_MAX=64 |
| 新：引导式构建 / 新建表单 / 函数页 | 已存在 | BuildGuideDrawer.vue；build-definition-forms.md；pages/functions（checklist F:63） |
| 新：多本体同名发布/回滚 | 完成（static-t0） | tests/test_static_build_writes.py：空间 1/2 同编码对象各自发布、回滚互不影响 |

### static-t0 本轮改动（2026-10-27）

- **测试夹具**：静态测试文件各自的 `seeded/facts` 夹具在 `grant_fixture_roles` 后补 `bind_workspace_members`（tests/helpers/workspace.py），修掉 136 条「用户没有此本体工作空间的访问权限」403。
- **陈旧用例按现行规则改**：提交前校验已拦新建关系缺 API 名/源端属性不存在（V3）、新建维度缺来源（V4）；种子 R-02（陈立军）已含 releases·manage，非管理员 403 改用任明（U010259）。
- **路由权限表**：`/metrics/{id}/trial-readiness` 改走 `get()` 登记进 `FUNC_OF`（原来漏登记，test_static_routes_func_permissions KeyError）。
- **指标执行口径可经 CR 编辑**：metric/def 新增 `timeField`/`defaultFilter` 两个 key（契约见 static-api.md「编辑定义」行）。之前这两行只能直接改库，CR 写回静默忽略，导致 15 个已有指标都无法经正规流程变为可试算。快照 metrics 节加 `execDef`，整版回滚与口径版本回滚一并恢复；口径版本 definition 加 `execDef`（FIELDS 不变，旧账本行不受影响）。测试：test_static_trials.py::test_metric_time_field_and_filter_via_cr_publish_and_rollback。
- **两空间发布/回滚**：tests/test_static_build_writes.py。注意新空间首版没有上一版，不能回滚（versioning/service.py:89 `latest.parent is None` → ROLLBACK_NOT_LATEST，设计如此，提示文案可改进）。
- **ruff**：app/modules/static 与 tests/test_static*.py 已 format + check 全过。
- **非静态问题（只报告）**：test_static.py::test_migration_round_trip 降级穿过 migrations/versions/notify_0022_inbox.py:67 时被有意阻断（RuntimeError），静态模块往返测试因此必然失败，需 notify/PDE 决定是否让 round-trip 只到 notify_0022 之上或给测试豁免。
- （仅供参考，不在静态范围）早先的具体指标口径草稿在 /workspace/scratch/static-t0/metrics-plan.md；按老板决定（通用本体平台），静态不修任何业务指标口径/取值，代码不含零售硬编码。

## static-t1（基于 master `c3db490`，迁移头 admin_0025，本轮无新迁移）

静态范围：保证指标可定义、编辑、发布、回滚、试算；不修业务指标口径或数值。

- **指标表单执行口径**：DefinitionEditor 指标段新增「时间字段」「默认过滤」两输入（≤256字、禁 NUL，占位符为通用格式 `对象API.属性API`）。回填取 `GET /metrics/{id}` 新增的 `execDef`（shapes.metric_detail），保存经 `definitionForms.execChanges` 生成 metric/def 项：新指标非空→add；已有值清空→delete（写回拒空值，必须 delete）；其余 update。
- **批量试算就绪（只读）**：`GET /metrics/trial-readiness?ids=&crId=`（metric_trial.metrics_readiness，复用单指标判定，最多200个）。BuildGuideDrawer 第3步显示「（ready/total 可试算）」，看历史版本时不显示，失败显示「可试算数加载失败」。
- **MetricFlow 导出（只读）**：`GET /exports/metricflow?format=json|yaml`（app/modules/static/metricflow_export.py），以试算编译器为准：可试算的才导出，其余进 skipped；敏感/隐藏属性不导出；行级范围不导出（notes 写明，消费方须自行过滤）；不支持 `?ver=`。契约见 static-api.md §3.4。
- **测试**：test_static_trials.py::test_batch_trial_readiness_counts_and_reports_each_metric、::test_metricflow_export_matches_trial_compiler；test_static.py 指标详情断言 execDef。

### 给 PDE（非静态，只报告）

- **首版回滚文案**：新本体/新空间的首个发布没有上一版，`versioning/service.py:89`（`latest.parent is None`）返回 `ROLLBACK_NOT_LATEST`，文案读起来像「不是最新版」。建议：改专门的错误码/文案（如「首个发布版本没有可回滚的上一版」），并在前端发布列表对首版隐藏回滚按钮。
- **迁移往返**：test_static.py::test_migration_round_trip 降级穿过 `migrations/versions/notify_0022_inbox.py:67` 时被有意阻断（RuntimeError），仍未解决，需 notify/PDE 决定。

## static-t3（基于 master `f0a7df3`，本轮无新迁移）

- **#8 总览去 mock 导入**：`OntologyOverview.vue` 不再 import `@/mock/*`；演示数据入口放到 `src/api/overview.ts`（`DEMO_DATA_AS_OF`、`demoFailedTypes`、`demoCards`、`demoTodos`、`demoCrOfRelease`、`demoAddChange`），演示模式行为不变（只搬入口，不碰 mock 数据一致性）。顺带修：真接口模式下发布记录仍去查 mock 变更单（`crOf`），现在只在演示模式查；待处理接口报业务错误时清掉旧列表。
- **能力目录 events 节**（Palantir 要求）：`events[]` 按 event-types 页 view 放行，读 `event.models.EventType` / `EventPayloadField`，不含样例与订阅方；`ingest` 给接收接口与 `accepting`，密钥调用另给 `keyAllowed`（白名单）。密钥需 `meta:read`+`events:ingest`。
- **参数 enum**（Palantir 要求）：动作参数「枚举 / 枚举列表」按 `action.checker.literal_enum_choices` 解析参数规则；函数入参按注解 `Literal[...]` 解析。只在声明了取值时加 `enum` 键。目录形状只增不改。
- **rules 节与 dynamic:read**：本体小白在 `c55f370` 已经通过 `SCOPE_PAGES` 加了 dynamic:read，并带测试 `tests/test_api_key_dynamic_capabilities.py`，静态没有重复实现；static-api.md 第 21 项改为已解决。
- **构建引导**：`BuildGuideDrawer.vue` 加「函数」步骤（放在指标之后，用 `GET /functions` 计数；历史版本下显示「不适用」，因为函数列表不区分版本）。
- **ROLLBACK_FIRST_RELEASE 文档**：按 PDE 的调整，交给本体小白（版本模块），静态没有改 api-index / versioning-api / contracts。
- **测试**：`test_static_capabilities.py` 新增参数枚举、events、密钥事件白名单的断言和 `test_literal_annotation_values`，同时修了 add_props 偶发 MissingGreenlet（改成只取外键）；`check-overview-integration.mjs` 在 master 上原本就跑不过（缺 `@/components/graphEdges` 依赖，平行边断言也过时了），现在已修好，并断言总览页不 import mock；`check-definition-forms.mjs` 加函数步骤断言。

### 给 PDE / 其他模块（只报告）

- `action/checker.py:450` 只对「枚举」做取值校验，「枚举列表」的元素不校验（`param_valid` :158 只看是不是字符串列表）；目录与 `/machine/tools`（`machine/tools.py:59-62`）都按声明给了 enum，提交端没有强制。
- `event/ingress.py:126` 只检查已发布 / 未下线，不限来源；`machine/tools.py:25` `INGESTIBLE` 只导出外部系统 / 数据同步来源。动作 / 规则来源的事件类型能经 `/internal/v1/events` 外部写入，是否要在接收端拦截由事件模块定。
- `machine/tools.py:128-131` 动作工具的参数 schema 调用 `type_schema(p["type"])` 时没传规则，所以动作工具目前没有枚举；现在可以直接读目录里的 `p.get("enum")`（Palantir 自己决定）。

## static-t2（基于 master `e97bc61`，本轮无新迁移）

- **PyYAML 依赖**（PDE 授权）：`backend/pyproject.toml` 加 `pyyaml>=6.0`（MetricFlow 导出 `format=yaml` 用）。`uv lock --offline` 因本机 uv 缓存缺 defusedxml 无法重新解析，`uv.lock` 改为手工补两处（项目包 `dependencies` 加 `{ name = "pyyaml" }`、`requires-dist` 加 `{ name = "pyyaml", specifier = ">=6.0" }`；pyyaml 6.0.3 包条目原已存在），`uv lock --check --offline` 通过（Resolved 69 packages）。联网环境建议再跑一次 `uv lock` 确认无差异。
- **首版回滚**（PDE 授权）：`versioning/service.py` `create_version` 回滚最新版且它没有上一版 → 409 `ROLLBACK_FIRST_RELEASE`「这是首个发布版本，没有可回滚到的上一版」（`details{current, rollbackOf}`）；其余不是最新 / 不存在仍是 `ROLLBACK_NOT_LATEST`。Releases.vue 用 `/releases/current` 的 `release.parentVer` 判首版，隐藏回滚按钮并给提示，`rollback()` 也加了守卫。
- **能力目录（只读）**：`GET /meta/capabilities`（`app/modules/static/capabilities.py`），给问数 / MCP。按节看页面 view 权限（无权限节进 `omitted`），支持 API 密钥 `meta:read`（`require_api_scope`）；敏感 / 隐藏属性不列，`rowAccess` 只做提示；动作带执行策略（`action.policy.version_policies` + `stored`）和机器执行接口路径，密钥调用另给 `keyAllowed`。只读其他模块的模型与公开函数，未改其文件。契约见 static-api.md §3.10。
- **#9f**：复核结论——CR 写入已按目标所属页面 manage 检查（`static/permissions.py` `check_target` / `check_cr`，`cr_write.py:309-311、357、366、394、436`），本轮无改动。
- **#8**（总览去 mock 导入）：本轮未做。OntologyOverview.vue:22-29 的 mock 导入仍用于演示模式卡片 / 待处理 / 新建草稿（:52-113、:252、:278），去掉需要先在 `api/overview.ts` 提供演示数据入口，留到下一批。
- **测试**：新增 `tests/test_static_capabilities.py`（3 个，需 `-p tests.use_ops_support`）；`test_static.py::test_static_routes_func_permissions` 对 `/meta/capabilities` 特判；`test_static_build_writes.py::test_second_workspace_publish_rollback_isolated` 断言首版 409 `ROLLBACK_FIRST_RELEASE`。

### 给 PDE / 其他模块（只报告）

- 能力目录 rules 节：API 密钥的 scope 都不带 `rules` 页（`app/modules/admin/api_keys.py:72-79` `SCOPE_PAGES`），密钥看不到规则；需本体小开决定是否给 `meta:read` 加 rules·view。
- `ROLLBACK_FIRST_RELEASE` 新错误码尚未进非静态文档：`frontend/docs/backend/api-index.md:102`、`versioning-api.md:37`、`app/contracts/versioning.py:10,145` 仍只写 `ROLLBACK_NOT_LATEST`。
- 迁移往返 `notify_0022_inbox.py:67` 仍未解决（同 static-t1）。

## static-t5（基于 master `343ba5f`，本轮无新迁移，迁移头仍为 dynamic_0027）

- **#12 semantic_map**：新增 `app/modules/static/semantic_map.py`（纯函数 `entries_from_snapshot` + `semantic_map(session, ver=None)`），`app/contracts/static.py` 去掉 TODO 并导出。只从快照拼词表，不硬编码业务词；不开 HTTP。[推断] 草稿对象/指标与已停用知识不列；dimValue 的 id 用快照 `key`、api 用 `code`，同义词含命中该取值的维度映射词。`docs/build-completion-checklist.md:29` 原已写“已实现”，现与代码一致，未改。
- **对象浏览标记经 CR 编辑**：写回 / 发布 / 回滚原已完成（第 18 条），本轮补强 `test_explorer_flags_publish_snapshot_history_and_rollback`：回滚后 hidden（area_sqm）与敏感（store_name）标记恢复为基线，DB `is_sensitive` 复原。
- **#20 long_text**：`P_TYPES` 增 `long_text`（`data_type` 为 String(32) 无约束，不需迁移）；`explorer.LONG_TEXT`，`default_flags` / `snapshot_flags` / `prop_meta` / `facetable` 与 `_set_prop` 一律不可筛选、不可排序、不分面（默认不列表显示）。前端 `DefinitionEditor` 与对象类型页「新增属性」可选 long_text，勾选框随类型禁用。
- **静态列表页批量编辑**：`ObjectTypesBody.vue` 属性表「批量编辑」在 real 模式、草稿可编辑时开放：勾选当前页属性 → 弹窗改展示层级与 可搜索/可筛选/可排序/列表显示/敏感（三态）→ `cr.save('properties', rows)` 整行写入变更单；hidden / 敏感 / long_text 约束前端预修正、后端 `_set_prop` 兜底。演示模式保持禁用（提示）。其他静态列表页无批量编辑按钮，未新增。
- **测试**（独立库 rengu_test_static_t0，只跑改动文件）：`test_static_semantic_map.py`（新，3）、`test_static_writeback.py`（新增 `test_long_text_never_facets_or_sorts`、`test_long_text_property_publishes_and_meta`，类型校验加 long_text）、`test_static_explorer.py`，合计 79 passed；ruff check/format 通过；`vue-tsc --noEmit` 通过；`check-definition-forms`、`check-new-type-form`、`check-static-draft-hydration`、`check-static-loading` 通过；`check-static-pages` 的静态 Body 断言通过，之后在 master 上原本就报错（见下）。

### #13 草稿项仍在定义表 —— 评估结论：不在本批实现（设计说明）

- **现状**：关系（`link_type.status=草稿`）、对象类型（`ObjectStatus.draft`，如储值卡）、指标（`MetricStatus.draft`）、知识（`KnowledgeStatus.draft`）、维度取值与映射（`dim_value.draft_cr` / `dim_mapping.draft_cr`，`models.py:559、584`）的草稿行与已发布行同表，读路径靠 status / draft_cr 过滤（`read_pages.py:69、255、287、592`，`mapping_pages.py:33-117`，`shapes.py:307、322`）；回滚还会把快照外仍被引用的条目**降级为草稿**保留（`writeback.py:1106、1278、1308、1398-1409`），其他模块（动作 / 规则 / 问数 / 自动建本体等）也按定义表 status 读取。
- **风险**：要迁移的是数据而非只改表结构——需把现存草稿行转成 `cr_item`（缺对应 CR 的历史草稿无处归属）、删列 `draft_cr`、改所有读路径与回滚“保留为草稿”语义，并协调其他模块；对跨模块外键指向草稿对象（如储值卡）尤其危险。属于大重构 + 迁移风险，按指示不做。
- **建议做法（下一期）**：1) 新写入一律只进 `cr_item`（现状已基本如此：变更单写接口落 cr_item，发布时 `apply_crs` 才写定义表）；2) 加一个只读视图/函数 `published_only(model)` 统一替换散落的 status/draft_cr 过滤；3) 回滚“保留为草稿”改为置 `已停用` 并记 warning，不再产生新草稿行；4) 等定义表里不再新增草稿行后，再出一次数据迁移把残留草稿转成挂在一张“历史草稿”CR 下的 cr_item，最后删 `draft_cr` 列。每一步可单独发布、可回退。

### 给 PDE / 其他模块（只报告）

- **long_text 详情单独分区**：对象使用页 `frontend/src/pages/objects/objMeta.ts:71` 只排除 json，long_text 会按普通属性渲染；设计文档（generic-object-explorer.md §3）要求详情页单独分区，由对象使用页负责（`ObjectDetail.vue`）。
- **动作 `bulk`**（结果页批量动作）：`app/modules/action/models.py` ActionType 无 bulk 字段，`frontend/src/pages/objects/ObjectSearch.vue` 无多选入口；属动作模块与对象使用页。
- **`frontend/scripts/check-static-pages.mjs`** 在 master `343ba5f` 上即失败：静态 Body 断言通过后，`MetricsBody.vue:15/172` 引入的 `crErr`（`b93c610`）未在脚本的 `staticApi` 桩（`check-static-pages.mjs:27`）里提供，报 `crErr is not a function`。补桩一行即可，脚本非静态本轮所有，未改。

## static-t6（基于 master `4f0d3b6`，仅前端 UI，PDE 指派）

- **long_text 详情单独成区**：`frontend/src/pages/objects/ObjectDetail.vue` 概览里 long_text 属性不进标题行和分组表格，每个属性单独一区（区标题 = 属性名，有说明时给提示；正文保留换行、超长滚动；敏感仍显示“已脱敏”，hidden 不显示）。`objMeta.ts` 新增 `isLongText`，mock 兜底的默认列表列排除 long_text（真接口由静态 meta 给 `listed=false`）。设计文档 generic-object-explorer.md 对照表更新为已做。
- **检查**：`vue-tsc --noEmit` 通过；`check-permission-apply`、`check-qa-eval-real`、`check-object-usage-pages`、`check-object-automation` 通过；`check-context-integration`、`check-qa-integration`、`check-qa-session-context`、`check-real-object-resolver`、`check-object-ask`、`check-object-behavior`、`check-real-data` 在 master 上原本就失败，改动前后报错一致（缺桩依赖 / 过时断言，非静态所有，未改）。

## static-t7（基于 master `06a1202`，本轮无新迁移，迁移头仍为 dynamic_0027）

- **semantic_map 补 qa 字段**（qa.md §2.0c 第 148 行）：`semantic_map.py` 在 6 个老键外只追加键（字段与规则见 static-api.md §3.7「static-t7 追加字段」）：object `table`；property `type/column/table`；metric `status/unit/desc/formula/execDef`，`synonyms` 有了来源；dimension `status/levels[{no,api,name,source,object,property,table,column}]/defaultLevel{api,name}/qa/binding`；dimValue `level/levelApi/levelNo/parentName/parentValue/status/count`。都来自已有定义（DimLevel、DimValue.parent、Dimension.qa/column、ObjectType.source_table、Property.source_column、metric def_rows）。
- **唯一新增的定义能力：指标同义词 / 说明**。原先没有结构化来源，只有 def_rows 展示行「同义词」，种子还把它抄给了全部 15 个指标。现在 `metric_versions.META_KEYS = {synonyms: 同义词, desc: 说明}`，变更单 metric/def 项 `synonyms`（词列表或分隔文本）/ `desc` 可写（writeback.metric_def 校验），快照 metrics 节每项带 `synonyms`/`desc`（sections.MetricsSection current 与 seed_section 一致），restore_metrics 整版回滚恢复（旧快照没有该键时不改）。不进 `definition()`，所以不升口径版本、不触发试算。种子 `seed_def_rows`：METRIC_DEF.json 的「同义词」「说明」只给 METRIC_LIST 首项（gmv），其余指标不再继承 gmv 的“营业额、流水…”。没有改种子 JSON，dev 库没有重灌（库里旧行要重灌种子才会变）。
- **给 qa**：维度默认层级用 `defaultLevel.api`；层级物理列用 `levels[i].table/column`，为 `null` 就是定义里没写物理列，别兜底猜；`parentValue` 指向同一维度另一条 dimValue 的 `id`。time 维度的月取值 parent 是「2026Q4」这类季度编码，但种子没有季度取值，所以 `parentValue=null`、`parentName` 给原文。属性物理列用 `column`，不要用 api。
- **测试**（独立库 rengu_test_static_t0，只跑改动相关的文件）：`test_static_semantic_map.py` 12 passed（新增 qa 字段、上级/旧快照、同义词 CR 发布 + 整版回滚、非法值 6 例）；`test_static.py`、`test_static_trials.py`、`test_static_build_writes.py`、`test_static_writeback.py`、`test_static_cr_write.py` 85 passed / 1 failed。失败的 `test_static.py::test_migration_round_trip` 在未改动的 06a1202 上同样失败：`migrations/versions/admin_0025_api_scopes_row_scope.py:165` 拒绝破坏性降级，不是静态模块的问题。ruff check/format 通过。

## 未完成清单（交 Codex）

整理时间 2026-10-27（UTC+8），基于 master `e657b08`。每条依次写：是什么 / 为什么没做（待定、Codex 修复或环境问题）/ 涉及文件和接口 / 怎么验证。

### A. 待老板 / PDE 决定（Codex 不要自行开工）

1. **#13 草稿项和定义表分开存**
   - 是什么：关系、对象类型、指标、知识的草稿行，以及维度取值和映射的草稿行（`draft_cr`），现在和已发布行放在同一张表里，读接口靠 status / draft_cr 过滤。按 §1.3 应该只存在 `cr_item` 里。
   - 为什么没做：**待定**。这是数据迁移加大重构（历史草稿没有对应的 CR、要删 `draft_cr` 列、要改回滚时“保留为草稿”的语义、其他模块有外键指向草稿对象），static-t5 评估后决定只出设计、不实现。
   - 涉及文件和接口：设计见上文「### #13 草稿项仍在定义表 —— 评估结论：不在本批实现（设计说明）」（static-t5 节）；`app/modules/static/models.py:12-25、559、584`；读路径 `read_pages.py:69、255、287、592`、`mapping_pages.py:33-117`；`writeback.apply_crs` / `restore_*`。
   - 怎么验证（批准后）：按设计分 4 步做，每步单独提交。新写入不产生定义表草稿行；`published_only` 替换全部散落的过滤后，`test_static*.py` 全部通过；回滚不再生成新的草稿行；最后一步迁移的 upgrade / downgrade 在独立库里往返通过。

2. **种子缺季度维度取值，月份 dimValue 的 `parentValue` 为 null**
   - 是什么：`backend/seeds/json/static/DIM_ITEMS.json:65、75、88` 等处，time 维度月份取值的 `parent` 是 `2026Q4`，但种子里没有季度层的取值，所以 `semantic_map` 给出 `parentValue=null`（`parentName` 原样）。
   - 为什么没做：**待定**。要改现有种子取值（以及与之一致的 mock / 快照），需老板批准；静态规则是不改 mock 数据。
   - 涉及文件和接口：`DIM_ITEMS.json`；`app/modules/static/semantic_map.py:19-23、147`；`app.contracts.static.semantic_map`。
   - 怎么验证（批准后）：补季度取值后重灌测试库；`test_static_semantic_map.py` 里加断言，确认月份 dimValue 的 `parentValue` = 该季度取值的 `id`，季度的 `levelNo` 小于月份；`test_static.py` 里 `current()` 和 `seed_section()` 的一致性测试仍通过。

3. **事件载荷字段没有枚举（enum）**
   - 是什么：事件类型定义的载荷字段只有 `name/api/type/required/description/sample`（`app/modules/event/models.py:102-117` `EventPayloadField`），没有可选取值。能力目录 events 节的 `payload[]`（`app/modules/static/capabilities.py:355-358`）因此给不出 `enum`。Palantir 在等静态补上，之后由 Palantir 在接收端校验。
   - 为什么没做：**待定**。表属于事件模块，要加列（迁移，接在当前头之后）、改事件类型的编辑、写回和快照（`event/sections.py:148`），需要 PDE 指定由谁改、确定列的形状（建议 `enum: list[str] | null`，规则同动作参数的 `literal_enum_choices`）。
   - 涉及文件和接口：`event/models.py:102`、`event/sections.py:148`、`event/ingress.py:41-48`（`validate_fields`，由 Palantir 加取值校验）、`static/capabilities.py:355`（只在声明了取值时加 `enum` 键，目录形状只增不改）。
   - 怎么验证：`test_static_capabilities.py` 断言声明了取值的载荷字段带 `enum`、没声明的不带；事件模块测试覆盖发布、回滚后 enum 恢复；Palantir 侧对不在 enum 里的值返回 422。

### B. 已知失败的测试（不是静态代码的问题，Codex 修复或交对应模块）

4. **`test_static.py::test_migration_round_trip` 失败**
   - 是什么：降级往返时被有意阻断的 downgrade 打断，在干净的 master 上同样失败。
   - 为什么没做：**Codex 修复 / 交对应模块决定**。阻断点是 `migrations/versions/admin_0025_api_scopes_row_scope.py:165` 和 `migrations/versions/notify_0022_inbox.py:67`，两处都是 `raise RuntimeError(... destructive downgrade is blocked)`，静态不能改。
   - 涉及文件和接口：上面两个迁移文件；`backend/tests/test_static.py` 里的 `test_migration_round_trip`。
   - 怎么验证：二选一。要么由对应模块决定允许降级；要么把往返测试改成只往返到最早的“可降级”版本（或对这些 revision 加白名单跳过）。之后在独立库里跑 `pytest tests/test_static.py -k migration_round_trip` 通过。

5. **前端检查脚本在 master 上就失败**
   - 是什么：`frontend/scripts/check-static-pages.mjs` 报 `crErr is not a function`，因为 `MetricsBody.vue:15/172` 引入了 `crErr`，脚本的 `staticApi` 桩（`check-static-pages.mjs:27`）没提供，补一行桩即可。另外 `check-context-integration`、`check-qa-integration`、`check-qa-session-context`、`check-real-object-resolver`、`check-object-ask`、`check-object-behavior`、`check-real-data` 在 master 上也原本就失败（缺桩依赖 / 断言过时，static-t6 时确认，改动前后报错一致）。
   - 为什么没做：**Codex 修复**。这些脚本都不归静态所有。
   - 涉及文件和接口：`frontend/scripts/check-*.mjs`。
   - 怎么验证：`cd frontend && node scripts/check-static-pages.mjs` 等逐个执行，退出码为 0；`vue-tsc --noEmit` 仍通过。

### C. 之前各节里仍然没做完的条目（Codex 可做）

6. **按角色范围的分面端到端测试**（原第 22 条，部分完成）
   - 是什么：真实的 `row_filter` 已经接上，但缺一条用例证明分面按角色范围计数，以及 DENY_ALL 时全部计数为 0。
   - 为什么没做：**Codex 修复**（只缺测试）。
   - 涉及文件和接口：`app/modules/static/explorer.py`（facets）、`tests/test_static_explorer.py` 里的 `test_facets_apply_row_filter`、row-access.md。
   - 怎么验证：新用例用真实角色（不 monkeypatch）请求 facets，范围内计数正确，DENY_ALL 计数全为 0。

7. **writeback 注释与代码不一致**（评审遗留，行号已漂移）
   - 是什么：原评审指出 `writeback.py:773-775` 的注释和 `:780` 的代码不一致，现在行号已经变了，需要重新定位。
   - 为什么没做：**待核**（Codex 修复）。
   - 涉及文件和接口：`app/modules/static/writeback.py`。
   - 怎么验证：注释和代码一致；`test_static_writeback.py` 通过。

8. **CR 写权限没有按目标页映射**（原 9f，复核结论有出入）
   - 是什么：2026-10-27 的复核表写的是“仍统一 `releases:manage`”，static-t2 复核写的是已按目标页 manage（`static/permissions.py` `check_target` / `check_cr`）。
   - 为什么没做：**待核**（Codex 修复）。
   - 涉及文件和接口：`static/permissions.py`、`cr_write.py`。
   - 怎么验证：加一条测试：只有 metrics:manage、没有 releases:manage 的用户能写 metric 目标的 CR，但不能写 object 目标的 CR。

9. **其他负责人 / PDE 的遗留**（只列出，静态不改）
   - `CR_DETAILS.json` 仍是一次性导出的（原第 6 条，`seeds/extra/static/CR_DETAILS.json`、`seeds/__init__.py:529`，等 PDE 改 `scripts/export-mock.mjs`）。
   - 启动时只在 versioning 切真接口时才读 `/releases/current`（原第 7 条，`src/api/index.ts`、`api/releases.ts`，PDE）。
   - 对 `ROLLBACK_FIRST_RELEASE` 的非静态文档（api-index.md:102、versioning-api.md:37、`contracts/versioning.py`）已经交给本体小白。
   - `action/checker.py:450` 不校验「枚举列表」的元素（动作模块）。
   - 事件接收端不限来源（`event/ingress.py:126`，事件模块）。
   - 动作工具 schema 不带 enum（`machine/tools.py:128-131`，Palantir）。
   - 怎么验证：由各负责人在自己的交接里关闭。

10. **设计限制（保持现状，记录在案）**
    - facets 只覆盖 sql_guard 登记过的类型（原第 19 条），新类型要先登记业务表，再写 `explorer_meta.json` 的 `backing`。
    - 回滚不恢复运行数据（指标当前值、取值计数、关系质量）。
    - 层级 source 不是「列 / 对象.属性 / 对象」写法时，`semantic_map` 的物理绑定为 null，不猜（static-t7）。

### D. 明确延后（二、三期，本期不做）

11. **属性级权限**：现在只有页面 / 功能级权限和行级范围，属性只有敏感 / hidden 标记，没有按角色控制属性可见。
12. **本体分支（branching）**：变更单基于当前版本线性发布，不支持分支并行和合并。
13. **跨本体复用 / 模板**：多本体（多空间）同编码只能各自定义、各自发布（static-t0 已测互不影响），没有共享定义或从模板创建。
14. **统一的 synonym 表**：`dim_mapping` 按维度单独建，统一的同义词表以后再做（见「[推断] 决定清单」）。

以上 11-14 条都要先由老板 / PDE 出范围再设计。验收在设计时定。

### E. 演示环境配置

- **没有静态专属的演示环境配置**。静态只用到共用项：前端 `VITE_API_MODE` / `VITE_API_REAL_MODULES` 里的 `static` 开关键（`frontend/.env.example`），后台 `demo_admin_codes`（`cr_write.py:297`，第一个人作为默认审核人，发起人就是他时提交会返回 409 `CR_NO_REVIEWER`），以及 `X-Demo-As` 登录头（未知 / 停用的返回 401）。
- 怎么验证：演示环境里 `VITE_API_REAL_MODULES` 包含 `static`（或留空 + real 模式），`demo_admin_codes` 第一个人在 sys_user 里存在并且有效。
