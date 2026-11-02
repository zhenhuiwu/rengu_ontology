# rengu_ontology 后台骨架

技术栈：Python 3.12、uv、FastAPI、Pydantic v2、SQLAlchemy 2（async，asyncpg 驱动）、Alembic、PostgreSQL 17 + pgvector、ruff、pytest、docker compose。

**开发约定见 [`docs/backend/CONVENTIONS.md`](docs/backend/CONVENTIONS.md)**。合并时这份文档移到仓库顶层的 `docs/backend/`。

## 运行

```bash
export DOCKER_HOST=tcp://127.0.0.1:2375   # 按本机 docker 实际情况设置
cp .env.example .env                      # 可选，默认值已能在本地跑
uv sync
make up        # PostgreSQL 17 + pgvector，宿主机端口 5440（RENGU_PG_PORT 可改）
make migrate   # alembic upgrade head
make seed      # 显式导入零售演示数据（仅非生产空间 1；生产禁止）
make dev       # 启动 http://127.0.0.1:8000，接口文档在 /docs
make check     # 提交前必跑：ruff 检查 + 格式检查 + alembic check + pytest
```

还没有模块登记版本快照 section 时，`make seed` 的 versioning 一步会记一行「跳过」日志，不写 `ontology_release`、不报错。

生产首次安装使用 `python -m app.cli system-init --database 实际数据库名` 默认只读检查，通过审阅后显式添加 `--apply`；不要运行演示 seed。存量演示事件审计、旧命令变化与保留业务引用的处置边界见[种子隔离说明](../docs/26-10-31-recheck-seed-isolation.md)。

- 健康检查：`GET /healthz`、`GET /readyz`（readyz 会连库）。
- 示例接口：`GET /api/ontology/v1/releases`、`/releases/current`、`/releases/{ver}`、`/releases/{ver}/snapshot`；演示业务数据 `GET /api/biz/v1/orders`、`/orders/{id}`。
- 升级到迁移 0002 前库里不能有版本行（published_by 改成外键），已有库请 `make db-reset`。
- 真人入口使用下方的管理员登录。正常 dev/prod 服务忽略 `X-Demo-As` 和默认身份。
- 以下预置人物仅供历史业务归属参考，当前只有 `U010001/admin` 可登录：

  | 编号 | 姓名 | 角色 | 来源 |
  |---|---|---|---|
  | U010001 | 王敏 | 本体管理员 | mock 审计日志 |
  | U010002 / U010003 / U010004 | 陈立军 / 何静 / 刘婷 | 非管理员 | mock 审计日志 |
  | U010005 / U010006 | 孙浩然 / 李娜 | 非管理员 | [推断]，PDE 已确认 |
  | U010007 / U010008 | 赵蕾 / 吴佳 | 非管理员 | [推断] |
  | U010231 | 苏刚 | 非管理员（业务查看） | 已在 `seeds/json/system/ADMIN_USERS.json`（mock 部门成员），不在 SYS_USERS.json 重复；前端 `?previewAs=` 的默认预览对象（`src/stores/preview.ts:14`） |

  seed 的 sys_user 姓名必须唯一（ADMIN_USERS.json + SYS_USERS.json 合起来校验；动态按姓名比较「本人」，重名 seed 报错）；`sys_user.alert_assignable`（1..7 = 动态 PEOPLE 顺序，null = 不可指派）由动态的 seed 按姓名回填，admin seed 不写。
