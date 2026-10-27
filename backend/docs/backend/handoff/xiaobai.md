# 本体小白 交接文档（分支 xiaobai-p1 / xiaobai-p2）

## 2026-10-27 batch 3（分支 xiaobai-p2）

### 1. 已知遗留（已修）

- **admin 路由识别**：FastAPI 0.162 的 `scope["route"].path` 是路由器相对路径，旧判断 `"/api/admin/" in route` 一直为假。现在 `auth.full_route_path(request)` 还原前缀，`auth.is_admin_request(request)` 按实际路径 + `api_prefix("admin")` 判断。admin 路由 X-Ontology-Id 不可用（999999 / abc / 0）时不再报 403 / 404，按会话已有范围（没有则不绑定）处理，也不受 viewer 只读拦截；非 admin 路由行为不变。工作空间绑定抽成 `auth.bind_workspace(session, request, user)`。测试 `tests/test_admin_route_discovery.py`。
- **审计导出 403**：根因就是上一条，已随之修复。
- **数据连接导入夹具**：`tests/test_datasource_ontology_import.py` 补本体成员（`bind_workspace_members`），25 个用例通过；`test_admin_tenant_settings.py` / `test_admin_users.py` 夹具同样补成员。

### 2. 服务账号身份（只做鉴权侧）

- 公开函数 `app.modules.admin.api_keys.build_service_account_principal(session, service_account_or_code, *, tenant_id=None, bind_ontology=True) -> CurrentUser`：
  - 不依赖请求，可以在 worker / 机器调用里用；按 code 重新读库（传实例也重读）。
  - 不存在或已吊销 → `Unauthorized("服务账号已吊销或不存在")`（details.principal = `api:{code}`）；绑定的角色不存在 → Unauthorized。
  - `roles=(role.code,)`、`func=role.func`、`is_service_account=True`、`service_account_id`。
  - `bind_ontology=True`（默认）时要求会话已绑定本体（`set_ontology_scope`）且角色已绑定该本体（`role_bound`），否则 Unauthorized；返回 `ontology_role="service"` 和角色的行级范围。
  - 旧私有名 `_service_account_user` 保留为别名（batch 2 是在 authenticate_key 里内联构造的，此前没有这个私有函数）。
- `CurrentUser` 新字段（人类调用方都是 None）：`service_account_id`、`api_key_prefix`、`api_scopes`、`action_allowlist`、`event_allowlist`（tuple，None 表示不限）、`quota = {"minute": {"limit", "used"}, "day": {...}}`（本次请求扣减后的状态，未配置窗口为 None）。`is_service_account` 已有。
- 无迁移，没有 run-as 字段。测试 `tests/test_service_account_principal.py`（含吊销、未绑定、不存在、请求内字段）。
- **Palantir 可替换的复制代码**：`app/modules/machine/principal.py` 的 `resolve_actor` 约 27-48 行（role_bound / ServiceAccount / SysRole / role_row_access 的导入，按 code 查账号 + 吊销判断，取角色，role_bound 校验，构造 CurrentUser）可以整段换成 `return await build_service_account_principal(session, code)`。错误语义一致（都是 Unauthorized），只是消息文字不同。我没有改他们的文件。

### 3. MetricFlow 引擎

- 见 [metricflow-engine.md](../metricflow-engine.md)。服务函数 `metric_query(session, user, metrics=, dimensions=, filters=, limit=, backend="auto") -> {sql, backend, columns, rows}`，执行一律走 run_guarded。
- 可选 extra `metricflow`（0.213，Apache-2.0，不含 dbt-core），没装时回退内置编译器。`uv lock` 联网成功，锁文件已更新。**影响基础环境**：sqlglot 30.24.0 → 30.25.0，python-dateutil 3.0.0 → 2.9.0.post0（metricflow 的上限）。合并后要 `uv sync`。
- time spine 默认 `public.metricflow_time_spine(date_day)`，本批次没有建表；需要读 spine 的查询（累计、补零、偏移）在建表并登记为受控表之前会被 run_guarded 拒绝。
- derived 指标 `a / b` 在 Postgres 里两边都是整数时做整除（MetricFlow 原样输出）；口径需要小数时，在导出的 expr 里写 `1.0 * a / b`。

