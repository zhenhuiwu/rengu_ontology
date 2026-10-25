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
| `/api/data/v1` | data（数据连接）、integration 的 `/ext-systems*`、`/writeback-calls*`（Palantir专家；integration **只**挂这里，不在 `/api/ontology/v1` 下开路由，action-event-api.md:140、:168） |
| `/api/admin/v1` | admin（用户 sys_user、角色、API Key、配置、审计） |
| `/api/biz/v1` | biz（演示业务数据：门店、商品、订单等，首页看板和问数对象搜索用；PDE 定） |

- 挂载表在 `app/api.py` 的 `MOUNTS`，前缀由 `settings.api_prefix_template = "/api/{module}/v1"` 生成。代码按模块分目录，路由按分组挂载。
- 资源名用复数、kebab-case：`/releases`、`/change-requests/{id}`。
- 动作用冒号：`POST /change-requests/{id}:submit`、`POST /alerts:batch-close`（PDE 定，和前端 / 各模块文档一致）。
- 健康检查：`/healthz`（进程活着）、`/readyz`（数据库可用）。不加前缀。

## 2. JSON 和错误

- **字段名和枚举值与前端 mock 完全一致**：camelCase；前端用中文状态值的，接口就返回中文。
- 数据库内部用 snake_case 加英文代码，在 Pydantic 层转换。
- 所有接口模型继承 `app.core.schema.ApiModel`，它带 `alias_generator=to_camel`、`populate_by_name`、`from_attributes`。
- 成功响应直接返回资源本身，不包 `{data: ...}`。
- 失败响应统一为 `{code, message, details, requestId}`，HTTP 状态码表达错误类别。未处理异常（含提交失败）也是这个结构的 500，并带 `X-Request-ID` 响应头。

| code | HTTP | 场景 |
|---|---|---|
| `VALIDATION_FAILED` | 422 | 参数校验失败，`details.fields = [{field, message}]` |
| `UNAUTHORIZED` / `NO_PERMISSION` | 401 / 403 | 未登录 / 无权限 |
| `NOT_FOUND` | 404 | 资源不存在 |
| `DUPLICATE_KEY` | 409 | 违反唯一约束（BaseRepository 自动转换；模块做「已存在」校验也用它，`DuplicateKey`） |
| `EMPTY_INPUT` | 409 | 必须至少一项的输入为空（`EmptyInput`） |
| `STATE_INVALID` | 409 | 当前状态不允许该操作（`ensure_transition`） |
| `VERSION_CONFLICT` | 409 | 乐观锁 `lockVersion` 不一致 |
| `IDEMPOTENCY_CONFLICT` | 409 | 同一幂等键用于内容不同的请求（`IdempotencyConflict`；内容相同的重放返回原结果） |
| `SUBMISSION_CRITERIA_FAILED` | 422 | 动作提交条件不满足，`details.failed = [{name, fail}]`（`SubmissionCriteriaFailed`）。**不设 `ACTION_BLOCKED`**，阻断一律用它 |
| `EXPORT_FORBIDDEN` | 403 | 没有导出权限（`ExportForbidden`） |
| `SQL_REJECTED` | 403 | 问数网关拒绝执行生成的 SQL（非单条只读 SELECT、查了未登记的表），`app/common/sql_guard.py` |
| `READONLY` | 403 | 在 `?ver=` / `?previewAs=` 下写入；`details.mode = "history" \| "preview"`，两个参数都带时按 preview |
| `ROLLBACK_NOT_LATEST` / `EMPTY_RELEASE` | 409 | 只能回滚最近一次发布 / 发布没有变更单 |
| `INTERNAL_ERROR` | 500 | 未处理异常（不泄露堆栈） |

- 业务错误要抛 `app.core.errors` 里的 AppError 子类，不要直接抛 HTTPException。
- 请求 ID 规则：
  - 入参 `X-Request-ID` 会原样透传，没有时由后台生成。
  - 后台在响应头里回写这个 ID，并写进日志。

## 3. 分页

- **页面上有 ‹ 1/N › 或页码的列表**用 `page`/`size`：
  - 参数：`?page=1&size=20`，size 不超过 200。
  - 返回 `{items, total, page, size}`。
  - **页码越界按前端收拢**（`src/mock/paginate.ts:13`：`page = min(max(1, page), max(1, ceil(total/size)))`）：超过最后一页返回**最后一页**的数据，响应 `page` 是收拢后的页码；`page` 为 0 或负数按第 1 页处理（`PageParams` 先收到至少 1 再 `clamp(total)`，响应 `page = 1`），不报 422；`size` 照旧校验（1–200，越界 422）；`total = 0` → `page = 1`、`items = []`。不返回空页、不报错。
  - 实现：`PageParamsDep` + `rows, total = await repo.list_page(stmt, page)`（先 count 再按收拢后的 offset 取一页，仍是两条语句）+ `Page.of(rows, total, page, **额外字段)`。不要再写 `page=page.page`。自己拼 SQL / 内存分页的用 `page.clamp(total).offset` 和 `.size`。
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
  - 不合法的流转返回 409 `STATE_INVALID`。
  - 允许哪些流转**照抄前端按钮的出现条件**，前端能点的后台都要放行。
- 时间格式（PDE 定：**按每个字段的 mock 格式**）：
  - 北京时间、不带时区。默认 `YYYY-MM-DD HH:mm` 用 `DateTimeMin`；mock 带秒的字段用 `DateTimeSec`（`YYYY-MM-DD HH:mm:ss`）。
  - 各模块文档里列「接口字段 → 格式」表；入参两种长度和 ISO 8601 都接受。
  - 数据库一律存 `timestamptz`。
  - 只有首行带日期的时间线（如 RUNS 时间线），seed 用 `fill_timeline_dates()` 补日期，时刻倒退时日期 +1。

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