- 时钟：dev/prod 默认使用真实时间；test 默认固定在 `2026-10-25T15:42:00+08:00`。显式 `RENGU_FIXED_NOW` 仅用于复现。
- 测试使用独立的 `rengu_test` 库，测试会自动建库和迁移。每个用例在事务里运行，跑完回滚。
- 问数评测（`qa_0006`）：使用Procrastinate持久队列，API请求内同一连接原子提交run/queue job；全局仅允许一个待执行或运行中的真实任务，重复请求409 `QA_EVAL_BUSY`。必须提供拥有的会话`sessionId`，冻结其scope/caliber，worker重新解析当前身份权限。scope是查询提示上下文，不充当SQL权限；行级控制经真实角色/范围与SQL守卫生效，详见 [行级权限执行](docs/backend/row-access.md)。
- 单独部署QA worker：先`uv run alembic upgrade head`，再`uv run python -m app.modules.qa.eval.worker --install-schema`；启动`uv run python -m app.modules.qa.eval.worker`。此入口只消费`qa-eval`，不登记其他模块空桩/周期任务，独立加载全部ORM模型；Windows自动选择psycopg支持的Selector loop。队列schema由Procrastinate管理（Alembic已排除`procrastinate_*`），后续升级需按该包schema迁移流程处理。
- 专用worker寿命内持有**1条主库连接**登记归属，执行时另占1条主库连接跨checkpoint持全局/单run锁（主库池至少留2条，另有只读查询连接）。API不再常驻进程锁连接。Ctrl+C/停止时等待5秒后取消，任务标失败、保留已提交结果并释放锁；硬退出由下一API/worker启动恢复孤儿running为失败，不误动真实queued或活worker。中断后手动重跑，不自动续跑；缺`DASHSCOPE_API_KEY`返回503，不生成假成绩。
- 也可以用容器跑 API：`docker compose -p rengu-backend --profile api up`。

### 本地API与事件worker

本次镜像升级需要应用`data_0048_mirror_scan_visibility`及缺失前置迁移；已有部署请先按[镜像数据库增量升级操作单](../docs/26-10-31-mirror-database-migration.md)备份并保持事件worker停止。不要重置库、seed或用开发机dump覆盖部署数据；完整问题对应见[统一部署连调说明](../docs/26-10-31-deployment-integration-fixes.md)。

在已安装依赖的Python环境中，从任意目录执行 `python /实际项目路径/backend/run_dev.py`，默认仅启动API；`--api-only` 可明确这一选择。脚本固定子进程工作目录到 `backend`，存在 `.env` 时按绝对路径读取，进程环境变量优先；不会自动安装依赖、迁移、seed或安装队列schema。Ctrl+C或任一子进程退出会停止同组子进程。

确需联合管理事件worker时，显式指定 `--with-worker --ontology <工作空间ID> --event-type <事件类型编码> --subscriber <订阅名称> --after-id <上线前只读取得的事件主键高水位>`。该模式仅处理高水位后的指定单订阅事件，不扫描全部积压、不支持多订阅事件；不要把 `0` 当作未经核对的默认高水位。API单独运行不会自动启动事件投递worker；API自己的已启用镜像定时同步仍按现有生命周期运行。

生产环境将API和事件worker作为独立服务部署：在 `backend` 执行 `uv run python -m app.modules.event.worker`，保持worker单实例，由队列任务锁和投递行锁协调；不要让每个API进程再启动worker。此通用worker会处理已有符合条件的积压，启用前先按[事件保留数据升级与处置](../docs/26-10-31-event-integration-fix.md)预览和人工处置。首次缺少Procrastinate队列schema时，仅执行一次 `uv run python -m app.modules.event.worker --install-schema`；该命令只安装队列schema，不做业务迁移、不消费任务。worker的CLI入口在应用导入前读取绝对路径 `backend/.env`，已有进程环境变量不会被覆盖。模块入口从其他目录启动时仍需先把backend加入Python模块搜索路径，推荐在backend目录启动。

演示用 `RENGU_FIXED_NOW` 会冻结业务时间，不应作为生产默认配置；compose已移除硬编码演示时钟，数据库配置不变。事件的预检等待、发送重试、处理超时和新投递记录创建时间使用真实UTC，不受业务时钟冻结影响；事件 `occurredAt` 等业务含义保持原样。用户已有 `.env` 不会自动改写。

### 周期任务恢复与订阅公平选择

升级本轮调度代码前，先正常停止全部旧版周期 worker，再部署并使用原命令重启；不要让没有归属锁的旧版进程与新版混跑。无需新增业务迁移或改写 Procrastinate schema。事件、规则/页面、质量、对账、QA评测及 `app.jobs.worker` 的既有入口统一使用已锁定 Procrastinate 3.10 的公共 worker middleware 和生命周期接口。