### 4. 种子修复方案（只是方案，未改 seed / mock）

目标链路：库存告警（R-012）→ 补货建议（`request_replenish` 排第 1）→ 人工确认 → Webhook 投递 → decision_log。依据 `handoff/dynamic.md` 的「种子缺口」。

| # | 改什么 | 为什么 |
|---|---|---|
| 1 | 新增角色 **供应链计划员**（非内置，func：alerts view + action-center use/manage + action-types view；行级范围 门店.大区 = 华东），并绑定到本体 1（`ontology_role_binding`） | `request_replenish` 条件「执行人角色 ∈ {门店店长, 供应链计划员, 规则 R-012}」里这个角色没登记，试运行一直「未登记角色」 |
| 2 | **周晓峰 U010011**（华东事业部 / 供应链，已在 SYS_USERS）：主角色设为供应链计划员；加入本体 1 成员（editor） | R-012 autoStage.confirmer 写的是「供应链计划员 周晓峰」，需要能定位到唯一在用用户；他同时作为演示告警的处理人和确认人 |
| 3 | 新增角色 **区域供应链经理**（func：action-center approve/manage + alerts view，行级范围 华东），选一个在用用户担任，例如 **姚娜 U010812**（华东 / 供应链，现为动作审批人，启用）或 白平 U010791；本体 1 成员（editor） | `request_replenish.approval` =「≤ 2 万元免审；超过需区域供应链经理审批」，只有角色泛称，被判 `approvalConfigured=false`，所以所有动作都「未配置可用审批人」。审批人配置改成 userCode，例如 `U010812`（「区域供应链经理 姚娜」能唯一定位） |
| 4 | 门店店长（R-06）绑定本体 1，并设一个在用的演示店长（例如华东的 林超 U010329，主角色改为门店店长，行级范围 本店） | 条件列表里另一类执行人，用来演示行级范围收窄后的候选 |
| 5 | 演示告警 AL-20261025-0139：`assignee` = U010011 | 条件「关联告警：当前用户必须是告警处理人」，现在 assignee 为 null，试运行不过 |
| 6 | 动作参数（Palantir / 动态负责，不是用户种子）：给 request_replenish 加「原因」参数或去掉「原因必填」条件；商品 / 门店参数规则改成可校验的条件；qty 映射要么接求值器，要么在演示里由确认人手填 | 剩下的试运行失败原因，和用户 / 角色无关，列出来方便一起排期 |
| 7 | Webhook：新增一个外部系统（类别 Webhook，测试环境指向本地回执端点 / 测试替身，带签名密钥引用，不写明文），并把 request_replenish 的执行通道配到它（或者另加一个「补货申请通知」事件订阅） | 现在默认提交 / 事件投递是 UnconfiguredAdapters，人工确认后整请求 503；没有通道就走不到 Webhook，也走不到 decision_log 的执行结果 |
| 8 | 服务账号（可选，演示机器调用）：新增 SA「补货助手」，绑定供应链计划员角色，密钥范围 dynamic:read + actions:dry-run，动作白名单只有 request_replenish | 验证 `caller_kind=service_account` 和 key-allowlist；用 `build_service_account_principal` 构造身份 |
| 9 | 审批人改真实用户：mock 里的赵磊、吴佳等非系统审批人，改成已在 sys_user 里的在用用户编号 | dynamic.md 第 74 行：这些动作在库里没人能审批 |

注意：
- 只改 `seeds/json`、`seeds/extra` 和对应的 seed 加载代码，开发库 `rengu` 不手工写。新增角色走 seed 后重跑，不需要迁移。
- 姓名比较是按唯一在用用户来的（seed CHECKS 不允许重名），所以选人时要避开重名和停用 / 待激活的用户（例如高伟、卢静是停用的）。
- 改完要重新导出 mock（`seeds/json/dynamic/*`），前端演示值（王敏 / 陈立军）不受影响。

### 5. 既有失败（master 上同样失败，本批次没改）

