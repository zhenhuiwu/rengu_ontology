# 交接文档 · 本体小白（版本层 / 骨架 / 表结构识别）

> 写于 2026-10-26 01:xx（UTC+8），基线 master `b688d5d`。所有结论都对照了代码或 mock，引用写成 `文件:行`；
> 前端路径相对仓库根，后端路径相对 `backend/`。标【推断】的是我的判断，前端/文档没写。

## 1. 已完成

| 项 | 分支 / 状态 | 内容 |
|---|---|---|
| 版本功能对齐前端 | `versioning-integ`（`b688d5d..`，02:00 送审） | `tests/test_versioning.py` 新增 13 个契约测试（`GET /releases`、`/releases/current` 与前端 mock 一致）；`tests/conftest.py` 顶部一次性 `import app.main`，修掉单独跑一个测试文件时 section 重复登记；新文档 `docs/backend/versioning-api.md`，`docs/backend/CONVENTIONS.md` §7 链过去。 |
| 表结构识别库 | `versioning-integ` 第二个提交 | `app/modules/auto_ontology/`：`introspect.py`（读表/列/主键/外键/注释）、`heuristic.py`（表→对象类型、列→属性、外键→关系、纯外键中间表→N:M）、`llm.py`（qwen-flash 只改名字/分组/说明；没 `DASHSCOPE_API_KEY` 时 `get_llm()` 返回 None，走规则结果）、`generate.py`（串起来，不写库）；测试 `tests/test_auto_ontology.py`。**不挂路由、不建表、无迁移、无 seed / sections。** |
| 数据连接页 `src/pages/data/Connections.vue` | 分支 `datasources`（worktree `/workspace/ui-vue-wt/xiaobai-ds`），**状态待定，由主会话补最终状态** | 后端 `/api/data/v1/datasources` 一组接口 + 前端切真接口。不碰 Palantir 的 `ext-systems` / `writeback-calls`、`src/api/integration.ts`、`app/modules/integration/`。 |
| 审计日志页 `src/pages/admin/AuditLog.vue` | 分支 `audit-logs`（worktree `/workspace/ui-vue-wt/xiaobai-audit`，`b688d5d..4d0e995`，391 passed，02:00 送审） | `c35cc7c` 后端：表 `audit_log`（触发器保证只追加）、`GET /api/admin/v1/audit-logs`（keyset 游标，`(time desc, id)`，`from,to` 必填，`api-index.md:960`）、`GET /audit-logs/actions`、`POST /audit-logs/export`（桩，只返回 jobId）；写入函数 `insert_audit()` 在 `app/modules/admin/repo.py`；种子为 `src/mock/admin.ts` `AUDIT_LOGS`（512 条随机 + 9 条固定 = 521 条，`admin.ts:53`、`:68-80`）。`4d0e995` 前端：`AuditLog.vue` 经 `src/api/admin.ts` 调真接口，接口不可用时回退 mock。**合并注意**：迁移 `admin_0001` 的 `down_revision` 合并时要改成 `data_0001`。详见该分支 `docs/backend/audit-logs-api.md`。 |

首页 `/dashboard/kpis`、`/stores/ranking` 已转给 PDE工程师（新模块 `app/modules/dashboard/`），不在本清单。

## 2. 未完成

### 2.1 用户页 `src/pages/admin/Users.vue`（api-index §4.1，`docs/backend/api-index.md:899-919`）

> 状态：正在 `audit-logs` 分支上尝试做后端，**最终状态待定，由主会话补**；做不完的部分按下面清单留到下一阶段。