- **表名不加模块前缀**：单数 snake_case 业务名词（`object_type`、`action_run`、`sys_user`），只有撞 SQL 关键字或有歧义时加限定词（`ontology_release`、`biz_order`）。integration 的 `writeback_call`、`ext_system` 沿用原名，**不要新建 `ext_call`**。
- **主键一律代理键 `id bigint`（identity，`IdMixin`）**（PDE 定）：
  - 前端可见的业务编号（`CR-0016`、`ACT-20261025-3834`、`R-007`）放 `code` 列 + UNIQUE（`CodeMixin`）；已有自然列名的（`ontology_release.ver`、对象类型 `api_name`）自己声明列 + `UniqueConstraint`。
  - **外键一律指向对方的 `id`**（含自引用，如 `ontology_release.parent_id / rollback_of_id`）。
  - **接口只暴露业务编号**：输出模型 `id: CodeId`（从 ORM 的 `code` 读），引用字段（`ruleId`、`parentVer`）连表换回对方编号；路由参数用编号，`BaseRepository.get_by_code_or_404`。
  - mock 的复合键改成业务唯一约束。
  - seed：transform 输出编号，`SeedSpec.refs={"外键列": Ref(目标模型, "目标业务键列")}` 由 loader 换成 id（找不到报错；自引用按层写入）。
- **约束命名**由 `NAMING_CONVENTION` 自动生成（`pk_`、`fk_`、`uq_`、`ix_`、`ck_`），不要手写约束名。
- **时间**一律用 `timestamptz`，不用 `timestamp`。日期用 `date`。
- **JSONB** 用 `JsonType`。只用于结构由前端整体读写、不参与过滤的字段，例如快照、配置、`calls[]`。要过滤的字段必须拆成列。
- **mixin**：

| mixin | 提供的列 |
|---|---|
| `IdMixin` / `CodeMixin` | `id bigint` 代理主键 / 另加 `code varchar(64) UNIQUE` |
| `MutableTableMixin` | 可变表 5 列：`created_at, updated_at, created_by, is_seed, lock_version`（接口 `lockVersion`，乐观锁） |
| `AppendOnlyTableMixin` | 只追加表：`created_at, created_by, is_seed`（无 lock_version、无软删除） |
| `SoftDeleteMixin` | `deleted_at/by`，**只给前端有删除/恢复的表** |
| `OntologyVersionMixin` | `release_id` → `ontology_release.id`（外键，不存版本号字符串；接口仍返回 `ver`，经关系取） |

- **只追加表**（版本、日志、事件、命中）在迁移里调 `app.db.ddl.enable_append_only(table)`：触发器拒绝 DELETE、TRUNCATE 和修改内容的 UPDATE（内容不变的 UPDATE 放行，seed 重跑幂等）。触发器是 `BEFORE UPDATE OR DELETE … FOR EACH ROW` 加 `BEFORE TRUNCATE … FOR EACH STATEMENT`。
- **指向 `ontology_release(id)` 的外键允许且推荐**（版本号类字段一律存外键，不存 `ver` 字符串）：`OntologyVersionMixin.release_id`、Palantir `action_type/event_type.retired_release_id`、qa 的 `release_id` 等。`ON DELETE` 用默认的 NO ACTION（或 RESTRICT），**不要 CASCADE / SET NULL**：版本行永不删除（只追加触发器保证），外键只用来保证引用存在。
  - 测试注意：有外键指向它之后，`TRUNCATE ontology_release` 不带 CASCADE 会先被外键检查拒绝（还没到触发器）；要验证只追加就用 `TRUNCATE ontology_release CASCADE`（放在 savepoint 里），Postgres 先触发所有表的 BEFORE TRUNCATE 语句级触发器，被只追加触发器拦下、数据不动（`tests/test_versioning.py::test_append_only_holds_with_fk_referencing_releases`）。测试和 fixture 不要靠 TRUNCATE / DELETE 清理版本行，靠每个测试的外层事务回滚。
- 批量写入用 `get_adapter(session).upsert(...)`：按 32767 个绑定参数自动分批，批内按冲突键去重（后出现的覆盖）。
- 跨数据库差异走 `app/db/dialects`：`get_adapter(session).upsert(...)` 和 `.xact_lock(key)`。业务代码不要直接写 `ON CONFLICT`。

## 7. 本体版本（整版本、只追加）

对外接口、前端对应关系、谁负责发布/回滚接口：见仓库根目录 `docs/backend/versioning-api.md`。测试注意：`tests/conftest.py` 在模块级 `import app.main`，让 `load_sections()` 在 autouse 隔离之前跑完，避免单独跑某个文件时报「section 已经登记过」。

接口定义在 `app/contracts/versioning.py`，参考实现是 `app/modules/versioning/service.py`。

- **唯一入口** `create_version(session, change_order_ids, *, rollback_of=None, published_by, note)`：
  - 和调用方的业务写入在**同一事务**里执行；先加全局锁 `ontology.publish`，再追加一行 `ontology_release`。
  - `published_by` 传 **sys_user.code**（如 `U010001`，取自 `CurrentUser.id`）；存 `published_by_id` 外键 → `sys_user.id`。编号不存在 → 422 `USER_NOT_FOUND`。
  - 接口 `Release` 两个人员字段：`by` = 发布人**姓名**（前端 `Release.by`，VersionPicker.vue:26、OntologyOverview.vue:243 直接显示）；`byId` = 发布人 **sys_user.code**（同一外键，团队约定“人员外键在接口上传/返回 sys_user.code”，api-index.md:134、static-api.md:523）。`byId` 是新增字段，前端不读；`ReleaseInfo.published_by_code` 同值。
  - **outbox**：同一事务里写一条 `outbox_event`（topic `release.published`，`event_id = ontology_release:<ver>:release.published`），发布和回滚都写；payload `{ver, parentVer, kind: "release"|"rollback", rollbackOf, changeOrderIds}`。发布回滚，事件一起回滚。
  - 版本号格式 `vYYYY.MM.N`，月份按北京时间算；`parent_id` 指向上一版的 id。
  - 快照 = 各 section 由负责模块组装（`SECTION_OWNERS`：静态 objectTypes/properties/links/metrics/dimensions/terms/qaPaths，动态 rules/lifecycles，Palantir actionTypes/eventTypes），段内按前端 id 作键。模块实现 `SnapshotSectionBuilder`（`build_section` + `seed_section`），按 §7「section 唯一登记点」登记；未登记的段沿用上一版。seed 不写空快照：还没有任何模块登记 section 时，versioning seed 经 `SeedSpec.skip_if` **跳过并记一行日志**（不报错，`ontology_release` 为空），全量 `python -m app.cli seed` 在新库上能跑完（`tests/test_seed_fresh_db.py`）；直接调 `seed_snapshot` 仍拒绝空快照。
