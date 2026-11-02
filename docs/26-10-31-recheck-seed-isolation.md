# 复检 §5.4：演示种子与工作空间隔离

## 核查结论及边界

本次只核查本地源码并运行不连接数据库的局部检查。未连接复检报告中的电脑、生产库或工作空间 6，未查询真实业务统计、迁移、重启、删除或移动任何记录。报告中的“同库同表”不足以证明跨本体泄漏；当前没有生产查询证据，不能宣称已清理或生产隔离验收通过。

- `Base.__init_subclass__` 为领域表统一添加 `ontology_id`，并把唯一约束扩展到工作空间。
- `ontology_0018_workspaces` 为事件、类型、载荷、统计等领域表开启 `ENABLE/FORCE ROW LEVEL SECURITY`，策略按当前事务 `rengu.ontology_id` 限制读写，并阻止跨空间引用或移动行。
- 请求经 `require_func → bind_workspace → set_ontology_scope` 校验成员和目标空间；数据库事务降权到 `rengu_ontology_scoped`。事件列表、类型查询和运行统计因此依赖同一数据库 RLS 边界。源码未发现这些读路径另行绕过该边界；不增加按事件名或 `is_seed` 隐藏数据的补丁。
- `event`、`event_type` 等已经有 `is_seed`。通用 loader 会标记种子，生产新写入默认 false；无需加字段或迁移。
- API `main.py`、Docker 启动和 `run_dev.py` 不自动灌种子。原 CLI `seed` 无显式选择即写固定空间 1；loader 的直接调用原可跟随任意会话空间，且自然键 upsert 可覆盖业务同名行。这是已修复的共享入口风险。报告中存量行的实际来源仍须部署方审计，不能从名称直接归因。
- 新空间原只初始化 `ActionExecuted`、`ActionOutcomeUnknown` 两个系统事件；保留这一行为。

## 入口变更

`seed` 现在必须显式 `--demo`；CLI 和共享 loader 都禁止 `RENGU_ENV=prod`，loader 还要求会话空间为 1。现有演示阶段含硬编码空间 1 的角色/成员引用，不能把该 loader 当作任意空间初始化工具。普通业务读取继续按空间正常显示同名事件。

种子 upsert 在数据库冲突语句内增加 `WHERE 既有行.is_seed IS TRUE`。遇到非种子同键行不会覆盖，也不会把它改标成种子，而是抛错；CLI 关闭会话并回滚整批。直接调用 `run_seeds` 的调用方必须保留其原有事务约定：异常时整批回滚，不得捕获异常后提交。普通同步/业务 upsert 未启用此选项，行为不变。

原 `python -m app.cli seed` 现在失败；隔离开发/评测库明确选择演示数据时改为：

```powershell
cd /实际项目路径/backend
python -m app.cli seed --demo --dry-run
python -m app.cli seed --demo
# 单阶段仅在依赖已经满足时使用：
python -m app.cli seed --demo -m event
```

`make seed`、`make seed-dry` 已对应添加显式选择。这里的旧式 `seed --dry-run` 仍是尝试写入后回滚，会执行插入/约束并消耗序列号；它不是存量业务库只读审计工具，禁止用它检查生产脏数据。

## 空白生产环境的独立系统初始化

新增 `system-init --database 数据库名`，默认只读；仅 `--apply` 才在一个事务中创建必要内容。配置连接必须与显式数据库名一致，否则不连接。部署方须先核对目标连接并完成备份、审阅正常增量迁移；本次没有执行这些操作。

```powershell
cd /实际项目路径/backend
# 按部署配置提供 RENGU_DATABASE_URL、RENGU_ENV=prod；不在日志中打印连接串。
python -m app.cli system-init --database 实际平台数据库名
# 审阅只读结果、确认这是首次安装的空白系统后执行：
python -m app.cli system-init --database 实际平台数据库名 --apply
```

该入口复用现有密码哈希接口，创建缺失的初始 admin、组织/租户“仁谷科技”、管理员功能角色、空间 1 owner 和角色绑定，以及缺失的标准系统事件。初始密码沿用既有部署约定，命令不输出密码/哈希；已有密码不会重置，已有字段不会覆盖。它不读取零售种子定义、业务动作或演示事件，不创建 API Key，不启动 worker。仅支持空白首次安装或该入口自身建立的、尚未装载业务定义的系统；既有其他用户、业务定义或运行数据会使初始化停止。既有系统应使用正常管理流程，不应靠重新 seed 恢复账号。