worker 启动时及每30秒只检查当前入口已登记、所属消费队列内的已知周期任务外壳。原生心跳超过120秒失联（worker记录已丢失时还要求 started 记录超过120秒）、任务仍为 doing、参数仅含周期 timestamp，并且不能检测到该任务仍持有归属 advisory 锁，才调用官方 finish 函数标为 failed。保留任务和事件历史；日志 `Retired orphan periodic shell` 给出 queue/task/job，不打印参数或连接。有效心跳、仍持归属锁、未知任务、非周期任务及带具体业务参数的任务均不处理。不会重放中断的函数体，不重发结果不明的外部写入；下一正常周期仍由各业务既有 claim、权限、幂等和人工核查门禁判断如何处理。

事件通用 worker 仍是原命令、单进程，总并发固定为2；`event.deliver` 与 `outbox.dispatch` 各自原有全局锁保留，事件最多占一个槽位，站内订阅不必等待整个事件 sweep 完成。该槽位隔离不等于订阅公平：事件待投递按“事件类型+订阅名称”分轮，最近较少服务的订阅优先，批总量仍200；页面站内订阅每页取最早未投递一条，按最近服务时间分批，批总量仍100，同页失败头未到重试时后续不越过。对象顺序、失败前序、next_retry、接收人权限及去重仍由原业务门禁保持。历史聚合读取尚未量化性能，保留量很大时再增加持久订阅游标。

### 对象角色绑定与API密钥路径

`PUT /api/ontology/v1/workspaces/{ontologyId}/role-bindings` 的 `{roleCodes:[...]}` 仍为完整替换，遗漏的绑定会被删除。只需增删少量绑定时，使用新增 `PATCH` 同路径，传 `{add:[...],remove:[...]}`；未提及的绑定保留，重复添加或移除已不存在的绑定不重复写入，同一角色不能同时add/remove，未知角色拒绝。两种写入共享工作空间锁，仍要求真实工作空间所有者、本体管理权限及全局只读/API范围守卫，不自动授予角色或跨空间权限。

API密钥管理真实路径为 `/api/admin/v1/api-keys`（GET列表、POST创建）、`/api-keys/meta`、`/api-keys/{id}`（GET详情、PATCH修改）、`/api-keys/{id}/calls`、`/api-keys/{id}:revoke`（POST撤销）；服务账号位于同一前缀的 `/service-accounts`。这些接口不在 `/api/ontology/v1` 重复挂载。API密钥的对象读取、事件接收等业务scope与管理入口权限分别校验。

HTTP响应头 `X-Request-ID`、错误体 `requestId` 与应用日志 `[request_id]` 已关联；未处理错误给客户端返回固定错误体，堆栈留在服务端。异步worker不处于原HTTP请求上下文，日志默认请求ID为 `-`，应按投递ID/事件ID及其尝试历史关联，不能把它误判为HTTP requestId丢失。操作细节见[部署联调补充](../docs/26-10-31-deployment-integration-operations.md)。

模型配置写在被忽略的 `backend/.env`：`DASHSCOPE_API_KEY` 为问数和本体生成共用凭据，
`QA_LLM_MODEL` / `QA_LLM_BASE_URL` 及 `AUTO_ONTOLOGY_LLM_MODEL` / `AUTO_ONTOLOGY_LLM_BASE_URL`
分别指定两处使用的模型和 OpenAI 兼容接口。环境变量优先，空的 `DASHSCOPE_API_KEY` 可显式禁用；
测试默认禁用本地文件中的真实密钥，实连测试需显式传入。修改配置后重启 API 和 QA worker；不要提交或打印真实密钥。

真实数据源连接使用 `RENGU_DS_REAL_CONNECT=1`，并必须配置 `RENGU_DS_SECRET_KEY`。
本机演示启动脚本另启用 `RENGU_DS_LOCAL_CONNECT=1`：仅允许与应用主库地址、端口和库名完全匹配的 PostgreSQL 回环连接；`localhost` 固定连接 127.0.0.1。该选项不放开其他数据源地址。
生产环境或启用真实连接时，缺少有效加密密钥会阻止应用启动；该密钥用于加密已保存的数据源密码，
需要稳定保存，不能在已有密文时随意更换。部署前先配置密钥，再运行迁移和启动服务。