- **回滚 = 同一个入口传 `rollback_of=<当前最新版本号>`**：
  - 只能回滚最近一次发布，否则 409 `ROLLBACK_NOT_LATEST`。
  - **不调用** section builder，快照原样复制被回滚版本的上一版（`changeSummary.restoredFrom`）；`kind=rollback`，`rollback_of_id` 指向被回滚的版本，接口返回 `rollbackOf` 版本号。
  - 回滚变更单号作为 `change_order_ids` 传入，供审计追溯。
  - **定义表也要回去**：版本层**先写入这次回滚的新版本行**，再在同一事务里按登记顺序对每个 builder 调可选钩子（`app/contracts/versioning.py` 的 `RestorableSection`）：

    ```python
    async def restore_section(self, session, content, *, release_id: int) -> None: ...
    ```

    - `content` = 被恢复版本快照里这一节（`snapshot.get(section)`），原样 JSON；
    - `release_id`（keyword-only）= 这次回滚新写入的 `ontology_release.id`（不是版本号；行已 flush，可 `session.get` 读到）。例如 Palantir 把快照里没有的类型 `retired_release_id = release_id`（action-event-api.md:325）。
    - 由模块把自己的定义表（如动态 `rule` 当前版本、`lifecycle.version`、静态对象/指标）恢复成那一版；不实现钩子的段只回快照。**钩子抛错 → 调用方不能 commit，整个事务回滚**（含新版本行、outbox、业务写入）：`restore_sections` 把所有钩子包在一个 SAVEPOINT（`session.begin_nested()`）里，任一钩子抛错时已执行的钩子写入先撤掉、异常原样上抛；新版本行在 SAVEPOINT 之外，只有调用方事务回滚才会消失。接口里走 `get_session` 的会在异常时自动 rollback；自己管事务的（脚本、后台任务）要 `except` 后 `rollback()`，不要吞掉异常再 commit。
    - 被恢复版本没有这一节（content 为 None）时**不调用**钩子、定义表不动（静态裁定，和动态一致）【推断】；空内容 `{}` 照样调用。
    - 旧的两参数写法 `restore_section(session, content)` 已废弃（此前没有模块实现）；照旧写会 TypeError、回滚失败。dynamic-api.md「写新版本之前调 restore_sections」的描述以此为准更新。
- **section 唯一登记点**（骨架负责，模块不用改 main / cli）：
  - 负责 section 的模块在 `app/modules/<模块>/sections.py` 导出模块级 `BUILDERS = [XxxSection(), ...]`（按此顺序登记）；或者导出一个**幂等**的 `register()`（已登记同类实例就直接返回）。模块包的 `__init__` 里不要再登记。
  - **禁止 import 时登记**：只有 `BUILDERS` 或 `register()` 算登记；不要在模块包 `__init__` 或 `sections.py` 顶层调 `register_section` / `register_all()`。
  - **「import 时登记」已废弃**：`sections.py` 既不导出 `BUILDERS` 也不导出 `register()`（只靠 import 副作用调 `register_section`）时，`load_sections()` 报 `ImportOnlySectionsError`，`create_app()` / seed 起不来。原因：import 只发生一次，`isolated_sections` 恢复后不会再登记，测试和 seed 子进程行为不一致。
  - `app.modules.versioning.sections.load_sections()` 按子包名排序扫描 `app.modules.*.sections`（pkgutil）并登记；`create_app()` 和 `python -m app.cli seed` 开头都会调。返回前把整个注册表**按包名排序**（同一模块内按 `BUILDERS` 顺序），所以不管之前谁先 import 过模块（`create_app` 里 `include_routers` 在 `load_sections` 之前），app 和 seed CLI 的登记顺序（= build / restore 调用顺序）一致。幂等：同一实例重复登记放行，同一 section 换了另一个实例照旧报 `ValueError`。
  - 直接调 `run_seeds(...)`（测试里）不会自动发现，测试自己登记要用的 builder。
  - 测试替身的 section 名一律用 `_test_` 前缀（如 `tests/fixtures/section_mods` 的 `_test_alpha` / `_test_beta`），**不要借用真实 section 名**（terms、eventTypes…）：和 `app.modules` 一起扫时会和真实模块冲突（「已经登记过」）。
  - **迁移**：静态现在在 `static/__init__.py` 里 `register_all()`、`sections.py` 已有 `BUILDERS` → 删掉 `__init__` 里的调用即可（留着也不冲突，同一实例）。Palantir `action/sections.py`、`event/sections.py` → 必须删掉模块底部的 `register()` 调用，只定义 `BUILDERS` 或 `register()`，由 `load_sections()` 调用。
  - **验收**：全新空库 `alembic upgrade head` + `python -m app.cli seed`（默认参数）后，每个 `ontology_release.snapshot` 都含你的 section（`tests/test_seed_fresh_db.py::test_full_seed_on_fresh_db` 会对本分支实际发现的 section 断言这一点）。
- **只追加，不改不删**：见 §6 的只追加触发器。
- `GET /releases` 不分页、登录即可调：`{items, current}`（前端版本选择器要全量）。
- `?ver=vX`：只读读取历史快照，`ViewContextDep` 负责解析。只读由**全局守卫**负责（§13），模块不用自己挂依赖。
- `[推断]` 规则：
  - 没有变更单时发布返回 409 `EMPTY_RELEASE`。
  - `kind` 字段（发布/回滚）。
- **最新版本 = id 最大**（`ReleaseRepo.latest()` / 列表按 `id desc`），不按 published_at。

### outbox（`app/common/outbox.py`，表 `outbox_event`）

- 只有基础层定义表和 API：`emit(session, topic, payload, *, event_id, available_at=None)` 在调用方事务里加一行；`event_id` 全局唯一，用来做下游幂等。
- 模块**只订阅**：`subscribe(topic, handler)`；分发任务 `outbox.dispatch`（PDE）扫 `dispatched_at IS NULL`（部分索引 `ix_outbox_event_pending`），成功写 `dispatched_at`，失败 `attempts+1`、记 `last_error`。
- 已定主题：`release.published`（versioning，见上）。新增主题先在 `outbox.py` 文档里登记 payload。