**历史迁移来源及本次前向修正：** `ontology_0018` 本身先创建空间 1；原 `dynamic_0024` 随后会在该空间存在时从 `SC_TEMPLATES.json` 无条件回填历史零售模板。本次保留 24 的 revision 和全部建表/隔离/授权步骤，只把演示回填改为显式 `alembic -x demo_seed=true upgrade head` 才执行；正常 `alembic upgrade head → system-init` 默认不会新灌模板。这个选项只适用于明确需要演示模板的隔离演示库，生产不用它。迁移 downgrade 改为拒绝，防止删除整张表误伤后续真实模板。

已记录 24 为完成的部署不会重跑其任何代码，不会自动清理或改写已有模板；仅替换程序不能宣称历史模板已消失。使用旧版本迁移脚本初始化的库仍需上述只读来源审阅。空间 1 已存在而 24 尚未应用的旧部署，升级前应核对使用的是带显式开关的新脚本，保留原配置和数据。这些模板与报告演示事件来源分开记录，不按模板名称猜测归属。

`SC_TEMPLATES.json` 现正常登记在 `dynamic` 演示 seed 阶段，因此隔离演示库在结构迁移完成后显式 `seed --demo` 即可导入这些定义，无需迁移时选择演示回填；不会调用场景执行器。该阶段复用生产禁止、空间限制和非seed同键冲突保护。

## 存量只读审计（由部署方在授权目标执行）

新增 `backend/scripts/audit_seed_events.py`，没有 apply/delete 模式。必须给出单一空间和与配置一致的数据库名：

```powershell
cd /实际项目路径/backend
python scripts/audit_seed_events.py --ontology 授权核查的空间编号 --database 实际平台数据库名
```

脚本在 `READ ONLY` 事务中降权到应用角色，设置指定空间，检查事件核心表 FORCE RLS，输出：

1. 与仓库演示清单同名的类型是否标记 `is_seed`；其事件总数、seed/非seed/测试数量及与原始事件编号清单匹配的计数。
2. 指向这些类型和事件的单列真实外键引用计数；有 `is_seed` 的子表额外统计非seed引用。只读统计不包含载荷、凭据、业务对象明细。
3. 固定 `deletableRows=null`：此结果只是审阅线索，不授权删除。脚本不查询别的空间，不逐空间循环；没有同名结果也不等于整库不存在其他来源的演示数据。

如存在跨空间显示，先检查实际部署版本、请求 `X-Ontology-Id`、成员授权、RLS策略和运行角色，而不是删除真实同名业务定义或绕过 RLS 做全库统计。脚本只检查 RLS 开关，完整策略表达式和运行账号属性仍应由部署方按隔离文档核对。

## 存量处置原则（默认不执行）

- 如果零售样本只在演示空间 1、实际业务在另一个空间且读取隔离正常，同库同表本身无需清理。修正错误的查询空间/运维统计口径即可。
- 名称、编号前缀、`is_seed=true` 任何单项都不足以证明可删除。必须比对对应版本种子文件的完整转换结果、定义/载荷、发生时间和来源，以及用户后续修改历史；被业务采纳或修改过的样本保留。
- 审计还必须检查软引用、规则/生命周期 JSON、动作输出、变更单与不可变版本快照；外键统计不能覆盖这些引用。存在任何非seed引用、真实运行/投递记录、版本历史或来源不确定时，保留记录并单独审阅，不级联删除、不改写快照、不移动 `ontology_id`。
- 只有经备份、确认完整来源和全链路引用后，才为**明确清单中的代理 ID**编写独立处置方案；事务必须同时校验目标空间、完整内容指纹与当前引用/并发版本，任一变化即停止。先输出 dry-run 清单，经部署方审核再执行。本次没有提供或执行自动删除命令，也不把种子审计输出当成删除白名单。

## 本次局部验证

```powershell
cd /实际项目路径/backend
.venv/Scripts/python.exe scripts/check_seed_isolation.py
.venv/Scripts/python.exe -m compileall -q app/seeds app/cli.py app/db/dialects app/modules/event/workspace_defaults.py migrations/versions/dynamic_0024_scenario_template.py scripts/audit_seed_events.py scripts/check_seed_isolation.py
.venv/Scripts/python.exe scripts/audit_seed_events.py --help
```

保留检查使用 mock 和 PostgreSQL SQL 编译，覆盖生产/缺失/错误空间拒绝、非seed冲突保护、CLI误操作前拒绝连接、系统初始化默认只读及已有用户拒绝、标准事件仅补缺失、审计事务只读/外键统计。迁移只解析 AST 并求值开关条件，确认默认/false 不回填、显式 true 选择演示及 downgrade 拒绝；不调用迁移函数。没有运行 pytest、完整测试、迁移升降级或任何真实初始化/清理。部署方仍需在备份后的专用空库验证首次安装、重跑幂等和 RLS 隔离，不应直接在已有业务库试灌演示数据。
