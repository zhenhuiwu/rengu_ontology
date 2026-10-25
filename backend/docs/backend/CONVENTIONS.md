# 后台开发约定（rengu_ontology backend）

> 基础层（core / db / common / contracts / seeds 框架）负责人：本体小白。扩展点（鉴权、行级过滤取数、审计落库、`?ver=` 快照读）由 PDE工程师实现。
> **团队规则：后台行为以前端为准，不额外加前端没有的限制；后台自己推断的规则一律标注 `[推断]`。**

## 0. 文档放哪

- 后台团队文档放仓库顶层 `docs/backend/`（和 `static-api.md`、`qa-api.md`、`action-event-api.md` 放在一起）。
- `backend/` 里只放代码和运行用的 `README.md`。
- 骨架目前是独立仓库，所以先放在 `backend/docs/backend/`，合并时由 PDE 移到顶层。

## 1. URL

| 分组前缀 | 模块（代码目录 `app/modules/<module>/`） |
|---|---|
| `/api/ontology/v1` | versioning（版本/发布）、static（静态本体）、dynamic（动态规则）、action、event |
| `/api/qa/v1` | qa（问数，代理 auto-ontology） |
| `/api/data/v1` | data（数据连接）、integration（外部系统 ext_system、回写调用 writeback_call、对账；Palantir专家） |
| `/api/admin/v1` | admin（用户、角色、API Key、配置、审计） |
| `/api/biz/v1` [推断] | biz（演示业务数据：门店、商品、订单等，首页看板和问数对象搜索用；分组待 PDE 确认） |

- 挂载表在 `app/api.py` 的 `MOUNTS`，前缀由 `settings.api_prefix_template = "/api/{module}/v1"` 生成。代码按模块分目录，路由按分组挂载。
- 资源名用复数、kebab-case：`/releases`、`/change-requests/{id}`。
- 动作用子路径 `POST /change-requests/{id}/submit`（不用 `:submit`）。
- 健康检查：`/healthz`（进程活着）、`/readyz`（数据库可用）。不加前缀。

## 2. JSON 和错误

- **字段名和枚举值与前端 mock 完全一致**：camelCase；前端用中文状态值的，接口就返回中文。
- 数据库内部用 snake_case 加英文代码，在 Pydantic 层转换。
- 所有接口模型继承 `app.core.schema.ApiModel`，它带 `alias_generator=to_camel`、`populate_by_name`、`from_attributes`。
- 成功响应直接返回资源本身，不包 `{data: ...}`。
- 失败响应统一为 `{code, message, details, requestId}`，HTTP 状态码表达错误类别。

| code | HTTP | 场景 |
|---|---|---|
| `VALIDATION_ERROR` | 422 | 参数校验失败（details 里是字段错误） |
| `UNAUTHORIZED` / `NO_PERMISSION` | 401 / 403 | 未登录 / 无权限 |
| `NOT_FOUND` | 404 | 资源不存在 |
| `DUPLICATE_KEY` | 409 | 违反唯一约束 |
| `VERSION_CONFLICT` | 409 | 乐观锁 `lockVersion` 不一致 |
| `READONLY_HISTORY` / `READONLY_PREVIEW` | 409 | 在 `?ver=` 或 `?previewAs=` 下写入 |
| `INTERNAL_ERROR` | 500 | 未处理异常（不泄露堆栈） |

- 业务错误要抛 `app.core.errors` 里的 AppError 子类，不要直接抛 HTTPException。
- 请求 ID 规则：
  - 入参 `X-Request-ID` 会原样透传，没有时由后台生成。
  - 后台在响应头里回写这个 ID，并写进日志。

## 3. 分页

- **页面上有 ‹ 1/N › 或页码的列表**用 `page`/`size`：
  - 参数：`?page=1&size=20`，size 不超过 200。
  - 返回 `{items, total, page, size}`。
  - 实现：`PageParamsDep` 加 `Page[T]`，或者用 `BaseRepository.list_page`。
- **页面本来就用游标的**（如审计日志）：
  - 参数：`?cursor=&size=`。
  - 返回 `{items, nextCursor}`。
  - 实现：`CursorParamsDep` 加 `CursorPage[T]`。游标内容由模块自定，建议 base64 编码的 `(sort_key, id)`。
- 前端一次拉全量、没有分页的列表，直接返回数组，不硬加分页。

