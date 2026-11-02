# 请求级数据库访问检查（2026-11-01）

本次检查 `backend/app/db/session.py`、`backend/app/common/auth.py`、`backend/app/common/ontology.py`、`backend/app/common/repository.py` 及其直接调用关系，关注管理与静态建模页面的只读请求入口。核心身份、事务及连接配置未改；本次运行时代码只修改 `backend/app/modules/data/external_mirror.py::governed_mirrors` 的元数据加载，将循环内查询改为本次加载中的批量读取。

## 本次实施：请求入口治理元数据批量读取

调用链为 `bind_workspace → set_ontology_scope → load_governed_tables → governed_mirrors`；`governed_mirrors` 只有这一个源码调用者。本次不修改镜像执行、同步调度或外源连接。

原实现对每个镜像状态单独查询对象，`selectinload(ObjectType.properties)` 加载属性，已发布对象再单独查询外部授权绑定与数据源。对于 N 个对象，这部分元数据查询按源码结构最多约 4N 条（对象、属性、绑定、数据源各一轮）；对象缺失、未发布、绑定缺失或 identity map 已有属性集合时会减少，**不是所有请求的固定次数，也不是测得的数据库往返数**。

新实现一次读取 `ObjectType LEFT JOIN ExternalReadBinding LEFT JOIN Datasource`，再由原来的 `selectinload` 批量加载属性。三个实体均带显式 `ontology_id` 条件，仍通过当前请求事务与原有 RLS。N=0 时不发这次批量元数据查询；对象存在且需要加载属性时是 1 条 JOIN 加属性加载批次。本地安装的 SQLAlchemy `orm/strategies.py` 中 selectin 批大小为 500，故最多 500 个待加载对象时通常是这部分 2 条 SQL；已有属性集合可能使属性加载进一步减少。不把 fake session 的方法调用次数当作真实 SQL 数。

对象 code / 数据源 code 的唯一约束由 `CodeMixin` 声明，`ontology_0018_workspaces` 迁移将其转换为空间内唯一；`data_0043_external_read` 对绑定声明 `(ontology_id, object_code)` 唯一。结合显式空间 JOIN 条件，每个对象至多匹配一个绑定及数据源，不会因跨空间同编号扩行。本次仅核对模型与迁移源码，没有探查线上约束状态。

状态可用性、签名、敏感与隐藏字段过滤、投影、主键、名称冲突、来源映射、gaps、旧 schema 分支均保留。没有增加跨请求缓存，没有调用外部数据库，也没有修改本地治理绑定的每次校验及表反射。

## 源码可证明的已有改善

以下比较来自当前工作区与 Git 基线的源码差异，**不是本次新增改动，也不是数据库实测**。只统计注明的局部 SELECT；不包括事务事件、角色聚合、治理表加载和页面自身查询。

| 局部流程 | Git 基线 | 当前工作区 | 确定收益及条件 |
| --- | --- | --- | --- |
| `bind_workspace` 真人有效成员：取成员、取用户、进入 `object_row_access` 再取成员 | 3 次 SELECT | 1 次 SELECT | 当前一次 JOIN 返回成员和用户编号，直接传 `member` / `user_code`；减少 2 次查询 |
| 同一流程，owner 分支获取 `user_code` | 额外 1 次 SELECT | 0 次 | 当前直接传入 JOIN 已返回的编号；适用于实际进入 owner 分支 |
| `resolve_user` 在会话已有空间时：读取空间成员，再由 `object_row_access` 获取成员 | 2 次 SELECT | 1 次 SELECT | `_resolved_user` 读取完整成员对象，并传给权限计算；减少 1 次查询 |

当前 Cookie 登录路径 `session_user → _resolved_user` 复用已验证的 `SysUser`，没有再次按编号读取用户。Git 基线使用另一种身份入口，不能将其错误地当成同一业务流程的前后性能对照。

`get_current_user` 在 `request.state.current_user` 保存本次请求身份，路由全局 `ApiKeyGuardDep`、`OntologyGuardDep` 与处理函数使用身份依赖时不会各自重新查一轮身份。没有增加跨请求权限缓存。

## 现有请求过程与不能删的工作

普通 Cookie 只读请求先执行会话与用户联合校验，再聚合功能权限。对于需要工作空间的请求，随后检查工作空间、成员资格、绑定事务空间，并加载对象行级权限。管理请求对无效选中空间有既定容错；空间发现接口有既定分支。不能按一个固定数字声称所有页面的总 SQL 次数。

`set_ontology_scope` 在已开启事务时获取空间生命周期共享锁，检查空间仍有效，设置事务本地 `rengu.ontology_id`，再加载治理表。`after_begin` 设置受限数据库角色；`SET LOCAL` 随提交或回滚重置。这些 SQL 构成隔离与删除并发的边界，未删除、合并或缓存其结果。后台专用开关及执行路径不在本次调优范围。

治理表结果已有会话/事务内复用和失效逻辑：`ensure_governed_tables`、绑定变更失效、事务结束清理。首次加载仍会进入 `load_bindings` 的逐绑定校验/反射；这部分涉及物理表与只读账号授权检查，本次保留。`governed_mirrors` 的循环元数据读取已批量化，但没有测量该函数的实际数据库耗时，不能断言它就是现网瓶颈。本次不跳过授权治理校验，不加全局缓存。

`BaseRepository.list_page` 先 count，再按总量收拢越界页，最后取该页，正常是 2 条业务查询；count 已移除 ORDER BY。没有改成窗口函数或取消 count，避免改变空列表、越界页与总数契约。`get` 已使用 SQLAlchemy identity map；按业务键读取不能直接换成主键缓存。

## 未实施的候选