## 8. 动作提交和幂等键

接口定义在 `app/contracts/actions.py`。

- `submit(action_run_id, idempotency_key) -> SubmitResult`：`SUCCESS`（成功）/ `FAILED`（失败）/ `UNKNOWN`（结果未知），外加 `receiptId`、`code`、`retries`、`elapsedMs`、`calls[]`。
  - 外部明确失败：适配器用**同一个键**自动重试，最多 `ext_system.maxRetry` 次；超时 = 结果未知，写操作不重试。
  - SUCCESS → dynamic 同事务提交本体变更并发 `ActionExecuted`；UNKNOWN → dynamic 生成 eventId，经 integration `EventEmitter.emit` 发 `ActionOutcomeUnknown`，integration 建 `reconcile_task`。
- 对账：`query_by_key` 只返回 查到·成功 / 查到·未执行 / 查无（「查无」也算一次）；cron `*/15` 每轮最多 3 次，仍查无 → 转人工。`ext_system.idempotent=false` 的系统从不调 `query_by_key`，任务建好即转人工。
  - 结论只有 `succeeded`（外部已成功）/ `not_executed`（外部未执行），经 `on_reconciled` 回调；not_executed 不能重新提交。
  - 人工选「仍无法确认」→ `ReconcileScheduler.reopen_reconcile(action_run_id)`：`round+1`、`tries=0`、`next_at=now`（idempotent=false 仍停在人工）。
  - seed：结果未知 74 条（ACTIONS 20 + RUNS 54）都是 `tries=3`、人工、`next_at=null`。
- **幂等键在暂存时生成**：`idempotency_key_for("ACT-20261025-3834") == "act-20261025-3834"`，之后不再改变。
- **用户重新提交（只从「失败」）**：新建一条 run + 新键 `new_resubmit_key(old)`（`<原键去掉 -rN>-r<N+1>`），旧 run 不改。
- 表结构写法：`idem_key varchar(64) not null`，加部分唯一索引 `unique_except_seed(...)`（见 §9）。

## 9. Seed：mock 数据入库

### 目录

```
backend/seeds/json/<组>/*.json          ← PDE 的 npm run export-mock 生成，不要手改；组 = common/static/dynamic/action/qa/system
backend/seeds/json/<组>/derived/*.json  ← 派生/函数类 mock：[{args, result}]，loader 自动展开（derived_args 取参数）
backend/seeds/extra/<组>/*.json         ← 后端补充：mock 里没有，只为满足外键等（手写）
backend/app/modules/<module>/seeds/__init__.py  ← 本模块的 loader（SeedSpec 列表）
```

### 规则

1. **与 mock 一一对应**：接口读出来的结果必须和前端 mock 完全相同（`tests/test_versioning.py::test_list_matches_mock` 是示例）。
   - 时间保持北京时间原值，由 `parse_display_dt` 解析。
