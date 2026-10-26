# 静态本体交接（static-integ2 → static-r3）

负责人：静态本体负责人。分支：`static-integ`（变基在 pde-integ `215819d` 上）→ `static-integ2`（叠在上面）→ `static-r3`（变基到 master `48a7094`，只做变更单写接口）。
接口设计细节见 `docs/backend/static-api.md`；本文件只写做到哪、代码在哪、怎么接着做。
标注沿用 static-api.md：**[推断]** = 前端没有限制或没有体现，按页面行为推出来的。

> **没有切换的页面仍然走 mock。** 真接口只在 `VITE_API_REAL_MODULES` 包含 `static`（或 `VITE_API_MODE=real`）时生效；
> 这个开关只在本地 `.env.local` 设置，不提交。没有开关时所有页面和以前完全一样（不发请求）。

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

1. **回滚不恢复静态定义表**
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
10. **定义页其他写接口没做**：指标试算 / 口径回滚、维度匹配试算、知识解析试一试、对象导入预览 / 重新采集元数据、函数全部接口（§3.8）。
11. **对象历史 `kind=cr` 没接**：`service.py:211` TODO，现在可以从 cr_target 查。
18. **本轮没做**（PDE 指示单独提）：第 1 条 restore_section、第 15 条 Releases.vue `crFallback`；Releases.vue 的“重新校验”按钮也还没接 `:validate`。
12. **`semantic_map` 契约没做**：`app/contracts/static.py:7` TODO（static-api.md §3.7，给本体小白）。
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

18. **对象浏览标记没进变更单 / 快照**：新字段只在定义表和种子里，`shapes.prop` / `sections.py` 的 properties 节没带，改标记暂时只能改种子或直接改库；`/meta` 忽略 `?ver`；指标 `variants` 也只在 meta.metrics 里返回，`GET /metrics` 形状没加。从 `shapes.py` 属性形状加字段 + 快照节测试开始。
19. **facets 只覆盖 sql_guard 登记过的类型**（门店、订单、商品等）；其他类型 `hasData:false`、空取值。新类型要先在 sql_guard 登记业务表，再在 `explorer_meta.json` 写 `backing`。行级范围 `row_filter` 目前返回 None（本体小白接入后自动生效）。
20. **long_text、动作 `bulk`** 按设计文档下一轮再做。
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
