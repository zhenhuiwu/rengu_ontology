# 本体小白 交接文档（分支 xiaobai-r2，基于 master bb47a5c）

## 2026-10-27 API 密钥当前规则

下文“没有后台管理/鉴权合同、真模式禁用”的 API 密钥记录为此前状态；本次已实现真实管理、默认 10 条数据库分页和固定当前身份的元数据读取 Bearer 白名单，详见 [API 密钥契约与实际证据](../admin-api-keys.md)。`admin_0017` 已保数据增量应用；真实空表/筛选/表单已检查，未实际签发或验证凭据认证闭环。通知等其余历史边界不由此扩大。

## 2026-10-26 真实管理数据追加（覆盖下方旧回退说明）

- 真模式 admin/roles/rowScope/tenants 网络失败不再回退演示数据；Users/Roles/AuditLog/TopBar 初始空态，错误可见，角色/部门候选读库；切换身份或版本防迟到结果。开发库已有种子记录保留，显式 mock 开发模式保留。
- 角色添加成员：按固定顺序锁定用户，整批验证真正新增成员必须启用，失败 422 且无成员或审计变化；已有成员继续幂等跳过（即使后来停用），不产生新授权。
- 对象权限页的角色/成员/权限对象来自数据库 API；不再用固定 R-07/R-04 作敏感列授权。当前后端 ObjPerm 没有 plain 契约，明文授权和该入口敏感标记草稿保存明确未接入并禁用。
- API 密钥没有可用后台管理/鉴权合同，真模式显示空态并禁用创建/复制/吊销和深链生成；通知没有后台服务，顶栏真实通知空态并说明未接入。未新增伪服务或删除开发库记录。
- 验证：`tests/test_admin_role_members_active.py`；前端 `node scripts/check-admin-db.mjs`，并兼容既有 audit-export/user-import/tenant-settings/qa-integration 状态自检。

## 1. 本轮完成

### 2026-10-26 数据连接 → 静态CR（覆盖旧待办第4项）

- 新增`data/ontology_import.py`、`ontology_schemas.py`、`mappings.py`。preview读取真实连接列/主外键，复用规则生成，无模型可用；import-to-cr确认重内省及指纹，受控对象命名/负责人、逐条关系选择，创建或加入带lockVersion的静态草稿。只生成定义，不复制源数据或扩大SQL执行白名单，不写auto_ontology系统草稿，无新增迁移。
- 映射编辑生成对象config的source/table CR，发布后从定义反查连接映射。新来源使用稳定DS编号，物理表使用schema.table；旧名称兼容。当前定义/未发布草稿引用连接时删除409，发布也验证连接存在并与删除取相同父行/每DS锁。字段映射仅回退实际DB来源列/类型。
- 公共`admin.permissions.resolve_owner_name`：用户编号优先，唯一存量姓名兼容；不存在/停用/歧义拒绝。草稿存用户code，发布重验后定义存真实姓名，失败回滚。
- 接口详见`frontend/docs/backend/datasources-api.md`；专项`test_datasource_ontology_import.py`实际PG内省+发布+删除保护+原子回滚，`test_owner_resolution.py`验证负责人边界；不删除开发库seed记录。

| 内容 | 代码 | 文档 / 测试 |
|---|---|---|
| **角色与权限页**（后端 + 前端）：角色增删改、复制、权限保存、行级范围、成员、有效权限 | `app/modules/admin/roles.py` 等；迁移 `admin_0003_sys_role`（down = admin_0002）；`src/api/roles.ts`、`Roles.vue` | `docs/backend/admin-roles-api.md`；`tests/test_admin_roles.py`（有效权限与 mock 逐人对拍） |
| 用户页的角色校验改读 `sys_role` | `admin/service.py` | `tests/test_admin_users.py` |
| **数据源密码加密**（Fernet，可轮换） | `app/modules/data/secrets.py`；新增依赖 `cryptography` | `docs/backend/datasources-api.md`；`tests/test_datasources_real.py` |
| **数据连接页接内省**：真实测试连接 + 内省刷新库表清单 | `app/modules/data/connector.py`、`service.py`、`router.py`；`Connections.vue` 测试成功后重拉清单 | 同上 |

合并注意：迁移只多一个 `admin_0003_sys_role`；`pyproject.toml` / `uv.lock` 加了 `cryptography`，合并后要 `uv sync`。
真实连接默认关闭（`RENGU_DS_REAL_CONNECT=1` 开），所以页面演示数据不变。