### 管理员登录与密码

当前只开放 `U010001/admin` 真人登录。真实认证在 dev/prod 默认启用；没有有效会话的业务请求统一返回 401 `UNAUTHORIZED`。已有 Bearer API Key 保持原有授权、范围及停用校验。历史用户 ID、作者归属和权限记录保留。

先备份平台数据库，再应用 `admin_0047_login_sessions` 增量迁移。在 `backend` 目录执行 `python scripts/init_admin_password.py` 初始化；已有密码不会被覆盖。默认账号 `admin`、密码 `admin123`，凭据写入忽略文件 `.local/admin-login.txt`，命令不输出密码。首次登录后应立即通过页面修改公开默认密码。账号重命名尚未完成的旧部署，先按 `scripts/set_default_admin.py` 的保护条件处理预置身份。管理员登录后可在页面修改密码；忘记密码时使用 `python scripts/init_admin_password.py --reset`，会恢复默认密码并撤销全部旧会话。

- `POST /api/auth/v1/login`：`{username,password}`；`GET /api/auth/v1/session`：均返回 `{authenticated:true,user:{code,name,account}}`。未登录会话返回 401。
- `POST /api/auth/v1/logout`：返回 `{authenticated:false}`，撤销服务器会话并清 Cookie。
- `POST /api/auth/v1/password`：`{currentPassword,newPassword}`，验证当前密码后修改，新密码 8–128 字符，不能与当前密码相同。成功撤销全部旧会话并给当前浏览器新会话；失败不改密码或会话。

会话为 `HttpOnly; SameSite=Lax; Path=/` Cookie，随机令牌仅存哈希于数据库，默认 8 小时过期；`RENGU_AUTH_SESSION_HOURS` 可设 1–24 小时。密码使用独立随机盐和 PBKDF2-SHA256 600,000 次迭代，不随用户目录导出。登录及改密尝试共享持久限流：15 分钟每来源 IP 最多 10 次、全站最多 100 次，超过返回 429 `LOGIN_RATE_LIMITED`；重启服务不会清除限制。反向代理应正确传递真实客户端地址，且只信任受控代理。

浏览器所有写请求（包括登录、退出、改密）必须携带与 API 同源的 `Origin`；本机 dev 额外允许 `http://localhost:5280`、`http://127.0.0.1:5280` 供 Vite 代理。部署代理应保留原 Host/协议；确有受控来源差异时配置 `RENGU_AUTH_ALLOWED_ORIGINS='["http://实际内网域名:端口"]'`，不要使用通配来源。前端请求须 `credentials: 'include'`，不向 localStorage 写入令牌。

内网 HTTP 可直接使用 Cookie 登录；HTTPS 请求自动启用 `Secure`，HTTPS 部署可显式设置 `RENGU_AUTH_COOKIE_SECURE=true`（TLS 在代理终止时也适用）。HTTP 部署保持 false。配置修改后重启 API。

只有显式设置 `RENGU_ENV=test`、`RENGU_TEST_IDENTITY_ENABLED=true`，且数据库为回环地址、库名以 `rengu_test` 开头时，旧测试夹具身份才可生效；其他环境的 `X-Demo-As` 均不能登录或切换用户。

### 将数据连接与问数部署到另一台电脑