## 4. 枚举和状态机

用 `LabelEnum`（`app/core/enums.py`），成员名就是数据库代码，值就是接口标签：

```python
class CrStatus(LabelEnum):
    draft = "草稿"
    in_review = "审核中"
    processing = "doing"     # DB processing ↔ API doing（标签不一定是中文，跟 mock 走）
    published = "已发布"

status: Mapped[CrStatus] = enum_column(CrStatus)        # 数据库存 name：'processing'
class Out(ApiModel): status: CrStatus                   # 接口输出 value：'doing'
from_api(CrStatus, "doing") is CrStatus.processing       # 入参 / seed 转换
enum_map(CrStatus)  # {'draft': '草稿', ...}：给文档和前端对照用
```

- 数据库列是 `varchar` 加 CHECK 约束（`native_enum=False`）。加枚举值只需写一个迁移改 CHECK，不用 `ALTER TYPE`。
- 状态机：每个模块在 `service.py` 里写一张 `TRANSITIONS: dict[Status, set[Status]]`，统一由 `ensure_transition(cur, nxt)` 校验。
  - 不合法的流转返回 409 `INVALID_TRANSITION`。
  - 允许哪些流转**照抄前端按钮的出现条件**，前端能点的后台都要放行。
- 时间格式：
  - 前端 mock 用 `YYYY-MM-DD HH:mm`（北京时间），接口用 `DisplayDateTime` 输出同样格式。
  - 入参既接受这个格式，也接受 ISO 8601。
  - 数据库一律存 `timestamptz`。

## 5. 时间：中央时钟

- **所有"现在"都要调 `app.core.clock.now()`**，包括 `local_now()`、`today()`、`last_days(n)`。
  - ruff 规则 TID251 禁用了 `datetime.now`、`utcnow` 和 `date.today`。
  - 只有 `clock.py` 不受这条规则限制。
- `RENGU_FIXED_NOW` 控制当前时间：
  - dev、test、seed 环境默认是 `2026-10-25T15:42:00+08:00`，和前端 mock 的“现在”一致。
  - `RENGU_ENV=prod` 时用真实时间。
- 以下逻辑一律基于 `clock`：
  - procrastinate 定时任务；
  - “今天 / 近 7 天”这类统计；
  - 默认值（mixin 的 `default=clock.now`）。
- 数据库的 `server_default now()` 只是兜底，ORM 写入时以应用时钟为准。
- 测试里用 `with clock.frozen(dt):` 冻结时间。

## 6. 表设计

- **表名前缀**按模块区分，格式是前缀加下划线：

| 前缀 | 模块 |
|---|---|
| `ont_` | 版本/发布（versioning） |
| `st_` | 静态本体（static） |
| `dy_` | 动态规则（dynamic） |
| `ac_` | 动作和事件（action、event） |
| `qa_` | 问数（qa） |
| `dt_` | 数据连接（data） |
| `sys_` | 管理（admin） |
| `bz_` | 演示业务数据（biz） |
| 无前缀 | integration 的 `writeback_call`、`ext_system`（团队约定沿用原表名；**不要新建 `ext_call`**） |

- 表名用单数 snake_case，例如 `st_object_type`。
- **主键**两种用法：
  - 有业务编码的（`CR-0016`、`v2026.10.3`、对象 api 名），用 `StrIdMixin`（`id varchar(64)`）或直接以业务码为主键。
  - 其余用 `IdMixin`（`bigint identity`）。
  - 前端 mock 的复合主键，在后台改成单列主键加唯一约束。
- **约束命名**由 `NAMING_CONVENTION` 自动生成（`pk_`、`fk_`、`uq_`、`ix_`、`ck_`），不要手写约束名。
- **时间**一律用 `timestamptz`，不用 `timestamp`。日期用 `date`。
- **JSONB** 用 `JsonType`。只用于结构由前端整体读写、不参与过滤的字段，例如快照、配置、`calls[]`。要过滤的字段必须拆成列。
- **mixin**：

| mixin | 提供的列 |
|---|---|
| `AuditColumnsMixin` | `created_at/by`、`updated_at/by` |
| `SoftDeleteMixin` | `deleted_at/by` |
| `LockVersionMixin` | `lock_version`，接口叫 `lockVersion`，乐观锁 |
| `SeedMarkMixin` | `is_seed` |
| `OntologyVersionMixin` | `ontology_ver` |