2. **先 json/ 再 extra/**，所有写入在同一事务内完成。
   - 如果某个外键要靠 extra 里的行才能满足，这个外键要声明 `DEFERRABLE INITIALLY DEFERRED`。
3. **按业务键 UPSERT**（`SeedSpec.key`，例如 `("ver",)`、`("code",)`，不是代理键 id），重跑幂等；外键编号用 `SeedSpec.refs` 换成 id。
   - 模块用 `JSON_MODULE` 指定默认组（admin/data/integration→system，event→action，biz→common，versioning→static）；读别的组用 `SeedSpec.group`。`derived/dsTables.json` 只认 system 组。`make seed` 跑两次结果相同，这一条有测试覆盖。
4. **`is_seed`**：表带 `is_seed` 列（`MutableTableMixin` / `AppendOnlyTableMixin` 都含）时，loader 会自动把 seed 写入的行标成 `is_seed=true`。
5. **mock 本身违反唯一约束时**（例如 31 个重复的幂等键），把唯一约束改成部分唯一索引：

```python
class ActionRun(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "action_run"
    __table_args__ = (unique_except_seed("action_run", "idem_key"),)
# → CREATE UNIQUE INDEX uq_action_run_idem_key_nonseed ON action_run (idem_key) WHERE NOT is_seed
```

   - seed 行之间可以重复，业务新写入的行仍然受唯一约束（见 `tests/test_conventions.py::test_seed_marks_and_partial_unique`）。
   - 只有在 mock 真的有重复时才这样做，不要预防性地放宽。

6. **改 seed 快照要重建库**：只追加表（`ontology_release` 等）的触发器拒绝修改内容的 UPDATE，所以改了导出 JSON 或 `seed_section` 导致快照变化时，`make seed` 会报错而不是覆盖；要 `make db-reset`（或删库后 `alembic upgrade head` + `make seed`）重建。

### 加载顺序（`app/seeds/registry.py` 的 `SEED_MODULES`）

列表里是**阶段名**，按顺序在同一事务里加载；被外键依赖的排在前面。

| # | 阶段 | 负责模块 | 写哪些表 / 为什么在这里 |
|---|---|---|---|
| 1 | `admin` | admin（PDE） | sys_user 等。其它模块引用人分两种：**外键**（库里存 `sys_user.id`，接口 / seed 按用户编号 code 换）——版本发布人 publishedBy、变更单 owner / reviewer、函数 owner / author；**姓名软引用**（存姓名字符串，不建外键）——告警 assignee、处理人 handler、定义上的 owner、审计 / 事件 actor。目前的 `sys_user` 是基础层放的**最小占位**（code/name/account/dept/status/last_login_at），PDE 补全；seed 里只有人名时用 `admin.seeds.code_by_name` 换成编号。演示人物（`extra/system/SYS_USERS.json`）：王敏 U010001（管理员）、陈立军 U010002、何静 U010003、刘婷 U010004（编号照抄 mock 审计日志）；孙浩然 U010005、李娜 U010006（PDE 已确认，仍标 [推断]）；赵蕾 U010007、吴佳 U010008（[推断]，非管理员）；苏刚 U010231（非管理员，前端 `?previewAs=` 默认预览对象 `preview.ts:14`）已在 ADMIN_USERS.json，不在 SYS_USERS 重复。**seed 的 sys_user.name 必须唯一**（动态按姓名比较「本人」）：`admin.seeds.CHECKS` 在写库前校验 ADMIN_USERS + SYS_USERS，重名整批报错（loader 支持模块包里的可选 `CHECKS`）。`sys_user.alert_assignable smallint NULL`：告警可指派的人在动态 PEOPLE 列表里的顺序 1..7，null = 不可指派；**admin seed 不写**，由动态的 seed 按姓名回填 |
| 2 | `data` | data | 数据源 |
| 3 | `integration` | integration（Palantir） | **只写 ext_system**（动作、数据源引用外部系统） |
| 4 | `biz` | biz | store / product / biz_order / order_line（不建 member、store_sales）。订单号在 mock 里有重复（2 组），`biz_order.code` 只对非 seed 行唯一，seed 键 = (code, created_at_biz)；详情按 id 取第一行 |
| 5 | `versioning` | versioning | ontology_release（只读导出 JSON 组装快照，不读表）；静态的 release_id 外键要先有版本行 |
| 6 | `static` | static | 本体定义、变更单 |
| 7 | `action` | action | 动作类型、执行记录 |
| 8 | `event` | event | 事件类型 |
| 9 | `dynamic` | dynamic | 规则、告警、生命周期 |
| 10 | `integration_runs` | integration（Palantir） | writeback_call、reconcile_task：外键指向 dynamic / action 的执行记录，所以排在 dynamic 之后 |
| 11 | `qa` | qa | 问数会话 |

- 阶段名就是模块名时，读 `app.modules.<模块>.seeds`；不是模块名的阶段（目前只有 `integration_runs`）在 `SEED_STAGES` 登记「负责模块 + seeds 包」（`app.modules.integration.seeds.runs`），默认 export 组跟负责模块一致。
- `uv run python -m app.cli seed -m integration_runs` 可以单跑一个阶段。顺序由 `tests/test_conventions.py::test_seed_order` 固定，改顺序要同时改这张表和测试。

### 模块负责人迁移 mock 的步骤

1. 让 PDE 在 export-mock 配置里加上你的文件，执行 `npm run export-mock`，输出到 `seeds/json/<组>/`。
2. 在 `models.py` 建表，执行 `make revision m="static: add object_type"` 生成迁移，再人工检查迁移内容。
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
  - **前端 RowScope 只有正向 op**（`=`、`∈`，`roles.ts` L12/L49；`in` 视同 `∈`）：`from_row_scope` 只认这三个，其它 op（`≠`、`not in`、拼错的…）→ 这条范围 `DENY_ALL`（fail closed）。下面「全 OR 合并」**只在正向 op 下成立**（负向条件 OR 起来会放大可见范围），以后要支持负向 op 得重新定合并规则。
  - `valueFrom` 只能写成 `user.<attr>`，取值来自 `CurrentUser.attrs`。
  - 当前用户缺少该属性时，条件结果为 false，也就是什么都看不到。
- **组合**：用 `{and: [...]}` 或 `{or: [...]}` 组合条件，最多嵌套 5 层。值为空或 null 表示不限制，与前端“空=全部”一致。
- **转 SQL**：`to_sql(expr, columns_whitelist, user.attrs, links=None)` 返回 SQLAlchemy 条件表达式。
  - 字段必须在白名单里，否则返回 `BAD_ROW_FILTER`。
  - **一跳关系**：字段写 `<关系 id>.<属性>`，`links` 由 `link_paths_from_definitions(对象类型, 静态 LINKS 定义, 对象表)` 生成，编译成 `EXISTS`；未声明的关系、多跳一律拒绝。
  - 值一律走绑定参数，不会被拼接进 SQL；`like` 只接受字符串，非 `in/not_in` 不接受数组；多值用户属性配 `=` 自动变 IN，配别的操作符报错。
- **前端 mock 转换**：`from_row_scope({prop, op, values})` 把前端 RowScope 转成这个结构。
- **多角色合并**（static-api.md §1.5 @ 团队 master 443c009，照 `roles.ts:110-117` effectiveAccess）：`sql_guard.effective_expr(object_type, [RoleAccess(view, default_deny, scopes)…])`，**所有对象（含商品）同一套**：
  1. 只算 granted 角色：对该对象有查看权，且不是「defaultDeny 又没配该对象范围」（roles.ts:110）。一个都没有 → `DENY_ALL`（`WHERE false`）。所以 defaultDeny 角色（R-05/R-06/R-08）能看但没配范围的对象 = 看不到，单独持有时是 DENY，不是不限制；
  2. granted 里任一角色没配该对象范围 → 不限制（None）；
  3. 否则把 granted 角色的**全部范围摊平取 OR**（角色间 OR；同一角色内多条也 OR **[推断]**，static-api.md §1.5 @ 团队 master 443c009，roles.ts:114）。「门店」和「大区」不会 AND。
  - **fail closed**：没映射的属性、非正向 op、一期没建表的对象（会员、库存快照）上的范围 → 那条 `DENY_ALL`，在 OR 里不贡献行，绝不被当成「不限制」或被忽略（static-api.md §1.5 @ 团队 master 443c009：会员范围按拒绝）。`combine_or` 丢掉 DENY 成员，全是 DENY → `DENY_ALL`。
  - 桩 `auth.row_filter`：本体管理员不限制，其余一律 `DENY_ALL`；PDE 实现时组 `RoleAccess` 调 `effective_expr`。
- **注入方式（biz 等业务数据必须这样写）**：
  `stmt = await apply_row_filter(select(Store), user, "store", {"region": Store.region, ...})`。
  - 行级条件由基础层注入，业务模块不要自己拼过滤条件。
  - 白名单 columns 由模块声明：字段名对应对象属性的 api 名。
  - `row_filter()` 目前是扩展点，由 PDE 实现：读取角色范围、处理 defaultDeny。
- **问数 NL2SQL 网关（api-index §1.4 硬规则，已定）**：模型生成的 SQL 不能走上面的 SQLAlchemy 注入，改用 `app/common/sql_guard.py`：
  - `assert_single_select(sql)`：sqlglot 解析，只放行单条只读 SELECT（拒多语句、DDL/DML、CTE 里的写、`SELECT INTO`、`FOR UPDATE`、用户 SQL 里的绑定占位符），不合规返回 403 `SQL_REJECTED`。
    - **函数白名单**（聚合、窗口、日期、字符串、数学、条件、类型转换），其它一律拒绝（`query_to_xml`、`pg_stat_file`、`current_setting`、`set_config`、`pg_advisory_lock`、`pg_sleep`、`dblink`、`lo_*` 等）。
    - **只允许 public schema**（`pg_catalog.*`、`information_schema.*`、跨库三段名都拒绝）。
    - **带 schema 的函数调用一律拒绝**（`pg_catalog.lower(x)`、`public.age(x)`，白名单函数也不行）。
    - **类型转换白名单**（`::`、`CAST`、`TRY_CAST` 同一套）：只允许数值（smallint/int/bigint/numeric/decimal/real/double）、文本（text/varchar/char）、日期时间（date/time/timetz/timestamp/timestamptz/interval，含字段限定 `interval day`、`interval day to second`）、布尔；`regclass`/`oid`/`regproc`/`regtype` 等对象标识类型、带 schema 的类型、自定义类型、json/bytea、数组一律拒绝。
    - 拒绝 `TABLESAMPLE`。
  - `inject_row_filters(sql, {表: (谓词 sql, params) | None}, allowed) -> (sql, params)`：受控表替换成 `(SELECT * FROM public.t WHERE 谓词) AS t`。
    - 不过滤的受控表（谓词 None）和 `allowed` 里的表也改写成 `public.<表>`，不依赖 search_path。
    - 按 sqlglot **作用域**判断：只跳过确实解析到 CTE 的引用；`with store as (select * from store) …` 里内层 store 照样过滤。谓词里的表固定成 `public.<表>`，用户同名 CTE 劫持不了。
    - 谓词值走 **`$1..$n` 绑定参数**，多表合并后重新编号，执行用 `conn.exec_driver_sql(sql, tuple(params))`；`DENY = ("FALSE", [])`；未登记的表一律拒绝。
    - 受控表由模块 `register_governed(GovernedTable(...))` 登记（biz：store 的「大区」→ `big_region`；biz_order 经一跳 `order_belongs_to_store.big_region` 编成 EXISTS）。
    - **继承上级权限**：`GovernedTable(..., inherit=InheritVia(local, parent, parent_key))`。biz 的 `order_line` 经 `order_id → biz_order` 继承订单谓词：`EXISTS (SELECT 1 FROM public.biz_order AS _rf_biz_order WHERE _rf_biz_order.id = order_line.order_id AND <订单谓词>)`，订单谓词再一跳到 store（实际两跳）。行级表达式按上级的对象类型取（order_line → `order`）。注入用的别名统一 `_rf_` 前缀，只在注入的子查询内部可见，和用户别名不冲突。
    - **显式放行**：`register_unrestricted(...)`，`run_guarded` 自动并进 `allowed`；同一张表不能既受控又放行。没登记的表照样拒绝（一期没有放行的表）。
    - **前端 RowScope → 表达式**：`scope_expr(g, 一个角色在该对象上的 scopes)`，属性按 `field_of_prop` 换字段、取值按 `value_alias` 换名（不在表里的原样比），多条范围 OR；没登记的属性 / 非正向 op → 那条 `DENY_ALL`。多个角色用 `effective_expr`（见上）。
    - **订单.门店**（static-api.md §1.5，团队 master 443c009）：biz_order 的「门店」→ `order_belongs_to_store.name`，一跳 `EXISTS (… public.store … store.name IN ($n…))`，范围值原样是门店名、不换 code；和「大区」是 OR（见多角色合并）。mock `roles.ts:26` 的门店名（「上海徐汇店」等 6 个）在 seed `ALL_STORES` 里一个都对不上（seed 是「上海徐汇漕河泾店」这类全称），照原样匹配 0 行。**用户已定（2026-10-25）：不加门店简称别名表（只是测试数据），0 行是预期结果。**
    - **R-06 门店店长**（defaultDeny，只在订单上配了 `订单.门店=上海徐汇店`）：订单、订单行 0 行（店名对不上，用户已定 2026-10-25：不加别名表，0 行是预期）；商品、门店、会员能看但没配范围 → defaultDeny 下 DENY；库存快照一期没表，直接拒绝。
    - **商品.品类**（同上）：product 是普通受控表，和其它对象一样走 `effective_expr`：非 defaultDeny 角色有查看权且没配品类 → 不限制；defaultDeny 角色（R-05/R-06/R-08）没配商品范围 → 看不到。谓词 `SPLIT_PART(product.category, '/', 1) IN ($n…)`（和文档的 `= ANY($n)` 等价；`'/'`、`1` 是字面量，只有范围值是参数）；范围值先经 `app/contracts/static.py` 的 `CATEGORY_SCOPE_ALIAS` 换成一级类目（乳品烘焙→乳品、休闲零食→休闲食品、日化清洁→日化家清，生鲜/粮油调味/酒水饮料同名）**[推断]**。
    - **order_line 只继承订单范围，不按商品品类过滤**（已定：静态，`roles.ts:62` 行级范围按对象类型配，S-41 配在订单、S-42 配在商品）。连 product 查时 product 一侧照样按品类过滤。
    - **大区取值**：`store.big_region` 存「华东/华南/…」（行级范围的取值，`roles.ts:25/27`），谓词按 `big_region = 用户范围值` 比较；维度里的「华东大区」由 semantic-map 同义词映射成 `big_region='华东'`（静态裁定）。`row_filters_for(user)` = `auth.row_filter` + `compile_predicate`；`run_guarded(conn, sql, user)` = 校验 + 注入 + 执行。
  - 执行只用只读引擎 `get_qa_readonly_engine()`（`RENGU_QA_READONLY_DATABASE_URL`，连接参数设 `default_transaction_read_only=on`、`statement_timeout=RENGU_QA_STATEMENT_TIMEOUT_MS`，库账号也应只授权业务表）；没配置抛 `QaReadonlyNotConfigured`，不回退到主库账号。
  - 身份只取 `CurrentUserDep`（会话 / API 密钥绑定身份 / 校验后的 `?previewAs=`），不读请求体或头里的用户 id；dev/test 的 `X-Demo-As`（值 = sys_user.code）是会话桩，prod 忽略。

## 11. 团队决定（PDE，一期）

| 主题 | 约定 | 落点 |
|---|---|---|
| 租户 | 一期单租户：接受 `X-Tenant-Id`，放进 `CurrentUser.tenant_id`，只透传，不过滤；**表里不加 tenant_id 列** | `app/common/auth.py` |
| 开发身份 | 演示身份请求头 `X-Demo-As: <sys_user.code>`（默认 `RENGU_DEV_USER=U010001` 王敏，本体管理员；`U010006` 李娜为非管理员；演示人物编号见 §9 admin 行），按 `sys_user.code` 解析 CurrentUser，编号不存在或停用 → 401；请求头只能是 ASCII，所以传编号不传姓名。管理员名单 `RENGU_DEMO_ADMIN_CODES`（桩）。**只在 dev/test 环境生效**；prod 忽略该头 | `app/common/auth.py` |
| 预览 | **每个用户同一时间只有一个预览会话**：开始新的预览会替换旧的，不报错；`?previewAs=` 下只读 | `app/contracts/preview.py`（Protocol + dev 内存实现） |
| 测试连接 | 数据连接和外部系统的“测试连接”接口在 dev/test 环境用 mock 适配器（`mockFail: true` 模拟失败）；prod 由负责人接入真实实现 | `app/modules/data/adapters.py` |
| 外部调用 | 表名沿用 `writeback_call`、`ext_system`，归 integration 模块（Palantir专家）；不建 `ext_call` | `app/modules/integration/` |
| 函数版本 | 函数（Functions）单独存 `fn_version` 表，各函数分别递增版本号；**不走** `create_version`、不写 `ontology_release`（编辑保存 / 回滚直接生成新函数版本） | `app/modules/static/`（PDE 定）、contracts/versioning 规则 11 |
| 业务数据 | 演示门店、商品、订单归 biz 模块；所有查询由基础层注入行级过滤（§10） | `app/modules/biz/` |
| 订单列表 | `GET /api/biz/v1/orders`（首页 Home.vue:32-46）：前端下拉选「全部」时客户端**不传** `status` / `channel`（后台不认“全部”）；`kw` = 订单号或门店名包含（**区分大小写**，同前端 `includes`，前端为准；`%`、`_` 按字面，值走绑定参数）；`cutoff` = `createdAt <= cutoff`，收 `YYYY-MM-DD HH:mm` / `HH:mm:ss`，不带时区按北京时间，只到分钟时含整分钟（与前端 `CUTOFF='2026-10-24 23:00:59'` 一致 **[推断]**），格式错 422 `VALIDATION_FAILED`（`details.fields[].field = "cutoff"`）。响应 = 分页 + `dataAsOf`（`min(cutoff, 当前时间)` 截到分钟，对应页头“数据截至”）；列表项带 `lineCount`（关联子查询，不加载 `lines`），详情带完整 `lines` | `app/modules/biz/router.py` |

### 后台任务（procrastinate，`app/jobs/__init__.py` + `app/jobs/worker.py`）

| 任务名 | 队列 | 周期 | 负责人 | 说明 |
|---|---|---|---|---|
| `event.deliver` | events | 按需 defer | event | 事件投递 |
| `event.redeliver_failed` | events | 每 5 分钟 | event | 失败事件重投（退避） |
| `outbox.dispatch` | outbox | 每分钟 | PDE | outbox 分发 |
| `release.after_commit` | release | 发布提交后 defer | static | 只做可重做的派生刷新（问数缓存等）；**定义表展开在发布事务里同步完成，不走任务** |
| `action.reconcile` | reconcile | `*/15 * * * *` | integration | 扫 `reconcile_task.next_at` 到期的任务调 `query_by_key`（§8） |
| `export.run` | export | 按需 defer | 各模块 | 导出 |
| `admin.expire_api_keys` | maintenance | 每小时 | admin | API Key 到期后置为过期 |
| `admin.cleanup_audit` | maintenance | **停用** | admin | 前端没有审计保留期，先不清理 |

- 任务必须幂等，因为可能被重投。时间一律用 `clock`（§5）。
- `app/jobs/worker.py` 按 `JOBS` 注册 procrastinate 任务和周期任务（连接串去掉 `+asyncpg`）；procrastinate 自己的表不进 alembic check。
- 行级过滤可选依赖 sqlglot（问数 SQL 改写用）。
- **用户已定（2026-10-25）**：数据库保持 PostgreSQL；模型一律经 API 调用（不在本机部署）。

## 12. 新增模块 / 迁移

- **新增模块**：
  1. 复制一个占位模块，例如 `app/modules/static/`。它有 `router / schemas / models / service / repo / seeds` 这几个文件。
  2. 在 `app/api.py` 的 `MOUNTS` 里挂到对应分组。
  3. 在 `app/db/models.py` 里 import 模型，alembic 才能发现。
  4. 在 `app/seeds/registry.py` 里注册 seed 顺序：被外键依赖的模块排在前面（见 §9「加载顺序」；一个模块要分两段加载时，用 `SEED_STAGES` 登记第二段）。
- **迁移**：
  - 每个 PR 最多一个迁移；revision id、文件名、链怎么接见 **§16 模块迁移和 seed**。
  - 必须能 downgrade（§16）。
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
- **测试库**：每个模块 / worktree 用**自己的** `RENGU_TEST_DB`（如 `rengu_test_static`、`rengu_test_qa`、`rengu_test_xiaobai`），建在本机已有的 Postgres 上。**不要共用 `rengu_test`**：各分支的 alembic 版本号不同，共用会互相把 `alembic_version` 盖掉，报 `Can't locate revision`。
- **测试里的 section 注册表**：只用公开接口，**不要直接动 `sections._registry`**：
  - `tests/conftest.py` 的 autouse fixture `_isolated_sections` 用 `isolated_sections()`：每个测试从空注册表开始，结束后恢复（含 `load_sections()` 发现的 section）。
  - `with isolated_sections(*builders, empty=True):` 进入时清空（`empty=False` 则保留现有）并登记 builders，退出（含异常）恢复原样；名字和冲突照常校验。
  - 需要自己存取时用 `snap = registry_snapshot()` / `registry_restore(snap)`（有序、不可变）。
  - 要真实 section：`load_sections()`（默认扫 `app.modules`）或直接 `register_section(b)` 你的 `BUILDERS`。静态 conftest 里 `saved=dict(reg._registry)…` 那段换成 `with isolated_sections(): yield`。