- 勾选源表字段只是授权范围，不会自动将任意数据库表开放给问数。先在当前本体将表映射为对象并保存受控读取授权；达梦外部表还需在“数据同步”中执行同步，并确认对象与同步结果可读。问数只允许当前本体已登记的受控表及授权列，未绑定、未同步或权限不足时不能查询。
- 前端、API、迁移脚本与数据库须使用同一版本。基于 Git 提交或 `git archive` 打包只包含已提交文件；工作区中未提交或未跟踪的功能文件不会自动出现在这种包中。构建前核对外部只读绑定和同步所需模块，以及 `data_0043_external_read`、`data_0044_external_mirror`、`data_0045_mirror_runs` 迁移是否包含在发布材料内，再按该版本重新构建前端与 API。Docker 从实际构建目录复制文件；不要把本机忽略的配置、日志、备份或密钥打入镜像。
- 数据库导出包含业务数据和平台绑定记录，但不包含 PostgreSQL 全局角色及授权，也不提供应用配置。部署方须安全配置指向平台库的 `RENGU_DATABASE_URL` 和受限只读账号的 `RENGU_QA_READONLY_DATABASE_URL`，并按本体隔离策略授予必需的读取权限；不能使用超级用户或绕过行权限的账号。部署方还须按需配置 `RENGU_DS_REAL_CONNECT` 与数据源网络允许范围。
- 已保存的数据源密码为加密值。要继续使用已有密文，目标环境需通过安全渠道配置原有的 `RENGU_DS_SECRET_KEY`；如不能取得原密钥，在目标环境重新保存数据源密码。不要将密钥、真实连接串或包含敏感数据的 SQL 备份提交到代码仓库或镜像。
- 旧版 `RENGU_OBJECT_READ_BINDINGS` 是可选的本机文件配置，不能直接复制另一台机器的绝对路径。当前外部镜像的绑定和同步状态存储于平台数据库；若使用旧文件绑定，须单独配置目标环境可访问的文件。部署后用目标环境的连接测试、对象读取及问数核对，不要以本机成功推断远端已成功。
- 外部镜像的问数逻辑表名使用小写下划线，例如 `PltPlatform` 对应 `plt_platform`。对象 API 名和源表名不变；旧名称只在当前本体已登记的别名唯一匹配时兼容，带引号名称仍精确匹配，未知或有歧义的名称仍拒绝。无法匹配时最多依据当前本体可查看的受控表和字段修正一次，仍须通过完整只读权限校验。该代码修复不会改数据库、源表或镜像数据，已有有效镜像无需因此重新同步。

## 目录

```
app/
  core/        config、clock（中央时钟）、logging、errors（错误信封）、request_id、schema（ApiModel/时间格式）、enums、state（状态机）
  db/          engine/session、base（mixin + 命名约定 + is_seed + unique_except_seed）、types、dialects（upsert / 事务锁）
  common/      扩展点（PDE）：auth（身份/租户/行级范围）、row_filter（jsonb 行级过滤结构 + 转 SQL + 注入）、
               audit、pagination（page/size 和 cursor）、context（?ver= / ?previewAs=）、repository
  contracts/   跨模块契约（Protocol）：versioning（create_version(rollback_of=…) 唯一入口、SECTION_OWNERS）、actions（submit / query_by_key / reopen_reconcile / emit）、preview
  jobs/        procrastinate 任务桩（8 个）
  seeds/       seed 框架（SeedSpec、loader、registry）
  modules/
    versioning/   ★ 完整示例：模型 + 迁移 + 服务 + 路由 + seed + 测试
    static dynamic action event qa admin data integration biz   占位模块（同样结构）
  api.py       路由挂载表（/api/<group>/v1）
  main.py      create_app、/healthz、/readyz
  cli.py       python -m app.cli seed --demo [-m 模块] [--dry-run] / list-seeds / system-init
migrations/    alembic（async）；0001：pgvector 扩展 + ont_release + 只追加触发器
seeds/json/<module>/         PDE 的 npm run export-mock 生成（不要手改）；派生数据放 derived/
seeds/extra/<module>/        后端补充的 seed（mock 里没有、只为满足外键等），is_seed=true
tests/         test_core / test_versioning / test_conventions
docs/backend/  CONVENTIONS.md（合并时移到顶层）
```

## auto-ontology 接入设计（插件式，独立服务）

auto-ontology（问数引擎）**作为独立服务部署在我们的网关后面**，不并进本仓库的进程。

