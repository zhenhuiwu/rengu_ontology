# 本体小白 交接文档（分支 xiaobai-r2，基于 master bb47a5c）

## 1. 本轮完成

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
- 删除角色时用户主角色名（`sys_user.role`）会悬空，api-index 是【待定】。
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