- **`?ver=` / `?previewAs=`**（只看 query 参数，不看请求头）：
  - 所有读接口都要支持这两个参数，用 `ViewContextDep` 解析。
  - **只读由全局守卫负责**：`app/api.py` 给每个分组路由挂 `ReadonlyGuardDep`，非 GET 请求带任一参数 → 403 `READONLY`（两个都带按 preview）。
  - 个别写接口前端允许在只读下调用（dry-run、qa W16 申请权限），在路由上标放行模式：`@allow_in_readonly`（两种都放行）、`@allow_in_readonly(modes={"history"})`，或 `openapi_extra=readonly_extra("history", "preview")`。
  - 测试 `tests/test_core.py::test_every_mounted_write_route_is_guarded` 扫描所有挂载的写接口。
  - `previewAs` 表示以另一个用户的身份预览，由 PDE 的鉴权扩展点实现。每人同一时间只有一个预览会话（§11）。

## 14. [推断] 清单

- `EMPTY_RELEASE`：没有变更单时不允许发布。
- `ontology_release.kind`（发布/回滚）、`rollbackOf` 字段：前端只展示文案，这两个字段是后台加的。
- 行级过滤中，缺少用户属性时结果为 false（宁严勿宽）。
- 商品品类范围值 → 一级类目的别名（`CATEGORY_SCOPE_ALIAS`，静态 static-api.md §1.5）：前端没给对应关系，按名称最接近的定；不在表里的值原样比。
- 同一角色对同一对象多条范围取 OR（static-api.md §1.5 @ 团队 master 443c009，roles.ts:114；mock 里没有这种角色）。
- 演示人物编号：孙浩然 U010005、李娜 U010006（PDE 已确认）、赵蕾 U010007、吴佳 U010008，mock 里都没有编号，后端补。
- 整版回滚时被恢复版本没有某一节（content 为 None）：不调用该节的 `restore_section`，定义表不动（静态裁定，和动态一致）。

