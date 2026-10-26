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
| PUT | /users/{id} | `{name,dept,role,status,lockVersion?}` | 同上 | 账号不能改；版本过期 → 409 `VERSION_CONFLICT`；不存在 / 占位人 → 404 |
| POST | /users:import-preview | `{rows:[{name,account,dept,role}]}` / `{csv:"…"}` / `{xlsxBase64:"…"}` 三选一 | `{rows:[{name,account,dept,role,error}]}` | 只校验不写库，最多500个非空数据行 |
| POST | /users:import | `{rows}`（≤500） | `{imported:n}` | 按预览规则逐行校验，有 error 的跳过，其余建为「待激活」 |
| POST | /users/{id}:enable | — | 用户 | 停用 / 待激活 → 启用 |
| POST | /users/{id}:disable | `{transferTo?}` | 用户 | 见下 |

### GET /users（和 mock 一一对应）

- 过滤同 Users.vue `rows`：`dept`、`status`（启用 / 停用 / 待激活；`全部` 或不传 = 不限，其他值 422）；
  `kw` 为姓名、账号、用户 ID 任一**包含**（区分大小写，**不 trim**，同 `u.name.includes(kw)`）。
- 顺序 = mock `ADMIN_USERS` 数组顺序 = 用户编号升序。
- 分页同 `paginate()`：`page` 越界收拢到最后一页，`page ≤ 0` 当第 1 页；`size` 默认 20（前端传 17），上限 200。
- `pendingApprovals` = mock `pendingApprovals`：启用 ? (编号数字 × 13) % 5 : 0。[推断] 审批模块接上后改真实计数。
- 字段：`id,name,account,dept,role,status,lastLogin('YYYY-MM-DD HH:mm'|null),createdAt('YYYY-MM-DD'),pendingApprovals,lockVersion`。

### import-preview 校验（按顺序取第一条错误）

行字段先 trim，再检查长度（姓名64、账号128、部门128、角色64），超长在预览标行错误，最终提交按同一规则跳过；随后为姓名为空 → 账号不是邮箱（`^\S+@\S+$`，同前端 formOk）→ 部门不存在（不在 ADMIN_DEPTS）→ 角色不存在（查 sys_role）→ 账号已存在，导入时跳过 → 文件内账号重复。最终提交重新校验，不信任客户端 `error`。

- 三种输入必须且只能提供一个；`rows`、文件预览和最终提交统一最多500行，超出422 `VALIDATION_FAILED`，不截断。字段 NUL / 孤立代理字符在预览和提交均为422，不进入数据库查询；CSV原文同样检查，避免解析后500。
- CSV继续 UTF-8（可带 BOM）、原文最多1,000,000字符，首行表头 `姓名,账号,部门,角色` 可重排，未知列忽略、缺列当空、空行忽略。重复**已知**表头422，避免静默覆盖；其他原有行为保留。
- 新增 XLSX：严格 base64 编码文件内容（不带 data URL 前缀），解码后最多2 MiB；后端 `openpyxl + defusedxml` 解析，无前端Excel库、multipart或新表。只导入第一个工作表，首个非空行必须包含四个模板表头，可重排，允许未知附加列；空工作簿 / 仅表头返回空预览。数值等非空值转文本，公式单元格直接422，不求值、不取公式缓存；宏、旧xls、模板xltx/xltm、加密/损坏包不支持。CSV中的公式样式字符串仍作普通文本处理，不执行。
- XLSX ZIP最多128项、总展开20 MiB；检查重复成员、加密及压缩方式、实际有界读取，内存处理且不解压落盘。包类型复用openpyxl清单模型识别，拒绝伪装宏包；defusedxml防实体扩展。使用只读工作簿、关闭外部链接、最后关闭工作簿；重置不可信dimension，最多扫描10000物理行和32列，过界422并提示移除多余格式/空行，不会把超出500人或伪造dimension静默丢掉。文件解析在线程中执行，不阻塞事件循环。

### disable

- 名下待办审批 > 0 且没给 `transferTo` → 422 `TRANSFER_REQUIRED`（api-index【待定】，本分支按必填做）。
- `transferTo` 必须是另一个「启用」的列表用户，否则 422 `VALIDATION_FAILED`。
- 已停用再停用：幂等，原样返回。转交本身是桩（审批数据在动作 / 变更单模块）；停用后 pendingApprovals 为 0。

## 和 mock / 契约的差异

1. 新建 / 导入的用户编号 = 现有最大 `U` 编号 + 1（`U011065`…，6 位补零），排在列表**末尾**；
   mock 是 `U${1000+n}` 插到最前（unshift）。
2. 部门、角色名是常量（mock 原样）；新建 / 编辑时 role 不在名单里 → 422。
3. 写操作暂不写审计日志（admin 自己的 insert_audit 可直接接，下一阶段做）。
4. 编辑支持可选正整数 `lockVersion`；不传兼容旧调用。过期时 409 `VERSION_CONFLICT`，不覆盖已保存内容。

## 前端

Users.vue 已接真接口（`src/api/admin.ts`）。只有显式 mock 模式使用本地数据；真模式网络、5xx 和业务错误全部显示错误，列表与候选保持空态，不切换演示用户。

- 列表：`queryUsers` 按 部门 / 状态 / 关键词 / page / size 查一页；部门来自 `GET /depts`，角色来自 `GET /roles`，失败或空列表不回退固定候选。身份或版本切换清空旧数据，并丢弃迟到结果；读取成功前禁止修改。
- 新建 / 编辑：`POST /users`、`PUT /users/{id}`，成功后重新拉当前页。
- 编辑抽屉打开时固定用户的 `lockVersion`；同用户列表刷新不替换编辑内容或版本。409 时保留抽屉、输入和原版本，提示核对最新数据后重新编辑。
- 批量导入：模板仍下载CSV（示例部门使用有效完整部门），可上传CSV或无宏XLSX，上传即调 `users:import-preview`，预览表显示校验结果。解析时清旧预览、显示busy并禁用确认；提交有busy防重复。文件选择 / 关闭窗口 / 身份及查看上下文变化均使旧结果失效；确认卡返回后再次核对上下文，避免将旧预览以新身份提交。演示模式明确不解析，不再为任意文件伪造SAMPLE用户。确认后 `users:import`，有错的行跳过。
- 启用 / 停用：`users/{id}:enable`、`users/{id}:disable`；有待办审批时把转交人作为 `transferTo` 传上去。
- 已知限制：真接口下编辑抽屉、停用转交人候选只在当前页里找（mock 下是全量）；导入仅创建「待激活」记录，没有实现激活邮件发送或多工作表合并。