- `test_admin_audit_export::test_export_migration_roundtrip_and_metadata`：注释漂移（`api_key.code`、`platform_setting.code` 是我的；`decision_log.code`、`rule_schedule_run.release_id` 是动态的）。
- `test_audit_logs` 9 个用例：返回多了 `ontologyId: None`，和 mock 不一致。
- `test_admin_roles::test_list_and_get_match_mock`。
- `test_admin_users`：`test_depts`（部门路径名）、`test_import_preview_csv_and_rows`（「部门不存在」的期望）、`test_enable_disable`（期望 422 TRANSFER_REQUIRED，实际 200）。
- `test_api_key_dynamic_capabilities::test_meta_read_plus_dynamic_read_sees_rules_section`：夹具报错，`grant_fixture_roles` 找不到 U010001（动态的夹具）。

## 2026-10-27 batch 2（基于 master e97bc61）

- 前端 ApiKeys.vue（真模式）：范围按编码 + 中文名、服务账号绑定、动作/事件白名单、限流配额、详情“调整授权与配额”（PATCH，只收窄）、服务账号抽屉（列表/新建/吊销）；写操作不回退 mock。Roles.vue 添加行级范围发送 `objectTypeCode`。
- 角色删除 500 修复：admin_0026 将 `ontology_role_binding.role_id` FK 改为 ON DELETE CASCADE（参照动作绕过 RLS，其他本体的绑定也删）；服务账号引用 → 409 ROLE_IN_USE。测试 `tests/test_role_delete_bindings.py`。
- 第 7 个范围 `dynamic:read`：alerts / rules / object-types / action-center view。接入写法 `Depends(require_api_scope("dynamic:read"))`。测试在 `tests/test_api_key_scopes.py`；meta:read + dynamic:read 看到 `/meta/capabilities` rules 分节：`tests/test_api_key_dynamic_capabilities.py`（`-p tests.use_ops_support`）。
- C1 组织规则：`app/modules/admin/org_scope.py`，`@org` 范围 + 每本体组织列配置 + gaps；`/roles/meta.scopeProps` 现在真的返回 `objectTypeCode`（之前 schema 丢了）。测试 `tests/test_org_scope.py`。
- C2 导入草稿取值提示：`app/modules/data/ontology_import.py` 的 `sample_distinct` / `value_hints`，入参 `sampleValues`（默认关），响应 `llm: false` / `sampled`；纯规则，无行业词表。测试 `tests/test_ontology_import_value_hints.py`。
- C3 MetricFlow 适配骨架：`app/common/metricflow_engine.py`（导出 → 内置编译 → run_guarded），`metricflow` 为可选依赖、未装、未改锁（batch 3 已接入，见上）。测试 `tests/test_metricflow_engine.py`（导出形状夹具）。
- 规则与契约见 [ontology-workspaces.md](../../ontology-workspaces.md) “API 密钥权限范围与服务账号”“行级范围按本体”。

### 已知问题（未在本轮修复）

- `tests/test_admin_roles.py::test_list_and_get_match_mock` 在 master 上即失败（与 mock 对比不一致），非本轮引入。
- `test_audit_logs` ontologyId 与 mock 不一致；`test_admin_audit_export` 工作空间 403。
- 元数据注释漂移：`api_key.code`、`platform_setting.code`、`rule_schedule_run.release_id`。
- 7 个 test_sql_guard e2e 用例的夹具在空间 1 没有对象类型（per-ontology governed_tables 后解析为空即拒绝）。
- `app/common/auth.py` 用 `"/api/admin/" in route` 判断，FastAPI 0.162 的 route.path 为路由器相对路径，判断失效（api_keys 用 `full_route_path` 自行还原）。
- `tests/test_datasource_ontology_import.py` 整体失败（本分支未改该文件和预览权限逻辑）：夹具未建本体成员，预览 403（C2 测试自行用 `bind_objects_workspace` 补成员，未改该文件）。
- 前端构建复用 /workspace/rengu/frontend/node_modules（worktree 内软链，已被 .gitignore 忽略）。


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
