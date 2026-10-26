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