## 2. 未完成

- 内省接到「新建对象类型」流程：等静态的变更单发起接口（`handoff/static.md`），再把库表 → 对象类型草稿接上。
- 达梦 / MySQL 的真实连接：缺驱动（dmPython / aiomysql），在 `connector.DRIVERS` 加一行即可。
- ~~删除角色时 `sys_user.role` 悬空~~：xiaobai-r3 已做（409 `ROLE_IN_USE` / 改派主角色）。
- `/preview-sessions`（§1.11）留给 PDE。
- 审计日志导出仍是桩（只返回 jobId）；用户页 Excel 导入（现在只认 CSV）和 lockVersion 没做。

## 10-26 追加：当前用户行级范围 + 顶栏租户组织

- 完成：`GET /api/admin/v1/me/row-scope`、`GET /api/admin/v1/tenants`，sys_tenant / sys_org（admin_0004），TopBar 读后台，`src/api/rowScope.ts` 给问数用。文档 `docs/backend/admin-tenants-row-scope-api.md`。
- 未做：系统配置页租户信息保存；按组织过滤数据（一期单租户）；问数页面接 rowScope.ts 由问数负责。
- 审核遗留（xiaobai-r3 已做 4 项，见下节）；仍未做：`SCOPE_PROPS` 改读 `/meta` 属性标记。

## xiaobai-r3（基于 master b475283）：审核遗留

- 密钥启动检查：`app.main` lifespan 调 `data.secrets.check_secret_key()`；prod 或 `RENGU_DS_REAL_CONNECT=1` 没配 `RENGU_DS_SECRET_KEY` → 打 error 日志并起不来（`DS_SECRET_KEY_MISSING`），格式不对 → `DS_SECRET_KEY_INVALID`；dev/test 没配只 warning。日志不打印密钥。
- 权限对象读本体：`GET /roles/meta` 的 `permObjects` / `objActions` 来自已发布的 `ObjectType`（按 seq）+ 非草稿 `ActionType`（target_api 对应），与 `PERM_OBJECTS.json` 兜底取并集（兜底里本体没有的接在后面），本体为空时整份用兜底；只读静态表，不改静态模块。前端 `Roles.vue` 真后台模式用 `loadPermObjects()`（`src/api/roles.ts`），读不到用 mock 常量。
- 删除角色：成员 + `sys_user.role` 等于该角色名的用户里，有人删完就没有任何角色 → 409 `ROLE_IN_USE`（details `{count, users:[{id,name}]}`），不删；否则主角色是它的用户改成剩下角色里编号最小的那个，再删。前端 mock 同口径（`orphansOf` / `nextPrimary`）。
- 编号加锁：`admin.repo.lock_code_seq()` = `pg_advisory_xact_lock(hashtext('rengu:code:<表>'))`，`sys_role` / `sys_role_scope`（`_next_num`）、`sys_user`（`next_code`）、`datasource`（`max_code_no`）取号前加锁，事务结束自动释放；无迁移。测试 `tests/test_code_lock.py`。
- Roles.vue：首次加载失败后写入成功、刷新失败也提示「已保存，刷新失败：…」。
- 遗留：`main.py` 加了一行（PDE 文件）；mock 模式 `effectiveAccess` 预览仍用 mock 常量；`SCOPE_PROPS` 未读 `/meta`；`/me/row-scope` 现在会列出本体里的对象类型（无权查看），qa 期望可能要跟着变。

## 下一轮待办（10-26 11:00 汇总）

1. `SCOPE_PROPS`（行级范围可选属性）改读 `/meta` 的属性标记，去掉写死列表。
2. mock 模式下 `effectiveAccess` 预览改用 `loadPermObjects()` 同一份对象列表。
3. `/me/row-scope` 现在列出全部本体对象类型（无权查看），和问数确认期望后再定是否只列已授权的。
4. 内省 → 新建对象类型草稿：静态变更单接口已在 master，下一轮接上。
5. 达梦 / MySQL 真实连接驱动（需用户授权后才连外部库）。
6. 审计日志真实导出；用户页 Excel 导入、lockVersion。
7. 系统配置页租户信息保存；按组织过滤数据（一期单租户）。
8. auto-ontology 搁置（前端无页面，用户决定）。