- **页面**：列表按部门、状态（启用/停用/待激活）、关键字（姓名/账号/ID）过滤（`Users.vue:18-26`），每页 17 条（`:21`）；列含“待办审批”数（`:22`）。抽屉新建/编辑：姓名、账号（新建才能填）、部门、角色、状态（`:50-68`）。批量导入：下载 CSV 模板 → 上传 → 预览 → 确认卡（`:91-120`）。启用（`:124-126`）、停用要选转交人（`:4`、`:130` 起）。
- **mock**：`src/mock/admin.ts` `ADMIN_USERS`（120 人，`:16-34`，字段 `id,name,account,dept,role,status,lastLogin,createdAt`，`:9-12`）、`ADMIN_DEPTS`（`:35`，7 个部门，`:6`）、`pendingApprovals`（`:37`，算出来的，不入库）；角色下拉来自 `src/mock/roles.ts` `ROLES`（`Users.vue:14`）。
- **接口（8 个，前缀 `/api/admin/v1`）**：`GET /users`、`GET /depts`、`POST /users`、`PUT /users/{id}`、`POST /users:import-preview`（不落库）、`POST /users:import`、`POST /users/{id}:enable`、`POST /users/{id}:disable {transferTo?}`（`api-index.md:912-919`）。
- **现状**：`app/modules/admin/models.py:28-49` `SysUser` 只有 `code,name,account,dept,status,last_login_at,alert_assignable`，**没有角色列、没有 created_at 业务值**；`app/modules/admin/router.py` 只是空 `APIRouter`。seed 共 128 人：`seeds/json/system/ADMIN_USERS.json` 120 人 + `seeds/extra/system/SYS_USERS.json` 8 人（`app/modules/admin/seeds/__init__.py:3-10`），转换 `_user()`（`:27-36`）丢掉了 mock 的 `role`、`createdAt`。
- **要改的表**：用户和角色是多对多（`roles.ts:80-91` `roleMembers` / `userRoles`），角色不能做成 `sys_user` 的一列，应放进 2.2 的 `role_member`；`sys_user` 只需补 mock 的 `createdAt`（可写进现有 `created_at` 或另加 `joined_at`【推断】）。“待办审批”按 `pendingApprovals` 规则算，或接动态/静态的待审数【待定，api-index:919】。
- **估时**：接口 + seed + 契约测试约 3–4 小时（停用转交依赖待办审批来源，另算）。

### 2.2 角色页 `src/pages/admin/Roles.vue`（api-index §4.2，`api-index.md:921-950`）

- **页面**：左栏角色列表，右栏 4 个页签 `func|obj|row|members`（`Roles.vue:9`、`:287-291`）；功能/对象权限就地编辑 + 保存条（`:5`、`:60-67`）；内置角色只读（`:37-38`）；行级范围增删（`:99`、`:327-334`）；成员增删（`:149-150`）；新建（权限模板 `NR_PERMS`，`:231`）、复制（`:203-207`）、编辑、删除（`:185-220`）；“以此用户预览”抽屉（`:159-165`，`effectiveAccess`）。
- **mock**：`src/mock/roles.ts`：类型 `RowScope / ObjPerm / Role`（`:12-21`）、`SCOPE_PROPS`（`:24-31`）、`PERM_OBJECTS`（`:33-42`）、`FUNC_PAGES`（`:44`，来自 `@/nav`）、8 个角色 `ROLES`（`:51-78`，R-01/02/03/05 内置）、`roleMembers`（`:81-88`）、`effectiveAccess`（`:103-120`）。
- **接口（14 个）**：`GET /roles`、`GET /roles/{id}`、`GET /roles/meta`、`POST /roles`、`PATCH /roles/{id}`、`PUT /roles/{id}/permissions`、`DELETE /roles/{id}`、`POST /roles/{id}/scopes`、`DELETE /roles/{id}/scopes/{scopeId}`、`GET|POST /roles/{id}/members`、`DELETE /roles/{id}/members/{userId}`、`GET /users/{id}/effective-access`，外加 `POST /preview-sessions`（`api-index.md:935-948`）。
- **建议的表**【推断】：`sys_role`（`code,name,desc,built_in,default_deny` + 可变表公共列；表名按 api-index:69）；`func` 和 `objs` 存成 `sys_role` 上的 JSONB 两列（`Record<pageKey,FuncLevel>`、`ObjPerm[]`，整体由 `PUT /permissions` 覆盖写，用 `lock_version` 防并发）；`role_scope`（`role_id,obj_type,prop,op,values jsonb`，前端 id 如 `S-31`）；`role_member`（`role_id,user_id` 唯一）。
- **和权限底层的接法**：`app/common/auth.py:80-93` `row_filter()` 现在对所有登录用户返回 None（用户定的“初版最简”）；`:96-103` `require_func()` 直接放行。表建好后按 `auth.py:88-92` 注释：取用户各角色的 {查看权, defaultDeny, RowScope[]}，交 `sql_guard.effective_expr` 合并（规则同 `roles.ts:108-117`）；`require_func` 比较 `func[page_key]` 等级。工具函数已在 `app/common/row_filter.py`（`from_row_scope :255`、`combine_or :271`、`is_deny_all :285`）。
- **要先定的一处不一致**：同一角色同一对象多条范围，前端提示写“取交集”（`Roles.vue:329`），后端注释写“也 OR【推断】”（`auth.py:91`），实现前请用户拍板。
- **估时**：约 1 天（14 个接口 + effectiveAccess 计算 + 打开 `row_filter`/`require_func` 后全模块回归）。打开权限会影响所有模块测试，建议单独一个阶段做。