真人 `bind_workspace` 当前分别取工作空间和成员，冷身份路径通常是 2 条局部业务 SELECT。可考虑 LEFT JOIN 合并为 1 条，但必须保持不存在为 404、非成员为 403、已删除空间拒绝、管理接口容错、API Key 分支和绑定后权限计算等行为。共享入口也服务执行类接口；为限制到管理/建模只读请求而增加分支，会使本次节省单次往返的方案更复杂。没有实测证据时，本次不新增该修改。

没有修改 `core/config.py`，没有调整连接池大小或连接检查，也没有修改请求事务提交/回滚时机。单次数据库快照不能反映应用连接池排队或完整负载分布，不能通过扩大 pool 推断性能改善。

## 补充：一次本机数据库只读元数据采样

后续获得仅连接本机 `rengu_ontology` 观察聚合元数据的明确授权后，完成一次采样。配置只从被忽略的 `backend/.env` 内存读取；连接前强制校验 PostgreSQL、host 为 loopback、数据库名精确等于 `rengu_ontology`，未输出 URL、账号或密码。使用独立诊断连接，startup 参数为 `default_transaction_read_only=on`、`statement_timeout=5000`，并在一个显式只读事务中执行 **4 条小型元数据 SELECT**。第一条验证实际数据库和 `transaction_read_only=on`；确认超时为 `5s` 后，读取 activity 聚合、锁聚合及数据库统计。事务结束后关闭诊断连接。

数据库返回的采样时间为 **2026-11-01 02:21:35.133262+08**。结果如下：

| 项目 | 单次观察值 | 解释边界 |
| --- | --- | --- |
| `pg_stat_activity`：idle / Client | 8 个连接 | 限当前数据库，排除诊断连接；Client 表示等待客户端，不代表数据库锁等待 |
| `pg_stat_activity`：idle in transaction / Client | 1 个连接 | 本次快照中的未结束事务；其事务年龄约 1.789 秒，不能仅凭这一次短事务判断连接泄漏 |
| 最大可见事务年龄 | 1.788596 秒 | 当前库、排除自身、按可见 `xact_start` 计算；没有输出会话 SQL 或参数 |
| `pg_locks` 未获授锁请求 | 0 | 通过 pid 关联当前数据库 activity，排除自身 |
| 等待未获授锁的连接 / `pg_blocking_pids` 非空的连接 | 0 / 0 | 当时未观察到阻塞；不代表过去或峰值时段没有阻塞 |
| `pg_stat_database.numbackends` | 10 | 当前数据库连接数，包含本次诊断连接；不是应用自身连接池大小 |
| `max_connections` | 100 | PostgreSQL **实例级配置上限**，不是当前库或单个应用的专用容量 |
| `temp_bytes` / `temp_files` | 0 / 0 | 数据库累计统计，本次没有执行排序负载或测量临时文件增量 |
| `deadlocks` | 0 | 数据库累计统计，不能解释成本轮请求的死锁率或优化收益 |
| `stats_reset` | NULL | 本次返回未提供重置时间，不能据此推断统计窗口长度 |

这次观察只读取 `pg_stat_activity`、`pg_locks`、`pg_stat_database` 及实例设置，没有输出 query 文本/参数、业务行或凭据，没有访问 DM、远程库、`auto_ontology` 或军事业务表。没有执行功能测试、EXPLAIN ANALYZE、benchmark、压测、DDL、配置修改或安装扩展，也没有持续监控、再次采样或尝试重启。

新源码未加载：采样反映当前旧后端与其他已存在连接的数据库状态，**不是优化后的性能测量，更不是前后对照**。当时没有锁阻塞、只有一个约 1.8 秒的闲置事务，不支持新增连接池/数据库全局调参；源码确认的逐对象元数据往返才是本轮实施依据。

## 本次验证与运行边界

上述 4 个核心源文件，以及本次修改的函数所在文件与自检脚本，均用 `compile(..., 'exec')` 在内存编译通过，没有启动服务。

新增 `backend/scripts/check_governed_mirror_metadata.py`，从源文件 AST 提取本次函数及其纯函数辅助逻辑，用内存 fixture 模型与 fake session 验证；不导入应用，不创建引擎，不使用网络或真实数据库。运行方式：在 `backend` 目录执行 `.venv\Scripts\python.exe scripts/check_governed_mirror_metadata.py`。已通过 1 与 12 个对象的固定批量调用结构、显式空间 LEFT JOIN、有效投影、缺对象/绑定/数据源、签名不符、敏感属性、未发布、重启标记、名称冲突、已有治理对象、孤立授权绑定、空状态、旧 schema 分支检查。它不是 RLS、ORM 真实属性加载或真实数据库 SQL 数的集成验证。

没有运行 pytest、集成测试、迁移测试、后台 benchmark、业务回放或全量后台流程；没有读取/修改真实业务记录，没有调用远程系统、模型或真实业务接口。数据库访问仅限上节明确授权的本机只读聚合元数据采样。规则求值、事件吞吐/调度、动作及军事自动执行链未做优化或业务验证。

会话记录中的 8000 服务为旧进程（PID 23236，启动于 2026-10-31 16:19）。本次未重新探测进程、重启、停进程、热加载、开新端口、修改环境变量或尝试绕过自动审核。普通重启曾被工具自动审核阻止，当前服务不能作为已加载工作区改动的证据。

本报告给出源码证据、静态编译、离线自检与一次当前数据库元数据快照。优化会在一次治理加载中持有所需对象、属性、绑定与源的内存映射，内存规模随镜像对象数增长；原逻辑最终也遍历相同元数据。优化对实际延迟、吞吐、连接占用及真实权限行为的效果仍未测量；应在正式允许的隔离环境中，以具体管理/建模页面的只读业务步骤验证，不能把本文的局部查询减少写成实测加速。
