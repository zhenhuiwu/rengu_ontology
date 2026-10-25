# 静态本体交接（static-integ2）

负责人：静态本体负责人。分支：`static-integ`（变基在 pde-integ `215819d` 上）→ `static-integ2`（叠在上面）。
接口设计细节见 `docs/backend/static-api.md`；本文件只写做到哪、代码在哪、怎么接着做。
标注沿用 static-api.md：**[推断]** = 前端没有限制或没有体现，按页面行为推出来的。

> **没有切换的页面仍然走 mock。** 真接口只在 `VITE_API_REAL_MODULES` 包含 `static`（或 `VITE_API_MODE=real`）时生效；
> 这个开关只在本地 `.env.local` 设置，不提交。没有开关时所有页面和以前完全一样（不发请求）。

## 已完成

后台（`backend/app/modules/static/`，前缀 `/api/ontology/v1`）：
- **定义表**（static_0001）：对象类型、属性、字段映射、关系类型、问数路径（`onto_qa_path` / `_step` / `_example`）、指标及口径版本、维度 / 层级 / 取值 / 说法映射、业务知识及适用范围等；种子由 export-mock JSON 灌入。
- **总览图谱 `GET /ontology/graph`**（`service.py` `graph`）：`{nodes: LinkObject[], edges: {from,to,linkId,status}[]}`，nodes = 已发布对象类型（同 mock LINK_OBJECTS 去掉草稿储值卡），edges = 两端都在 nodes 里的关系，支持 `?ver=`。
- **26 个只读 GET**：对象类型 7、关系类型 5、指标 4、维度 6、业务知识 4（`router.py:86-267`），支持 `?ver=` 读历史版本（§2.7）和 `?previewAs=`，返回记录与 mock 同形（`shapes.py`）。
- **7 个快照 section**（`sections.py`）：objectTypes、properties、links、metrics、dimensions、terms、**qaPaths**（已在骨架 `SECTION_OWNERS` 登记）；`seed_section` 按版本规则裁剪，`build_section` 发布时组装。
- **static_0002**（`change_request` + `cr_event`，down = `event_0002`；dynamic_0002 会接在它后面，**不要改名或重新编号**）。
- **合并校验 / 整版发布 / 回滚**：`POST /releases:merge-check`、`POST /releases`、`POST /releases/{ver}:rollback`（`publish.py`，经版本层 `create_version`）。
- **变更单**：`GET /change-requests`（`?status`、`full=true`、`kw`；带 `page`/`size`/`focusId` 任一时按骨架 `PageParams.clamp` / `Page.of` 分页，每页默认 17，`focusId` 返回所在页；都不带返回整个数组，Releases.vue 现在仍整表载入）、`GET /change-requests/{id}`；6 个状态动作 `:submit` / `:withdraw` / `:approve` / `:reject` / `:unapprove` / `:reopen`；`DELETE /change-requests/{id}`（`crs.py`）。

前端：
- **5 个页面切真接口**：对象类型、关系类型、指标、维度、业务知识。页面外面包一层 `parts/StaticGate.vue` 整表载入（`src/api/static.ts` `loadStatic`），页面本体移到 `*Body.vue`，mock 模式下渲染不变。
- **变更与发布（Releases.vue）**：真接口模式下载入变更单列表和详情；6 个动作、删除草稿、发布、回滚都写库，成功后重新载入，失败弹错误提示、页面不变；发布 / 回滚后按 `/releases/current` 刷新顶栏版本号。

验证（`743ec27`）：pytest 399 passed / 1 skipped（库 `rengu_test_static_integ`）；ruff 通过；alembic 单 head `static_0002`；vue-tsc、`npm run build` 通过；浏览器脚本验证真接口删除草稿后刷新仍有效，mock 模式不发请求。

## 未完成

每条：缺什么 / 为什么 / 从哪开始。