```
浏览器 ──▶ 网关 ──▶ rengu backend  /api/qa/v1/*  （qa 模块：鉴权、行级范围、审计、数据截止日）
                         │  内网 HTTP，带 x-auto-ontology-user-id
                         ▼
                    auto-ontology（只在内网可达）
```

- **对外不暴露**：auto-ontology 直接信任 `x-auto-ontology-user-id` 请求头，自己不做鉴权。
  - 它只能在内网访问，网关不能把它直接对外。
  - 所有请求都必须经过 qa 模块转发，由 qa 模块在转发前注入用户身份。
- **auto-ontology 不负责**行级过滤、数据截止日、变更单流程。这些由本后台在 qa 模块里补齐：
  - 行级过滤：转发前把用户的 `row_filter` 换算成它能接受的条件；做不到的就在返回后过滤。
    多角色合并照前端 `roles.ts:110-117`：defaultDeny 又没配该对象范围的角色不算；任一剩下的角色没配范围 → 不限制；否则全部范围 OR（同一角色内也 OR，[推断]）；没映射的属性 / 非正向 op / 没建表的对象 → 那条拒绝（详见 CONVENTIONS §10）。
  - 数据截止日：`RENGU_DATA_CUTOFF`。
  - 本体变更：走我们的变更单流程，不让它直接改模型。
- **按角色配置模型（LLM）**：模型 API Key 由 auto-ontology 服务端从自己的环境变量读取，不经过本后台，也不写进 seed 或日志。
- **接口映射**（qa 模块代理，前缀 `/api/qa/v1`；地址配置项是 `RENGU_AUTO_ONTOLOGY_URL`）：

| 我们的接口 | auto-ontology | 说明 |
|---|---|---|
| `/api/qa/v1/terms` | `/terms` | 术语 / 同义词 |
| `/api/qa/v1/sql-attributes` | `/sql-attributes` | SQL 属性 |
| `/api/qa/v1/custom-analyses` | `/custom-analyses` | 自定义分析 |
| `/api/qa/v1/chat` | `/chat/completions`（SSE） | 问数对话，原样流式转发 |
| `/api/qa/v1/model/import`、`/model/export` | `/model/import`、`/model/export` | 本体模型同步（导入必须先通过我们的变更单） |

- 具体字段以 `qa-api.md` 为准，由问数调研员维护。
- 插件边界：qa 模块内部定义 `AutoOntologyClient` 接口，测试时可替换成 mock，不依赖 auto-ontology 服务在线。

定时对账使用既有 `action.reconcile` / `reconcile` 队列，每15分钟检查持久任务。部署先运行 `uv run python -m app.modules.integration.worker --install-schema`，再启动 `uv run python -m app.modules.integration.worker`（仅消费对账）。默认不连接外部系统；获得授权后在ignored `.env`配置 `RENGU_INTEGRATION_REAL_CONNECT` 与 `RENGU_INTEGRATION_ADAPTERS`，详细路径/响应/失败行为见 [持久对账交接](docs/backend/handoff/reconciliation.md)。

### 达梦镜像定时同步

API 进程启动时同时启动镜像调度协程，无需另启 quality worker；仅对由工作空间所有者重新保存并明确启用的达梦定时全量配置执行。旧配置不会自动启用。暂停工作空间或将连接改为手动会停止后续批次。支持五段 Cron，按平台展示时区计算；每次从头按主键分批 upsert，不读取 CDC，不删除源端已消失的镜像行。`sync.mirrorScheduleEnabled` 与 `/api/data/v1/mirror-scheduler/status` 分别显示配置启用和进程心跳。运行记录的 readCount / writeCount 表示本次读取和执行 upsert 的行数，不代表新增或实际变更行数。API 未运行时不执行调度，恢复后对已到期计划最多执行一次，不逐次补跑错过的周期。

### 工作空间永久删除

所有者确认后，工作空间不可恢复，相关后台任务不再派发；该空间的数据源加密密码、同步计划和平台镜像行会清理。系统保留不可操作的工作空间占位、必要审计及历史记录，源数据库原始表和已发送的外部请求不受影响。旧版已停用空间不会因升级自动清理。默认工作空间不可删除。
