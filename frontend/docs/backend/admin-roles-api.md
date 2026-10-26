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

元数据常量（页面清单、对象清单、行级可选属性）直接读 `FUNC_PAGES.json` / `PERM_OBJECTS.json` / `SCOPE_PROPS.json`。
新加成员的 seq = 当前最小值 − 1，所以排在最前，和页面 `[...add, ...members]` 一致。

## 接口

读 `require_func("roles","view")`，写 `require_func("roles","manage")`（目前放行桩）；`?ver=` / `?previewAs=` 下写接口 403 `READONLY`。
每个写操作记一条审计（`insert_audit`，动作「修改权限」等，同 AuditLog 页）。

| 方法 | 路径 | 入参 | 返回 | 说明 |
|---|---|---|---|---|
| GET | /roles | — | `Role & {memberCount, lockVersion}`[] | 按 code 排序 |
| GET | /roles/meta | — | `{funcPages, permObjects, scopeProps}` | mock 常量 |
| GET | /roles/{id} | — | 同上单个 | 不存在 404 |
| POST | /roles | `{name, desc?, perms?, copyFrom?}` | 角色 | 名称重复 409；`copyFrom` 复制 func/objs/scopes，不带成员；`perms` 是新建抽屉的模板勾选，按 NR_PERMS 推初始权限【推断】 |
| PATCH | /roles/{id} | `{name, desc, lockVersion?}` | 角色 | 内置 403 `BUILT_IN_ROLE`；改名同步 `sys_user.role` |
| PUT | /roles/{id}/permissions | `{func, objs, lockVersion?}` | 角色 | 页面 key / 动作不在清单 → 422；查看关掉时清空导出和动作 |
| DELETE | /roles/{id} | — | 204 | 内置 403；成员、范围一起删（api-index 是【待定】，先这样）。用户主角色名不改，会悬空 |
| POST | /roles/{id}/scopes | `{objType, prop, op?, values}` | RowScope | 属性须在 SCOPE_PROPS；op 不传按取值个数推 |
| DELETE | /roles/{id}/scopes/{scopeId} | — | 204 | |
| GET | /roles/{id}/members | `page,size,kw` | `PageResult<AdminUser>` | kw 包含匹配姓名 / 账号 / 部门 |
| POST | /roles/{id}/members | `{userIds}` | `{added}` | 已是成员跳过；内置角色也能改成员 |
| DELETE | /roles/{id}/members/{userId} | — | 204 | |
| GET | /users/{id}/effective-access | — | `{rs, func:[{key,lv,...}], objs, areasIn}` | 移植 mock `effectiveAccess()` |

lockVersion 不对 → 409 `CONFLICT`（通用乐观锁）。`/preview-sessions`（§1.11）留给 PDE。

## 有效权限对拍

`backend/tests/fixtures/roles_effective_access.json` 是 mock `effectiveAccess()` 对 120 个用户的结果（func 只留 `[key, lv]`），
测试逐人比对。mock 改了要重生成（在仓库根目录跑，vite 要从这里解析）：

```
node scripts/export-effective-access.mjs /tmp/eff.json   # 再按测试里的格式裁剪 func
```

## 前端接法

`loadRolesState()` 一次拉角色、全部用户（/users 分页拉完）和每个角色的成员；返回 null 表示用 mock。
写操作成功后整份重新拉取；失败弹后台 message，本地不改。预览抽屉的有效权限仍在页面算（规则同后台）。