## 15. 待 PDE 定的事项

- （已定）`GET /releases` = `{items, current}`。

## 16. 模块迁移和 seed

- **编号**：
  - 骨架（基础层）占 `0001`–`0003`，revision id 是纯数字，文件名保留 `YYYYMMDD_NNNN_<说明>.py`。
  - 各模块的迁移：revision id = `<模块>_<NNNN>`，模块 = `app/modules/` 下的目录名，如 `static_0001`、`dynamic_0001`、`action_0001`、`qa_0001`。
  - 文件名 = `<模块>_<NNNN>_<说明>.py`，放在现有唯一的 `migrations/versions/` 目录里，不另开目录、不用 `version_locations`。
  - 生成：`uv run alembic revision --autogenerate --rev-id static_0001 -m "说明"`（`alembic.ini` 的 `file_template = %(rev)s_%(slug)s`，文件名前缀自动等于 revision id）。
  - 合并前，模块的第一个迁移 `down_revision = "0003"`。
- **历史保持线性、只有一个 head**：
  - 后合并的一方负责 rebase：在一个后续提交里把自己第一个迁移的 `down_revision` 改成当前 head。
  - 不写 merge 迁移，不用 `branch_labels`。
  - 一个人负责多个模块时，也按模块分开写迁移（每个模块各自的 `<模块>_<NNNN>`，不合成一个），在自己的分支里线性串起来，如 `integration_0001` → `action_0001` → `event_0001`；其中第一个的 `down_revision` 设为当前 head，后面的依次指向前一个。
