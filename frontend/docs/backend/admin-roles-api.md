# 角色与权限 API（admin 模块，前端 `src/pages/admin/Roles.vue`，api-index §4.2）

挂载：`/api/admin/v1`（原有挂载）。代码：`backend/app/modules/admin/roles.py`（仓库 + 服务 + 有效权限）、
`schemas.py`、`router.py`；迁移 `admin_0003_sys_role`（down_revision = admin_0002）；测试 `backend/tests/test_admin_roles.py`。
前端：`src/api/roles.ts`，Roles.vue 在开关 admin 切到真接口且后台可用时走后台，否则用原 mock（逻辑不变）。

## 数据

| 表 | 内容 | 种子 |
|---|---|---|
| `sys_role` | code（R-xx）、name（唯一）、description、built_in、default_deny、func（JSON：页面 → 无/查看/管理）、objs（JSON：ObjPerm[]）、changed_at/by | `seeds/json/system/ROLES.json`（8 个） |
| `sys_role_scope` | code（S-xx）、role_id（级联删）、seq、obj_type、prop、op（= / ∈）、values | 同上 `scopes`（12 条） |
| `sys_role_member` | role_id、user_id、seq；(role_id,user_id) 唯一 | `derived/roleMembers.json`（mock `roleMembers()` 结果，225 条） |

页面清单读 `FUNC_PAGES.json`；对象清单和行级可选属性读已发布本体，空本体时分别使用 `PERM_OBJECTS.json` / `SCOPE_PROPS.json` 兜底。
新加成员的 seq = 当前最小值 − 1，所以排在最前，和页面 `[...add, ...members]` 一致。

## 接口

读 `require_func("roles","view")`，写 `require_func("roles","manage")`（目前放行桩）；`?ver=` / `?previewAs=` 下写接口 403 `READONLY`。
每个写操作记一条审计（`insert_audit`，动作「修改权限」等，同 AuditLog 页）。

| 方法 | 路径 | 入参 | 返回 | 说明 |
|---|---|---|---|---|
| GET | /roles | — | `Role & {memberCount, lockVersion}`[] | 按 code 排序 |
| GET | /roles/meta | — | `{funcPages, permObjects, scopeProps, objActions}` | 已发布本体与可执行行级依据；空本体用 mock |
| GET | /roles/{id} | — | 同上单个 | 不存在 404 |
| POST | /roles | `{name, desc?, perms?, copyFrom?}` | 角色 | 名称重复 409；`copyFrom` 复制 func/objs/scopes，不带成员；`perms` 是新建抽屉的模板勾选，按 NR_PERMS 推初始权限【推断】 |
| PATCH | /roles/{id} | `{name, desc, lockVersion?}` | 角色 | 内置 403 `BUILT_IN_ROLE`；改名同步 `sys_user.role` |
| PUT | /roles/{id}/permissions | `{func, objs, lockVersion?}` | 角色 | 页面 key / 动作不在清单 → 422；查看关掉时清空导出和动作 |
| DELETE | /roles/{id} | — | 204 | 内置 403；有人删完无角色时 409，否则重派主角色；成员、范围级联删 |
| POST | /roles/{id}/scopes | `{objType, prop, op?, values}` | RowScope | 属性和取值按当前 `/roles/meta.scopeProps` 校验；op 不传按取值个数推 |
| DELETE | /roles/{id}/scopes/{scopeId} | — | 204 | |
| GET | /roles/{id}/members | `page,size,kw` | `PageResult<AdminUser>` | kw 包含匹配姓名 / 账号 / 部门 |
| POST | /roles/{id}/members | `{userIds}` | `{added}` | 已是成员跳过；新增成员必须启用，否则整批 422，无成员/审计变化；内置角色也能改成员 |
| DELETE | /roles/{id}/members/{userId} | — | 204 | |
| GET | /users/{id}/effective-access | — | `{rs, func:[{key,lv,...}], objs, areasIn}` | 移植 mock `effectiveAccess()` |

lockVersion 不对 → 409 `CONFLICT`（通用乐观锁）。`/preview-sessions`（§1.11）留给 PDE。