1. **回滚不恢复静态定义表**
   - 缺：静态 section 没有实现 `restore_section`，整版回滚只复制快照、生成新版本，定义表不变，回滚后当前读接口仍是回滚前的定义。
   - 为什么：恢复要把快照节反写回十几张有外键的表（对象→属性→映射、关系→路径→步骤等），风险大，本期先不做 [推断，第二期]。
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
9. **变更单写路径没做**
   - 缺：发起变更 `POST /change-requests`、加入已有 `/targets`、保存草稿 `PUT /items`、放弃对象草稿、`:validate`、改动分页 `/diffs`、`PATCH migration`。真接口模式下 5 个定义页的编辑仍只改前端本地 mock，不落库。
   - 为什么：需要 cr_item / cr_target / cr_check / cr_impact / cr_trial 表（§1.3 已设计），static_0002 只建了 `change_request`（CRDetail 整份存 `detail` jsonb）和 `cr_event`。
   - 从哪开始：新迁移（接在当时的 head 后面）建 cr_* 表；`crs.py` 加写方法；`sections.py` `build_section` 按 CR 展开改动（现在 base 不为空就沿用上一版）。
10. **定义页其他写接口没做**：指标试算 / 口径回滚、维度匹配试算、知识解析试一试、对象导入预览 / 重新采集元数据、函数全部接口（§3.8）。
11. **对象历史 `kind=cr` 没接**：`service.py:211` TODO，依赖第 9 条。
12. **`semantic_map` 契约没做**：`app/contracts/static.py:7` TODO（static-api.md §3.7，给本体小白）。
13. **草稿项仍在定义表**：草稿 / 已驳回的关系、知识、取值仍放在定义表里，用 `status` / `draft_cr` 区分（`models.py:12-25`）；按 §1.3 应移到 cr_item，依赖第 9 条。
14. **种子里已删除的 CR**：开发库 `rengu_static_integ` 里 CR-0013、CR-0012 在测试中被删了，需要的话重新灌种子。

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

## 代码在哪

| 部分 | 文件 |
|---|---|
| 路由 | `backend/app/modules/static/router.py`（只读 GET 用 `get()` 包装；变更单 / 发布写接口在后半段） |
| 只读取数 | `service.py`（筛选 / 分页 / `?ver=`）、`repo.py`（整表读取）、`shapes.py`（ORM → mock 形状）、`history.py`（§2.7 版本规则） |
| 快照 section | `sections.py`（7 节）；契约 `app/contracts/versioning.py`、`app/contracts/static.py` |
| 变更单 | `crs.py`（`ChangeRequestService`：list / detail / transition / delete） |
| 发布 / 回滚 | `publish.py`（`merge_check`、`PublishService.publish / rollback`） |
| 模型 / 入参 | `models.py`、`schemas.py` |
| 种子 | `app/modules/static/seeds/__init__.py`；数据 `backend/seeds/json/`（export-mock）+ `backend/seeds/extra/static/CR_DETAILS.json` |
| 迁移 | `backend/migrations/versions/static_0001_static_definitions.py`、`static_0002_change_request.py` |
| 测试 | `backend/tests/test_static.py`（读接口）、`test_static_cr.py`（变更单）、`test_static_publish.py`（发布 / 回滚） |
| 前端 API | `src/api/static.ts`（`loadStatic`、`fetchChangeRequests`、`crAction`、`deleteCR`、发布 / 回滚、`fetchCurrentAfterWrite`） |
| 前端页面 | `src/pages/ontology/{ObjectTypes,LinkTypes,Metrics,Dimensions,Glossary}.vue`（包 `parts/StaticGate.vue`）+ 对应 `*Body.vue`；`Releases.vue` |
| 接口文档 | `docs/backend/static-api.md` |

## 下一步怎么接

1. 本地起服务：开发库 `DATABASE_URL=postgresql+asyncpg://…/rengu_static_integ`，`alembic upgrade head` 后灌种子；前端在 `.env.local` 设 `VITE_API_REAL_MODULES=static`（**不提交**），代理到后台。
2. 测试：`RENGU_TEST_DB=<自己的测试库> pytest -q`，`ruff check . && ruff format --check .`，`npx vue-tsc --noEmit`，`npm run build`。
3. 建议顺序：第 1 条（restore_section，在 `begin_nested` 里按外键顺序写，测试：回滚后 GET 返回旧定义）→ 第 9 条（cr_* 表 + 写路径，最大）。
4. 新迁移接在当时的单 head 后面；不要改 static_0002 的 revision。
5. 每做完一条，更新本文件「已完成 / 未完成」，并在 static-api.md 对应行写「已实现」。