- 跨数据库差异走 `app/db/dialects`：`get_adapter(session).upsert(...)` 和 `.xact_lock(key)`。业务代码不要直接写 `ON CONFLICT`。

## 7. 本体版本（整版本、只追加）

接口定义在 `app/contracts/versioning.py`，参考实现是 `app/modules/versioning/service.py`。

- `create_version(session, change_order_ids, *, published_by, note)`：
  - 和调用方的业务写入在**同一事务**里执行。
  - 会先加全局锁 `ontology.publish`，再写一行新的 `ont_release`。
  - 版本号格式 `vYYYY.MM.N`，月份按北京时间算。
- **回滚 = 生成新版本**：`create_version_from(session, target_ver, rollback_cr_id, ...)`。
  - 新版本内容等于 `target_ver`，`kind=rollback`，`rollbackOf=target_ver`。
  - 同时生成一张回滚变更单，供审计追溯。
  - 前端规则：“只能回滚最近一次发布”，也就是 target 必须是当前版本的 parent。
- **只追加，不改不删**：数据库触发器拒绝 `DELETE` 和修改内容的 `UPDATE`（内容不变的 UPDATE 放行，seed 重跑才能幂等）。
- `?ver=vX`：只读读取历史快照，`ViewContextDep` 负责解析。写接口要依赖 `WritableDep`，在 `?ver=` 或 `?previewAs=` 下写入会返回 409。
- `[推断]` 规则：
  - 没有变更单时发布返回 409 `EMPTY_RELEASE`。
  - `kind` 字段（发布/回滚）。

## 8. 动作提交和幂等键

接口定义在 `app/contracts/actions.py`。

- `submit(action_run_id, idempotency_key) -> SubmitResult`：
  - 返回状态之一：`SUCCESS`（成功）、`FAILED`（失败）、`UNKNOWN`（结果未知），外加回执 `receiptId`、`code`、`retries`、`elapsedMs`、`calls[]`。
  - `UNKNOWN` 交给对账处理：`ReconcileQuery.query_by_key` 查结果，然后回调 `ReconciliationListener.on_reconciled(action_run_id, result)`。
- **幂等键在暂存（staging）时生成**（`new_idempotency_key()`），之后不再改变。
- **重新提交时新建一条 run，并换一个新键**（`new_resubmit_key(old, n)`，格式是原键加 `-rN` 后缀），不复用旧 run。
- 表结构写法：`idempotency_key varchar(64) not null`，加部分唯一索引 `unique_except_seed(...)`（见 §9）。

## 9. Seed：mock 数据入库

### 目录

```
backend/seeds/json/<module>/*.json          ← PDE 的 npm run export-mock 生成，不要手改
backend/seeds/json/<module>/derived/*.json  ← 派生/函数类 mock（export-mock 的 derived 配置）
backend/seeds/extra/<module>/*.json         ← 后端补充：mock 里没有，只为满足外键等（手写）
backend/app/modules/<module>/seeds/__init__.py  ← 本模块的 loader（SeedSpec 列表）
```

### 规则

1. **与 mock 一一对应**：接口读出来的结果必须和前端 mock 完全相同（`tests/test_versioning.py::test_list_matches_mock` 是示例）。
   - 时间保持北京时间原值，由 `parse_display_dt` 解析。