真模式角色、成员、用户及元数据读取失败时清空页面并显示错误，不回退演示列表；成功空列表保持空态。对象类型权限页复用同一角色/成员 API，以已加载本体的 API 名匹配对象授权，遵守 defaultDeny。当前 ObjPerm 契约没有敏感列明文授权字段，页面明确显示未接入，不用 R-07/R-04 模拟授权；该页敏感标记编辑尚未接入草稿保存，真模式禁用。API 密钥也没有服务端管理/鉴权契约，真模式只显示未接入空态，禁止创建、复制、吊销及 URL 深链生成。

## 有效权限对拍

`backend/tests/fixtures/roles_effective_access.json` 是 mock `effectiveAccess()` 对 120 个用户的结果（func 只留 `[key, lv]`），
测试逐人比对。mock 改了要重生成（在仓库根目录跑，vite 要从这里解析）：

```
node scripts/export-effective-access.mjs /tmp/eff.json   # 再按测试里的格式裁剪 func
```

## 前端接法

`loadRolesState()` 一次拉角色、全部用户（/users 分页拉完）和每个角色的成员；返回 null 表示用 mock。
写操作成功后整份重新拉取；失败弹后台 message，本地不改。预览抽屉的有效权限仍在页面算（规则同后台）。
`loadRolesMeta()` 一次读取对象与范围属性；空 `scopeProps` 表示无候选，不回填旧列表。元数据业务错误会显示出来。
`effectiveAccess()` 接受可选的对象列表，角色页传入同一份 `permObjects`，因此本体新增对象也参与预览；默认参数保持 mock 行为。

## 行级依据属性（10-26）

`scopeProps` 项为 `{objType, prop, displayName, dataType, values: string[]}`。`displayName` 是本体属性显示名，
`prop` 是新增范围时必须原样提交的行级执行器名称；例如本体「所属大区」映射到已登记的「区域」。
旧 JSON 兜底项的 `displayName=null`、`dataType=string`。

目前 Property 没有独立 `permissionBasis` 字段，因此候选取以下交集，不把所有 `filterable` 属性直接当作权限依据：

- 已发布对象、可筛选、非隐藏、非敏感，且为 `string` 或 `enum`；
- 对象已登记为受控表，属性显示名或 `backing_column` 能对应 `field_of_prop` 中的可执行字段。

取值从该受控字段（含已声明的一跳关系）读取、去重排序，并应用当前用户行级过滤；新增范围复用同一份查询校验。
本体存在时不把旧 JSON 中不存在的属性补回。空值列表会展示但不可选；未登记字段不会生成无法执行的范围。
现有范围保留，不因元数据标记改变自动删除。数值、日期、布尔和复合类型暂不开放：当前 RowScope 仅存字符串并支持 `=` / `∈`，
需要先扩展存储与执行器类型校验才能开放。跨角色及同一对象多条范围均按并集合并，与有效权限和 SQL 执行器一致。
真实取值若与旧范围别名重名、且会被映射成另一值（例如自定义真实类目「休闲零食」会被旧规则改为「休闲食品」），
不出现在新候选中，新增范围也拒绝该值；既有角色的别名解释保持原契约。支持这些重名新值需先给范围保存格式增加值来源区分。

## xiaobai-r3 变更

### 对象权限页的分页投影（2026-10-26）

`GET /api/admin/v1/roles/object-permissions?objectId={code}&page=1&size=10`
要求 `roles:view`、`users:view` 和 `object-types:view`。返回
`{object:{id,name,api},items:[{id,name,defaultDeny,objs,scopes,memberCount}],total,page,size}`。
SQL 先分页角色，再读取本页当前对象的精确 API 授权、范围及成员总数；不读取或返回用户名单。
权限表按页请求；矩阵需要的完整轻量角色投影独立读取，不混入其他对象权限或人员信息。
对象、身份、版本或预览切换会清空旧结果，迟到响应丢弃；空数据和失败不回演示值。

- `GET /roles/meta`：`permObjects` / `objActions` 读本体（已发布 ObjectType 按 seq；动作 = 兜底动作 ∪ 非草稿 ActionType 名），与 `PERM_OBJECTS.json` 兜底取并集；本体为空时整份用兜底。
- `DELETE /roles/{id}`：有用户删完后没有任何角色（成员或 `sys_user.role` 指向该角色）→ 409 `ROLE_IN_USE`，details `{count, users:[{id,name}]}`；否则主角色指向它的用户改为剩余角色里编号最小的一个。
- 新建角色 / 范围取号前加 `pg_advisory_xact_lock`，并发不撞号。
