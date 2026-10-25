# 用户与组织 API（admin 模块，前端 `src/pages/admin/Users.vue`，api-index §4.1）

挂载：`/api/admin/v1`（`app/api.py` 原有挂载，未改）。代码：`backend/app/modules/admin/*`；
迁移 `admin_0002_sys_user_role`；测试 `backend/tests/test_admin_users.py`。

## 数据

- `sys_user` 新增两列（admin_0002，down_revision = admin_0001）：
  - `role varchar(64)`：角色名（单个字符串，同 mock `AdminUser.role`）。角色表 sys_role 由 PDE 建，届时改外键。
  - `joined_on date`：前端 `createdAt`。
- 回填：迁移按 `seeds/json/system/ADMIN_USERS.json` 的 `role`、`createdAt` 按 code 更新已有行；seed 的 `_user`
  transform 也写这两列（重跑 seed 值一致）。`extra/system/SYS_USERS.json` 的 8 个占位人（王敏 U010001 等）没有 role，保持 null。
- **用户列表 = role 非空的 sys_user**：占位人不出现在列表里，和 mock 的 120 人一一对应。

## 接口

读接口 `require_func("users","view")`，写接口 `require_func("users","manage")`（目前是放行桩）。
写接口在 `?ver=` / `?previewAs=` 下由全局 ReadonlyGuardDep 返回 403 `READONLY`。

| 方法 | 路径 | 入参 | 返回 | 说明 |
|---|---|---|---|---|
| GET | /users | `dept,status,kw,page,size` | `{items,total,page,size}`，item = `AdminUser & {pendingApprovals}` | 见下 |
| GET | /depts | — | `string[]` | mock `ADMIN_DEPTS` 原顺序（常量，部门表由 PDE 补） |
| POST | /users | `{name,account,dept,role,status?}` | `AdminUser & {pendingApprovals}` | account 唯一 → 409 `DUPLICATE_KEY`；status 默认「待激活」 |
| PUT | /users/{id} | `{name,dept,role,status}` | 同上 | 账号不能改；不存在 / 占位人 → 404 |
| POST | /users:import-preview | `{rows:[{name,account,dept,role}]}` 或 `{csv:"…"}` | `{rows:[{name,account,dept,role,error}]}` | 只校验不写库 |
| POST | /users:import | `{rows}`（≤500） | `{imported:n}` | 按预览规则逐行校验，有 error 的跳过，其余建为「待激活」 |
| POST | /users/{id}:enable | — | 用户 | 停用 / 待激活 → 启用 |
| POST | /users/{id}:disable | `{transferTo?}` | 用户 | 见下 |

### GET /users（和 mock 一一对应）

- 过滤同 Users.vue `rows`：`dept`、`status`（启用 / 停用 / 待激活；`全部` 或不传 = 不限，其他值 422）；
  `kw` 为姓名、账号、用户 ID 任一**包含**（区分大小写，**不 trim**，同 `u.name.includes(kw)`）。
- 顺序 = mock `ADMIN_USERS` 数组顺序 = 用户编号升序。
- 分页同 `paginate()`：`page` 越界收拢到最后一页，`page ≤ 0` 当第 1 页；`size` 默认 20（前端传 17），上限 200。
- `pendingApprovals` = mock `pendingApprovals`：启用 ? (编号数字 × 13) % 5 : 0。[推断] 审批模块接上后改真实计数。
- 字段：`id,name,account,dept,role,status,lastLogin('YYYY-MM-DD HH:mm'|null),createdAt('YYYY-MM-DD'),pendingApprovals`。

### import-preview 校验（按顺序取第一条错误）

姓名为空 → 账号不是邮箱（`^\S+@\S+$`，同前端 formOk）→ 部门不存在（不在 ADMIN_DEPTS）→ 角色不存在（不在 mock ROLES 名单）
→ 账号已存在，导入时跳过 → 文件内账号重复。CSV：UTF-8，可带 BOM，首行表头 `姓名,账号,部门,角色`（顺序不限），空行忽略。
Excel 不收（api-index【待定】）。

### disable

- 名下待办审批 > 0 且没给 `transferTo` → 422 `TRANSFER_REQUIRED`（api-index【待定】，本分支按必填做）。
- `transferTo` 必须是另一个「启用」的列表用户，否则 422 `VALIDATION_FAILED`。
- 已停用再停用：幂等，原样返回。转交本身是桩（审批数据在动作 / 变更单模块）；停用后 pendingApprovals 为 0。

## 和 mock / 契约的差异

1. 新建 / 导入的用户编号 = 现有最大 `U` 编号 + 1（`U011065`…，6 位补零），排在列表**末尾**；
   mock 是 `U${1000+n}` 插到最前（unshift）。
2. 部门、角色名是常量（mock 原样）；新建 / 编辑时 role 不在名单里 → 422。
3. 写操作暂不写审计日志（admin 自己的 insert_audit 可直接接，下一阶段做）。
4. 编辑不带 lockVersion（契约没有），后写覆盖。

## 前端

Users.vue 已接真接口（`src/api/admin.ts`，apiClient 的 put/patch/delete 由 cherry-pick 215819d 补上），回退规则同审计日志：
开关 admin 没切到真接口、或后台没起来（网络错 / 5xx）→ 用本地 mock，原逻辑不变；其他错误（409 / 422 等）弹提示。

- 列表：`queryUsers` 按 部门 / 状态 / 关键词 / page / size 查一页；部门下拉来自 `GET /depts`（失败回退 ADMIN_DEPTS）。
- 新建 / 编辑：`POST /users`、`PUT /users/{id}`，成功后重新拉当前页。
- 批量导入：只收模板 CSV，上传即调 `users:import-preview`，预览表多一列「校验」（error）；确认后 `users:import`，有错的行跳过。
- 启用 / 停用：`users/{id}:enable`、`users/{id}:disable`；有待办审批时把转交人作为 `transferTo` 传上去。
- 已知限制：真接口下编辑抽屉、停用转交人候选只在当前页里找（mock 下是全量）；Excel 导入未做。