2. **先 json/ 再 extra/**，所有写入在同一事务内完成。
   - 如果某个外键要靠 extra 里的行才能满足，这个外键要声明 `DEFERRABLE INITIALLY DEFERRED`。
3. **按业务键 UPSERT**（`SeedSpec.key`，例如 `("ver",)`），重跑幂等。`make seed` 跑两次结果相同，这一条有测试覆盖。
4. **`is_seed`**：表混入了 `SeedMarkMixin` 时，loader 会自动把 seed 写入的行标成 `is_seed=true`。
5. **mock 本身违反唯一约束时**（例如 31 个重复的幂等键），把唯一约束改成部分唯一索引：

```python
class ActionRun(IdMixin, SeedMarkMixin, Base):
    __tablename__ = "ac_action_run"
    __table_args__ = (unique_except_seed("ac_action_run", "idempotency_key"),)
# → CREATE UNIQUE INDEX uq_ac_action_run_idempotency_key_nonseed ON ac_action_run (idempotency_key) WHERE NOT is_seed
```

   - seed 行之间可以重复，业务新写入的行仍然受唯一约束（见 `tests/test_conventions.py::test_seed_marks_and_partial_unique`）。
   - 只有在 mock 真的有重复时才这样做，不要预防性地放宽。

### 模块负责人迁移 mock 的步骤

1. 让 PDE 在 export-mock 配置里加上你的文件，执行 `npm run export-mock`，输出到 `seeds/json/<module>/`。
2. 在 `models.py` 建表，执行 `make revision m="st: add object_type"` 生成迁移，再人工检查迁移内容。
3. 在 `app/modules/<module>/seeds/__init__.py` 写 `SEEDS = [SeedSpec(Model, "xxx.json", key=(...), transform=...)]`。
   - transform 负责三件事：camelCase 转 snake_case、中文标签转英文代码（`from_api(Enum, v)`）、时间解析。
   - JSON 顶层是对象时，用 `path=` 指定要读的数组。
4. 运行 `make seed-dry` 校验，再运行 `make seed`。
5. 写测试，确认列表、详情接口的输出和 mock 逐字段相等。

## 10. 行级过滤（jsonb 结构，基础层负责）

结构定义在 `app/common/row_filter.py`。静态模块的 `ROLE_OBJECT_SCOPE.row_filter` 列存的就是这个结构。

```json
{"and": [
  {"field": "region", "op": "=", "valueFrom": "user.region"},
  {"or": [{"field": "format", "op": "in", "value": ["社区店", "前置仓"]},
          {"field": "storeId", "op": "is_null"}]}
]}
```

- **条件写法**：每个条件是 `{field, op, value | valueFrom}`。
  - op 可选：`= != in not_in > >= < <= like is_null not_null`。前端写的 `∈` 等同于 `in`。
  - `valueFrom` 只能写成 `user.<attr>`，取值来自 `CurrentUser.attrs`。
  - 当前用户缺少该属性时，条件结果为 false，也就是什么都看不到。
- **组合**：用 `{and: [...]}` 或 `{or: [...]}` 组合条件，最多嵌套 5 层。值为空或 null 表示不限制，与前端“空=全部”一致。
- **转 SQL**：`to_sql(expr, columns_whitelist, user.attrs)` 返回 SQLAlchemy 条件表达式。
  - 字段必须在白名单里，否则返回 `BAD_ROW_FILTER`。
  - 值一律走绑定参数，不会被拼接进 SQL。
- **前端 mock 转换**：`from_row_scope({prop, op, values})` 把前端 RowScope 转成这个结构。
- **多角色合并**：用 `combine_or`，结果取并集；任一角色不限制，整体就不限制。
- **注入方式（biz 等业务数据必须这样写）**：
  `stmt = await apply_row_filter(select(Store), user, "store", {"region": Store.region, ...})`。
  - 行级条件由基础层注入，业务模块不要自己拼过滤条件。
  - 白名单 columns 由模块声明：字段名对应对象属性的 api 名。
  - `row_filter()` 目前是扩展点，由 PDE 实现：读取角色范围、处理 defaultDeny。

## 11. 团队决定（PDE，一期）

| 主题 | 约定 | 落点 |
|---|---|---|
| 租户 | 一期单租户：接受 `X-Tenant-Id`，放进 `CurrentUser.tenant_id`，只透传，不过滤；**表里不加 tenant_id 列** | `app/common/auth.py` |
| 开发身份 | 演示身份请求头 `X-Demo-As`（默认 `RENGU_DEV_USER`），**只在 dev/test 环境生效**；prod 忽略该头 | `app/common/auth.py` |
| 预览 | **每个用户同一时间只有一个预览会话**：开始新的预览会替换旧的，不报错；`?previewAs=` 下只读 | `app/contracts/preview.py`（Protocol + dev 内存实现） |
| 测试连接 | 数据连接和外部系统的“测试连接”接口在 dev/test 环境用 mock 适配器（`mockFail: true` 模拟失败）；prod 由负责人接入真实实现 | `app/modules/data/adapters.py` |
| 外部调用 | 表名沿用 `writeback_call`、`ext_system`，归 integration 模块（Palantir专家）；不建 `ext_call` | `app/modules/integration/` |
| 函数版本 | 函数（Functions）单独存 `fn_version` 表，各函数分别递增版本号；**不走** `create_version` | `app/modules/dynamic/`、contracts/versioning 规则 6 |
| 业务数据 | 演示门店、商品、订单归 biz 模块；所有查询由基础层注入行级过滤（§10） | `app/modules/biz/` |

### 后台任务（procrastinate 桩，`app/jobs/__init__.py`）

| 任务名 | 队列 | 周期 | 负责人 | 说明 |
|---|---|---|---|---|
| `event.deliver` | events | 按需 defer | event | 事件投递 |
| `event.redeliver_failed` | events | 每 5 分钟 | event | 失败事件重投（退避） |
| `outbox.dispatch` | outbox | 每分钟 | PDE | outbox 分发 |
| `release.expand` | release | 发布提交后 defer | static | 发布后把快照展开到已发布定义表、刷新缓存 |
| `action.reconcile` | reconcile | 按需 defer | integration | 处理结果未知的提交：标为「仍无法确认」后**再额外对账 3 轮**（`EXTRA_RECONCILE_ROUNDS`），之后转人工 |
| `export.run` | export | 按需 defer | 各模块 | 导出 |
| `admin.expire_api_keys` | maintenance | 每小时 | admin | API Key 到期后置为过期 |
| `admin.cleanup_audit` | maintenance | 每天 03:30 | admin | 按保留期清理审计日志（180 天，[推断]，待 PDE 定） |

- 任务必须幂等，因为可能被重投。
- 时间一律用 `clock`（§5）。
- 骨架还没引入 procrastinate 依赖，接入时由 PDE 在 `app/jobs/worker.py` 里按 `JOBS` 注册。

## 12. 新增模块 / 迁移

- **新增模块**：
  1. 复制一个占位模块，例如 `app/modules/static/`。它有 `router / schemas / models / service / repo / seeds` 这几个文件。
  2. 在 `app/api.py` 的 `MOUNTS` 里挂到对应分组。
  3. 在 `app/db/models.py` 里 import 模型，alembic 才能发现。
  4. 在 `app/seeds/registry.py` 里注册 seed 顺序：被外键依赖的模块排在前面。
- **迁移**：
  - 每个 PR 最多一个迁移，文件名格式 `YYYYMMDD_NNNN_<模块>_<说明>.py`。
  - 必须能 downgrade。
  - 提交前运行 `make lint`，其中包含 `alembic check`，保证模型和迁移一致。
  - 已合并的迁移不再修改，有问题就写新迁移修正。
  - 触发器、扩展这类 DDL 用 `op.execute`，每次只执行一条语句（asyncpg 不支持一次执行多条）。

## 13. 协作：worktree、评审、预览

- **分支和推送**：
  - 每人在自己的 git worktree 和分支上开发，不在别人的工作目录里改文件。
  - **只有 PDE工程师向 rengu_ontology 推送和合并**。
- **评审**：每个 PR 需要**两名非作者**评审通过。
  - 改动基础层（core/db/common/contracts）时，必须有本体小白评审。
  - 改动扩展点时，必须有 PDE 评审。
- **提交前**：必须跑 `make check`（ruff、alembic check、pytest）。
- **`?ver=` / `?previewAs=`**：
  - 所有读接口都要支持这两个参数，用 `ViewContextDep` 解析。
  - 写接口要挂 `WritableDep`。
  - `previewAs` 表示以另一个用户的身份预览，由 PDE 的鉴权扩展点实现。每人同一时间只有一个预览会话（§11）。

## 14. [推断] 清单

- `EMPTY_RELEASE`：没有变更单时不允许发布。
- `ont_release.kind`（发布/回滚）、`rollbackOf` 字段：前端只展示文案，这两个字段是后台加的。
- 行级过滤中，缺少用户属性时结果为 false（宁严勿宽）。
- biz 挂在 `/api/biz/v1`（api-index v0.2 的四个分组里没有 biz）。
- 审计日志保留 180 天；cron 周期。

## 15. 不确定、待 PDE 定的事项

- 时间是否全部用 `YYYY-MM-DD HH:mm` 格式。
- 由 PDE 定（见 README 和交付报告）：
  - CR 号序列放在哪个模块；
  - 快照 JSON 的结构；
  - export-mock 输出的 JSON 顶层结构。