### 2.3 审计：真写库 + 真导出（`app/common/audit.py`）——**PDE 决定推迟到下一阶段**

- **现状**：`audit()` 签名已定（`audit.py:22-32`），函数体只打日志（`:31-32`，`EXTENSION POINT (PDE)`）。保留期清理任务 `app/jobs/__init__.py:80-82` `cleanup_audit_logs()` 一期不删数据、直接返回 0。
- **推迟原因（PDE 定）**：一改成真写库，所有模块写接口的测试都会多一次 `audit_log` 插入，风险面是全部模块，本阶段不动。
- **导出**：`POST /audit-logs/export` 现在是桩（只返回 jobId，不生成文件），真导出任务（procrastinate，api-index §1.8b）未做。
- **下一步**：等 `audit-logs` 分支合进来、`audit_log` 表存在后，在 `audit()` 里调用 `app/modules/admin/repo.py` 的 `insert_audit()`（`audit-logs` 分支，写 `audit_log` 表；与业务写同一事务、只 flush 不 commit）；actor 写法见 `api-index.md:163-167`（API 密钥调用写 `keyActor`）。需要 PDE 点头，文件归 PDE。

### 2.4 表结构识别接入已有页面（auto-ontology 的用途）

这块原本是调研：借 NVIDIA/auto-ontology 的思路补强**已有页面**，不是另做一个前端没有的功能。之前我做偏了，做成了独立的草稿表 + 审核接口（分支 `auto-ontology`，`b688d5d..2b4cfb1`，已搁置，不合并）。现在只把可复用的库合进来（见 §1）。

**没合进来的部分（留在搁置分支，不打算再用）**：`auto_ontology_draft` 表和模型、迁移 `auto_ontology_0001_draft.py`、草稿接口（`POST/GET /auto-ontology/drafts`、`:accept`、`:reject`）、`service.py`/`repo.py`/`schemas.py`/`router.py`、对应的接口测试，以及 `app/api.py`、`app/db/models.py` 里的两行登记。

**可以接的页面（页面归属方自己改，我只提供函数，不改他们的文件）**：

