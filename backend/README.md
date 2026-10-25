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
make seed      # 把 seeds/json/<module>/ 和 seeds/extra/<module>/ 幂等写入数据库
make dev       # 启动 http://127.0.0.1:8000，接口文档在 /docs
make check     # 提交前必跑：ruff 检查 + 格式检查 + alembic check + pytest
```

还没有模块登记版本快照 section 时，`make seed` 的 versioning 一步会记一行「跳过」日志，不写 `ontology_release`、不报错。

- 健康检查：`GET /healthz`、`GET /readyz`（readyz 会连库）。
- 示例接口：`GET /api/ontology/v1/releases`、`/releases/current`、`/releases/{ver}`、`/releases/{ver}/snapshot`；演示业务数据 `GET /api/biz/v1/orders`、`/orders/{id}`。
- 升级到迁移 0002 前库里不能有版本行（published_by 改成外键），已有库请 `make db-reset`。
- 演示身份：请求头 `X-Demo-As: U010001`（sys_user 编号；王敏=管理员，U010006 李娜=非管理员），只在 dev/test 生效。
- 演示人物（共 9 人：前 8 人在 `seeds/extra/system/SYS_USERS.json`，苏刚在 `seeds/json/system/ADMIN_USERS.json`）：

  | 编号 | 姓名 | 角色 | 来源 |
  |---|---|---|---|
  | U010001 | 王敏 | 本体管理员 | mock 审计日志 |
  | U010002 / U010003 / U010004 | 陈立军 / 何静 / 刘婷 | 非管理员 | mock 审计日志 |
  | U010005 / U010006 | 孙浩然 / 李娜 | 非管理员 | [推断]，PDE 已确认 |
  | U010007 / U010008 | 赵蕾 / 吴佳 | 非管理员 | [推断] |
  | U010231 | 苏刚 | 非管理员（业务查看） | 已在 `seeds/json/system/ADMIN_USERS.json`（mock 部门成员），不在 SYS_USERS.json 重复；前端 `?previewAs=` 的默认预览对象（`src/stores/preview.ts:14`） |

  seed 的 sys_user 姓名必须唯一（ADMIN_USERS.json + SYS_USERS.json 合起来校验；动态按姓名比较「本人」，重名 seed 报错）；`sys_user.alert_assignable`（1..7 = 动态 PEOPLE 顺序，null = 不可指派）由动态的 seed 按姓名回填，admin seed 不写。
- 时钟：dev/test 环境固定在 `2026-10-25T15:42:00+08:00`（`RENGU_FIXED_NOW`）；prod 用真实时间。
- 测试使用独立的 `rengu_test` 库，测试会自动建库和迁移。每个用例在事务里运行，跑完回滚。
- 也可以用容器跑 API：`docker compose -p rengu-backend --profile api up`。

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
  cli.py       python -m app.cli seed [-m 模块] [--dry-run] / list-seeds
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