- **只动自己的表**：
  - 模块的迁移只建 / 改本模块的表。
  - 指向别的模块表的外键：只有对方已经合并（在链上更早）才能建。否则先按编号（code）软引用，等对方合并后在后面的迁移里补外键。
- **必须能回退**：
  - 每个迁移都要有可用的 `downgrade()`。
  - `alembic upgrade head` → `downgrade base` → `upgrade head` 必须跑通。
- **测试**：`tests/test_migrations.py` 会检查以下几点：
  - 只有一个 head；
  - 非骨架 revision id 都符合 `^[a-z_]+_\d{4}$`，且文件名前缀等于 revision id；
  - 在临时库上跑一遍升 → 降到 base → 再升（不留表、自定义类型）。
- **模块自己的往返测试**：**不要假设自己是 head，也不要假设父迁移是 `0003`**（合并时 `down_revision` 会被改指当前 head）。不写死 `downgrade "0003"`、`get_current_head() == "static_0001"`、`down_revision == "0003"` 这类断言。直接用骨架的辅助函数：

  ```python
  from tests.helpers.migrations import assert_module_round_trip

  async def test_static_migration_round_trip():
      await assert_module_round_trip("static", STATIC_TABLES, own_types={"..."})  # 只列本模块自己的表 / 类型
  ```

  - 它用 `ScriptDirectory` 查本模块第一个迁移的 `down_revision`（`module_parent("static")`），在临时库上：升到该 revision 记状态 → 升 head（本模块的表都在）→ 降回该 revision（排在后面的模块迁移一起降掉；本模块的表消失，表 / 列 / 索引 / 触发器 / 序列 / 视图 / enum 等自定义类型 / 函数和降之前**完全一致**，不留残留）→ 再升 head（和第一次 head 完全一致）。
  - 只比较本模块自己的表和类型，别的模块的表不用列。骨架自己也用它（`module="skeleton"`，父 = base）。
  - 要检查迁移链形状时用 `module_revisions(m)` / `module_parent(m)`，只断言「本模块的迁移在链上连续、第一个的父不属于本模块」这类不随合并变化的性质。
- **seed**：
  - 只放在 `app/modules/<模块>/seeds/`，经现有的 seed 注册表加载（`app/seeds/registry.py` 的 `SEED_MODULES` / `SEED_STAGES`，§9）。
  - 已登记的模块不用改注册表；新模块 / 新阶段只加自己那一行。
  - 不改别的模块的 seed。