| 页面位置 | 现在 | 可以用的函数 | 归属 |
|---|---|---|---|
| 数据连接 `Connections.vue:79` “映射对象类型”列（详情 `:216`；mock `src/mock/datasources.ts:13` `objectTypes`） | mock 写死 | `introspect()` 读表，`build_proposal()` 给出表→对象类型 | 本体小白（`datasources` 分支） |
| 对象类型 `ObjectTypes.vue:211` “从数据源导入” | 只弹变更单 | `generate(session, schema, tables)` → 对象类型 + 属性 + 关系草案，再由静态建变更单 | 静态 |
| 对象类型 `ObjectTypes.vue:387` “重新采集元数据” | 只弹提示 | `introspect()` 对比现有属性，报新增/删除列 | 静态 |
| 对象类型 `ObjectTypes.vue:322` “新增属性”（弹窗 `:401`，提交 `:128`） | 手填 | `column_kind()` + `heuristic` 给类型/名称默认值 | 静态 |
| 对象类型 `ObjectTypes.vue:357` “新增关系” → 关系类型 `LinkTypes.vue:97-110` 新建草稿（`from/to/card/key/join` 全空） | 手填 | 外键推出的 `from,to,card,key,join`（`heuristic.build_proposal`） | 静态 |
| 问数语义地图同义词（`app/contracts/static.py:7` TODO；`qa_synonym_link`，`app/modules/qa/models.py:593`） | 手工维护 | 列注释、`llm.py` 生成的中文名可作候选同义词 | 静态 / 问数 |

## 3. 代码在哪

- 版本层：`app/modules/versioning/`；文档 `docs/backend/versioning-api.md`；测试 `tests/test_versioning.py`。
- 表结构识别库（合入 master 后）：`backend/app/modules/auto_ontology/`
  - `introspect.py`：`introspect(session, schema, tables)`、`column_kind()`、错误 `SchemaNotFound / TablesNotFound / TooManyTables`；
  - `heuristic.py`：`build_proposal(tables, datasource_name)`、`structural_warnings(tables)`；
  - `llm.py`：`get_llm()`（只读 `DASHSCOPE_API_KEY`，没有就 None）、`merge()`（只认已有 id，不改结构）、`FakeLLM`（测试用）；
  - `generate.py`：`generate(session, schema, tables, use_llm=, llm=)` → `Generated{tables, proposal, warnings, generator, model}`，不写库；
  - 测试 `tests/test_auto_ontology.py`（在测试事务里建临时 schema `ao_test`，随事务回滚）。
- 搁置分支 `auto-ontology`（只存在本地 worktree，不推送）：草稿表/接口版本，仅供参考。
- 用户/角色：`app/modules/admin/`（`models.py` 只有 `SysUser`，`router.py` 空）；权限底层 `app/common/auth.py`、`app/common/row_filter.py`、`app/common/sql_guard.py`。
- 审计：`app/common/audit.py`、`app/jobs/__init__.py:80`；读侧在 `audit-logs` 分支。
- 前端：`src/pages/admin/{Users,Roles,AuditLog}.vue`、`src/pages/data/Connections.vue`、`src/pages/ontology/{ObjectTypes,LinkTypes}.vue`；mock `src/mock/{admin,roles,datasources}.ts`。

## 4. 下一步怎么接

1. **表结构识别库挪到 data 模块**【推断】：`datasources` 合入后，把 `introspect.py`、`heuristic.py` 挪到 `app/modules/data/`，`introspect()` 改成按 datasource 取连接（`introspect.py` 里 `EXTENSION POINT (data)`）；`auto_ontology_draft` 表不再建。
2. **静态接“从数据源导入”**：静态的变更单接口就绪后，`ObjectTypes.vue:211` 调 `generate()` 拿草案，直接建变更单（不经草稿表）。其余几处见 §2.4 表，各归属方按需接。
3. **用户/角色**：先建 `sys_role / role_scope / role_member`（迁移接在当时 master 的 head 后），seed 按 `roles.ts` `ROLES` 和 `roleMembers()` 规则展开；再做 §2.1、§2.2 的 22 个接口；最后单独一个提交打开 `row_filter` / `require_func`，跑全量回归。先请用户定“同一角色多条范围 AND 还是 OR”。
4. **审计真写**：在 3 之后或同一阶段，PDE 点头后改 `audit.py`，全模块写测试一起回归。
5. 测试统一用 `RENGU_TEST_DB=<自己的库> backend/.venv/bin/python -m pytest`；提交用自己的 `git -c user.name=… -c user.email=…`，只有 PDE 推送。
